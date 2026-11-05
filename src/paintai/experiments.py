"""Frozen-memory paired ablations; experiment outputs never train either arm."""

import hashlib
import json
import math
import statistics
from copy import deepcopy
from typing import Literal

from pydantic import Field

from .evolution import fingerprint
from .learning import LearningError
from .memory import Memory
from .models import Model, Text
from .storage import now


class ExperimentRequest(Model):
    topic: Text = Field(max_length=1000)
    pairs: int = Field(default=3, ge=1, le=10)
    mode: Literal["memory", "style_profile", "image_reference"] = "memory"
    reference_index: int = Field(default=0, ge=0, le=2)
    reference_strength: float = Field(default=0.6, gt=0, le=1, allow_inf_nan=False)


class FrozenMemory:
    def __init__(self, notes):
        self.notes = deepcopy(notes)

    def search(self, painter_id, topic, skills, k=3):
        return deepcopy(self.notes[:k])


def summarize(record):
    pairs = []
    for index in range(record["pairs"]):
        arms = {r["arm"]: r for r in record["results"] if r["pair"] == index}
        if set(arms) == {"baseline", "learned"}:
            pairs.append(arms)
    count = len(pairs)
    if not count:
        return {
            "paired": 0,
            "conclusion": "等待完整配对",
            "delta": None,
            "ci95": None,
            "dimensions": {},
        }
    before = [p["baseline"]["critique"]["overall"] for p in pairs]
    after = [p["learned"]["critique"]["overall"] for p in pairs]
    differences = [b - a for a, b in zip(before, after, strict=True)]
    mean = statistics.mean(differences)
    ci = None
    if count >= 3 and statistics.stdev(differences) > 0:
        critical = {2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}[
            count - 1
        ]
        radius = critical * statistics.stdev(differences) / math.sqrt(count)
        ci = [round(mean - radius, 3), round(mean + radius, 3)]
    if record["simulated"]:
        conclusion = "模拟结果，不用于判断成长"
    elif record["status"] != "complete" or count != record["pairs"]:
        conclusion = "实验未完整完成，暂不下结论"
    elif record.get("mode") == "style_profile" and all(
        p["baseline"].get("prompt_digest")
        and p["baseline"]["prompt_digest"] == p["learned"].get("prompt_digest")
        for p in pairs
    ):
        conclusion = "风格档案未改变生图提示词，无法比较学习效果"
    elif count < 3:
        conclusion = "样本不足，尚不能判断"
    elif not record.get("effective_memory"):
        conclusion = "没有注入有效经验，无法比较学习效果"
    elif ci and ci[0] > 0:
        conclusion = "本题有提升迹象，仍需更多主题复验"
    elif ci and ci[1] < 0:
        conclusion = "本题出现退步迹象，建议检查经验规则"
    else:
        conclusion = "尚未观察到稳定提升"
    dimensions = {}
    for key in pairs[0]["baseline"]["critique"]["scores"]:
        a = statistics.mean(p["baseline"]["critique"]["scores"][key] for p in pairs)
        b = statistics.mean(p["learned"]["critique"]["scores"][key] for p in pairs)
        dimensions[key] = {
            "baseline": round(a, 3),
            "learned": round(b, 3),
            "delta": round(b - a, 3),
        }
    return {
        "paired": count,
        "baseline": round(statistics.mean(before), 3),
        "learned": round(statistics.mean(after), 3),
        "delta": round(mean, 3),
        "ci95": ci,
        "wins": sum(d > 0.05 for d in differences),
        "ties": sum(abs(d) <= 0.05 for d in differences),
        "losses": sum(d < -0.05 for d in differences),
        "dimensions": dimensions,
        "conclusion": conclusion,
    }


class Experiments:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS growth_experiments (
                id TEXT PRIMARY KEY, painter_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)""")

    def snapshot(self, painter_id, request, settings):
        painter = self.store.painter(painter_id)
        notes = Memory(self.store).search(
            painter_id, request.topic + " " + painter.growth_goal, [], k=settings.memory_top_k
        )
        if request.mode in ("style_profile", "image_reference"):
            if not painter.style_profile:
                raise LearningError("请先启用风格档案。")
            notes = []
        elif not notes or not settings.memory_top_k:
            raise LearningError("还没有可用于此主题的真实经验。请先创作或学习，并启用记忆检索。")
        snapshot = {
            **request.model_dump(),
            "painter_snapshot": painter.model_dump(),
            "art_brief": {
                **({"growth_goal": painter.growth_goal} if painter.growth_goal else {}),
                **(
                    {"style_profile": painter.style_profile.model_dump()}
                    if painter.style_profile
                    else {}
                ),
            },
            "memory_snapshot": notes,
            "memory_digest": fingerprint(notes),
            "protocol": "frozen_memory_ab_v1",
            "created_at": now(),
            "conditions": {
                role: {
                    "provider": getattr(settings, role).provider,
                    "model": getattr(settings, role).model,
                    "parameters": getattr(settings, role).parameters,
                }
                for role in ("planner", "generator", "critic")
            },
            "settings_digest": fingerprint(settings.model_dump(mode="json")),
            "seed_controlled": settings.generator.provider == "sd_cpp",
            "base_seed": settings.generator.local_image.seed
            if settings.generator.provider == "sd_cpp"
            else None,
            "memory_limit": settings.memory_top_k,
        }
        if settings.generator.provider == "sd_cpp":
            local = settings.generator.local_image.model_dump(mode="json")
            snapshot["conditions"]["generator"]["local_image"] = {
                key: value
                for key, value in local.items()
                if key
                not in (
                    "executable",
                    "model_path",
                    "work_dir",
                    "ip_adapter_path",
                    "clip_vision_path",
                )
            }
        if request.mode == "image_reference":
            from .style_learning import StyleLearning

            local = settings.generator.local_image
            if (
                settings.generator.provider != "sd_cpp"
                or not local
                or not local.ip_adapter_path
                or not local.clip_vision_path
            ):
                raise LearningError("参考图生图需要本地 SD 引擎和 IP-Adapter 配置。")
            if not local.ip_adapter_path.is_file() or not local.clip_vision_path.is_file():
                raise LearningError("请先安装参考图适配器：scripts/setup-reference-adapter.py。")
            profile = painter.style_profile
            if request.reference_index >= len(profile.reference_ids):
                raise LearningError("所选参考图不在当前风格档案中。")
            rid = profile.reference_ids[request.reference_index]
            digest = profile.reference_hashes[request.reference_index]
            if (
                hashlib.sha256(
                    StyleLearning(self.store).path(painter_id, rid).read_bytes()
                ).hexdigest()
                != digest
            ):
                raise LearningError("参考图与冻结档案不一致。")
            snapshot["image_conditioning"] = {
                "method": "ip-adapter-plus-sd15",
                "reference_id": rid,
                "sha256": digest,
                "strength": request.reference_strength,
                "prompt_policy": "identical_prepared_prompt_both_arms",
            }
            snapshot["image_conditioning"]["weights"] = {}
            for key in ("ip_adapter_path", "clip_vision_path"):
                path = getattr(local, key)
                with path.open("rb") as handle:
                    weight_hash = hashlib.file_digest(handle, "sha256").hexdigest()
                snapshot["image_conditioning"]["weights"][key] = {
                    "name": path.name,
                    "sha256": weight_hash,
                }
        return snapshot

    def save(self, record):
        record["summary"] = summarize(record)
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO growth_experiments VALUES (?,?,?,?) ON CONFLICT(id) "
                "DO UPDATE SET payload=excluded.payload",
                (
                    record["id"],
                    record["painter_id"],
                    json.dumps(record, ensure_ascii=False),
                    record["created_at"],
                ),
            )

    def list(self, painter_id):
        self.store.painter(painter_id)
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM growth_experiments WHERE painter_id=? "
                "ORDER BY created_at DESC LIMIT 30",
                (painter_id,),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def run(self, painter_id, snapshot, *, run_id, factory, on_started=None):
        record = {
            **deepcopy(snapshot),
            "id": run_id,
            "painter_id": painter_id,
            "status": "running",
            "results": [],
            "simulated": False,
            "effective_memory": False,
        }
        self.save(record)
        try:
            for index in range(record["pairs"]):
                prepared_plan = None
                order = ["baseline", "learned"] if index % 2 == 0 else ["learned", "baseline"]
                seed = (
                    (record["base_seed"] + index) % 2147483648
                    if record["seed_controlled"]
                    else None
                )
                for arm in order:
                    agent = factory(index, arm, seed, record["memory_snapshot"])
                    painter = deepcopy(record["painter_snapshot"])
                    brief = deepcopy(record["art_brief"])
                    if record.get("mode") in ("style_profile", "image_reference"):
                        agent.settings = agent.settings.model_copy(update={"memory_top_k": 0})
                        # Isolate reference learning from artist-name/broad-goal priming in BOTH arms.
                        brief.pop("growth_goal", None)
                        painter["growth_goal"] = ""
                        record["generation_goal_policy"] = "excluded_in_both_arms"
                        if arm == "baseline" and record.get("mode") == "style_profile":
                            painter["style_profile"] = None
                            brief.pop("style_profile", None)
                    extra = {}
                    if record.get("mode") == "image_reference":
                        from .style_learning import StyleLearning

                        if arm == "learned":
                            ref = record["image_conditioning"]
                            agent.generator.set_reference(
                                StyleLearning(self.store).path(painter_id, ref["reference_id"]),
                                ref["sha256"],
                                ref["strength"],
                                ref["reference_id"],
                            )
                        if prepared_plan is not None:
                            extra["prepared_plan"] = prepared_plan
                    entry = agent.create(
                        record["topic"],
                        painter_id,
                        painter_snapshot=painter,
                        art_brief=brief,
                        evaluation_requirements=record["art_brief"],
                        experiment={"id": run_id, "pair": index, "arm": arm, "seed": seed},
                        on_started=on_started,
                        **extra,
                    )
                    if record.get("mode") == "image_reference" and prepared_plan is None:
                        prepared_plan = {
                            "plan": deepcopy(entry["plan"]),
                            "generation_constraints": deepcopy(entry["generation_constraints"]),
                        }
                    record["simulated"] |= entry["simulated"]
                    if arm == "learned" and (
                        entry["memory_used"]
                        or record.get("mode") in ("style_profile", "image_reference")
                    ):
                        record["effective_memory"] = True
                    record["results"].append(
                        {
                            "pair": index,
                            "arm": arm,
                            "seed": seed,
                            "prompt_digest": fingerprint(entry["plan"]["prompt"]),
                            "entry_id": entry["id"],
                            "critique": entry["critique"],
                            "memory_used": entry["memory_used"],
                            "simulated": entry["simulated"],
                        }
                    )
                    self.save(record)
            record["status"] = "complete"
            self.save(record)
            return entry
        except Exception as exc:
            record["status"] = "failed"
            if getattr(exc, "entry", None):
                record["failed_entry_id"] = exc.entry["id"]
            self.save(record)
            raise

    def recover(self):
        with self.store.connect() as db:
            rows = db.execute("SELECT payload FROM growth_experiments").fetchall()
        for row in rows:
            record = json.loads(row["payload"])
            changed = False
            for review in record.get("reference_reviews", []):
                if review["status"] == "running":
                    review["status"] = "interrupted"
                    review["error"] = "服务重启中断了对照评价；已保存的分项保留，可另开一轮重评。"
                    changed = True
            if record["status"] == "running":
                record["status"] = "interrupted"
                changed = True
            if changed:
                self.save(record)
