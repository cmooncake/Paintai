import hashlib
from collections.abc import Callable

from .config import Settings
from .memory import Memory, Retriever
from .models import Painter, Plan
from .providers import Critic, Generator, Planner, normalize_image
from .storage import Store, now


class CreationFailed(RuntimeError):
    def __init__(self, entry: dict):
        self.entry = entry
        super().__init__(f"Creation {entry['id']} is {entry['status']}: {entry['error']}")


class PainterAgent:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        planner: Planner,
        generator: Generator,
        critic: Critic,
        retriever: Retriever | None = None,
    ):
        self.store = store
        self.settings = settings
        self.planner = planner
        self.generator = generator
        self.critic = critic
        self.memory = Memory(store)
        self.retriever = retriever or self.memory

    def create(self, topic: str, painter_id: str, *,
               on_started: Callable[[dict], None] | None = None, learning: dict | None = None,
               art_brief: dict | None = None, painter_snapshot: dict | None = None) -> dict:
        topic = topic.strip()
        if not topic:
            raise ValueError("Topic cannot be empty")
        painter = Painter.model_validate(painter_snapshot) if painter_snapshot else self.store.painter(painter_id)
        if painter.painter_id != painter_id:
            raise ValueError('Painter snapshot mismatch')
        art_brief = art_brief if art_brief is not None else (
            {'growth_goal': painter.growth_goal} if painter.growth_goal else {})
        for role in (self.settings.planner, self.settings.generator, self.settings.critic):
            role.credential()  # Fail before incurring generation charges.
        config = {
            role: getattr(self.settings, role).model_dump(mode="json")
            for role in ("planner", "generator", "critic")
        }
        entry = self.store.begin(painter_id, topic, config)
        try:
            entry['learning'] = learning
            entry['art_brief'] = art_brief
            entry['painter_snapshot'] = painter.model_dump()
            query = topic + ' ' + art_brief.get('growth_goal', '') + ' ' + (art_brief.get('practice') or {}).get('focus', '')
            notes = (self.retriever.search(painter_id, query, (learning or {}).get('skills', []),
                                          k=self.settings.memory_top_k)
                     if self.settings.memory_top_k else [])
            entry['memory_used'] = [{'entry_id': note['entry_id'], 'topic': note['topic'][:160],
                                     'rules': [rule[:160] for rule in note['rules'][:2]]}
                                    for note in notes[:self.settings.memory_top_k]]
            entry['memory_policy'] = {'retrieval_limit': self.settings.memory_top_k}
            self.store.save(entry)
            if on_started:
                on_started(entry)
            kwargs = {}
            if entry['memory_used']:
                kwargs['context'] = entry['memory_used']
            if art_brief:
                kwargs['requirements'] = art_brief
            plan = self.planner.plan(topic, painter, **kwargs)
            entry["plan"] = plan.model_dump()
            entry["status"] = "generating"
            self.store.save(entry)
            image = normalize_image(self.generator.generate(plan))
            self.store.save_image(entry, image)
        except Exception as exc:
            self._fail(entry, "creation_failed", exc)
        return self._evaluate(entry)

    def evaluate(self, painter_id: str, entry_id: str, *, force: bool = False) -> dict:
        entry = self.store.get(painter_id, entry_id)
        if entry["status"] == "complete" and not force:
            return entry  # Idempotent: no additional API call on completed work.
        if not entry["image_path"] or not entry["plan"]:
            raise ValueError("No saved image to evaluate; inspect the failed creation first")
        self.settings.critic.credential()
        return self._evaluate(entry)

    def _evaluate(self, entry: dict) -> dict:
        # Preserve older records written before per-attempt results were stored.
        if entry.get("critique"):
            for previous in reversed(entry["evaluation_attempts"]):
                if previous["status"] == "complete":
                    previous.setdefault("critique", entry["critique"])
                    break
        attempt = {
            "started_at": now(),
            "config": self.settings.critic.model_dump(mode="json"),
            "status": "evaluating",
            "rubric_version": entry["rubric_version"],
        }
        entry["evaluation_attempts"].append(attempt)
        entry["status"] = "evaluating"
        entry["error"] = None
        entry["critique"] = None
        self.store.save(entry)
        try:
            image = self.store.image_path(entry["image_path"])
            attempt["image_sha256"] = hashlib.sha256(image.read_bytes()).hexdigest()
            kwargs = {'requirements': entry['art_brief']} if entry.get('art_brief') else {}
            critique = self.critic.evaluate(
                image,
                entry["topic"],
                Painter.model_validate(entry['painter_snapshot']) if entry.get('painter_snapshot')
                else self.store.painter(entry["painter_id"]),
                Plan.model_validate(entry["plan"]),
                **kwargs,
            )
            entry["critique"] = critique.model_dump()
            entry["simulation"] = {
                role: entry["config"][role]["provider"] == "mock"
                for role in ("planner", "generator")
            }
            entry["simulation"]["critic"] = self.settings.critic.provider == "mock"
            entry["simulated"] = (
                any(
                    entry["config"][role]["provider"] == "mock" for role in ("planner", "generator")
                )
                or self.settings.critic.provider == "mock"
            )
            entry["status"] = "complete"
            attempt.update(status="complete", finished_at=now(), critique=entry["critique"])
            self.store.save(entry)
        except Exception as exc:
            attempt.update(status="failed", finished_at=now())
            self._fail(entry, "evaluation_failed", exc)
        # Learning notes are derived from a successful critique, never fabricated on failure.
        self.memory.record(entry)
        return entry

    def _fail(self, entry: dict, status: str, exc: Exception):
        from .providers import ProviderError

        entry["status"] = status
        # Arbitrary exception strings may contain keys, request bodies, or signed URLs.
        entry["error"] = str(exc) if isinstance(exc, ProviderError) else type(exc).__name__
        self.store.save(entry)
        raise CreationFailed(entry) from None
