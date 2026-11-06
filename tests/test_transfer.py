import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from paintai.api import create_app
from paintai.config import Settings
from paintai.lora_reports import LoraReport, LoraReports


def app_at(path):
    return create_app(Settings(data_dir=path), start_worker=False)


@pytest.fixture
def source(tmp_path):
    app = app_at(tmp_path / "source")
    client = TestClient(app)
    pid = client.post("/api/v1/painters", json={"name": "小笑", "growth_goal": "柔和光影"}).json()[
        "painter_id"
    ]
    response = client.post(
        f"/api/v1/painters/{pid}/train", json={}, headers={"Idempotency-Key": "train-transfer-test"}
    )
    assert response.status_code == 202
    assert app.state.worker.run_one()
    store = app.state.store
    entry = store.gallery(pid)[0]
    entry["config"]["planner"]["api_key"] = "TEST-SECRET-DO-NOT-EXPORT"
    entry["evaluation_attempts"][0]["error"] = "Bearer TEST-SECRET-DO-NOT-EXPORT"
    store.save(entry)
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "green").save(image, "PNG")
    ref = client.post(f"/api/v1/painters/{pid}/references", content=image.getvalue()).json()
    from paintai.models import StyleProfile, StyleTraits
    from paintai.storage import now

    profile = StyleProfile(
        id="style-test",
        name="光影",
        created_at=now(),
        traits=StyleTraits(
            palette="柔和",
            lines="细线",
            lighting="柔光",
            composition="人物",
            texture="细腻",
            guidance=["细彩线"],
        ),
        reference_ids=[ref["id"]],
        reference_hashes=[ref["sha256"]],
    )
    with store.connect() as db:
        db.execute(
            "INSERT INTO style_profiles VALUES (?,?,?)",
            (
                profile.id,
                pid,
                json.dumps(
                    {"profile": profile.model_dump(), "analysis_model": "test", "status": "draft"}
                ),
            ),
        )
    assert (
        client.post(
            f"/api/v1/painters/{pid}/styles/activate", json={"profile_id": profile.id}
        ).status_code
        == 200
    )
    report = LoraReport(
        id="trial",
        painter_id=pid,
        title="对照",
        protocol="seed 42",
        conclusion="尚无改善",
        image_provenance="test fixture",
        completed_steps=160,
        total_steps=160,
        technical_pass=True,
        style_pass=False,
        technical_check_count=35,
        limitations=[],
        findings=[],
        arms=[dict(id="base", label="底模", step=0, hair="金色", clothing="白裙", style="平涂")],
        references=[dict(id="reference", label="原作")],
    )
    LoraReports(store).publish(
        report,
        {
            "base": store.image_path(entry["image_path"]),
            "reference": app.state.jobs.styles.path(pid, ref["id"]),
        },
    )
    return client, app, pid


def test_roundtrip_progress_memory_images_reports_and_evaluate(source, tmp_path):
    client, app, pid = source
    exported = client.get(f"/api/v1/painters/{pid}/export")
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(exported.content)) as bundle:
        manifest = bundle.read("manifest.json")
        assert b"TEST-SECRET" not in manifest
        assert not any("reference" in name for name in bundle.namelist())
        assert "jobs" not in json.loads(manifest)["tables"]
    destination = app_at(tmp_path / "destination")
    remote = TestClient(destination)
    restored = remote.post("/api/v1/painters/import", content=exported.content)
    assert restored.status_code == 201, restored.text
    assert restored.json()["counts"]["creations"] == 3
    for suffix in ("learning", "memory", "distilled", "experiments"):
        assert (
            remote.get(f"/api/v1/painters/{pid}/{suffix}").json()
            == client.get(f"/api/v1/painters/{pid}/{suffix}").json()
        )
    gallery = remote.get(f"/api/v1/painters/{pid}/creations").json()
    for entry in gallery["items"]:
        assert remote.get(entry["image_url"]).content == client.get(entry["image_url"]).content
    report = remote.get(f"/api/v1/painters/{pid}/lora-reports").json()["items"][0]
    assert report["references"] == []
    assert report["style_pass"] is False
    assert remote.get(report["arms"][0]["image_url"]).status_code == 200
    assert remote.get(f"/api/v1/painters/{pid}/styles").json()["references"] == []
    style = remote.get(f"/api/v1/painters/{pid}/styles").json()["active_profile"]
    assert style["reference_ids"] == []
    assert (
        style["traits"]
        == client.get(f"/api/v1/painters/{pid}/styles").json()["active_profile"]["traits"]
    )
    assert remote.get("/api/v1/jobs").json() == []
    # A migrated entry can still be evaluated using the destination's model settings.
    entry_id = gallery["items"][0]["id"]
    assert (
        remote.post(
            f"/api/v1/painters/{pid}/creations/{entry_id}/evaluate",
            headers={"Idempotency-Key": "after-import-eval"},
        ).status_code
        == 202
    )
    destination.state.worker.run_one()
    assert destination.state.store.get(pid, entry_id)["status"] == "complete"
    assert remote.post("/api/v1/painters/import", content=exported.content).status_code == 409
    reopened = TestClient(app_at(tmp_path / "destination"))
    assert reopened.get(f"/api/v1/painters/{pid}/creations").json()["total"] == 3


def mutate(raw, change):
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        files = {name: bundle.read(name) for name in bundle.namelist()}
    manifest = json.loads(files["manifest.json"])
    change(manifest, files)
    files["manifest.json"] = json.dumps(manifest).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return output.getvalue()


@pytest.mark.parametrize(
    "case", ["traversal", "missing", "corrupt", "owner", "version", "duplicate-id"]
)
def test_reject_bad_archive_without_partial_import(source, tmp_path, case):
    client, _, pid = source
    raw = client.get(f"/api/v1/painters/{pid}/export").content

    def change(manifest, files):
        image = next(name for name in files if name.endswith(".png"))
        if case == "traversal":
            files["../../outside.txt"] = b"bad"
        elif case == "missing":
            del files[image]
        elif case == "corrupt":
            files[image] = b"corrupt"
        elif case == "owner":
            manifest["tables"]["experiences"][0]["painter_id"] = "someone_else"
        elif case == "version":
            manifest["version"] = 99
        else:
            manifest["tables"]["creations"].append(manifest["tables"]["creations"][0])

    destination = TestClient(app_at(tmp_path / "destination"))
    response = destination.post("/api/v1/painters/import", content=mutate(raw, change))
    assert response.status_code in (400, 409)
    assert destination.get("/api/v1/painters").json() == []
    assert not (tmp_path / "destination" / "painters" / pid).exists()


def test_active_export_and_invalid_zip(source, tmp_path):
    client, _, pid = source
    assert (
        client.post(
            "/api/v1/creations",
            json={"painter_id": pid, "topic": "forest"},
            headers={"Idempotency-Key": "block-export-test"},
        ).status_code
        == 202
    )
    assert client.get(f"/api/v1/painters/{pid}/export").status_code == 409
    assert client.post("/api/v1/painters/import", content=b"not a zip").status_code == 400
