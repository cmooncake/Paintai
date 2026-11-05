"""Matched SDXL base/LoRA comparison, reusing only a verified adapter-free control."""

import argparse
import hashlib
import json
import re
import shutil
import time
from pathlib import Path

from paintai.config import ProviderConfig
from paintai.local_image import LocalImageGenerator
from paintai.models import Painter
from paintai.providers import MockProvider


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def same_setting(flag, current, previous):
    # Pydantic restores a JSON integer CFG as float; compare its numerical value.
    if flag == "--cfg-scale":
        return float(current) == float(previous)
    return current == previous


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help="Revalidate a saved image after a verification-only failure; never regenerate",
    )
    args = parser.parse_args()
    trained = json.loads((args.training / "report.json").read_text("utf-8"))
    prior = json.loads((args.baseline / "comparison.json").read_text("utf-8"))
    if trained["status"] != "complete" or trained["architecture"] != "sdxl":
        raise ValueError("Requires a completed, verified SDXL adapter")
    if prior["status"] != "complete" or prior["adapters"] is not None:
        raise ValueError("Requires an adapter-free completed baseline")
    config = ProviderConfig.model_validate(prior["generator"])
    if prior["model_sha256"] != trained["manifest"]["base_model"]["sha256"]:
        raise ValueError("Baseline was generated with a different checkpoint")
    if digest(config.local_image.model_path) != trained["manifest"]["base_model"]["sha256"]:
        raise ValueError("Training and inference base models differ")
    baseline = prior["images"]["animagine"]
    if digest(baseline["path"]) != baseline["sha256"]:
        raise ValueError("Baseline image hash mismatch")
    baseline_log = Path(baseline["runtime_log"])
    invocation = json.loads((baseline_log.parent / "invocation.json").read_text("utf-8"))
    if invocation["lora"] or invocation["reference"]:
        raise ValueError("Baseline runtime used an adapter")
    if digest(config.local_image.executable) != prior["runtime_sha256"]:
        raise ValueError("Runtime differs from baseline")
    if (baseline_log.parent / "prompt.txt").read_text("utf-8") != prior["prompt"]:
        raise ValueError("Baseline actual prompt mismatch")
    weights = args.training / "xiaoxiao-smoke.safetensors"
    if digest(weights) != trained["artifacts"][weights.name]:
        raise ValueError("Adapter hash mismatch")
    previous_result = None
    if args.verify_existing:
        previous_result = json.loads((args.output / "comparison.json").read_text("utf-8"))
        if previous_result["status"] != "failed":
            raise ValueError("Only failed verification records may be recovered")
        if (
            previous_result["prompt"] != prior["prompt"]
            or digest(args.output / "baseline.png") != baseline["sha256"]
        ):
            raise ValueError("Existing comparison does not match this baseline")
        backup = args.output / "comparison-original-failure.json"
        if backup.exists():
            raise ValueError("Verification recovery already attempted")
        shutil.copy2(args.output / "comparison.json", backup)
        shutil.copy2(__file__, args.output / "verification-source.py")
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        shutil.copy2(__file__, args.output / "comparison-source.py")
        shutil.copy2(baseline["path"], args.output / "baseline.png")
    config.local_image.work_dir = args.output.resolve() / "runtime"
    plan = MockProvider().plan(
        prior["topic"], Painter(painter_id="sdxl-lora-test", name="SDXL test")
    )
    plan.prompt = prior["prompt"]
    generator = LocalImageGenerator(config)
    generator.set_lora(weights, trained["artifacts"][weights.name], 1.0)
    result = {
        "status": "running",
        "topic": prior["topic"],
        "prompt": plan.prompt,
        "constraints": prior["constraints"],
        "generator": config.model_dump(mode="json"),
        "baseline_source": str(args.baseline.resolve()),
        "training": str(args.training.resolve()),
        "only_changed": "SDXL UNet LoRA enabled at strength 1.0",
        "images": {"baseline": {**baseline, "path": str((args.output / "baseline.png").resolve())}},
        "limitations": [
            "Single seed, development validation, not stable improvement evidence.",
            "Training at 512, inference at 1024; CPU pilot only.",
        ],
    }
    report = args.output / "comparison.json"
    report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    start = time.monotonic()
    try:
        path = args.output / "lora.png"
        if not args.verify_existing:
            path.write_bytes(generator.generate(plan))
        elif not path.is_file():
            raise ValueError("No saved image to verify")
        (run_dir,) = config.local_image.work_dir.iterdir()
        log = (run_dir / "generation.log").read_text("utf-8", errors="replace")
        if "1/1 images saved" not in log:
            raise ValueError("Native image generation did not finish")
        applied = re.findall(r"\((\d+) / (\d+)\) LoRA tensors have been applied", log)
        if not any(int(a) == int(b) and int(a) > 0 for a, b in applied):
            raise ValueError("Runtime did not confirm applying all adapter tensors")
        actual = json.loads((run_dir / "invocation.json").read_text("utf-8"))
        if actual["lora"] != generator.lora_metadata or actual["reference"] is not None:
            raise ValueError("Actual adapter metadata differs")
        expected_prompt = plan.prompt + f"\n<lora:{weights.stem}:1.0>"
        if (run_dir / "prompt.txt").read_text("utf-8") != expected_prompt:
            raise ValueError("Actual LoRA prompt differs")
        old_command = invocation["command"]
        for flag in [
            "-m",
            "--width",
            "--height",
            "--steps",
            "--cfg-scale",
            "--sampling-method",
            "--scheduler",
            "--seed",
            "--threads",
            "--backend",
            "--batch-count",
            "--clip-skip",
            "--negative-prompt",
        ]:
            if not same_setting(
                flag,
                actual["command"][actual["command"].index(flag) + 1],
                old_command[old_command.index(flag) + 1],
            ):
                raise ValueError("Runtime condition changed: " + flag)
        result["images"]["lora"] = {
            "path": str(path.resolve()),
            "sha256": digest(path),
            "runtime_log": str(run_dir / "generation.log"),
            "adapter": generator.lora_metadata,
            "applied_tensors": applied,
        }
        result["status"] = "complete"
    except Exception as exc:
        result.update(status="failed", error=str(exc))
        raise
    finally:
        result["elapsed_seconds"] = (
            previous_result["elapsed_seconds"] if previous_result else time.monotonic() - start
        )
        if previous_result:
            result["verification_recovery"] = {
                "original_error": previous_result.get("error"),
                "seconds": time.monotonic() - start,
                "image_regenerated": False,
            }
        report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        "Matched comparison complete; inspect images before accepting style improvement.",
        flush=True,
    )


if __name__ == "__main__":
    main()
