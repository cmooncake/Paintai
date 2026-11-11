"""Server-owned source images and normalized lasso masks for local inpainting."""

import hashlib
import io

from PIL import Image, ImageDraw
from pydantic import Field

from .config import ProviderConfig
from .models import Model, Plan, Text
from .revision_comparison import publish_edit_pair


class InpaintError(ValueError):
    pass


class Point(Model):
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)


class InpaintRequest(Model):
    instructions: Text = Field(max_length=1200)
    regions: list[list[Point]] = Field(min_length=1, max_length=8)
    strength: float = Field(default=0.6, ge=0.1, le=0.95)


def make_mask(size, regions):
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for region in regions:
        if not 3 <= len(region) <= 1000:
            raise InpaintError("每个圈选区域需要 3–1000 个点。")
        draw.polygon(
            [(round(p.x * (size[0] - 1)), round(p.y * (size[1] - 1))) for p in region], fill=255
        )
    area = sum(mask.histogram()[1:]) / (size[0] * size[1])
    if not 0.0001 <= area <= 0.8:
        raise InpaintError("圈选区域过小或超过画面 80%，请重新圈选。")
    return mask


def snapshot(store, painter_id, source_id, request, settings):
    source = store.get(painter_id, source_id)
    if not source.get("image_path") or not source.get("plan"):
        raise ValueError("作品尚无可编辑的图片。")
    path = store.image_path(source["image_path"])
    with Image.open(path) as im:
        make_mask(im.size, request.regions)
    ancestor = source
    for _ in range(12):
        if ancestor["config"]["generator"].get("provider") == "sd_cpp":
            settings.generator = ProviderConfig.model_validate(ancestor["config"]["generator"])
            break
        parent = ancestor.get("image_edit", {}).get("source_id")
        if not parent:
            break
        ancestor = store.get(painter_id, parent)
    if settings.generator.provider != "sd_cpp":
        raise InpaintError("局部重绘需要配置本地 sd_cpp 生图模型。")
    local = settings.generator.local_image
    if not local.executable.is_file() or not local.model_path.is_file():
        raise InpaintError("本地重绘模型或运行器不存在，请先配置本地生图模型。")
    return {
        "source_id": source_id,
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "_request": request.model_dump(),
        "model": settings.generator.model,
    }


def run(agent, payload, painter_id, on_started):
    store = agent.store
    old = store.get(painter_id, payload["source_id"])
    path = store.image_path(old["image_path"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != payload["source_sha256"]:
        raise ValueError("源图片已变化，请重新圈选。")
    request = InpaintRequest.model_validate(payload["_request"])
    original = Image.open(path).convert("RGB")
    mask = make_mask(original.size, request.regions)
    entry = store.begin(
        painter_id,
        old["topic"],
        {
            r: getattr(agent.settings, r).model_dump(mode="json")
            for r in ("planner", "generator", "critic")
        },
    )
    for key in ("painter_snapshot", "art_brief"):
        entry[key] = old.get(key)
    plan = Plan.model_validate(old["plan"]).model_copy(deep=True)
    plan.prompt = request.instructions + ", " + agent.settings.generator.local_image.prompt_suffix
    entry["plan"] = plan.model_dump()
    entry["image_edit"] = {
        **payload,
        "method": "sd_cpp_masked_inpaint",
        "mask_supplied": True,
        "preserves_unselected_pixels": True,
        "instructions": request.instructions,
    }
    entry["rubric_version"] = old["rubric_version"]
    entry["status"] = "generating"
    store.save(entry)
    on_started(entry)
    folder = store.root / "painters" / painter_id / "edits" / entry["id"]
    folder.mkdir(parents=True, exist_ok=True)
    mask.save(folder / "mask.png")
    local = agent.settings.generator.local_image
    size = (local.width, local.height)
    original.resize(size, Image.Resampling.LANCZOS).save(folder / "input.png")
    mask.resize(size, Image.Resampling.NEAREST).save(folder / "runtime-mask.png")
    agent.generator.inpaint = (folder / "input.png", folder / "runtime-mask.png", request.strength)
    try:
        generated = (
            Image.open(io.BytesIO(agent.generator.generate(plan)))
            .convert("RGB")
            .resize(original.size, Image.Resampling.LANCZOS)
        )
        result = Image.composite(generated, original, mask)
        out = io.BytesIO()
        result.save(out, format="PNG")
        store.save_image(entry, out.getvalue())
    except Exception:
        entry["status"] = "creation_failed"
        entry["error"] = "局部重绘失败，原图保留。请检查本地模型运行日志。"
        store.save(entry)
        raise
    publish_edit_pair(
        store,
        painter_id,
        old["id"],
        entry["id"],
        conclusion="已生成局部重绘。蒙版外像素保留原图；选区内手部等细节需查看图像验收，自动分数不代表修复成功。",
        checks=[],
    )
    entry = agent.evaluate(painter_id, entry["id"])
    publish_edit_pair(
        store,
        painter_id,
        old["id"],
        entry["id"],
        conclusion="已完成本地蒙版重绘与模型评价。蒙版外像素保留原图；请检查选区中的手指数量、关节和托盒接触关系。",
        checks=[],
    )
    return entry
