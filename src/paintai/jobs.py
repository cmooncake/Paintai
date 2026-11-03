"""Durable local job queue. One worker owns model execution; HTTP never waits on inference."""
import json
import os
import threading
from uuid import uuid4

import httpx

from .agent import CreationFailed, PainterAgent
from .config import Settings
from .learning import Learning
from .models import PracticeOptions
from .providers import make_provider
from .storage import Store, now


class QueueConflict(ValueError):
    pass


class Jobs:
    def __init__(self, store: Store, settings: Settings):
        self.store, self.settings = store, settings
        self.learning = Learning(store)
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, painter_id TEXT NOT NULL,
                    kind TEXT NOT NULL, topic TEXT NOT NULL,
                    entry_id TEXT, status TEXT NOT NULL, error TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    request_key TEXT UNIQUE NOT NULL, settings TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_job_per_painter
                    ON jobs(painter_id) WHERE status IN ('queued', 'running');
            """)
            if 'payload' not in {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}:
                db.execute("ALTER TABLE jobs ADD COLUMN payload TEXT NOT NULL DEFAULT '{}'")

    def submit(self, painter_id, kind, topic, key, entry_id=None, *, options=None):
        options = options or PracticeOptions()
        self.store.painter(painter_id)
        if entry_id:
            entry = self.store.get(painter_id, entry_id)
            if not entry["image_path"] or not entry["plan"]:
                raise ValueError("这件作品还没有可评价的图片，请重新创作。")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM jobs WHERE request_key=?", (key,)).fetchone()
            if previous:
                if kind == 'train' and json.loads(previous['payload']).get('_request', PracticeOptions().model_dump()) != options.model_dump():
                    raise QueueConflict('请求标识已用于其他练习要求，请重新提交。')
                if (previous["painter_id"], previous["kind"], previous["topic"], previous["entry_id"]) != (
                    painter_id, kind, topic, entry_id
                ) and not (kind in ('create', 'train') and previous["painter_id"] == painter_id
                           and previous["kind"] == kind and previous["topic"] == topic):
                    raise QueueConflict("请求标识已用于其他操作，请刷新后重试。")
                return dict(previous)
            if db.execute("SELECT 1 FROM jobs WHERE painter_id=? AND status IN ('queued','running')",
                          (painter_id,)).fetchone():
                raise QueueConflict("这位画师正在创作，请等当前任务完成。")
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 8:
                raise QueueConflict("画室当前任务较多，请稍后再试。")
            job_id, stamp = uuid4().hex, now()
            payload = (self.learning.snapshot(painter_id, topic or None, options) if kind == 'train'
                       else {'painter_snapshot': self.store.painter(painter_id).model_dump()})
            if kind == 'train':
                payload['_request'] = options.model_dump()
            db.execute("INSERT INTO jobs (id,painter_id,kind,topic,entry_id,status,error,created_at,"
                       "updated_at,request_key,settings,payload) VALUES (?,?,?,?,?,'queued',NULL,?,?,?,?,?)",
                       (job_id, painter_id, kind, topic, entry_id, stamp, stamp, key,
                        self.settings.model_dump_json(), json.dumps(payload, ensure_ascii=False)))
        return self.get(job_id)

    def get(self, job_id):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise ValueError("任务不存在。")
        return dict(row)

    def list(self, painter_id=None):
        with self.store.connect() as db:
            rows = db.execute("SELECT * FROM jobs WHERE (? IS NULL OR painter_id=?) "
                              "ORDER BY created_at DESC LIMIT 50", (painter_id, painter_id)).fetchall()
        return [dict(row) for row in rows]

    def update(self, job_id, *, status=None, entry_id=None, error=None):
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET status=COALESCE(?,status), entry_id=COALESCE(?,entry_id), "
                       "error=?, updated_at=? WHERE id=?", (status, entry_id, error, now(), job_id))

    def recover(self):
        self.learning.recover()
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET status='interrupted', error=?, updated_at=? WHERE status='running'",
                       ("上次运行意外中断。已保存的作品仍可查看，有图片时可以重新评价。", now()))

    def claim(self):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='running', updated_at=? WHERE id=?", (now(), row["id"]))
        return dict(row)


class Worker:
    def __init__(self, jobs: Jobs):
        self.jobs = jobs
        self.stopping = threading.Event()
        self.thread = None
        self.lock = None

    def start(self):
        # OS lock is released on process exit; another API cannot claim the same data directory.
        self.lock = (self.jobs.store.root / "api-worker.lock").open("a+b")
        if self.lock.seek(0, 2) == 0:
            self.lock.write(b"1")
            self.lock.flush()
        self.lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise RuntimeError("Another Paintai API owns this data directory; use one worker.") from None
        self.jobs.recover()
        self.thread = threading.Thread(target=self._loop, name="paintai-inference", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=5)

    def run_one(self):
        job = self.jobs.claim()
        if not job:
            return False
        try:
            settings = Settings.model_validate_json(job["settings"])
            with httpx.Client() as client:
                agent = PainterAgent(self.jobs.store, settings,
                                     *(make_provider(getattr(settings, role), client)
                                       for role in ("planner", "generator", "critic")))
                if job["kind"] == "create":
                    result = agent.create(job["topic"], job["painter_id"],
                                          painter_snapshot=json.loads(job['payload']).get('painter_snapshot'), on_started=lambda entry:
                                          self.jobs.update(job["id"], entry_id=entry["id"]))
                elif job['kind'] == 'train':
                    result = self.jobs.learning.run(agent, job['painter_id'], json.loads(job['payload']),
                                                   run_id=job['id'], on_started=lambda entry:
                                                   self.jobs.update(job['id'], entry_id=entry['id']))
                else:
                    result = agent.evaluate(job["painter_id"], job["entry_id"], force=True)
            self.jobs.update(job["id"], status="succeeded", entry_id=result["id"])
        except CreationFailed as exc:
            message = ("图片已保存，但评价未完成。请检查评价服务后重新评价。"
                       if exc.entry["status"] == "evaluation_failed" else
                       "创作未完成。请确认模型服务已启动，或尝试更简短的英文主题。")
            self.jobs.update(job["id"], status="failed", entry_id=exc.entry["id"], error=message)
        except Exception:
            self.jobs.update(job["id"], status="failed", error="任务未完成，请检查模型配置或稍后重试。")
        return True

    def _loop(self):
        try:
            while not self.stopping.is_set():
                if not self.run_one():
                    self.stopping.wait(0.5)
        finally:
            self.lock.close()
