"""Render a frozen, adapter-free SDXL control for compare-sdxl-lora.py."""

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path

from paintai.config import load_settings
from paintai.local_image import LocalImageGenerator
from paintai.models import Painter
from paintai.prompt_guard import negative_constraints, portrait_locks
from paintai.providers import MockProvider


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text("utf-8"))
    config = load_settings(Path(spec["config"])).generator
    local = config.local_image
    if digest(local.model_path) != spec["model_sha256"]:
        raise ValueError("Frozen benchmark checkpoint differs")
    for key, value in spec["sampling"].items():
        if key not in {
            "width",
            "height",
            "steps",
            "cfg_scale",
            "sampling_method",
            "scheduler",
            "seed",
            "threads",
            "clip_skip",
        }:
            raise ValueError("Unsupported sampling field: " + key)
        setattr(local, key, value)
    local.ip_adapter_path = local.clip_vision_path = None
    local.negative_prompt = spec["negative_prompt"]
    local.work_dir = args.output.resolve() / "runtime"
    locks = portrait_locks(spec["prompt"].splitlines()[0])
    negative = ", ".join(filter(None, [local.negative_prompt, negative_constraints(locks)]))
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(args.spec, args.output / "spec.json")
    shutil.copy2(__file__, args.output / "render-source.py")
    plan = MockProvider().plan(spec["topic"], Painter(painter_id="lillie-test", name="小笑"))
    plan.prompt = spec["prompt"]
    result = {
        "status": "running",
        "topic": spec["topic"],
        "prompt": plan.prompt,
        "constraints": {
            "character": spec["character"],
            "locks": locks,
            "negative_prompt": negative,
        },
        "generator": config.model_dump(mode="json"),
        "model_sha256": spec["model_sha256"],
        "runtime_sha256": digest(local.executable),
        "spec_sha256": digest(args.spec),
        "adapters": None,
        "images": {},
        "limitations": spec["limitations"],
    }
    report = args.output / "comparison.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    start = time.monotonic()
    try:
        path = args.output / "baseline.png"
        path.write_bytes(LocalImageGenerator(config).generate(plan))
        (run_dir,) = local.work_dir.iterdir()
        invocation = json.loads((run_dir / "invocation.json").read_text("utf-8"))
        command = invocation["command"]
        if invocation["lora"] or invocation["reference"]:
            raise ValueError("Unexpected adapter in control")
        if (run_dir / "prompt.txt").read_text("utf-8") != plan.prompt:
            raise ValueError("Actual prompt differs")
        if command[command.index("--negative-prompt") + 1] != negative:
            raise ValueError("Actual negative prompt differs")
        log = run_dir / "generation.log"
        if "1/1 images saved" not in log.read_text("utf-8", errors="replace"):
            raise ValueError("Native generation did not complete")
        result["images"]["animagine"] = {
            "path": str(path.resolve()),
            "sha256": digest(path),
            "runtime_log": str(log),
        }
        result["status"] = "complete"
    except Exception as exc:
        result.update(status="failed", error=str(exc))
        raise
    finally:
        result["elapsed_seconds"] = time.monotonic() - start
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Control complete:", path, flush=True)


if __name__ == "__main__":
    main()
