"""Publish a verified continuous-checkpoint experiment to the painter's web archive."""

import argparse
import hashlib
import json
from pathlib import Path

from paintai.lora_reports import LoraReport, LoraReports
from paintai.storage import Store


def read(path):
    return json.loads(path.read_text("utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--id", default="lillie-continuous-160")
    parser.add_argument("--title", default="莉莉艾 · 40 / 80 / 160 步连续训练")
    args = parser.parse_args()
    store = Store(args.data_dir)
    plan = read(args.review / "plan.json")
    assessment = read(args.review / "assessment.json")
    audit = read(args.review / "technical-acceptance.json")
    training = read(Path(plan["training_root"]) / "report.json")
    if training["status"] != "complete" or not audit["technical_pass"]:
        raise ValueError("Only completed, verified experiments can be archived")
    arms, images = [], {}
    for row in assessment["checkpoint_results"]:
        step = row["step"]
        folder = (
            Path(plan["baseline"])
            if step == 0
            else args.review.parent / f"lillie-continuous-{step}"
        )
        comparison = read(folder / "comparison.json")
        if comparison["status"] != "complete":
            raise ValueError("Comparison incomplete")
        image = comparison["images"]["animagine" if step == 0 else "lora"]
        if hashlib.sha256(Path(image["path"]).read_bytes()).hexdigest() != image["sha256"]:
            raise ValueError("Comparison image hash mismatch")
        image_id = f"step-{step}"
        images[image_id] = Path(image["path"])
        mse = (
            audit["validation_base_mse"]
            if step == 0
            else next(r["validation_mse"] for r in audit["checkpoints"] if r["step"] == step)
        )
        arms.append(
            {
                "id": image_id,
                "label": "底模 · 未训练" if step == 0 else f"{step} 步",
                "step": step,
                "reused": "reuse_proof" in comparison,
                "hair": row["hair"],
                "clothing": row["clothing"],
                "style": row["style"],
                "validation_mse": mse,
            }
        )
    references = []
    for ref in assessment["references"]:
        image_id = ref["id"]
        images[image_id] = args.review.parent / "expanded/candidates" / f"{image_id}.jpg"
        references.append({"id": image_id, "label": f"dsmile · {image_id}"})
    report = LoraReport(
        id=args.id,
        painter_id=training["manifest"]["painter_id"],
        title=args.title,
        protocol=assessment["protocol"],
        conclusion=assessment["conclusion"],
        image_provenance=assessment["image_provenance"],
        completed_steps=len(training["losses"]),
        total_steps=training["steps"],
        technical_pass=audit["technical_pass"],
        style_pass=assessment["overall_style_pass"],
        technical_check_count=len(audit["checks"]),
        limitations=assessment["interpretation"],
        findings=assessment["dimension_findings"],
        arms=arms,
        references=references,
    )
    LoraReports(store).publish(report, images)
    print(f"Archived {report.id} for painter {report.painter_id}: {len(arms)} comparisons")


if __name__ == "__main__":
    main()
