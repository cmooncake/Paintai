"""Conservative, extractive best/worst distillation; no model-weight training."""

import hashlib
import json

from .storage import now


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class Evolution:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS distilled_lessons (
                painter_id TEXT NOT NULL, group_key TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(painter_id,group_key))""")

    def list(self, painter_id):
        self.store.painter(painter_id)
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM distilled_lessons WHERE painter_id=?", (painter_id,)
            ).fetchall()
        return sorted(
            (json.loads(r["payload"]) for r in rows), key=lambda r: r["updated_at"], reverse=True
        )

    def distill(self, painter_id):
        self.store.painter(painter_id)
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM creations WHERE painter_id=? AND status='complete' "
                "ORDER BY created_at DESC LIMIT 2000",
                (painter_id,),
            ).fetchall()
        groups = {}
        for row in rows:
            entry = json.loads(row["payload"])
            if entry.get("experiment") or entry["simulated"] or not entry.get("critique"):
                continue
            config = dict(entry["config"])
            # A re-evaluation must be compared using its actual critic configuration.
            attempts = [a for a in entry["evaluation_attempts"] if a["status"] == "complete"]
            if attempts:
                config["critic"] = attempts[-1]["config"]
            key = fingerprint(
                [
                    entry["topic"],
                    entry.get("painter_snapshot", {}).get("style"),
                    entry.get("art_brief", {}),
                    config,
                    entry["rubric_version"],
                    entry.get("pipeline_version", "legacy"),
                ]
            )
            groups.setdefault(key, []).append(entry)
        distilled = []
        for key, entries in groups.items():
            if len(entries) < 2:
                continue
            entries.sort(key=lambda e: (e["critique"]["overall"], e["id"]))
            worst, best = entries[0], entries[-1]
            gap = round(best["critique"]["overall"] - worst["critique"]["overall"], 3)
            if gap < 0.25:
                continue  # Ties and tiny score fluctuations do not yield a rule.
            delta = {
                k: round(best["critique"]["scores"][k] - v, 3)
                for k, v in worst["critique"]["scores"].items()
            }
            evidence = [
                {
                    "entry_id": e["id"],
                    "score": e["critique"]["overall"],
                    "scores": e["critique"]["scores"],
                    "feedback": e["critique"]["feedback"],
                    "plan": e["plan"],
                    "evaluation_count": len(e["evaluation_attempts"]),
                }
                for e in (best, worst)
            ]
            rules = [
                f"Candidate to test: {best['plan']['composition'][:130]}",
                *list(dict.fromkeys(worst["critique"]["improvements"]))[:1],
            ]
            if not rules:
                continue
            candidate = {
                "id": "distilled_" + fingerprint(evidence)[:24],
                "entry_id": best["id"],
                "painter_id": painter_id,
                "topic": best["topic"],
                "art_brief": best.get("art_brief", {}),
                "skills": (best.get("learning") or {}).get("skills", []),
                "source": "best_worst_extract_v1",
                "simulated": False,
                "rules": rules,
                "distilled_lesson": {
                    "what_worked": best["critique"]["feedback"],
                    "what_failed": worst["critique"]["feedback"],
                    "rule": rules,
                    "prompt_candidate": best["plan"]["prompt"],
                },
                "evidence": evidence,
                "score_gap": gap,
                "dimension_delta": delta,
                "sample_count": len(entries),
                "updated_at": now(),
                "status": "candidate",
                "conditions_digest": key,
                "pipeline_version": best.get("pipeline_version", "legacy"),
            }
            distilled.append((key, candidate))
        # Replace current candidates so changed/re-evaluated evidence cannot leave stale rules active.
        with self.store.connect() as db:
            db.execute("DELETE FROM distilled_lessons WHERE painter_id=?", (painter_id,))
            db.executemany(
                "INSERT INTO distilled_lessons VALUES (?,?,?)",
                [
                    (painter_id, key, json.dumps(value, ensure_ascii=False))
                    for key, value in distilled
                ],
            )
        return {
            "items": [value for _, value in distilled],
            "scanned": len(rows),
            "method": "best_worst_extract_v1",
        }
