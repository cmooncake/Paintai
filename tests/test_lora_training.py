"""Fail before loading a model when holdout data could leak into training."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("values", [[0], [41], [20, 20]])
def test_invalid_checkpoint_schedule_rejected_before_loading(monkeypatch, tmp_path, values):
    with pytest.raises(SystemExit) as error:
        run_trainer(
            monkeypatch,
            tmp_path,
            {"images": []},
            extra=["--steps", "40", "--checkpoints", *map(str, values)],
        )
    assert error.value.code == 2
    assert not (tmp_path / "output").exists()


def test_checkpoint_schedule_sorted_without_changing_budget():
    import runpy

    schedule = runpy.run_path(str(Path(__file__).parents[1] / "scripts/train-style-lora.py"))[
        "checkpoint_steps"
    ]
    assert schedule([160, 40, 80], 160) == [40, 80, 160]


def test_center_crop_geometry_for_sdxl_time_conditioning():
    import runpy

    fit = runpy.run_path(str(Path(__file__).parents[1] / "scripts/train-style-lora.py"))[
        "fit_geometry"
    ]
    assert fit((800, 1200), (512, 512)) == ((512, 768), (0, 128))
    assert fit((1200, 800), (512, 512)) == ((768, 512), (128, 0))
    assert fit((640, 640), (768, 768)) == ((768, 768), (0, 0))
    with pytest.raises(ValueError, match="positive"):
        fit((0, 640), (768, 768))


def run_trainer(monkeypatch, tmp_path, manifest, parent=None, extra=()):
    spec = importlib.util.spec_from_file_location(
        "train_style_lora", Path(__file__).parents[1] / "scripts/train-style-lora.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    args = ["trainer", "--manifest", str(path), "--output", str(tmp_path / "output")]
    args.extend(extra)
    if parent:
        folder = tmp_path / "parent"
        folder.mkdir()
        (folder / "report.json").write_text(json.dumps(parent), encoding="utf-8")
        args += ["--resume-training", str(folder)]
    monkeypatch.setattr(sys, "argv", args)
    module.main()


@pytest.mark.parametrize(
    "validation",
    [
        {"id": "b", "sha256": "same-image", "group": "other"},
        {"id": "b", "sha256": "other-image", "group": "same-character"},
    ],
)
def test_reject_image_and_character_overlap(monkeypatch, tmp_path, validation):
    manifest = {
        "images": [{"id": "a", "sha256": "same-image", "group": "same-character"}],
        "validation_images": [validation],
    }
    with pytest.raises(ValueError, match="leakage"):
        run_trainer(monkeypatch, tmp_path, manifest)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "history",
    [
        {"seen_training_hashes": ["old-image"]},
        {"seen_training_groups": ["heldout"]},
    ],
)
def test_reject_holdout_seen_in_ancestor_training(monkeypatch, tmp_path, history):
    manifest = {
        "base_model": {"sha256": "base"},
        "images": [{"id": "new", "sha256": "new-image"}],
        "validation_images": [{"id": "heldout", "sha256": "old-image"}],
    }
    parent = {
        "status": "complete",
        "manifest": {
            "base_model": manifest["base_model"],
            "images": [{"id": "parent", "sha256": "parent-image"}],
        },
        **history,
    }
    with pytest.raises(ValueError, match="parent training"):
        run_trainer(monkeypatch, tmp_path, manifest, parent)
    assert not (tmp_path / "output").exists()


def test_reject_empty_training_set(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="Empty training"):
        run_trainer(monkeypatch, tmp_path, {"images": []})


def test_reject_cross_architecture_manifest_before_loading(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="architecture differ"):
        run_trainer(monkeypatch, tmp_path, {"architecture": "sdxl", "images": []})
    assert not (tmp_path / "output").exists()


def test_reject_sd15_parent_for_sdxl_before_loading(monkeypatch, tmp_path):
    manifest = {"architecture": "sdxl", "images": [{"id": "a", "sha256": "image"}]}
    with pytest.raises(ValueError, match="Parent adapter architecture"):
        run_trainer(
            monkeypatch,
            tmp_path,
            manifest,
            {"architecture": "sd15"},
            extra=["--architecture", "sdxl"],
        )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("rate", ["nan", "inf", "0", "-0.0001"])
def test_reject_invalid_optimizer_rate_before_loading_model(monkeypatch, tmp_path, rate):
    with pytest.raises(SystemExit) as error:
        run_trainer(monkeypatch, tmp_path, {"images": []}, extra=["--learning-rate", rate])
    assert error.value.code == 2
    assert not (tmp_path / "output").exists()


def test_loss_regions_use_max_overlap_and_correct_coordinates():
    import runpy

    weights = runpy.run_path(str(Path(__file__).parents[1] / "scripts/train-style-lora.py"))[
        "region_weights"
    ]
    grid = weights(
        [{"box": [0.25, 0.25, 0.75, 0.75], "weight": 4}, {"box": [0.5, 0.5, 1, 1], "weight": 2}],
        8,
        8,
    )
    values = [v for row in grid for v in row]
    assert values.count(4) == 16
    assert values.count(2) == 12
    assert grid[0][0] == 1 and grid[7][7] == 2
    with pytest.raises(ValueError, match="Invalid loss region"):
        weights([{"box": [0, 0, 1.2, 1], "weight": 4}], 8, 8)
    assert weights([], 2, 2) == [[1, 1], [1, 1]]
