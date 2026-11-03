import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import Painter


def now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    """SQLite owns metadata; images use root-relative paths for future archives."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "paintai.sqlite3"
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported database schema version")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS painters (
                    painter_id TEXT PRIMARY KEY, profile TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS creations (
                    id TEXT PRIMARY KEY, painter_id TEXT NOT NULL REFERENCES painters(painter_id),
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS creations_painter ON creations(painter_id, created_at);
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def add_painter(self, painter: Painter) -> Painter:
        try:
            with self.connect() as connection:
                connection.execute(
                    "INSERT INTO painters VALUES (?, ?, ?)",
                    (painter.painter_id, painter.model_dump_json(), now()),
                )
        except sqlite3.IntegrityError:
            raise ValueError(f"Painter already exists: {painter.painter_id}") from None
        return painter

    def painter(self, painter_id: str) -> Painter:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT profile FROM painters WHERE painter_id=?", (painter_id,)
            ).fetchone()
        if row is None:
            raise ValueError(f"Painter not found: {painter_id}; create it with painter add")
        return Painter.model_validate_json(row["profile"])

    def painters(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT profile FROM painters ORDER BY created_at, painter_id"
            ).fetchall()
        return [Painter.model_validate_json(row['profile']).model_dump() for row in rows]

    def update_goal(self, painter_id: str, goal: str) -> Painter:
        from .learning import LearningError
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT profile FROM painters WHERE painter_id=?', (painter_id,)).fetchone()
            if not row:
                raise ValueError('Painter not found')
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='jobs'").fetchone():
                if db.execute("SELECT 1 FROM jobs WHERE painter_id=? AND status IN ('queued','running')",
                              (painter_id,)).fetchone():
                    raise LearningError('请等待当前任务完成后修改长期目标。')
            value = json.loads(row['profile'])
            value['growth_goal'] = goal
            painter = Painter.model_validate(value)
            db.execute('UPDATE painters SET profile=? WHERE painter_id=?', (painter.model_dump_json(), painter_id))
        return painter

    def begin(self, painter_id: str, topic: str, config: dict) -> dict:
        self.painter(painter_id)
        stamp = now()
        entry = {
            "id": uuid4().hex,
            "painter_id": painter_id,
            "topic": topic,
            "schema_version": 1,
            "rubric_version": "m0-six-equal-v1",
            "created_at": stamp,
            "updated_at": stamp,
            "status": "planning",
            "config": config,
            "simulated": any(c["provider"] == "mock" for c in config.values()),
            "simulation": {role: c["provider"] == "mock" for role, c in config.items()},
            "plan": None,
            "image_path": None,
            "critique": None,
            "error": None,
            "evaluation_attempts": [],
        }
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO creations VALUES (?, ?, ?, ?, ?, ?)",
                (
                    entry["id"],
                    painter_id,
                    stamp,
                    stamp,
                    entry["status"],
                    json.dumps(entry, ensure_ascii=False),
                ),
            )
        return entry

    def save(self, entry: dict):
        entry["updated_at"] = now()
        with self.connect() as connection:
            result = connection.execute(
                "UPDATE creations SET updated_at=?, status=?, payload=? WHERE id=? AND painter_id=?",
                (
                    entry["updated_at"],
                    entry["status"],
                    json.dumps(entry, ensure_ascii=False),
                    entry["id"],
                    entry["painter_id"],
                ),
            )
            if result.rowcount != 1:
                raise ValueError("Creation not found")

    def get(self, painter_id: str, entry_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM creations WHERE painter_id=? AND id=?", (painter_id, entry_id)
            ).fetchone()
        if row is None:
            raise ValueError("Creation not found for this painter")
        return json.loads(row["payload"])

    def gallery(self, painter_id: str, limit: int = 20) -> list[dict]:
        self.painter(painter_id)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM creations WHERE painter_id=? ORDER BY created_at DESC LIMIT ?",
                (painter_id, limit),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def image_path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Image path escapes data directory")
        return path

    def save_image(self, entry: dict, image: bytes):
        relative = f"painters/{entry['painter_id']}/gallery/{entry['id']}.png"
        target = self.image_path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(image)
        temporary.replace(target)
        entry["image_path"] = relative
        entry["status"] = "generated"
        self.save(entry)
