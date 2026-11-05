"""Traceable critique-derived notes with a replaceable local retrieval baseline."""

import json
import re
from typing import Protocol

from .evolution import Evolution
from .storage import Store, now


class Retriever(Protocol):
    def search(self, painter_id: str, topic: str, skills: list[str], k: int = 3) -> list[dict]: ...


def tokens(text):
    words = set(re.findall(r"[a-z]{3,}", text.lower()))
    for phrase in re.findall(r"[\u4e00-\u9fff]+", text):
        words.update(phrase[i : i + 2] for i in range(len(phrase) - 1))
    return words


class Memory:
    def __init__(self, store: Store):
        self.store = store
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS experiences (
                painter_id TEXT NOT NULL, entry_id TEXT NOT NULL, payload TEXT NOT NULL,
                updated_at TEXT NOT NULL, PRIMARY KEY(painter_id,entry_id))""")

    def record(self, entry, *, distill=True):
        if entry.get("experiment"):
            return
        if entry["status"] != "complete" or not entry.get("critique"):
            return
        critique = entry["critique"]
        scores = critique["scores"]
        learning = entry.get("learning") or {}
        note = {
            "id": entry["id"],
            "entry_id": entry["id"],
            "painter_id": entry["painter_id"],
            "topic": entry["topic"],
            "style": (entry.get("painter_snapshot") or {}).get("style")
            or self.store.painter(entry["painter_id"]).style,
            "art_brief": entry.get("art_brief", {}),
            "skills": learning.get("skills", []),
            "stage_id": learning.get("stage_id"),
            "lesson_id": learning.get("lesson_id"),
            "score": critique["overall"],
            "type": "success" if critique["overall"] >= 7 else "insight",
            "feedback": critique["feedback"],
            "rules": critique["improvements"][:3],
            "strongest": max(scores, key=scores.get),
            "weakest": min(scores, key=scores.get),
            "simulated": entry["simulated"],
            "updated_at": now(),
            "source": "critic_feedback",
            "evaluation_count": len(entry["evaluation_attempts"]),
        }
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO experiences VALUES (?,?,?,?) ON CONFLICT(painter_id,entry_id) "
                "DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                (
                    entry["painter_id"],
                    entry["id"],
                    json.dumps(note, ensure_ascii=False),
                    note["updated_at"],
                ),
            )
        if distill:
            Evolution(self.store).distill(entry["painter_id"])

    def list(self, painter_id, limit=100):
        self.store.painter(painter_id)
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM experiences WHERE painter_id=? "
                "ORDER BY updated_at DESC LIMIT ?",
                (painter_id, limit),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def search(self, painter_id, topic, skills, k=3):
        query = tokens(topic)
        ranked = []
        for note in [*Evolution(self.store).list(painter_id), *self.list(painter_id, 1000)]:
            if note["simulated"]:
                continue
            text = tokens(
                note["topic"] + " " + json.dumps(note.get("art_brief", {}), ensure_ascii=False)
            )
            relevance = len(query & text) / max(len(query | text), 1)
            relevance += 2 * len(set(skills) & set(note["skills"]))
            if relevance > 0:
                ranked.append(
                    (relevance + (0.1 if note["source"] == "best_worst_extract_v1" else 0), note)
                )
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [note for _, note in ranked[:k]]

    def sync(self, painter_id):
        # Explicit backfill, including M0 work; bounded memory retrieval is separate.
        self.store.painter(painter_id)
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM creations WHERE painter_id=? AND status='complete'",
                (painter_id,),
            ).fetchall()
        for row in rows:
            self.record(json.loads(row["payload"]), distill=False)
        Evolution(self.store).distill(painter_id)
        return len(rows)
