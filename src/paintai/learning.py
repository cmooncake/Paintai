"""Three-image lesson rounds, immutable assessment evidence and stable-ID progress."""

import hashlib
import json
from uuid import uuid4

from .curriculum.spec import CurriculumSpec, generate_tasks, lesson_key, load_spec
from .models import PracticeOptions
from .storage import Store, now


class LearningError(ValueError):
    pass


class Learning:
    def __init__(self, store: Store):
        self.store = store
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS curricula (
                    painter_id TEXT PRIMARY KEY, spec TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS learning_rounds (
                    id TEXT PRIMARY KEY, painter_id TEXT NOT NULL, lesson_key TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL);
            """)

    def spec(self, painter_id):
        self.store.painter(painter_id)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT spec FROM curricula WHERE painter_id=?", (painter_id,)
            ).fetchone()
        return CurriculumSpec.model_validate_json(row["spec"]) if row else load_spec()

    def assign(self, painter_id, spec: CurriculumSpec):
        self.store.painter(painter_id)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='jobs'").fetchone():
                if db.execute(
                    "SELECT 1 FROM jobs WHERE painter_id=? AND status IN ('queued','running')",
                    (painter_id,),
                ).fetchone():
                    raise LearningError("请等待当前任务完成后再更换课程。")
            db.execute(
                "INSERT INTO curricula VALUES (?,?) ON CONFLICT(painter_id) "
                "DO UPDATE SET spec=excluded.spec",
                (painter_id, spec.model_dump_json()),
            )
        return self.progress(painter_id)

    def rounds(self, painter_id):
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM learning_rounds WHERE painter_id=? ORDER BY created_at DESC",
                (painter_id,),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def progress(self, painter_id):
        spec = self.spec(painter_id)
        goal = self.store.painter(painter_id).growth_goal
        rounds = self.rounds(painter_id)
        stages, completed, current = [], [], None
        for stage in spec.stages:
            lessons = []
            for lesson in stage.lessons:
                key = self.goal_key(lesson_key(spec, stage, lesson), goal)
                history = [r for r in rounds if r["lesson_key"] == key]
                passed = any(r["passed"] for r in history)
                if passed:
                    completed.append(lesson.id)
                status = "passed" if passed else "locked"
                if not passed and current is None and set(lesson.depends_on).issubset(completed):
                    status = "current"
                    current = {"stage_id": stage.id, "lesson_id": lesson.id, "name": lesson.name}
                lessons.append(
                    {
                        **lesson.model_dump(),
                        "status": status,
                        "rounds": len(history),
                        "latest": history[0] if history else None,
                    }
                )
            stages.append(
                {"id": stage.id, "name": stage.name, "goals": stage.goals, "lessons": lessons}
            )
        total = sum(len(s.lessons) for s in spec.stages)
        return {
            "spec": spec.model_dump(),
            "growth_goal": goal,
            "stages": stages,
            "current": current,
            "completed": completed,
            "total": total,
            "percent": round(len(completed) / total * 100),
            "history": rounds[:30],
        }

    @staticmethod
    def goal_key(key, goal):
        return hashlib.sha256((key + "\n" + goal).encode()).hexdigest() if goal else key

    def snapshot(self, painter_id, lesson_id=None, options: PracticeOptions | None = None):
        options = options or PracticeOptions()
        painter = self.store.painter(painter_id)
        progress = self.progress(painter_id)
        target = lesson_id or (progress["current"] or {}).get("lesson_id")
        if not target:
            raise LearningError("课程已完成，可以选择已完成的关卡复习。")
        spec = self.spec(painter_id)
        for stage in spec.stages:
            for lesson in stage.lessons:
                if lesson.id != target:
                    continue
                if target not in progress["completed"] and target != (
                    progress["current"] or {}
                ).get("lesson_id"):
                    raise LearningError("请先完成前面的关卡。")
                return {
                    "spec_id": spec.id,
                    "stage_id": stage.id,
                    "lesson_id": lesson.id,
                    "lesson_key": self.goal_key(
                        lesson_key(spec, stage, lesson), painter.growth_goal
                    ),
                    "name": lesson.name,
                    "skills": lesson.skills,
                    "pass_score": lesson.pass_score,
                    "painter_snapshot": painter.model_dump(),
                    "art_brief": {
                        "growth_goal": painter.growth_goal,
                        "stage_goals": stage.goals,
                        **(
                            {"style_profile": painter.style_profile.model_dump()}
                            if painter.style_profile
                            else {}
                        ),
                        "skills": lesson.skills,
                        "practice": options.model_dump(),
                    },
                    "assessment": options.assessment,
                    "tasks": generate_tasks(
                        lesson, options.count, goal=painter.growth_goal, practice=options
                    ),
                }
        raise LearningError("课程中没有这节课。")

    def save_round(self, record):
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO learning_rounds VALUES (?,?,?,?,?) ON CONFLICT(id) "
                "DO UPDATE SET payload=excluded.payload",
                (
                    record["id"],
                    record["painter_id"],
                    record["lesson_key"],
                    json.dumps(record, ensure_ascii=False),
                    record["created_at"],
                ),
            )

    def run(self, agent, painter_id, snapshot=None, *, run_id=None, on_started=None):
        snapshot = snapshot or self.snapshot(painter_id)
        record = {
            **snapshot,
            "id": run_id or uuid4().hex,
            "painter_id": painter_id,
            "created_at": now(),
            "status": "running",
            "passed": False,
            "average": None,
            "simulated": False,
            "results": [],
        }
        self.save_round(record)
        try:
            for index, topic in enumerate(snapshot["tasks"]):
                context = {
                    key: snapshot[key] for key in ("stage_id", "lesson_id", "skills", "lesson_key")
                }
                context.update(
                    round_id=record["id"], exercise=index + 1, lesson_name=snapshot["name"]
                )
                entry = agent.create(
                    topic,
                    painter_id,
                    learning=context,
                    on_started=on_started,
                    art_brief=snapshot.get("art_brief"),
                    painter_snapshot=snapshot.get("painter_snapshot"),
                )
                record["results"].append(
                    {
                        "entry_id": entry["id"],
                        "score": entry["critique"]["overall"],
                        "simulated": entry["simulated"],
                    }
                )
                record["simulated"] |= entry["simulated"]
                self.save_round(record)
            record["average"] = round(
                sum(r["score"] for r in record["results"]) / len(snapshot["tasks"]), 3
            )
            record["passed"] = (
                snapshot.get("assessment", True)
                and not record["simulated"]
                and record["average"] >= snapshot["pass_score"]
            )
            record["status"] = "complete"
            self.save_round(record)
            return entry
        except Exception as exc:
            record["status"] = "failed"
            if getattr(exc, "entry", None):
                record["failed_entry_id"] = exc.entry["id"]
            self.save_round(record)
            raise

    def recover(self):
        with self.store.connect() as db:
            rows = db.execute("SELECT payload FROM learning_rounds").fetchall()
        for row in rows:
            record = json.loads(row["payload"])
            if record["status"] == "running":
                record["status"] = "interrupted"
                self.save_round(record)
