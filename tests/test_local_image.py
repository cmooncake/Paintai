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


def test_local_generator_uses_prompt_file_and_no_shell(local_config, plan, monkeypatch):
    raw = MockProvider().generate(plan)

    def run(command, **kwargs):
        assert kwargs["shell"] is False
        assert kwargs["timeout"] == 120
        assert plan.prompt not in command
        assert Path(command[command.index("--prompt-file") + 1]).read_text("utf-8") == plan.prompt
        assert command[command.index("--backend") + 1] == "cpu"
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


def test_local_generator_config_validation(local_config):
    with pytest.raises(ValidationError, match="requires local_image"):
        ProviderConfig(provider="sd_cpp")
    with pytest.raises(ValidationError, match="not a planner"):
        Settings(planner=local_config)
    with pytest.raises(ValidationError):
        LocalImageConfig(executable=Path("sd"), model_path=Path("model"), width=513)


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
