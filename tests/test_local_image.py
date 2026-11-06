import subprocess
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from paintai.agent import PainterAgent
from paintai.config import LocalImageConfig, ProviderConfig, Settings, load_settings
from paintai.local_image import LocalImageGenerator
from paintai.models import Painter
from paintai.providers import MockProvider, ProviderError, make_provider
from paintai.storage import Store


@pytest.fixture
def local_config(tmp_path):
    executable = tmp_path / "runtime with spaces" / "sd-cli.exe"
    executable.parent.mkdir()
    executable.touch()
    model = tmp_path / "weights.gguf"
    model.touch()
    return ProviderConfig(
        provider="sd_cpp",
        model="test-sd",
        local_image=LocalImageConfig(
            executable=executable, model_path=model, work_dir=tmp_path / "runs"
        ),
    )


@pytest.fixture
def plan():
    result = MockProvider().plan("树林", Painter(painter_id="test", name="test"))
    result.prompt = 'A forest; $(not a command) "quoted"'
    return result


def test_fullbody_config_roundtrip_preserves_sampling(local_config):
    local_config.local_image.width = 768
    local_config.local_image.height = 1152
    restored = ProviderConfig.model_validate_json(local_config.model_dump_json())
    assert restored.local_image.width == 768
    assert restored.local_image.height == 1152
    for invalid_height in (1153, 1216):
        payload = local_config.model_dump()
        payload['local_image']['height'] = invalid_height
        with pytest.raises(ValidationError):
            ProviderConfig.model_validate(payload)


def test_local_generator_uses_prompt_file_and_no_shell(local_config, plan, monkeypatch):
    raw = MockProvider().generate(plan)
    local_config.local_image.clip_skip = 2
    local_config.local_image.negative_prompt = "blurry, extra fingers"

    def run(command, **kwargs):
        assert kwargs["shell"] is False
        assert kwargs["timeout"] == 120
        assert plan.prompt not in command
        assert Path(command[command.index("--prompt-file") + 1]).read_text("utf-8") == plan.prompt
        assert command[command.index("--backend") + 1] == "cpu"
        assert command[command.index("--clip-skip") + 1] == "2"
        assert command[command.index("--negative-prompt") + 1] == "blurry, extra fingers"
        Path(command[command.index("-o") + 1]).write_bytes(raw)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    with httpx.Client() as client:
        generator = make_provider(local_config, client)
        assert isinstance(generator, LocalImageGenerator)
        assert generator.generate(plan) == raw
    assert local_config.credential() == ""


@pytest.mark.parametrize("failure", ["timeout", "exit", "missing"])
def test_local_failures_are_reported(local_config, plan, monkeypatch, failure):
    def run(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 120)
        return subprocess.CompletedProcess(command, 3 if failure == "exit" else 0)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ProviderError, match="log:"):
        LocalImageGenerator(local_config).generate(plan)


def test_missing_runtime_does_not_start_process(local_config, plan, monkeypatch):
    local_config.local_image.executable.unlink()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Must not execute"))
    with pytest.raises(ProviderError, match="missing"):
        LocalImageGenerator(local_config).generate(plan)


def test_model_suffix_preserves_brief_and_is_visible_in_prepared_plan(local_config, plan):
    plan.prompt = (
        "An adult woman with chestnut brown bob hair and amber eyes, "
        "wearing a plain ivory blouse, plain muted teal background."
    )
    original = plan.prompt
    local_config.local_image.prompt_suffix = "masterpiece, high score, great score, absurdres"
    generator = LocalImageGenerator(local_config)
    prepared, constraints = generator.prepare(plan)
    assert original in prepared.prompt
    assert plan.prompt == original
    assert constraints["protected_attributes"]["eyes"] == "amber eyes"
    assert "green eyes" in constraints["negative_prompt"]
    assert prepared.prompt.endswith(local_config.local_image.prompt_suffix)
    assert constraints["model_prompt_suffix"] == local_config.local_image.prompt_suffix
    repeated, _ = generator.prepare(prepared)
    assert repeated.prompt.count(local_config.local_image.prompt_suffix) == 1


def test_reference_image_is_passed_to_runtime_and_hash_checked(
    local_config, plan, monkeypatch, tmp_path
):
    import hashlib

    reference = tmp_path / "reference.png"
    reference.write_bytes(MockProvider().generate(plan))
    local_config.local_image.ip_adapter_path = tmp_path / "adapter.safetensors"
    local_config.local_image.clip_vision_path = tmp_path / "vision.safetensors"
    local_config.local_image.ip_adapter_path.touch()
    local_config.local_image.clip_vision_path.touch()
    generator = LocalImageGenerator(local_config)
    generator.set_reference(
        reference, hashlib.sha256(reference.read_bytes()).hexdigest(), 0.6, "ref-id"
    )

    def run(command, **kwargs):
        assert command[command.index("--ip-adapter-image") + 1] == str(reference.resolve())
        assert command[command.index("--ip-adapter-strength") + 1] == "0.6"
        assert "--clip_vision" in command and "--ip-adapter" in command
        Path(command[command.index("-o") + 1]).write_bytes(reference.read_bytes())
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    assert generator.generate(plan) == reference.read_bytes()
    reference.write_bytes(b"changed")
    with pytest.raises(ProviderError, match="changed"):
        generator.generate(plan)


def test_local_generator_config_validation(local_config):
    with pytest.raises(ValidationError, match="requires local_image"):
        ProviderConfig(provider="sd_cpp")
    with pytest.raises(ValidationError, match="not a planner"):
        Settings(planner=local_config)
    with pytest.raises(ValidationError):
        LocalImageConfig(executable=Path("sd"), model_path=Path("model"), width=513)


def test_lora_runtime_loading_and_integrity(local_config, plan, monkeypatch, tmp_path):
    import hashlib

    adapter = tmp_path / "trial.safetensors"
    adapter.write_bytes(b"test-adapter")
    generator = LocalImageGenerator(local_config)
    sha = hashlib.sha256(adapter.read_bytes()).hexdigest()
    generator.set_lora(adapter, sha, 0.75)
    original_prompt = plan.prompt

    def run(command, **kwargs):
        assert command[command.index("--lora-model-dir") + 1] == str(tmp_path.resolve())
        prompt = Path(command[command.index("--prompt-file") + 1]).read_text("utf-8")
        assert prompt == original_prompt + "\n<lora:trial:0.75>"
        assert kwargs["shell"] is False
        Path(command[command.index("-o") + 1]).write_bytes(MockProvider().generate(plan))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    generator.generate(plan)
    assert plan.prompt == original_prompt
    adapter.write_bytes(b"changed")
    with pytest.raises(ProviderError, match="changed"):
        generator.generate(plan)
    with pytest.raises(ProviderError, match="hash mismatch"):
        generator.set_lora(adapter, sha)
    for scale in [0, -1, 1.1, float("nan")]:
        with pytest.raises(ProviderError, match="strength"):
            generator.set_lora(adapter, sha, scale)


def test_local_paths_relative_to_config(tmp_path):
    path = tmp_path / "local.toml"
    path.write_text(
        """
[generator]
provider = "sd_cpp"
model = "test"
[generator.local_image]
executable = "runtime/sd-cli.exe"
model_path = "weights.gguf"
work_dir = "runs"
""",
        encoding="utf-8",
    )
    settings = load_settings(path)
    assert settings.generator.local_image.executable == tmp_path / "runtime" / "sd-cli.exe"
    assert settings.generator.local_image.model_path == tmp_path / "weights.gguf"


def test_pipeline_marks_only_mock_roles(local_config, tmp_path, plan, monkeypatch):
    raw = MockProvider().generate(plan)

    def run(command, **kwargs):
        Path(command[command.index("-o") + 1]).write_bytes(raw)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    store = Store(tmp_path / "gallery")
    store.add_painter(Painter(painter_id="test", name="test"))
    settings = Settings(generator=local_config)
    mock = MockProvider()
    result = PainterAgent(store, settings, mock, LocalImageGenerator(local_config), mock).create(
        "树林", "test"
    )
    assert result["status"] == "complete"
    assert result["simulation"] == {"planner": True, "generator": False, "critic": True}
    assert result["simulated"] is True
    assert store.image_path(result["image_path"]).read_bytes().startswith(b"\x89PNG")
