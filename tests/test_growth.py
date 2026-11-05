import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from paintai.agent import PainterAgent
from paintai.api import create_app
from paintai.config import LocalImageConfig, ProviderConfig, Settings
from paintai.evolution import Evolution
from paintai.experiments import ExperimentRequest, Experiments, FrozenMemory, summarize
from paintai.jobs import Jobs, Worker
from paintai.memory import Memory
from paintai.models import Painter
from paintai.providers import MockProvider
from paintai.storage import Store


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path)
    store.add_painter(Painter(painter_id="artist", name="Artist"))
    store.add_painter(Painter(painter_id="other", name="Other"))
    settings = Settings(data_dir=tmp_path)
    mock = MockProvider()
    agent = PainterAgent(store, settings, mock, mock, mock)
    return store, settings, agent


def evidence(store, agent, score=8, topic="A quiet mountain lake", *, simulated=False):
    """Synthetic evidence only inside isolated tests; no fixture scores in runtime."""
    entry = agent.create(topic, "artist")
    entry["critique"]["scores"] = {k: score for k in entry["critique"]["scores"]}
    entry["critique"]["overall"] = score
    entry["critique"]["improvements"] = ["Use a clear focal point"]
    entry["simulated"] = simulated
    store.save(entry)
    Memory(store).record(entry)
    return entry


def test_distillation_pair_provenance_retrieval_and_regrade(setup):
    store, _, agent = setup
    low = evidence(store, agent, 5)
    high = evidence(store, agent, 8)
    evolution = Evolution(store)
    rules = evolution.list("artist")
    assert len(rules) == 1
    rule = rules[0]
    assert [e["entry_id"] for e in rule["evidence"]] == [high["id"], low["id"]]
    assert rule["score_gap"] == 3 and rule["dimension_delta"]["color"] == 3
    assert rule["distilled_lesson"]["prompt_candidate"] == high["plan"]["prompt"]
    assert rule["rules"][0].startswith("Candidate to test:")
    assert Memory(store).search("artist", high["topic"], [], k=1)[0]["id"] == rule["id"]
    assert evolution.list("other") == []
    assert evolution.distill("artist")["items"][0]["id"] == rule["id"]
    low["critique"] = deepcopy(high["critique"])
    store.save(low)
    Memory(store).record(low)
    assert evolution.list("artist") == []  # A stale high/low contrast must disappear.


def test_distillation_excludes_noncomparable_and_simulated(setup):
    store, _, agent = setup
    low = evidence(store, agent, 4)
    high = evidence(store, agent, 9, topic="different topic")
    assert Evolution(store).list("artist") == []
    high["topic"] = low["topic"]
    high["art_brief"] = {"growth_goal": "different direction"}
    store.save(high)
    assert not Evolution(store).distill("artist")["items"]
    high["art_brief"] = low["art_brief"]
    high["simulated"] = True
    store.save(high)
    assert not Evolution(store).distill("artist")["items"]
    high["simulated"] = False
    high["experiment"] = {"id": "test"}
    store.save(high)
    assert not Evolution(store).distill("artist")["items"]
    high["experiment"] = None
    high["evaluation_attempts"][-1]["config"]["model"] = "another-critic"
    store.save(high)
    assert not Evolution(store).distill("artist")["items"]


def test_frozen_experiment_isolation_order_seeds_and_original_evidence(setup):
    store, settings, agent = setup
    note = evidence(store, agent)
    experiments = Experiments(store)
    snapshot = experiments.snapshot(
        "artist", ExperimentRequest(topic=note["topic"], pairs=3), settings
    )
    snapshot.update(seed_controlled=True, base_seed=42)
    original_notes = deepcopy(snapshot["memory_snapshot"])
    # New real memory after the snapshot must not leak into either arm.
    evidence(store, agent, 9)
    memory_before = Memory(store).list("artist")
    calls = []

    class Planner(MockProvider):
        def plan(self, topic, painter, *, context=None, **kwargs):
            calls.append(context)
            return super().plan(topic, painter)

    observed = []

    def factory(index, arm, seed, notes):
        observed.append((index, arm, seed))
        config = settings.model_copy(deep=True)
        config.memory_top_k = 3 if arm == "learned" else 0
        mock = MockProvider()
        return PainterAgent(store, config, Planner(), mock, mock, retriever=FrozenMemory(notes))

    experiments.run("artist", snapshot, run_id="test-run", factory=factory)
    record = experiments.list("artist")[0]
    assert observed == [
        (0, "baseline", 42),
        (0, "learned", 42),
        (1, "learned", 43),
        (1, "baseline", 43),
        (2, "baseline", 44),
        (2, "learned", 44),
    ]
    assert calls[0] is None and calls[3] is None and calls[4] is None
    assert all(c[0]["entry_id"] == original_notes[0]["entry_id"] for c in calls if c)
    assert record["memory_snapshot"] == original_notes
    assert record["summary"]["paired"] == 3 and record["summary"]["delta"] == 0
    assert record["summary"]["conclusion"] == "模拟结果，不用于判断成长"
    assert Memory(store).list("artist") == memory_before
    Memory(store).sync("artist")
    assert len(Memory(store).list("artist")) == len(memory_before)
    agent.evaluate("artist", record["results"][0]["entry_id"], force=True)
    assert experiments.list("artist")[0] == record
    assert len(Memory(store).list("artist")) == len(memory_before)


def test_partial_results_recovery(setup):
    store, settings, agent = setup
    note = evidence(store, agent)
    experiments = Experiments(store)
    snapshot = experiments.snapshot(
        "artist", ExperimentRequest(topic=note["topic"], pairs=3), settings
    )

    def factory(index, arm, seed, notes):
        if index == 1:
            raise RuntimeError("fixture failure")
        return agent

    with pytest.raises(RuntimeError):
        experiments.run("artist", snapshot, run_id="partial", factory=factory)
    result = experiments.list("artist")[0]
    assert result["status"] == "failed" and len(result["results"]) == 2
    result["status"] = "running"
    experiments.save(result)
    experiments.recover()
    assert experiments.list("artist")[0]["status"] == "interrupted"
    assert len(experiments.list("artist")[0]["results"]) == 2


def test_style_ablation_has_identical_critic_target_and_no_memory(setup):
    from paintai.models import StyleProfile, StyleTraits

    store, settings, agent = setup
    profile = StyleProfile(
        id="style",
        name="Style",
        created_at="test",
        reference_ids=[],
        reference_hashes=[],
        traits=StyleTraits(
            palette="warm",
            lines="fine",
            lighting="soft",
            composition="center",
            texture="smooth",
            guidance=["soft light"],
        ),
    )
    experiments = Experiments(store)
    painter = store.painter("artist").model_copy(update={"style_profile": profile})
    # The persisted painter need not be changed: run from a frozen reviewed profile.
    snapshot = {
        "topic": "portrait",
        "pairs": 1,
        "mode": "style_profile",
        "painter_snapshot": painter.model_dump(),
        "art_brief": {"style_profile": profile.model_dump(), "growth_goal": "artist-name priming"},
        "memory_snapshot": [],
        "seed_controlled": True,
        "base_seed": 42,
        "created_at": "test",
    }
    seen = []

    class Recorder(MockProvider):
        def plan(self, topic, painter, **kwargs):
            seen.append(("plan", painter.style_profile, kwargs))
            return super().plan(topic, painter, **kwargs)

        def evaluate(self, image, topic, painter, plan, **kwargs):
            seen.append(("critic", kwargs["requirements"]))
            return super().evaluate(image, topic, painter, plan, **kwargs)

    def factory(*args):
        mock = Recorder()
        return PainterAgent(store, settings.model_copy(deep=True), mock, mock, mock)

    experiments.run("artist", snapshot, run_id="style-test", factory=factory)
    assert seen[0][1] is None and "style_profile" not in seen[0][2].get("requirements", {})
    assert seen[2][1] == profile and seen[2][2]["requirements"]["style_profile"]["id"] == "style"
    assert all("growth_goal" not in call[2].get("requirements", {}) for call in (seen[0], seen[2]))
    assert seen[1] == seen[3]
    assert all("context" not in call[2] for call in (seen[0], seen[2]))
    assert Memory(store).list("artist") == []
    assert store.painter("artist").style_profile is None


def test_identical_style_prompts_are_not_learning_evidence():
    record = {
        "mode": "style_profile",
        "pairs": 1,
        "status": "complete",
        "simulated": False,
        "effective_memory": True,
        "results": [],
    }
    for arm in ("baseline", "learned"):
        record["results"].append(
            {
                "pair": 0,
                "arm": arm,
                "prompt_digest": "identical",
                "critique": {"overall": 6, "scores": {"topic": 6}},
            }
        )
    assert summarize(record)["conclusion"] == "风格档案未改变生图提示词，无法比较学习效果"


@pytest.mark.parametrize(
    ("diffs", "expected"),
    [
        ([1], "样本不足"),
        ([0, 0, 0], "尚未观察"),
        ([0.8, 1, 1.2], "提升迹象"),
        ([-0.8, -1, -1.2], "退步迹象"),
        ([-1, 0, 1], "尚未观察"),
    ],
)
def test_summary_does_not_claim_progress_without_evidence(diffs, expected):
    record = {
        "pairs": len(diffs),
        "status": "complete",
        "simulated": False,
        "effective_memory": True,
        "results": [],
    }
    for i, diff in enumerate(diffs):
        for arm, score in [("baseline", 5), ("learned", 5 + diff)]:
            record["results"].append(
                {"pair": i, "arm": arm, "critique": {"overall": score, "scores": {"color": score}}}
            )
    assert expected in summarize(record)["conclusion"]
    record["status"] = "failed"
    assert "暂不下结论" in summarize(record)["conclusion"]


def test_experiment_api_queue_idempotency_and_worker(setup):
    store, settings, agent = setup
    app = create_app(settings, start_worker=False)
    with TestClient(app) as client:
        root = "/api/v1/painters/artist/experiments"
        assert client.post(root + "/preview", json={"topic": "lake"}).status_code == 409
        note = evidence(store, agent)
        body = {"topic": note["topic"], "pairs": 1}
        assert client.post(root + "/preview", json=body).status_code == 200
        assert client.get("/api/v1/jobs").json() == []
        assert client.post(root + "/preview", json={**body, "pairs": 11}).status_code == 422
        headers = {"Idempotency-Key": "experiment-key"}
        response = client.post(root, json=body, headers=headers)
        assert response.status_code == 202 and response.json()["training"]["total"] == 2
        job = response.json()["id"]
        assert client.post(root, json={**body, "pairs": 3}, headers=headers).status_code == 409
        assert (
            client.post("/api/v1/painters/artist/goal", json={"growth_goal": "new"}).status_code
            == 409
        )
        assert Worker(Jobs(store, settings)).run_one()
        assert client.get("/api/v1/jobs/" + job).json()["status"] == "succeeded"
        assert client.post(root, json=body, headers=headers).json()["id"] == job
        result = client.get(root).json()["items"][0]
        assert result["summary"]["paired"] == 1
        assert client.get("/api/v1/painters/other/experiments").json()["items"] == []
        encoded = json.dumps(result)
        assert (
            "api_key_env" not in encoded
            and "base_url" not in encoded
            and str(store.root) not in encoded
        )


def test_worker_uses_matching_actual_generator_seeds_and_blind_critic(setup, monkeypatch, tmp_path):
    store, settings, agent = setup
    note = evidence(store, agent)
    settings.generator = ProviderConfig(
        provider="sd_cpp",
        model="test-image",
        local_image=LocalImageConfig(
            executable=tmp_path / "fake.exe", model_path=tmp_path / "weights.bin", seed=51
        ),
    )
    generator_seeds, critiques = [], []

    class BlindCritic(MockProvider):
        def evaluate(self, image, topic, painter, plan, *, requirements=None):
            critiques.append(requirements)
            return super().evaluate(image, topic, painter, plan)

    def provider(config, client):
        if config.provider == "sd_cpp":
            generator_seeds.append(config.local_image.seed)
        return BlindCritic()

    monkeypatch.setattr("paintai.jobs.make_provider", provider)
    jobs = Jobs(store, settings)
    job = jobs.submit(
        "artist",
        "experiment",
        note["topic"],
        "seeds-test",
        experiment=ExperimentRequest(topic=note["topic"], pairs=3),
    )
    snapshot = json.loads(job["payload"])
    assert snapshot["conditions"]["generator"]["local_image"]["seed"] == 51
    assert "model_path" not in json.dumps(snapshot)
    assert Worker(jobs).run_one()
    assert jobs.get(job["id"])["status"] == "succeeded"
    assert generator_seeds == [
        51,
        51,
        51,
        52,
        52,
        53,
        53,
    ]  # initial unused worker agent plus six arms
    assert (
        critiques == [None] * 6
    )  # No arm, prior grades, memory or candidate evidence reaches the critic.
    used = [r["memory_used"] for r in jobs.experiments.list("artist")[0]["results"]]
    assert [bool(x) for x in used] == [False, True, True, False, False, True]
