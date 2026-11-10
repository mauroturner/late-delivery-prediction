"""The artifact of the frozen model (U12, M03, AC21 in part, decision D30): nothing is deserialized from a file whose bytes do not have the registered SHA-256.

The proof that the fingerprint is checked before ``pickle`` sees any byte uses a file that, if unpickled, creates a marker file (a real side effect): it is
refused with the documented error and the marker does not exist. The same bytes with their own fingerprint do create it, so the absence of the marker is not trivial.
"""

from __future__ import annotations

import hashlib
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from latedel import registry
from latedel.registry import ArtifactMismatchError, load_artifact, save_artifact


class Marker:
    """When unpickled, it opens (and so creates) the file at `path`: a side effect that a test can see."""

    def __init__(self, path: Path) -> None:
        self.path = str(path)

    def __reduce__(self):
        return (open, (self.path, "w"))


def evil_bytes(marker: Path) -> bytes:
    return pickle.dumps(Marker(marker), protocol=5)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SpyPickle:
    """Stands for the ``pickle`` module inside the registry and records what reaches ``loads``."""

    def __init__(self) -> None:
        self.loaded: list[bytes] = []

    def dumps(self, obj, protocol=None):
        return pickle.dumps(obj, protocol=protocol)

    def loads(self, data):
        self.loaded.append(bytes(data))
        return pickle.loads(data)


def test_artifact_with_other_hash_is_refused_before_loading(tmp_path, monkeypatch):
    marker = tmp_path / "side_effect.marker"
    artifact = tmp_path / "v1.pkl"
    artifact.write_bytes(evil_bytes(marker))
    spy = SpyPickle()
    monkeypatch.setattr(registry, "pickle", spy)
    registered = sha(b"the bytes that were registered, not these")
    with pytest.raises(ArtifactMismatchError, match=r"M03.*R6"):
        load_artifact(artifact, registered)
    assert not marker.exists(), "nothing was unpickled: the side effect did not happen"
    assert spy.loaded == [], "no byte reached pickle"
    assert artifact.read_bytes() == evil_bytes(marker), "the refused file is left as it was"


def test_the_same_bytes_with_their_own_fingerprint_do_load_and_run_the_side_effect(tmp_path):
    marker = tmp_path / "side_effect.marker"
    artifact = tmp_path / "v1.pkl"
    artifact.write_bytes(evil_bytes(marker))
    handle = load_artifact(artifact, sha(artifact.read_bytes()))
    handle.close()
    assert marker.exists(), "control: this file does have a side effect when it is unpickled"


def test_the_error_names_the_file_and_both_fingerprints_but_never_a_path(tmp_path):
    artifact = tmp_path / "folder" / "v1.pkl"
    artifact.parent.mkdir()
    artifact.write_bytes(b"\x80\x05N.")
    registered = sha(b"other")
    with pytest.raises(ArtifactMismatchError) as caught:
        load_artifact(artifact, registered)
    text = str(caught.value)
    assert "v1.pkl" in text and sha(b"\x80\x05N.") in text and registered in text
    assert str(tmp_path) not in text


@pytest.mark.parametrize("damage", ["flipped_byte", "truncated", "appended", "empty", "replaced"])
def test_a_file_that_changed_after_it_was_registered_is_refused_without_side_effects(tmp_path, monkeypatch, damage):
    marker = tmp_path / "side_effect.marker"
    artifact = tmp_path / "v1.pkl"
    original = pickle.dumps({"model": [1, 2, 3]}, protocol=5)
    artifact.write_bytes(original)
    registered = sha(original)
    spy = SpyPickle()
    monkeypatch.setattr(registry, "pickle", spy)
    changed = {
        "flipped_byte": original[:-3] + bytes([original[-3] ^ 1]) + original[-2:],
        "truncated": original[:-5],
        "appended": original + b".",
        "empty": b"",
        "replaced": evil_bytes(marker),
    }[damage]
    artifact.write_bytes(changed)
    with pytest.raises(ArtifactMismatchError):
        load_artifact(artifact, registered)
    assert spy.loaded == [] and not marker.exists()


class CountingPath(type(Path())):
    reads = 0

    def read_bytes(self):
        CountingPath.reads += 1
        return super().read_bytes()


def test_the_bytes_that_are_hashed_are_the_bytes_that_are_loaded_and_the_file_is_read_once(tmp_path, monkeypatch):
    artifact = tmp_path / "v1.pkl"
    payload = pickle.dumps({"a": list(range(50))}, protocol=5)
    artifact.write_bytes(payload)
    spy = SpyPickle()
    monkeypatch.setattr(registry, "pickle", spy)
    monkeypatch.setattr(registry, "Path", CountingPath)
    CountingPath.reads = 0
    assert load_artifact(artifact, sha(payload)) == {"a": list(range(50))}
    assert CountingPath.reads == 1, "one read: there is no window between checking the fingerprint and loading"
    assert len(spy.loaded) == 1 and sha(spy.loaded[0]) == sha(payload), "pickle received exactly the bytes whose fingerprint was checked"


@pytest.mark.parametrize("bad", ["", "abc", "A" * 64, "g" * 64, "a" * 63, "a" * 65, " " + "a" * 63, None, 12345])
def test_a_malformed_expected_fingerprint_is_a_value_error_and_the_file_is_not_even_read(tmp_path, monkeypatch, bad):
    artifact = tmp_path / "v1.pkl"
    artifact.write_bytes(pickle.dumps(1))
    monkeypatch.setattr(registry, "Path", CountingPath)
    CountingPath.reads = 0
    with pytest.raises(ValueError, match="SHA-256"):
        load_artifact(artifact, bad)
    assert CountingPath.reads == 0


def test_a_missing_artifact_is_a_file_not_found_error_and_creates_nothing(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_artifact(tmp_path / "missing.pkl", "0" * 64)
    assert list(tmp_path.iterdir()) == []


def _tiny_model(config):
    from latedel.model import Candidate, make_pipeline

    rng = np.random.default_rng(0)
    x = pd.DataFrame({"shipping_mode": rng.choice(["Same Day", "First Class", "Standard Class"], 300), "scheduled_days": rng.choice([0, 1, 4], 300)})
    y = (x["shipping_mode"] == "First Class").astype(int).to_numpy()
    pipeline = make_pipeline(Candidate("f0", 0.1, 15, 20), config.model)
    pipeline.fit(x, y)
    return pipeline, x


def test_a_saved_model_round_trips_with_the_fingerprint_of_its_bytes_and_predicts_the_same(cfg, tmp_path):
    pipeline, x = _tiny_model(cfg)
    path = tmp_path / "models" / "v1.pkl"
    digest = save_artifact(pipeline, path)
    assert digest == sha(path.read_bytes()) and len(digest) == 64
    assert path.read_bytes()[:2] == b"\x80\x05", "pickle protocol 5"
    loaded = load_artifact(path, digest)
    np.testing.assert_array_equal(loaded.predict_proba(x), pipeline.predict_proba(x))
    assert [p.name for p in path.parent.iterdir()] == ["v1.pkl"], "no temporary file is left"


def test_saving_again_changes_the_file_and_the_old_fingerprint_no_longer_loads_it(tmp_path):
    path = tmp_path / "v1.pkl"
    first = save_artifact({"n": 1}, path)
    second = save_artifact({"n": 2}, path)
    assert first != second
    assert load_artifact(path, second) == {"n": 2}
    with pytest.raises(ArtifactMismatchError):
        load_artifact(path, first)


def test_a_model_that_cannot_be_serialized_writes_nothing_and_keeps_the_file_that_was_there(tmp_path):
    path = tmp_path / "v1.pkl"
    good = save_artifact({"n": 1}, path)
    with pytest.raises((pickle.PicklingError, AttributeError, TypeError)):
        save_artifact({"unpicklable": lambda: 1}, path)
    assert load_artifact(path, good) == {"n": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["v1.pkl"]
    fresh = tmp_path / "other.pkl"
    with pytest.raises((pickle.PicklingError, AttributeError, TypeError)):
        save_artifact({"unpicklable": lambda: 1}, fresh)
    assert not fresh.exists()


def test_the_registry_serializes_with_the_standard_library_only():
    source = Path(registry.__file__).read_text(encoding="utf-8")
    for forbidden in ("joblib", "cloudpickle", "dill", "skops"):
        assert forbidden not in source
    assert "import pickle" in source
