import pytest
from fastapi.testclient import TestClient
from PIL import Image

from paintai.api import create_app
from paintai.config import Settings
from paintai.lora_reports import LoraReport, LoraReports
from paintai.models import Painter


def test_archive_is_persistent_painter_scoped_and_only_serves_registered_pngs(tmp_path):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings, start_worker=False)
    store = app.state.store
    for name in ["alice", "bob"]:
        store.add_painter(Painter(painter_id=name, name=name))
    source = tmp_path / "result.png"
    Image.new("RGB", (16, 16), "green").save(source)
    report = LoraReport(
        id="trial",
        painter_id="alice",
        title="连续训练",
        protocol="seed 42",
        conclusion="未通过",
        image_provenance="真实生成",
        completed_steps=160,
        total_steps=160,
        technical_pass=True,
        style_pass=False,
        technical_check_count=63,
        limitations=[],
        findings=[],
        arms=[
            dict(
                id="step-160", label="160 步", step=160, hair="偏绿", clothing="白裙", style="平涂"
            )
        ],
    )
    archive = LoraReports(store)
    archive.publish(report, {"step-160": source})
    with TestClient(create_app(settings, start_worker=False)) as client:
        data = client.get("/api/v1/painters/alice/lora-reports").json()["items"]
        assert len(data) == 1 and data[0]["style_pass"] is False
        assert str(tmp_path) not in str(data)
        url = data[0]["arms"][0]["image_url"]
        response = client.get(url)
        assert response.status_code == 200 and response.content == source.read_bytes()
        assert response.headers["content-type"] == "image/png"
        assert client.get("/api/v1/painters/bob/lora-reports").json() == {"items": []}
        assert client.get(url.replace("alice", "bob")).status_code == 404
        assert client.get(url.replace("step-160", "report.json")).status_code == 404
        assert client.get("/api/v1/painters/missing/lora-reports").status_code == 404
    with pytest.raises(ValueError):
        archive.image("alice", "../trial", "step-160")
    with pytest.raises(ValueError):
        archive.publish(report, {"unknown": source})
    (archive.folder("alice", "trial") / "step-160.png").unlink()
    with TestClient(app) as client:
        assert client.get(url).status_code == 404


def test_report_owner_tampering_is_rejected(tmp_path):
    import json

    app = create_app(Settings(data_dir=tmp_path), start_worker=False)
    app.state.store.add_painter(Painter(painter_id="alice", name="alice"))
    folder = LoraReports(app.state.store).folder("alice", "trial")
    folder.mkdir(parents=True)
    (folder / "report.json").write_text(
        json.dumps(
            {
                "id": "trial",
                "painter_id": "bob",
                "title": "x",
                "protocol": "x",
                "conclusion": "x",
                "image_provenance": "x",
                "completed_steps": 1,
                "total_steps": 1,
                "technical_pass": True,
                "style_pass": False,
                "technical_check_count": 0,
                "limitations": [],
                "findings": [],
                "arms": [],
            }
        )
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/painters/alice/lora-reports").status_code == 404
