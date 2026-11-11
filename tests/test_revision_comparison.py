import hashlib
from copy import deepcopy

import pytest

from paintai.api import public_creation
from paintai.revision_comparison import publish_edit_pair, publish_revision_pair


class PairStore:
    def __init__(self, tmp_path):
        self.image = tmp_path / "image.png"
        self.image.write_bytes(b"fixture")
        entry = {
            "painter_id": "p",
            "topic": "same",
            "status": "complete",
            "critique": {"overall": 4},
            "config": {"generator": {"model": "test", "local_image": {"seed": 42}}},
            "rubric_version": "v4",
            "image_path": "fixture",
            "plan": {},
            "evaluation_attempts": [],
        }
        self.entries = {key: {**deepcopy(entry), "id": key} for key in ("before", "after")}

    def get(self, painter_id, key):
        if painter_id != "p":
            raise ValueError("owner mismatch")
        return deepcopy(self.entries[key])

    def image_path(self, path):
        return self.image

    def save(self, entry):
        self.entries[entry["id"]] = deepcopy(entry)


def test_edit_pair_requires_source_provenance_without_claiming_seed_match(tmp_path):
    store = PairStore(tmp_path)
    with pytest.raises(ValueError, match="provenance"):
        publish_edit_pair(store, "p", "before", "after", conclusion="edited", checks=[])
    store.entries["after"]["image_edit"] = {
        "source_id": "before",
        "source_sha256": hashlib.sha256(store.image.read_bytes()).hexdigest(),
    }
    store.entries["after"]["config"]["generator"] = {"model": "imagegen"}
    pair = publish_edit_pair(store, "p", "before", "after", conclusion="edited", checks=[])
    assert pair["kind"] == "image_edit"
    assert pair["seed"] is None
    assert public_creation(store.entries["after"])["revision_comparison"] == pair
    store.entries["after"]["topic"] = "refined costume request"
    with pytest.raises(ValueError, match="source topic"):
        publish_edit_pair(store, "p", "before", "after", conclusion="edited", checks=[])
    store.entries["after"]["image_edit"]["source_topic"] = store.entries["before"]["topic"]
    refined = publish_edit_pair(store, "p", "before", "after", conclusion="edited", checks=[])
    assert "不作分数增益比较" in refined["note"]
    store.image.write_bytes(b"changed source")
    with pytest.raises(ValueError, match="provenance"):
        publish_edit_pair(store, "p", "before", "after", conclusion="edited", checks=[])


def test_matched_pair_publication_and_mismatch_rejection(tmp_path):
    store = PairStore(tmp_path)
    pair = publish_revision_pair(store, "p", "before", "after", conclusion="尚待验证")
    assert public_creation(store.entries["after"])["revision_comparison"] == pair
    assert store.entries["before"]["revision_comparison"] == pair
    store.entries["after"]["config"]["generator"]["local_image"]["seed"] = 43
    with pytest.raises(ValueError, match="must match"):
        publish_revision_pair(store, "p", "before", "after", conclusion="invalid")
    store.entries["after"]["config"]["generator"]["local_image"]["seed"] = 42
    store.entries["after"]["rubric_version"] = "v3"
    with pytest.raises(ValueError, match="rubric_version"):
        publish_revision_pair(store, "p", "before", "after", conclusion="invalid")


def test_successive_revisions_keep_previous_pair(tmp_path):
    store = PairStore(tmp_path)
    original = publish_revision_pair(store, "p", "before", "after", conclusion="first")
    store.entries["next"] = {**deepcopy(store.entries["after"]), "id": "next"}
    store.entries["next"].pop("revision_comparison")
    store.entries["next"].pop("revision_comparisons")
    checks = [{"name": "服装", "before": "偏紫", "after": "待复核"}]
    newer = publish_revision_pair(store, "p", "after", "next", conclusion="second", checks=checks)
    assert store.entries["before"]["revision_comparison"] == original
    assert store.entries["after"]["revision_comparisons"] == [original, newer]
    assert public_creation(store.entries["next"])["revision_comparisons"][0]["checks"] == checks
    publish_revision_pair(store, "p", "after", "next", conclusion="updated")
    assert len(store.entries["after"]["revision_comparisons"]) == 2
