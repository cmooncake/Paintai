import hashlib
from io import BytesIO

from PIL import Image

from paintai.agent import PainterAgent
from paintai.config import LocalImageConfig, ProviderConfig, Settings
from paintai.experiments import ExperimentRequest, Experiments, FrozenMemory
from paintai.models import Painter, StyleProfile, StyleTraits
from paintai.providers import MockProvider
from paintai.storage import Store
from paintai.style_learning import StyleLearning


def test_image_reference_pairs_share_prompt_and_isolate_pixels(tmp_path):
    s = Store(tmp_path)
    s.add_painter(Painter(painter_id="p", name="p", growth_goal="keep stored goal"))
    out = BytesIO()
    Image.new("RGB", (32, 32), "pink").save(out, "PNG")
    ref = StyleLearning(s).upload("p", out.getvalue())
    p = s.painter("p")
    p.style_profile = StyleProfile(
        id="profile",
        name="reference",
        reference_ids=[ref["id"]],
        reference_hashes=[ref["sha256"]],
        created_at="now",
        traits=StyleTraits(
            palette="soft",
            lines="fine",
            lighting="soft",
            composition="portrait",
            texture="smooth",
            guidance=["clean contours"],
        ),
    )
    with s.connect() as db:
        db.execute("UPDATE painters SET profile=? WHERE painter_id=?", (p.model_dump_json(), "p"))
    weights = tmp_path / "weights"
    weights.write_bytes(b"weights")
    settings = Settings(
        data_dir=tmp_path,
        generator=ProviderConfig(
            provider="sd_cpp",
            model="local",
            local_image=LocalImageConfig(
                executable=weights,
                model_path=weights,
                ip_adapter_path=weights,
                clip_vision_path=weights,
            ),
        ),
    )
    exp = Experiments(s)
    snap = exp.snapshot(
        "p",
        ExperimentRequest(topic="brown hair portrait", pairs=2, mode="image_reference"),
        settings,
    )
    calls = []
    seen = []

    class Planner(MockProvider):
        def plan(self, *args, **kwargs):
            calls.append(1)
            return super().plan(*args, **kwargs)

    class Generator(MockProvider):
        reference_metadata = None

        def prepare(self, plan):
            return plan, {"protected_attributes": {"hair": "brown hair"}}

        def set_reference(self, path, digest, strength, rid):
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
            self.reference_metadata = {"sha256": digest, "strength": strength, "reference_id": rid}

        def generate(self, plan):
            seen.append((plan.prompt, self.reference_metadata))
            return super().generate(plan)

    def factory(index, arm, seed, notes):
        cfg = settings.model_copy(deep=True)
        cfg.generator.local_image.seed = seed
        return PainterAgent(
            s, cfg, Planner(), Generator(), MockProvider(), retriever=FrozenMemory([])
        )

    exp.run("p", snap, run_id="run", factory=factory)
    r = exp.list("p")[0]
    assert r["status"] == "complete" and len(calls) == 2
    for i in [0, 1]:
        arms = {w["arm"]: s.get("p", w["entry_id"]) for w in r["results"] if w["pair"] == i}
        a, b = arms["learned"], arms["baseline"]
        assert a["plan"] == b["plan"] and a["generation_constraints"] == b["generation_constraints"]
        assert (
            a["reference_conditioning"]["sha256"] == ref["sha256"]
            and b["reference_conditioning"] is None
        )
        assert not a["memory_used"] and not b["memory_used"]
        assert a["painter_snapshot"]["style_profile"] == b["painter_snapshot"]["style_profile"]
    assert s.painter("p").growth_goal == "keep stored goal"
    assert [bool(x[1]) for x in seen] == [False, True, True, False]
