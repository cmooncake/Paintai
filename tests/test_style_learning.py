import base64
import json
from io import BytesIO

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from paintai.api import create_app
from paintai.config import ProviderConfig, Settings
from paintai.memory import Memory
from paintai.models import StyleTraits
from paintai.providers import CompatibleProvider, MockProvider


def picture():
    out = BytesIO()
    Image.new("RGB", (900, 600), "blue").save(out, "PNG")
    return out.getvalue()


def traits():
    return StyleTraits(
        palette="蓝色与暖白",
        lines="细线条",
        lighting="柔和侧光",
        composition="主体偏左，背景留白",
        texture="平涂",
        guidance=["保持清晰轮廓与柔和侧光"],
    )


def setup(tmp_path):
    return create_app(
        Settings(
            data_dir=tmp_path,
            critic=ProviderConfig(
                provider="openai_compatible",
                model="visual-test",
                auth="none",
                base_url="http://127.0.0.1:18081/v1",
            ),
        ),
        start_worker=False,
    )


def test_reference_validation_and_painter_isolation(tmp_path):
    with TestClient(setup(tmp_path)) as c:
        a = c.post("/api/v1/painters", json={"name": "a"}).json()["painter_id"]
        b = c.post("/api/v1/painters", json={"name": "b"}).json()["painter_id"]
        base = f"/api/v1/painters/{a}"
        assert c.post(base + "/references", content=b"not an image").status_code == 409
        assert (
            c.post(base + "/references", content=b"x" * (10 * 1024 * 1024 + 1)).status_code == 413
        )
        ref = c.post(base + "/references", content=picture()).json()
        assert ref["width"] == 768 and ref["height"] == 512
        assert c.post(base + "/references", content=picture()).json()["id"] == ref["id"]
        assert c.get(f"/api/v1/painters/{b}/references/{ref['id']}/image").status_code == 404
        response = c.post(
            f"/api/v1/painters/{b}/styles/analyze",
            json={"name": "wrong owner", "reference_ids": [ref["id"]]},
            headers={"Idempotency-Key": "style-isolation"},
        )
        assert response.status_code == 409
        assert (
            c.post(
                base + "/references",
                content=picture(),
                headers={"origin": "https://untrusted.example"},
            ).status_code
            == 403
        )


def test_real_image_analysis_profile_review_and_creation_memory(tmp_path, monkeypatch):
    app = setup(tmp_path)
    observed = []

    def analyze(self, schema, system, content, **kwargs):
        assert schema is StyleTraits
        image = base64.b64decode(content[1]["image_url"]["url"].split(",")[1])
        assert Image.open(BytesIO(image)).size == (768, 512)
        observed.append(image)
        return traits()

    monkeypatch.setattr(CompatibleProvider, "_json", analyze)
    with TestClient(app) as c:
        pid = c.post("/api/v1/painters", json={"name": "test"}).json()["painter_id"]
        base = f"/api/v1/painters/{pid}"
        ref = c.post(base + "/references", content=picture()).json()
        request = {"name": "清透风格", "reference_ids": [ref["id"]]}
        job = c.post(
            base + "/styles/analyze", json=request, headers={"Idempotency-Key": "style-analysis"}
        ).json()
        assert (
            c.post(
                base + "/styles/analyze",
                json=request,
                headers={"Idempotency-Key": "style-analysis"},
            ).json()["id"]
            == job["id"]
        )
        assert app.state.worker.run_one()
        assert app.state.jobs.get(job["id"])["status"] == "succeeded"
        assert len(observed) == 1
        data = c.get(base + "/styles").json()
        assert data["active_profile"] is None
        original = data["profiles"][0]["profile"]
        updated = c.post(
            base + "/styles/activate",
            json={"profile_id": original["id"], "name": "已审核", "traits": traits().model_dump()},
        ).json()["style_profile"]
        assert updated["id"] != original["id"]
        lesson = c.post(base + "/train/preview", json={}).json()
        assert lesson["art_brief"]["style_profile"]["id"] == updated["id"]
        with monkeypatch.context() as scoped:
            scoped.setattr(
                Memory,
                "search",
                lambda *args, **kwargs: [{"entry_id": "prior", "rules": ["Soft light"]}],
            )
            experiment = c.post(
                base + "/experiments/preview", json={"topic": "blue flowers", "pairs": 1}
            ).json()
        assert experiment["art_brief"]["style_profile"]["id"] == updated["id"]
        assert c.get(base + "/styles").json()["profiles"][1]["profile"] == original
        captures = []

        class Recorder(MockProvider):
            def plan(self, topic, painter, **kwargs):
                captures.append(kwargs["requirements"])
                return super().plan(topic, painter, **kwargs)

            def evaluate(self, image, topic, painter, plan, **kwargs):
                captures.append(kwargs["requirements"])
                return super().evaluate(image, topic, painter, plan, **kwargs)

        monkeypatch.setattr("paintai.jobs.make_provider", lambda config, client: Recorder())
        create = c.post(
            "/api/v1/creations",
            json={"painter_id": pid, "topic": "blue flowers"},
            headers={"Idempotency-Key": "style-creation"},
        ).json()
        snapshot = json.loads(app.state.jobs.get(create["id"])["payload"])
        assert snapshot["painter_snapshot"]["style_profile"]["id"] == updated["id"]
        assert c.post(base + "/styles/activate", json={"profile_id": None}).status_code == 409
        assert app.state.worker.run_one()
        entry_id = app.state.jobs.get(create["id"])["entry_id"]
        entry = app.state.store.get(pid, entry_id)
        assert all(r["style_profile"]["id"] == updated["id"] for r in captures)
        assert entry["art_brief"]["style_profile"]["reference_hashes"] == [ref["sha256"]]
        assert Memory(app.state.store).list(pid)[0]["art_brief"] == entry["art_brief"]
        assert c.post(base + "/styles/activate", json={"profile_id": None}).status_code == 200
        assert (
            app.state.store.get(pid, entry_id)["art_brief"]["style_profile"]["id"] == updated["id"]
        )
        assert c.get(base + "/styles").json()["active_profile"] is None


def test_failed_analysis_does_not_publish_profile(tmp_path, monkeypatch):
    app = setup(tmp_path)

    def fail(*args, **kwargs):
        raise httpx.ReadTimeout("fixture failure")

    monkeypatch.setattr(CompatibleProvider, "_json", fail)
    with TestClient(app) as c:
        pid = c.post("/api/v1/painters", json={"name": "test"}).json()["painter_id"]
        base = f"/api/v1/painters/{pid}"
        ref = c.post(base + "/references", content=picture()).json()
        job = c.post(
            base + "/styles/analyze",
            json={"name": "失败测试", "reference_ids": [ref["id"]]},
            headers={"Idempotency-Key": "style-failure"},
        ).json()
        app.state.worker.run_one()
        assert app.state.jobs.get(job["id"])["status"] == "failed"
        assert c.get(base + "/styles").json()["profiles"] == []
