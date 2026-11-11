"""Publish a matched prompt-revision pair without calling it a learning experiment."""

import hashlib

from .storage import now


def publish_revision_pair(store, painter_id, baseline_id, revised_id, *, conclusion, checks=None):
    before = store.get(painter_id, baseline_id)
    after = store.get(painter_id, revised_id)
    if baseline_id == revised_id or before["topic"] != after["topic"]:
        raise ValueError("Comparison needs distinct works with the same topic")
    settings = []
    for entry in (before, after):
        if entry["status"] != "complete" or not entry.get("critique"):
            raise ValueError("Complete both generation and evaluation before publishing")
        config = entry["config"]["generator"]
        local = config.get("local_image") or {}
        settings.append(
            {
                "model": config["model"],
                **{
                    k: local.get(k)
                    for k in (
                        "model_path",
                        "executable",
                        "width",
                        "height",
                        "steps",
                        "cfg_scale",
                        "sampling_method",
                        "scheduler",
                        "seed",
                        "clip_skip",
                        "threads",
                    )
                },
            }
        )
    if settings[0] != settings[1] or settings[0]["seed"] is None:
        raise ValueError("Base model, runtime, seed and sampling settings must match")
    for key in (
        "painter_snapshot",
        "reference_conditioning",
        "lora_conditioning",
        "rubric_version",
    ):
        if before.get(key) != after.get(key):
            raise ValueError(f"Comparison differs in {key}")
    pair = {
        "baseline_id": baseline_id,
        "revised_id": revised_id,
        "seed": settings[0]["seed"],
        "created_at": now(),
        "rubric": after["rubric_version"],
        "note": "同题、同底模、同尺寸与采样参数；仅调整提示词编译与属性约束。两图均按新评价规则重评，分数为发布时快照。",
        "conclusion": conclusion,
        "checks": checks or [],
        "baseline_critique": before["critique"],
        "revised_critique": after["critique"],
        "image_hashes": {
            e["id"]: hashlib.sha256(store.image_path(e["image_path"]).read_bytes()).hexdigest()
            for e in (before, after)
        },
    }
    return _save_pair(store, before, after, pair)


def publish_edit_pair(store, painter_id, baseline_id, revised_id, *, conclusion, checks):
    before = store.get(painter_id, baseline_id)
    after = store.get(painter_id, revised_id)
    source_hash = hashlib.sha256(store.image_path(before["image_path"]).read_bytes()).hexdigest()
    edit = after.get("image_edit", {})
    if baseline_id == revised_id:
        raise ValueError("Edit comparison needs distinct works")
    if before["topic"] != after["topic"] and edit.get("source_topic") != before["topic"]:
        raise ValueError("Changed edit topic requires recorded source topic")
    if edit.get("source_id") != baseline_id or edit.get("source_sha256") != source_hash:
        raise ValueError("Edit source provenance mismatch")
    local_edit = edit.get("method") == "sd_cpp_masked_inpaint"
    if not local_edit and any(
        e["status"] != "complete" or not e.get("critique") for e in (before, after)
    ):
        raise ValueError("Complete evaluation before publishing edit")
    pair = {
        "kind": "image_edit",
        "seed": None,
        "baseline_id": baseline_id,
        "revised_id": revised_id,
        "created_at": now(),
        "rubric": after["rubric_version"],
        "note": (
            "本地 sd_cpp 蒙版重绘；保留原图和原尺寸，蒙版外像素与原图一致。修改要求："
            + edit.get("instructions", "")
        )
        if local_edit
        else "基于原图的局部图像编辑；使用内置 imagegen，非本地 Animagine 同种子重生成。未提供像素锁定蒙版，不保证编辑区域外像素完全一致。",
        "conclusion": conclusion,
        "checks": checks,
        "baseline_critique": before["critique"],
        "revised_critique": after["critique"],
        "image_hashes": {
            e["id"]: hashlib.sha256(store.image_path(e["image_path"]).read_bytes()).hexdigest()
            for e in (before, after)
        },
    }
    if before["topic"] != after["topic"]:
        pair["note"] += " 本次按用户要求细化了服装目标；前后评分对应各自题目，不作分数增益比较。"
    return _save_pair(store, before, after, pair)


def _save_pair(store, before, after, pair):
    baseline_id, revised_id = pair["baseline_id"], pair["revised_id"]
    for entry in (before, after):
        history = entry.setdefault("revision_comparisons", [])
        previous = entry.get("revision_comparison")
        if previous and not any(
            r["baseline_id"] == previous["baseline_id"]
            and r["revised_id"] == previous["revised_id"]
            for r in history
        ):
            history.append(previous)
        history[:] = [
            r for r in history if (r["baseline_id"], r["revised_id"]) != (baseline_id, revised_id)
        ]
        history.append(pair)
        entry["revision_comparison"] = pair
        store.save(entry)
    return pair
