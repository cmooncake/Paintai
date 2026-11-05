"""Compare a pinned SDXL base against the archived, verified SD1.5 control."""

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path

from paintai.config import ProviderConfig
from paintai.local_image import LocalImageGenerator
from paintai.models import Painter
from paintai.providers import MockProvider

ROOT = Path(__file__).resolve().parents[1]
MODEL_SHA = "6327eca98bfb6538dd7a4edce22484a1bbc57a8cff6b11d075d40da1afb847ac"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolution", type=int, choices=[512, 1024], default=512)
    parser.add_argument(
        "--recommended",
        action="store_true",
        help="Separate adaptive test: concise tags, CFG 5, 28 steps",
    )
    parser.add_argument(
        "--counterfeit-control",
        action="store_true",
        help="Render the old base with the adaptive prompt for a matched-prompt control",
    )
    args = parser.parse_args()
    prior = json.loads((args.control / "comparison.json").read_text("utf-8"))
    baseline = prior["images"]["baseline"]
    if baseline["lora"] is not None or digest(Path(baseline["path"])) != baseline["sha256"]:
        raise ValueError("Control must be a verified base-only generation")
    config = ProviderConfig.model_validate(prior["generator"])
    config.timeout_seconds = 3600
    local = config.local_image
    model_sha = MODEL_SHA
    if args.counterfeit_control:
        if not args.recommended:
            raise ValueError("Counterfeit control is only for the adaptive profile")
        model_sha = "a54c944e4c04e9d9ca43468ef5b90ea0408bb6264829a4980de79a768df7179f"
    else:
        config.model = "animagine-xl-4.0-opt"
        local.model_path = ROOT / "data/local-image/animagine-xl-4.0-opt.safetensors"
    if digest(local.model_path) != model_sha:
        raise ValueError("New model SHA256 mismatch")
    local.ip_adapter_path = local.clip_vision_path = None
    local.width = local.height = args.resolution
    if args.recommended:
        local.steps = 28
        local.cfg_scale = 5
    local.work_dir = args.output.resolve() / "runtime"
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(baseline["path"], args.output / "baseline.png")
    plan = MockProvider().plan(prior["topic"], Painter(painter_id="base-test", name="Base test"))
    plan.prompt = prior["prompt"]
    if args.recommended:
        plan.prompt = (
            "1girl, solo, adult woman, safe, portrait, head and shoulders, looking at viewer, "
            "(chestnut brown bob hair:1.25), (amber eyes:1.25), "
            "(plain ivory blouse:1.5), (plain muted teal background:1.25), "
            "centered face, gentle smile, layered irises, small eye highlights, "
            "subtle cheek shading, plain white fabric, fold-down shirt collar, "
            "small white buttons, masterpiece, high score, great score, absurdres"
        )
    result = {
        "status": "running",
        "topic": prior["topic"],
        "prompt": plan.prompt,
        "constraints": prior["constraints"],
        "control_source": str(args.control.resolve()),
        "control_generator": prior["generator"],
        "generator": config.model_dump(mode="json"),
        "model_sha256": model_sha,
        "seed": local.seed,
        "adapters": None,
        "runner_sha256": digest(Path(__file__)),
        "runtime_sha256": digest(local.executable),
        "changed": ["base model"] + (["resolution"] if args.resolution != 512 else []),
        "recommended_profile": args.recommended,
        "limitations": [
            "One seed does not establish stable improvement.",
            "SDXL and SD1.5 use different architectures and conditioning.",
            "Same seed does not imply equivalent samples across architectures.",
            "No SDXL LoRA has been trained; this tests base capability only.",
            "1024 arm changes resolution; all other sampling settings remain fixed.",
        ],
        "images": {"baseline": {**baseline, "path": str((args.output / "baseline.png").resolve())}},
    }
    if args.recommended:
        result["changed"] = [] if args.counterfeit_control else ["base model"]
        result["changed"] += ["resolution"] if args.resolution != 512 else []
        result["changed"] += ["positive prompt formatting", "CFG", "steps"]
        result["limitations"].append(
            "Adaptive recommended-settings test, not a one-variable comparison."
        )
    report = args.output / "comparison.json"
    report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    start = time.monotonic()
    try:
        raw = LocalImageGenerator(config).generate(plan)
        arm = "counterfeit" if args.counterfeit_control else "animagine"
        image = args.output / (arm + ".png")
        image.write_bytes(raw)
        (run_dir,) = local.work_dir.iterdir()
        invocation = json.loads((run_dir / "invocation.json").read_text("utf-8"))
        command = invocation["command"]
        if (
            command[command.index("--negative-prompt") + 1]
            != prior["constraints"]["negative_prompt"]
        ):
            raise ValueError("Effective negative prompt changed")
        result["images"][arm] = {
            "path": str(image.resolve()),
            "sha256": digest(image),
            "runtime_log": str(run_dir / "generation.log"),
        }
        result["status"] = "complete"
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        raise
    finally:
        result["elapsed_seconds"] = time.monotonic() - start
        report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Completed {args.resolution} comparison in {result['elapsed_seconds']:.1f}s", flush=True)


if __name__ == "__main__":
    main()
