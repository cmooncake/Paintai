import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from paintai.api import create_app
from paintai.config import Settings
from paintai.model_settings import ModelSettings, protect
from paintai.storage import Store


def editable(value):
    return {
        "revision": value["revision"],
        **{
            role: {
                key: v
                for key, v in value[role].items()
                if key not in ("key_configured", "stored_key")
            }
            for role in ("planner", "generator", "critic")
        },
    }


@pytest.mark.parametrize("preset_name", ["bailian-seedream", "ark-all"])
def test_cloud_preset_saves_without_credentials(tmp_path, monkeypatch, preset_name):
    for name in ("DASHSCOPE_API_KEY", "ARK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False)) as client:
        preset = client.get(f"/api/v1/model-settings/presets/{preset_name}").json()
        assert all("api_key" not in role for role in preset.values())
        result = client.post("/api/v1/model-settings", json={"revision": 0, **preset})
        assert result.status_code == 200
        assert result.json()["critic"]["model"] == preset["critic"]["model"]
        assert not any(result.json()[r]["key_configured"] for r in preset)
        assert client.get("/api/v1/jobs").json() == []
        preset["generator"]["parameters"]["sequential_image_generation"] = "auto"
        assert (
            client.post("/api/v1/model-settings", json={"revision": 1, **preset}).status_code == 409
        )
        preset["generator"]["parameters"]["sequential_image_generation"] = "disabled"
        preset["planner"]["parameters"]["thinking"] = {"type": "disabled", "secret": "invalid"}
        assert (
            client.post("/api/v1/model-settings", json={"revision": 1, **preset}).status_code == 409
        )


def test_preferences_persist_queue_freezes_and_reset(tmp_path):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings, start_worker=False)
    with TestClient(app) as client:
        painter = client.post("/api/v1/painters", json={"name": "test"}).json()["painter_id"]
        job = client.post(
            "/api/v1/creations",
            json={"painter_id": painter, "topic": "test"},
            headers={"Idempotency-Key": "model-settings-1"},
        ).json()
        body = editable(client.get("/api/v1/model-settings").json())
        body["planner"].update(
            provider="openai_compatible",
            auth="none",
            model="bigger-local-model",
            base_url="http://127.0.0.1:12345/v1",
        )
        response = client.post("/api/v1/model-settings", json=body)
        assert response.status_code == 200
        assert response.json()["planner"]["model"] == "bigger-local-model"
        assert (
            json.loads(app.state.jobs.get(job["id"])["settings"])["planner"]["provider"] == "mock"
        )
        assert client.post("/api/v1/model-settings", json=body).status_code == 409
        painter2 = client.post("/api/v1/painters", json={"name": "second"}).json()["painter_id"]
        second = client.post(
            "/api/v1/creations",
            json={"painter_id": painter2, "topic": "test"},
            headers={"Idempotency-Key": "model-settings-2"},
        ).json()
        assert (
            json.loads(app.state.jobs.get(second["id"])["settings"])["planner"]["model"]
            == "bigger-local-model"
        )
        restored = create_app(settings, start_worker=False)
        with TestClient(restored) as reopened:
            assert (
                reopened.get("/api/v1/model-settings").json()["planner"]["model"]
                == "bigger-local-model"
            )
            assert (
                reopened.post("/api/v1/model-settings/reset", json={"revision": 1}).status_code
                == 200
            )
            assert reopened.get("/api/v1/model-settings").json()["planner"]["provider"] == "mock"


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI integration")
def test_encrypted_secret_never_in_response_or_job_and_restores(tmp_path):
    assert protect(protect(b"fixture-key-only"), decrypt=True) == b"fixture-key-only"
    app = create_app(Settings(data_dir=tmp_path), start_worker=False)
    with TestClient(app) as client:
        body = editable(client.get("/api/v1/model-settings").json())
        body["planner"].update(
            provider="openai_compatible",
            model="test-model",
            base_url="https://service.example/v1",
            api_key="fixture-not-a-real-key",
        )
        result = client.post("/api/v1/model-settings", json=body)
        assert result.status_code == 200 and result.json()["planner"]["key_configured"]
        assert "fixture-not-a-real-key" not in result.text
        key_id = result.json()["planner"]["api_key_env"]
        try:
            with app.state.store.connect() as db:
                ciphertext = db.execute("SELECT ciphertext FROM model_credentials").fetchone()[0]
                payload = db.execute("SELECT payload FROM model_preferences").fetchone()[0]
            assert b"fixture-not-a-real-key" not in ciphertext
            assert "fixture-not-a-real-key" not in payload
            os.environ.pop(key_id)
            preferences = ModelSettings(Store(tmp_path), Settings(data_dir=tmp_path))
            assert preferences.current().planner.credential() == "fixture-not-a-real-key"
            painter = client.post("/api/v1/painters", json={"name": "test"}).json()["painter_id"]
            job = client.post(
                "/api/v1/creations",
                json={"painter_id": painter, "topic": "test"},
                headers={"Idempotency-Key": "encrypted-job"},
            ).json()
            assert "fixture-not-a-real-key" not in app.state.jobs.get(job["id"])["settings"]
            updated = editable(client.get("/api/v1/model-settings").json())
            updated["planner"]["base_url"] = "https://other.example/v1"
            assert client.post("/api/v1/model-settings", json=updated).status_code == 409
            assert (
                client.post(
                    "/api/v1/model-settings/planner/check", json=updated["planner"]
                ).status_code
                == 409
            )
        finally:
            os.environ.pop(key_id, None)


def test_connection_check_no_save_no_inference_and_safe_error(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path), start_worker=False)
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) > 1:
            return httpx.Response(200, json={"data": None})
        return httpx.Response(200, json={"data": [{"id": "vision-test"}]})

    real_client = httpx.Client
    monkeypatch.setattr(
        "paintai.model_settings.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    with TestClient(app) as client:
        body = editable(client.get("/api/v1/model-settings").json())["critic"]
        body.update(
            provider="openai_compatible",
            model="vision-test",
            auth="none",
            base_url="http://127.0.0.1:18081/v1",
        )
        response = client.post("/api/v1/model-settings/critic/check", json=body)
        assert response.json()["ok"] and response.json()["models"] == ["vision-test"]
        assert len(calls) == 1 and calls[0].method == "GET" and calls[0].url.path == "/v1/models"
        assert client.get("/api/v1/model-settings").json()["revision"] == 0
        assert client.get("/api/v1/jobs").json() == []
        assert not client.post("/api/v1/model-settings/critic/check", json=body).json()["ok"]


def test_reject_bad_config_and_hide_invalid_request_inputs(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False)) as client:
        body = editable(client.get("/api/v1/model-settings").json())
        body["planner"]["parameters"] = {"temperature": {"secret": "hidden-fixture"}}
        assert client.post("/api/v1/model-settings", json=body).status_code == 409
        body["planner"]["parameters"] = {}
        body["planner"].update(
            provider="openai_compatible", base_url="http://remote.example/v1", model="test"
        )
        assert client.post("/api/v1/model-settings", json=body).status_code == 409
        body["planner"]["api_key"] = {"wrong": "hidden-fixture"}
        response = client.post("/api/v1/model-settings", json=body)
        assert response.status_code == 422 and "hidden-fixture" not in response.text
        assert (
            client.post(
                "/api/v1/model-settings", json=body, headers={"origin": "https://untrusted.example"}
            ).status_code
            == 403
        )
        assert client.get("/api/v1/model-settings").json()["revision"] == 0
