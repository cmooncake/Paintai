import json
import subprocess
from pathlib import Path

import httpx
import pytest

from paintai.config import LocalImageConfig, ProviderConfig
from paintai.local_image import LocalImageGenerator
from paintai.models import Painter
from paintai.prompt_guard import negative_constraints, portrait_locks
from paintai.providers import CompatibleProvider, MockProvider, ProviderError

TOPIC = (
    "Original anime head-and-shoulders portrait of an adult woman with chestnut brown bob hair "
    "and amber eyes, wearing a simple ivory blouse. Face centered, gentle smile, "
    "plain muted teal background. No text or logos."
)


def test_finality_halfbody_locks_and_runtime_agree(tmp_path, monkeypatch):
    topic = (
        "Kiana Kaslana (Honkai Impact 3rd), Herrscher of Finality form, solo, "
        "waist-up half-body portrait with entire head visible, long white hair, "
        "violet eyes with cyan iris highlights, white and purple dress with black floral ornaments"
    )
    locks = portrait_locks(topic)
    assert set(locks) == {"hair", "eyes", "clothing", "framing"}
    assert locks["clothing"] == "white and purple dress with black floral ornaments"
    negatives = negative_constraints(locks)
    assert "black hair" in negatives and "close-up" in negatives
    assert "blue eyes" not in negatives and "purple eyes" not in negatives
    exe, weights = tmp_path / "sd.exe", tmp_path / "weights"
    exe.touch()
    weights.touch()
    cfg = ProviderConfig(
        provider="sd_cpp",
        model="animagine-xl-4.0-opt",
        local_image=LocalImageConfig(
            executable=exe, model_path=weights, work_dir=tmp_path / "runs"
        ),
    )
    generator = LocalImageGenerator(cfg)
    plan = MockProvider().plan(topic, Painter(painter_id="test", name="test"))
    plan.prompt = topic + "\nclean contours"
    prepared, metadata = generator.prepare(plan)
    assert prepared.prompt.startswith("1girl, kiana kaslana")
    assert "(white hair:1.3)" in prepared.prompt
    assert "(upper body, waist-up, entire head visible:1.3)" in prepared.prompt
    assert "(white and purple dress, black floral ornaments:1.3)" in prepared.prompt

    def run(command, **kwargs):
        assert command[command.index("--negative-prompt") + 1] == metadata["negative_prompt"]
        Path(command[command.index("-o") + 1]).write_bytes(MockProvider().generate(plan))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    generator.generate(prepared)


def test_explicit_attributes_and_negative_constraints():
    locks = portrait_locks(TOPIC)
    assert locks == {
        "hair": "chestnut brown bob hair",
        "eyes": "amber eyes",
        "clothing": "simple ivory blouse",
        "background": "plain muted teal background",
    }
    negative = negative_constraints(locks)
    assert "green eyes" in negative and "dyed hair tips" in negative and "high collar" in negative
    assert "beige background" in negative
    assert "brown hair" not in negative and "yellow eyes" not in negative
    assert portrait_locks("A landscape at sunset") == {}
    assert "high collar" not in negative_constraints(
        portrait_locks("an ivory blouse with a high collar")
    )
    assert "clothing" not in portrait_locks("a simple ivory blouse with a high collar")
    assert "hair" not in portrait_locks("black and white hair")
    assert "eyes" not in portrait_locks("a woman with blue eyes and another with amber eyes")


def test_style_cannot_return_subject_changing_tokens():
    painter = Painter(painter_id="test", name="test")
    replies = [MockProvider().plan(TOPIC, painter).model_dump(), {"tags": ["green eyes"]}]
    cfg = ProviderConfig(
        provider="openai_compatible",
        model="test",
        auth="none",
        base_url="http://localhost:18081/v1",
        prompt_language="en",
    )
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps(replies.pop(0))}}]}
            )
        )
    ) as client:
        with pytest.raises(ProviderError, match="invalid structured output"):
            CompatibleProvider(cfg, client).plan(
                TOPIC,
                painter,
                requirements={
                    "style_profile": {"traits": {"palette": "green eyes, beige background"}}
                },
            )


def test_local_prompt_and_negative_constraints_match_saved_metadata(tmp_path, monkeypatch):
    exe, weights = tmp_path / "sd.exe", tmp_path / "model"
    exe.touch()
    weights.touch()
    cfg = ProviderConfig(
        provider="sd_cpp",
        model="fixture",
        local_image=LocalImageConfig(
            executable=exe, model_path=weights, work_dir=tmp_path / "runs", negative_prompt="blurry"
        ),
    )
    plan = MockProvider().plan(TOPIC, Painter(painter_id="test", name="Test"))
    plan.prompt = TOPIC + "\nsoft facial shadows"
    generator = LocalImageGenerator(cfg)
    prepared, metadata = generator.prepare(plan)
    assert TOPIC in prepared.prompt and prepared.prompt.startswith(
        "(chestnut brown bob hair:1.25), (amber eyes:1.5)"
    )
    assert plan.prompt == TOPIC + "\nsoft facial shadows"
    raw = MockProvider().generate(plan)

    def run(command, **kwargs):
        assert command[command.index("--negative-prompt") + 1] == metadata["negative_prompt"]
        assert (
            Path(command[command.index("--prompt-file") + 1]).read_text("utf-8") == prepared.prompt
        )
        Path(command[command.index("-o") + 1]).write_bytes(raw)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    assert generator.generate(prepared) == raw
