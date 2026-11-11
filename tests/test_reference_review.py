import base64
from io import BytesIO

import httpx
import pytest
from PIL import Image

from paintai.agent import PainterAgent
from paintai.config import ProviderConfig, Settings
from paintai.experiments import Experiments
from paintai.models import Painter
from paintai.providers import CompatibleProvider, MockProvider
from paintai.reference_review import Comparison, ReferenceReview, evidence_warning
from paintai.storage import Store
from paintai.style_learning import StyleLearning


def test_copied_evidence_cannot_support_perfect_score():
    comparison = Comparison(
        reference_evidence="参考图中，皮肤高光细腻。",
        generated_evidence="生成图中，皮肤高光细腻。",
        gap="参考更细腻",
        score=4,
        confidence="high",
    )
    assert "证据重复" in evidence_warning(comparison)


def fixture(tmp_path):
    s = Store(tmp_path)
    s.add_painter(Painter(painter_id="p", name="P"))
    out = BytesIO()
    Image.new("RGB", (32, 32), "red").save(out, "PNG")
    ref = StyleLearning(s).upload("p", out.getvalue())
    mock = MockProvider()
    e = PainterAgent(s, Settings(data_dir=tmp_path), mock, mock, mock).create("portrait", "p")
    r = {
        "id": "exp",
        "painter_id": "p",
        "created_at": "now",
        "status": "complete",
        "pairs": 1,
        "mode": "style_profile",
        "simulated": True,
        "painter_snapshot": {
            "style_profile": {
                "name": "test",
                "reference_ids": [ref["id"]],
                "reference_hashes": [ref["sha256"]],
            }
        },
        "results": [
            {"pair": 0, "arm": a, "seed": 42, "entry_id": e["id"], "critique": e["critique"]}
            for a in ["baseline", "learned"]
        ],
    }
    Experiments(s).save(r)
    return s, e, r


def test_multi_image_evidence_and_history(tmp_path, monkeypatch):
    s, e, r = fixture(tmp_path)
    service = ReferenceReview(s)
    snapshot = service.snapshot("p", "exp")
    seen = []

    def compare(self, schema, system, content):
        pictures = [
            base64.b64decode(c["image_url"]["url"].split(",")[1])
            for c in content
            if c["type"] == "image_url"
        ]
        assert len(pictures) == 2 and pictures[-1] == s.image_path(e["image_path"]).read_bytes()
        assert "baseline" not in str(content) and "learned" not in str(content)
        seen.append(pictures)
        return Comparison(
            reference_evidence="参考1细线",
            generated_evidence="目标粗线",
            gap="轮廓更粗",
            score=1,
            confidence="medium",
        )

    monkeypatch.setattr(CompatibleProvider, "_json", compare)
    config = ProviderConfig(
        provider="openai_compatible",
        auth="none",
        model="vision-test",
        base_url="http://127.0.0.1:18081/v1",
    )
    with httpx.Client() as client:
        service.run("p", snapshot, config, client, "review1")
        service.run("p", snapshot, config, client, "review2")
    saved = Experiments(s).list("p")[0]
    assert len(seen) == 20 and len(saved["reference_reviews"]) == 2
    assert saved["results"] == r["results"] and s.get("p", e["id"]) == e
    assert all(
        x["status"] == "complete" and x["mean"] == 1
        for x in saved["reference_reviews"][0]["results"]
    )


def test_failure_preserves_partial_and_checks_hash(tmp_path, monkeypatch):
    s, e, r = fixture(tmp_path)
    service = ReferenceReview(s)
    snap = service.snapshot("p", "exp")
    calls = []

    def compare(*args):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("truncated")
        return Comparison(
            reference_evidence="a", generated_evidence="b", gap="c", score=2, confidence="low"
        )

    monkeypatch.setattr(CompatibleProvider, "_json", compare)
    with httpx.Client() as client, pytest.raises(RuntimeError):
        service.run(
            "p",
            snap,
            ProviderConfig(
                provider="openai_compatible",
                auth="none",
                model="vision-test",
                base_url="http://127.0.0.1:18081/v1",
            ),
            client,
            "failed",
        )
    review = Experiments(s).list("p")[0]["reference_reviews"][0]
    assert review["status"] == "failed" and len(review["results"][0]["dimensions"]) == 1
    assert "mean" not in review["results"][0]
    StyleLearning(s).path("p", snap["references"][0]["id"]).write_bytes(b"changed")
    with pytest.raises(Exception, match="不一致"):
        service.snapshot("p", "exp")


def test_confounded_evidence_and_queue_recovery(tmp_path):
    s, e, r = fixture(tmp_path)
    assert evidence_warning(
        Comparison(
            reference_evidence="a",
            generated_evidence="b",
            gap="发色不同",
            score=4,
            confidence="high",
        )
    )
    assert evidence_warning(
        Comparison(
            reference_evidence="a",
            generated_evidence="b",
            gap="线条更粗",
            score=3,
            confidence="low",
        )
    )
    from paintai.jobs import Jobs, QueueConflict

    settings = Settings(
        data_dir=tmp_path,
        critic=ProviderConfig(
            provider="openai_compatible",
            auth="none",
            model="vision-test",
            base_url="http://127.0.0.1:18081/v1",
        ),
    )
    jobs = Jobs(s, settings)
    job = jobs.submit("p", "reference_review", "exp", "review-key")
    assert jobs.submit("p", "reference_review", "exp", "review-key")["id"] == job["id"]
    with pytest.raises(QueueConflict):
        jobs.submit("p", "reference_review", "other", "review-key")
    ReferenceReview(s).save("p", "exp", {"id": job["id"], "status": "running", "results": []})
    Experiments(s).recover()
    saved = Experiments(s).list("p")[0]
    assert (
        saved["status"] == "complete" and saved["reference_reviews"][0]["status"] == "interrupted"
    )


def test_single_creation_review_keeps_critique_and_is_public(tmp_path, monkeypatch):
    from paintai.api import public_creation

    store, entry, experiment = fixture(tmp_path)
    entry["painter_snapshot"]["style_profile"] = experiment["painter_snapshot"]["style_profile"]
    store.save(entry)
    service = ReferenceReview(store)
    snapshot = service.creation_snapshot("p", entry["id"])
    original_critique = entry["critique"]
    original_attempts = entry["evaluation_attempts"]
    seen = []

    def compare(self, schema, system, content):
        pictures = [c for c in content if c["type"] == "image_url"]
        assert len(pictures) == 2
        assert "前1张为参考" in content[-1]["text"]
        assert (
            base64.b64decode(pictures[-1]["image_url"]["url"].split(",")[1])
            == store.image_path(entry["image_path"]).read_bytes()
        )
        seen.append(1)
        return Comparison(
            reference_evidence="内线较细",
            generated_evidence="内线较粗",
            gap="内线粗细变化不足",
            score=2,
            confidence="medium",
        )

    monkeypatch.setattr(CompatibleProvider, "_json", compare)
    config = ProviderConfig(
        provider="openai_compatible",
        auth="none",
        model="test-vision",
        base_url="http://localhost/v1",
    )
    with httpx.Client() as client:
        service.run("p", snapshot, config, client, "single1")
        service.run("p", snapshot, config, client, "single2")
    restored = Store(tmp_path).get("p", entry["id"])
    assert len(seen) == 10
    assert restored["critique"] == original_critique
    assert restored["evaluation_attempts"] == original_attempts
    assert len(public_creation(restored)["reference_reviews"]) == 2
    assert restored["reference_reviews"][0]["results"][0]["mean"] == 2
    with pytest.raises(ValueError):
        service.creation_snapshot("other", entry["id"])
    StyleLearning(store).path("p", snapshot["references"][0]["id"]).write_bytes(b"modified")
    with pytest.raises(ValueError, match="不一致"):
        service.creation_snapshot("p", entry["id"])


def test_single_creation_partial_failure_preserves_evidence(tmp_path, monkeypatch):
    store, entry, experiment = fixture(tmp_path)
    service = ReferenceReview(store)
    with pytest.raises(ValueError, match="没有冻结"):
        service.creation_snapshot("p", entry["id"])
    entry["painter_snapshot"]["style_profile"] = experiment["painter_snapshot"]["style_profile"]
    store.save(entry)
    count = 0

    def compare(*args):
        nonlocal count
        count += 1
        if count > 1:
            raise RuntimeError("inference failed")
        return Comparison(
            reference_evidence="a", generated_evidence="b", gap="c", score=1, confidence="low"
        )

    monkeypatch.setattr(CompatibleProvider, "_json", compare)
    config = ProviderConfig(
        provider="openai_compatible",
        auth="none",
        model="test-vision",
        base_url="http://localhost/v1",
    )
    with httpx.Client() as client, pytest.raises(RuntimeError):
        service.run(
            "p", service.creation_snapshot("p", entry["id"]), config, client, "failed-single"
        )
    review = store.get("p", entry["id"])["reference_reviews"][0]
    assert review["status"] == "failed"
    assert len(review["results"][0]["dimensions"]) == 1
    assert "mean" not in review["results"][0]
