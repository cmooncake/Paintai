import json
from io import BytesIO

import pytest
from PIL import Image
from pydantic import ValidationError

from paintai.agent import CreationFailed, PainterAgent
from paintai.cli import main
from paintai.config import ProviderConfig, Settings, load_settings
from paintai.models import Painter, Scores
from paintai.providers import MockProvider, ProviderError, normalize_image
from paintai.storage import Store


@pytest.fixture
def pipeline(tmp_path):
    store = Store(tmp_path)
    store.add_painter(Painter(painter_id="alice", name="阿梨"))
    store.add_painter(Painter(painter_id="bob", name="小白"))
    mock = MockProvider()
    agent = PainterAgent(store, Settings(data_dir=tmp_path), mock, mock, mock)
    return store, agent


def test_creation_persists_after_restart_and_isolates_painters(pipeline):
    store, agent = pipeline
    entry = agent.create("秋天的树林", "alice")
    assert entry["status"] == "complete"
    assert entry["simulated"] is True
    assert entry["simulation"] == {"planner": True, "generator": True, "critic": True}
    assert entry["critique"]["overall"] == 5
    assert entry["plan"]["perception"]["subject"] == "秋天的树林"
    reopened = Store(store.root)
    assert reopened.get("alice", entry["id"]) == entry
    assert reopened.gallery("bob") == []
    with pytest.raises(ValueError, match="not found"):
        reopened.get("bob", entry["id"])
    with Image.open(store.image_path(entry["image_path"])) as image:
        assert image.size == (512, 512)
        image.verify()


def test_critic_failure_keeps_image_and_retries_without_generation(pipeline):
    store, agent = pipeline

    class FailingCritic:
        def evaluate(self, *args):
            raise ProviderError("API HTTP 429; check model/config/quota")

    class ForbiddenGenerator:
        def generate(self, *args):
            pytest.fail("Retry must not generate another image")

    agent.critic = FailingCritic()
    with pytest.raises(CreationFailed) as failure:
        agent.create("山", "alice")
    entry = store.get("alice", failure.value.entry["id"])
    assert entry["status"] == "evaluation_failed"
    before = store.image_path(entry["image_path"]).read_bytes()
    agent.critic = MockProvider()
    agent.generator = ForbiddenGenerator()
    completed = agent.evaluate("alice", entry["id"])
    assert completed["status"] == "complete"
    assert len(completed["evaluation_attempts"]) == 2
    assert store.image_path(entry["image_path"]).read_bytes() == before
    agent.critic = FailingCritic()
    assert agent.evaluate("alice", entry["id"]) == completed


def test_failed_generation_is_recorded_without_secret_leak(pipeline):
    store, agent = pipeline

    class BrokenGenerator:
        def generate(self, *args):
            raise RuntimeError("super-secret-api-key")

    agent.generator = BrokenGenerator()
    with pytest.raises(CreationFailed) as failure:
        agent.create("海", "alice")
    entry = store.get("alice", failure.value.entry["id"])
    assert entry["status"] == "creation_failed"
    assert entry["image_path"] is None
    assert "super-secret" not in json.dumps(entry)
    with pytest.raises(ValueError, match="No saved image"):
        agent.evaluate("alice", entry["id"])


def test_missing_key_fails_before_creating_record(pipeline, monkeypatch):
    store, agent = pipeline
    monkeypatch.delenv("PAINTAI_TEST_MISSING_KEY", raising=False)
    agent.settings.generator = ProviderConfig(
        provider="openai_compatible", model="test-image", api_key_env="PAINTAI_TEST_MISSING_KEY"
    )
    with pytest.raises(ValueError, match="Missing API key"):
        agent.create("海", "alice")
    assert store.gallery("alice") == []


def test_explicit_reevaluation_preserves_previous_score_and_image(pipeline):
    import hashlib

    store, agent = pipeline
    entry = agent.create("秋天的树林", "alice")
    image = store.image_path(entry["image_path"])
    before = image.read_bytes()

    class DifferentCritic(MockProvider):
        def evaluate(self, *args):
            result = super().evaluate(*args)
            result.scores.topic = 9
            return result

    agent.critic = DifferentCritic()
    entry = agent.evaluate("alice", entry["id"], force=True)
    assert entry["critique"]["scores"]["topic"] == 9
    assert entry["evaluation_attempts"][0]["critique"]["scores"]["topic"] == 5
    assert entry["evaluation_attempts"][1]["critique"]["scores"]["topic"] == 9
    assert entry["evaluation_attempts"][1]["image_sha256"] == hashlib.sha256(before).hexdigest()
    assert image.read_bytes() == before


def test_failed_reevaluation_does_not_present_old_score_as_new(pipeline):
    store, agent = pipeline
    entry = agent.create("秋天的树林", "alice")

    class FailingCritic:
        def evaluate(self, *args):
            raise ProviderError("API HTTP 500")

    agent.critic = FailingCritic()
    with pytest.raises(CreationFailed):
        agent.evaluate("alice", entry["id"], force=True)
    failed = store.get("alice", entry["id"])
    assert failed["critique"] is None
    assert failed["evaluation_attempts"][0]["critique"]["overall"] == 5
    assert failed["status"] == "evaluation_failed"


@pytest.mark.parametrize("topic", ["", "  "])
def test_empty_topic_does_not_create_record(pipeline, topic):
    store, agent = pipeline
    with pytest.raises(ValueError):
        agent.create(topic, "alice")
    assert store.gallery("alice") == []


@pytest.mark.parametrize("painter_id", ["../escape", "a/b", "", "a\\b"])
def test_invalid_painter_id(painter_id):
    with pytest.raises(ValidationError):
        Painter(painter_id=painter_id, name="test")


def test_duplicate_painter_does_not_overwrite(pipeline):
    store, _ = pipeline
    with pytest.raises(ValueError, match="already exists"):
        store.add_painter(Painter(painter_id="alice", name="changed"))
    assert store.painter("alice").name == "阿梨"


@pytest.mark.parametrize("score", [0, 11, float("nan"), float("inf")])
def test_reject_invalid_scores(score):
    with pytest.raises(ValidationError):
        Scores(topic=score, technique=5, composition=5, color=5, style=5, creativity=5)


def test_invalid_image_and_path(pipeline):
    with pytest.raises(ProviderError, match="invalid image"):
        normalize_image(b"this is not an image")
    with pytest.raises(ValueError, match="escapes"):
        pipeline[0].image_path("../outside.png")


def test_image_normalized_to_png():
    buffer = BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, "JPEG")
    assert normalize_image(buffer.getvalue()).startswith(b"\x89PNG")


def test_config_relative_path_env_override_and_no_silent_mock(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text('data_dir = "art"\n[planner]\nprovider="mock"\n', encoding="utf-8")
    assert load_settings(config).data_dir == tmp_path / "art"
    monkeypatch.setenv("PAINTAI_DATA_DIR", str(tmp_path / "override"))
    assert load_settings(config).data_dir == tmp_path / "override"
    with pytest.raises(ValueError, match="Config not found"):
        load_settings(tmp_path / "missing.toml")


def test_cli_offline_round_trip(tmp_path, capsys):
    prefix = ["--offline", "--data-dir", str(tmp_path)]
    assert main(prefix + ["painter", "add", "demo", "--name", "测试画师"]) == 0
    capsys.readouterr()
    assert main(prefix + ["create", "秋天的树林", "--painter", "demo"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "complete" and result["simulated"]
    assert main(prefix + ["show", result["id"], "--painter", "demo"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == result["id"]
    assert main(prefix + ["gallery", "--painter", "demo"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert main(prefix + ["create", "x", "--painter", "missing"]) == 2
    assert "not found" in capsys.readouterr().err
