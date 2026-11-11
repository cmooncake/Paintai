import hashlib
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from paintai.config import LocalImageConfig, ProviderConfig, Settings
from paintai.inpainting import InpaintRequest, make_mask, run, snapshot
from paintai.jobs import Jobs, QueueConflict
from paintai.models import Painter
from paintai.storage import Store


def test_mask_validation_and_coordinate_mapping():
    req = InpaintRequest(
        instructions="repair",
        regions=[
            [{"x": 0.1, "y": 0.2}, {"x": 0.4, "y": 0.2}, {"x": 0.4, "y": 0.5}, {"x": 0.1, "y": 0.5}]
        ],
    )
    mask = make_mask((101, 101), req.regions)
    assert mask.getpixel((20, 30)) == 255
    assert mask.getpixel((80, 80)) == 0
    with pytest.raises(ValueError):
        make_mask((101, 101), [[]])
    with pytest.raises(ValueError):
        InpaintRequest(instructions="repair", regions=[[{"x": float("nan"), "y": 0}]])


def test_edit_preserves_original_and_unselected_pixels(tmp_path):
    store = Store(tmp_path)
    store.add_painter(Painter(painter_id="p", name="p"))
    executable = tmp_path / "runtime"
    executable.touch()
    model = tmp_path / "model"
    model.touch()
    settings = Settings(
        data_dir=tmp_path,
        generator=ProviderConfig(
            provider="sd_cpp",
            model="test",
            local_image=LocalImageConfig(executable=executable, model_path=model),
        ),
    )
    old = store.begin(
        "p",
        "original",
        {
            r: getattr(settings, r).model_dump(mode="json")
            for r in ("planner", "generator", "critic")
        },
    )
    old["plan"] = {
        "perception": {"subject": "s", "scene": "s", "mood": "s", "constraints": []},
        "prompt": "p",
        "composition": "c",
        "palette": "p",
    }
    original = io.BytesIO()
    Image.new("RGB", (101, 101), "blue").save(original, format="PNG")
    store.save_image(old, original.getvalue())
    original_hash = hashlib.sha256(store.image_path(old["image_path"]).read_bytes()).hexdigest()
    req = InpaintRequest(
        instructions="repair",
        regions=[
            [{"x": 0.1, "y": 0.1}, {"x": 0.4, "y": 0.1}, {"x": 0.4, "y": 0.4}, {"x": 0.1, "y": 0.4}]
        ],
    )
    payload = snapshot(store, "p", old["id"], req, settings)
    jobs = Jobs(store, settings)
    job = jobs.submit("p", "inpaint", old["id"], "edit-test-key", old["id"], edit_request=req)
    assert jobs.submit(
        "p", "inpaint", old["id"], "edit-test-key", old["id"], edit_request=req
    )["id"] == job["id"]
    with pytest.raises(QueueConflict):
        jobs.submit(
            "p", "inpaint", old["id"], "edit-test-key", old["id"],
            edit_request=req.model_copy(update={"instructions": "different request"}),
        )
    store.add_painter(Painter(painter_id="other", name="other"))
    with pytest.raises(ValueError):
        snapshot(store, "other", old["id"], req, settings)
    red = io.BytesIO()
    Image.new("RGB", (512, 512), "red").save(red, format="PNG")
    generator = SimpleNamespace(generate=lambda plan: red.getvalue())

    def evaluate(pid, eid):
        e = store.get(pid, eid)
        e["status"] = "complete"
        e["critique"] = {"overall": 4}
        store.save(e)
        return e

    agent = SimpleNamespace(store=store, settings=settings, generator=generator, evaluate=evaluate)
    result = run(agent, payload, "p", lambda e: None)
    assert result["id"] != old["id"]
    im = Image.open(store.image_path(result["image_path"]))
    assert im.size == (101, 101)
    assert im.getpixel((20, 20)) == (255, 0, 0)
    assert im.getpixel((80, 80)) == (0, 0, 255)
    assert (
        hashlib.sha256(store.image_path(old["image_path"]).read_bytes()).hexdigest()
        == original_hash
    )
    assert store.get("p", result["id"])["revision_comparison"]["kind"] == "image_edit"
