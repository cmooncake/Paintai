"""Portable painter snapshots: allowlisted records and generated images only."""

import hashlib
import io
import json
import re
import shutil
import sqlite3
import tempfile
import zipfile
import zlib
from pathlib import Path

from PIL import Image

from .curriculum.spec import CurriculumSpec, load_spec
from .evolution import Evolution
from .lora_reports import LoraReport, LoraReports
from .models import Painter, StyleProfile
from .storage import now

MAX_ARCHIVE = 256 * 1024 * 1024
MAX_JSON = 16 * 1024 * 1024
TABLES = {
    "painters": ("painter_id", "profile", "created_at"),
    "creations": ("id", "painter_id", "created_at", "updated_at", "status", "payload"),
    "curricula": ("painter_id", "spec"),
    "learning_rounds": ("id", "painter_id", "lesson_key", "payload", "created_at"),
    "experiences": ("painter_id", "entry_id", "payload", "updated_at"),
    "distilled_lessons": ("painter_id", "group_key", "payload"),
    "growth_experiments": ("id", "painter_id", "payload", "created_at"),
    "style_profiles": ("id", "painter_id", "payload"),
}
NOTICE = "迁移包不含密钥、模型权重和参考原作；历史评价保留，参考图功能需重新上传原作。"


class TransferError(ValueError):
    pass


class TransferConflict(TransferError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


def clean(value):
    """Drop runtime settings and reference attachments, retaining historical text."""
    if isinstance(value, list):
        return [clean(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        lower = key.lower()
        if lower in {"reference_ids", "reference_hashes", "references"}:
            result[key] = []
        elif lower == "config":
            result[key] = (
                {
                    role: {k: v for k, v in cfg.items() if k in {"provider", "model"}}
                    for role, cfg in item.items()
                    if isinstance(cfg, dict)
                }
                if isinstance(item, dict)
                else {}
            )
        elif (
            any(word in lower for word in ("secret", "token", "credential", "password", "api_key"))
            or lower in {"settings", "headers", "authorization", "base_url", "error"}
            or lower.endswith(("_path", "_url"))
        ):
            continue
        else:
            result[key] = clean(item)
    return result


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise TransferError("迁移包包含无效标识符。")
    return value


class Transfer:
    def __init__(self, store):
        self.store = store
        Evolution(store)

    def export(self, painter_id):
        identifier(painter_id)
        files = {}
        size = 0

        def add(name, path):
            nonlocal size
            path = path.resolve()
            # Only caller-selected generated images under this painter can be exported.
            owner = self.store.image_path(f"painters/{painter_id}")
            if not path.is_relative_to(owner) or not path.is_file():
                raise TransferError("部分生成图缺失或不在画师目录内，无法完整导出。")
            size += path.stat().st_size
            if size > MAX_ARCHIVE:
                raise TransferError("迁移数据超过 256 MB，请先分批整理作品。")
            files[name] = path.read_bytes()

        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM jobs WHERE painter_id=? AND status IN ('queued','running')",
                (painter_id,),
            ).fetchone():
                raise TransferConflict("请等待该画师的任务完成后再导出。")
            tables = {}
            for table, columns in TABLES.items():
                rows = db.execute(
                    f"SELECT {','.join(columns)} FROM {table} WHERE painter_id=?", (painter_id,)
                ).fetchall()
                tables[table] = []
                for raw in rows:
                    row = dict(raw)
                    for column in ("profile", "spec", "payload"):
                        if column not in row:
                            continue
                        original = json.loads(row[column])
                        value = clean(original)
                        if table == "creations":
                            value["image_path"] = None
                            if original.get("image_path"):
                                name = f"gallery/{identifier(row['id'])}.png"
                                add(name, self.store.image_path(original["image_path"]))
                                value["image_path"] = f"painters/{painter_id}/{name}"
                        row[column] = encode(value).decode()
                    tables[table].append(row)
            if len(tables["painters"]) != 1:
                raise TransferError("画师不存在。")
            # Freeze the effective default too: a later installation may ship new lessons.
            if not tables["curricula"]:
                tables["curricula"] = [
                    {"painter_id": painter_id, "spec": load_spec().model_dump_json()}
                ]
            reports = []
            archive = LoraReports(self.store)
            for item in archive.list(painter_id):
                report = archive.get(painter_id, item["id"])
                report.references = []
                if NOTICE not in report.limitations:
                    report.limitations.append(NOTICE)
                reports.append(report.model_dump())
                for arm in report.arms:
                    add(
                        f"lora_reports/{report.id}/{arm.id}.png",
                        archive.image(painter_id, report.id, arm.id),
                    )
        manifest = {
            "format": "paintai-painter",
            "version": 1,
            "exported_at": now(),
            "painter_id": painter_id,
            "notice": NOTICE,
            "tables": tables,
            "reports": reports,
            "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()},
        }
        raw_manifest = encode(manifest)
        if len(raw_manifest) > MAX_JSON or size + len(raw_manifest) > MAX_ARCHIVE:
            raise TransferError("迁移包过大（元数据限 16 MB，总计限 256 MB）。")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("manifest.json", raw_manifest)
            for name, raw in files.items():
                bundle.writestr(name, raw)
        if output.tell() > MAX_ARCHIVE:
            raise TransferError("迁移包超过 256 MB。")
        return output.getvalue()

    def read(self, raw):
        try:
            return self._read(raw)
        except TransferError:
            raise
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            zipfile.BadZipFile,
            zlib.error,
            Image.DecompressionBombError,
            RuntimeError,
        ):
            raise TransferError("迁移包损坏或格式不正确，未导入任何数据。") from None

    def _read(self, raw):
        if len(raw) > MAX_ARCHIVE:
            raise TransferError("迁移包不能超过 256 MB。")
        with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
            entries = bundle.infolist()
            names = [entry.filename for entry in entries]
            if (
                len(entries) > 10000
                or len(names) != len({name.casefold() for name in names})
                or sum(entry.file_size for entry in entries) > MAX_ARCHIVE
            ):
                raise TransferError("迁移包体积或文件列表异常。")
            if bundle.getinfo("manifest.json").file_size > MAX_JSON:
                raise TransferError("迁移包元数据过大。")
            manifest = json.loads(bundle.read("manifest.json"))
            if manifest["format"] != "paintai-painter" or manifest["version"] != 1:
                raise TransferError("不支持此迁移包版本，请使用匹配版本的 Paintai。")
            pid = identifier(manifest["painter_id"])
            tables = manifest["tables"]
            if set(tables) != set(TABLES) or len(tables["painters"]) != 1:
                raise TransferError("迁移包数据表不完整。")
            expected = set()
            for table, columns in TABLES.items():
                for row in tables[table]:
                    if (
                        set(row) != set(columns)
                        or not all(isinstance(v, str) for v in row.values())
                        or row["painter_id"] != pid
                    ):
                        raise TransferError("迁移包的记录归属或字段不正确。")
                    if "id" in row:
                        identifier(row["id"])
                    for column in ("profile", "spec", "payload"):
                        if column not in row:
                            continue
                        value = json.loads(row[column])
                        if not isinstance(value, dict) or value.get("painter_id", pid) != pid:
                            raise TransferError("迁移包包含其他画师的记录。")
                        if "id" in row and value.get("id", row["id"]) != row["id"]:
                            raise TransferError("记录标识符不一致。")
                        if table == "painters":
                            Painter.model_validate(value)
                        if table == "curricula":
                            CurriculumSpec.model_validate(value)
                        if table == "style_profiles":
                            profile = StyleProfile.model_validate(value["profile"])
                            if profile.id != row["id"]:
                                raise TransferError("风格档案标识符不一致。")
                        image_path = value.get("image_path")
                        value = clean(value)
                        if table == "creations":
                            name = f"gallery/{row['id']}.png"
                            if image_path not in (None, f"painters/{pid}/{name}"):
                                raise TransferError("作品路径不合法。")
                            value["image_path"] = image_path
                            if image_path:
                                expected.add(name)
                        row[column] = encode(value).decode()
            reports = [LoraReport.model_validate(item) for item in manifest["reports"]]
            if len({r.id for r in reports}) != len(reports):
                raise TransferError("实验报告重复。")
            for report in reports:
                if report.painter_id != pid or report.references:
                    raise TransferError("实验报告归属不正确或包含参考原作。")
                if len({a.id for a in report.arms}) != len(report.arms):
                    raise TransferError("实验图片重复。")
                expected.update(f"lora_reports/{report.id}/{arm.id}.png" for arm in report.arms)
            if set(names) != expected | {"manifest.json"} or set(manifest["files"]) != expected:
                raise TransferError("迁移包图片缺失或包含未授权文件。")
            files = {}
            for name in expected:
                content = bundle.read(name)
                if hashlib.sha256(content).hexdigest() != manifest["files"][name]:
                    raise TransferError("图片校验失败，迁移包可能已损坏。")
                with Image.open(io.BytesIO(content)) as image:
                    if image.format != "PNG" or image.width * image.height > 25_000_000:
                        raise TransferError("迁移包包含不支持的图片。")
                    image.verify()
                files[name] = content
            return manifest, reports, files

    def restore(self, raw):
        manifest, reports, files = self.read(raw)
        pid = manifest["painter_id"]
        tables = manifest["tables"]
        target = self.store.image_path(f"painters/{pid}")
        parent = self.store.image_path("painters")
        parent.mkdir(exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".import-", dir=parent))
        moved = False
        try:
            for name, content in files.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            for report in reports:
                path = stage / "lora_reports" / report.id / "report.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(encode(report.model_dump()))
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if (
                    target.exists()
                    or db.execute("SELECT 1 FROM painters WHERE painter_id=?", (pid,)).fetchone()
                ):
                    raise TransferConflict(
                        "此画师已存在，未覆盖任何数据。请在空白画室或另一台电脑导入。"
                    )
                for table, columns in TABLES.items():
                    for row in tables[table]:
                        db.execute(
                            f"INSERT INTO {table} ({','.join(columns)}) VALUES "
                            f"({','.join('?' for _ in columns)})",
                            tuple(row[c] for c in columns),
                        )
                stage.rename(target)
                moved = True
        except Exception as exc:
            if moved:
                shutil.rmtree(target)
            if isinstance(exc, sqlite3.IntegrityError):
                raise TransferConflict("记录标识符与现有数据冲突，未导入或覆盖任何数据。") from None
            raise
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        return {
            "painter": self.store.painter(pid).model_dump(),
            "notice": NOTICE,
            "counts": {
                **{table: len(rows) for table, rows in tables.items()},
                "lora_reports": len(reports),
                "images": len(files),
            },
        }
