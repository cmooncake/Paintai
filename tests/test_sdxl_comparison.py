"""CFG serialization must not invalidate an otherwise matched native run."""

import runpy
from pathlib import Path


def test_cfg_representation_and_real_changes():
    same = runpy.run_path(str(Path(__file__).parents[1] / "scripts/compare-sdxl-lora.py"))[
        "same_setting"
    ]
    assert same("--cfg-scale", "5.0", "5")
    assert not same("--cfg-scale", "7", "5")
    assert not same("--seed", "43", "42")
    assert not same("--negative-prompt", "blue eyes", "green eyes")
