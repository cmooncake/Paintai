"""Public, painter-scoped experiment archives; never serve arbitrary report paths."""

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Arm(ReportModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    label: str
    step: int
    reused: bool = False
    hair: str
    clothing: str
    style: str
    validation_mse: float | None = None


class Reference(ReportModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    label: str


class LoraReport(ReportModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    painter_id: str
    title: str
    protocol: str
    conclusion: str
    image_provenance: str
    completed_steps: int = Field(ge=0)
    total_steps: int = Field(gt=0)
    technical_pass: bool
    style_pass: bool
    technical_check_count: int
    limitations: list[str]
    findings: list[dict[str, str]]
    arms: list[Arm]
    references: list[Reference] = Field(default_factory=list)


class LoraReports:
    def __init__(self, store):
        self.store = store

    def folder(self, painter_id, report_id=None):
        self.store.painter(painter_id)
        for value in [painter_id, *([report_id] if report_id is not None else [])]:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
                raise ValueError("Invalid archive ID")
        base = self.store.image_path(f"painters/{painter_id}/lora_reports")
        target = base if report_id is None else (base / report_id).resolve()
        if not target.is_relative_to(base):
            raise ValueError("Archive path escapes painter directory")
        return target

    def get(self, painter_id, report_id):
        path = self.folder(painter_id, report_id) / "report.json"
        if not path.is_file():
            raise ValueError("Report not found")
        report = LoraReport.model_validate_json(path.read_text("utf-8"))
        if report.painter_id != painter_id or report.id != report_id:
            raise ValueError("Report owner mismatch")
        return report

    def list(self, painter_id):
        result = []
        for path in sorted(self.folder(painter_id).glob("*/report.json"), reverse=True):
            report = self.get(painter_id, path.parent.name)
            public = report.model_dump()
            for field in ["arms", "references"]:
                for item in public[field]:
                    item["image_url"] = (
                        f"/api/v1/painters/{painter_id}/lora-reports/{report.id}/images/{item['id']}"
                    )
            result.append(public)
        return result

    def image(self, painter_id, report_id, image_id):
        report = self.get(painter_id, report_id)
        if image_id not in {a.id for a in [*report.arms, *report.references]}:
            raise ValueError("Image not registered")
        folder = self.folder(painter_id, report_id).resolve()
        path = (folder / f"{image_id}.png").resolve()
        if (
            not path.is_relative_to(folder)
            or not path.is_relative_to(self.store.root)
            or not path.is_file()
        ):
            raise ValueError("Image not available")
        return path

    def publish(self, report: LoraReport, images: dict[str, Path]):
        from PIL import Image

        expected = {a.id for a in [*report.arms, *report.references]}
        if len(expected) != len(report.arms) + len(report.references) or set(images) != expected:
            raise ValueError("Images must match unique registered IDs")
        folder = self.folder(report.painter_id, report.id)
        for path in images.values():
            if not path.resolve().is_relative_to(self.store.root):
                raise ValueError("Source outside data directory")
            with Image.open(path) as image:
                image.verify()
        folder.mkdir(parents=True, exist_ok=True)
        for image_id, path in images.items():
            temporary = folder / f"{image_id}.tmp"
            with Image.open(path) as image:
                if image.format == "PNG":
                    temporary.write_bytes(path.read_bytes())
                else:
                    image.convert("RGB").save(temporary, format="PNG")
            temporary.replace(folder / f"{image_id}.png")
        temporary = folder / "report.tmp"
        temporary.write_text(
            json.dumps(report.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(folder / "report.json")
