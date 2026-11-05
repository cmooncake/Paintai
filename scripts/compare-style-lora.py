"""Run an isolated same-prompt/same-seed adapter comparison in the application's runtime."""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from paintai.config import load_settings
from paintai.local_image import LocalImageGenerator
from paintai.models import Painter
from paintai.prompt_guard import negative_constraints, portrait_locks
from paintai.providers import MockProvider

ROOT = Path(__file__).resolve().parents[1]
TOPIC = (
    "Original anime head-and-shoulders portrait of an adult woman with chestnut brown bob hair "
    "and amber eyes, wearing a simple ivory blouse. Face centered, gentle smile, plain muted "
    "teal background. No text or logos."
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--profile",
        choices=["historical", "face-clothing-v1", "face-clothing-v2"],
        default="historical",
    )
    parser.add_argument(
        "--reuse-baseline",
        type=Path,
        help="Reuse a hash-verified baseline only when prompt and complete generator config match",
    )
    args = parser.parse_args()
    report = json.loads((args.training / "report.json").read_text("utf-8"))
    if report["status"] != "complete":
        raise ValueError("Training has not passed reload verification")
    weights = args.training / "xiaoxiao-smoke.safetensors"
    expected_hash = report["artifacts"][weights.name]
    if hashlib.sha256(weights.read_bytes()).hexdigest() != expected_hash:
        raise ValueError("Adapter hash mismatch")
    config = load_settings(ROOT / "configs/local-full.toml").generator.model_copy(deep=True)
    config.local_image.seed = 42
    config.local_image.width = config.local_image.height = 512
    config.local_image.steps = 20
    config.timeout_seconds = 1200
    config.local_image.work_dir = args.output.resolve() / "runtime"
    if (
        hashlib.sha256(config.local_image.model_path.read_bytes()).hexdigest()
        != report["manifest"]["base_model"]["sha256"]
    ):
        raise ValueError("Inference base model differs from training")
    args.output.mkdir(parents=True, exist_ok=False)
    plan = MockProvider().plan(TOPIC, Painter(painter_id="lora-test", name="LoRA test"))
    plan.prompt = TOPIC + "\nxiaoxiao_style, anime illustration, detailed eyes, clean line art"
    plan, constraints = LocalImageGenerator(config).prepare(plan)
    if args.profile.startswith("face-clothing-"):
        # Same explicit requirements, compiled once without repeated color anchors.
        # Apply identically to base and all tested adapters; this is a separate prompt intervention.
        plan.prompt = (
            "xiaoxiao_style, anime portrait of an adult woman, head and shoulders, "
            "(chestnut brown bob hair:1.25), (amber eyes:1.25), "
            "(plain ivory blouse:1.5), (plain muted teal background:1.25), "
            "centered face, gentle smile, layered irises, small eye highlights, "
            "subtle cheek shading, plain white fabric"
        )
        config.local_image.negative_prompt += (
            ", excessive blush, cheek hatch marks, face stripes, "
            "red nose, glowing eyes, flat irises, ribbon bow, brooch, colored buttons, "
            "contrasting trim, patterned blouse, open blouse, visible undershirt"
        )
        if args.profile == "face-clothing-v2":
            plan.prompt += (
                ", ordinary casual button-up shirt, fold-down shirt collar, "
                "solid white sleeves and bodice, small white buttons"
            )
            config.local_image.negative_prompt += (
                ", cheongsam, qipao, mandarin collar, "
                "standing collar, color-block clothing, contrasting sleeves, decorative piping"
            )
        locks = portrait_locks(plan.prompt)
        constraints = {
            "version": args.profile,
            "protected_attributes": locks,
            "negative_prompt": ", ".join(
                [config.local_image.negative_prompt, negative_constraints(locks)]
            ),
        }
    result = {
        "topic": TOPIC,
        "prompt": plan.prompt,
        "constraints": constraints,
        "generator": config.model_dump(mode="json"),
        "training": str(args.training),
        "training_steps": report["steps"],
        "adapter_sha256": expected_hash,
        "only_changed": "LoRA enabled at strength 1.0",
        "images": {},
        "limitations": "Single prompt/seed; image similarity alone does not prove general style learning",
        "validation_count": report.get("validation_count", 0),
    }
    result["prompt_profile"] = args.profile
    for arm in ["baseline", "lora"]:
        if arm == "baseline" and args.reuse_baseline:
            prior = json.loads((args.reuse_baseline / "comparison.json").read_text("utf-8"))
            old_config = prior["generator"]
            new_config = config.model_dump(mode="json")
            old_config["local_image"].pop("work_dir", None)
            new_config["local_image"].pop("work_dir", None)
            if (
                old_config != new_config
                or prior["prompt"] != plan.prompt
                or prior["constraints"] != constraints
            ):
                raise ValueError("Cannot reuse baseline: generation conditions differ")
            previous_image = args.reuse_baseline / "baseline.png"
            raw = previous_image.read_bytes()
            if hashlib.sha256(raw).hexdigest() != prior["images"]["baseline"]["sha256"]:
                raise ValueError("Baseline image hash mismatch")
            if prior["images"]["baseline"].get("lora") is not None:
                raise ValueError("Baseline unexpectedly used a LoRA")
            path = args.output / "baseline.png"
            shutil.copyfile(previous_image, path)
            result["images"]["baseline"] = {
                **prior["images"]["baseline"],
                "path": str(path.resolve()),
                "reused_from": str(args.reuse_baseline),
            }
            print("Reused verified identical-condition baseline", flush=True)
            continue
        generator = LocalImageGenerator(config)
        if arm == "lora":
            generator.set_lora(weights, expected_hash, 1.0)
        print("Generating", arm, flush=True)
        previous = set(config.local_image.work_dir.glob("*"))
        raw = generator.generate(plan)
        run_dirs = set(config.local_image.work_dir.glob("*")) - previous
        if len(run_dirs) != 1:
            raise ValueError("Cannot uniquely identify generation runtime log")
        run_dir = run_dirs.pop()
        log = (run_dir / "generation.log").read_text(encoding="utf-8", errors="replace")
        applied = re.findall(r"\((\d+) / (\d+)\) LoRA tensors have been applied", log)
        if arm == "lora" and not any(int(a) == int(b) and int(a) > 0 for a, b in applied):
            raise ValueError("Native runtime did not confirm applying every LoRA tensor")
        path = args.output / (arm + ".png")
        path.write_bytes(raw)
        result["images"][arm] = {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "lora": generator.lora_metadata,
            "runtime_log": str(run_dir / "generation.log"),
            "applied_tensor_counts": applied,
        }
        (args.output / "comparison.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print("Comparison images saved; inspect actual runtime logs and visual results.", flush=True)


if __name__ == "__main__":
    main()
