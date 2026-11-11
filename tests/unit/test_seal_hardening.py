"""Hardening of the seal after the native review of U14 (H3).

R4-seal-fallback-toctou: on a file system without hard links the seal was created by "check, then rename", which reopens the window the hard link closes; the fallback is
now an exclusive create (``O_CREAT | O_EXCL``), and an error that is not "this file system has no hard links" (a full disk, a permission) is an error, not a reason to fall back.
R3-write-test-seal-race: the part of the test is added by reading the seal, deciding and rewriting it; the flow has one writer by design (D61), and a second writer that
started from a stale read and wrote another result is now noticed by the one that was overwritten.
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from latedel import registry
from latedel.registry import SealMismatchError, _create_exclusive, read_seal, write_launch_seal, write_test_seal
from tests.unit.test_seal import launch_for, part_of_test


def without_hard_links(monkeypatch, code: int = errno.EPERM) -> None:
    def link(src, dst, **kwargs):
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(os, "link", link)


@pytest.mark.parametrize("code", [errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.ENOSYS])
def test_without_hard_links_the_seal_is_created_once_and_a_second_creator_finds_it(tmp_path, monkeypatch, code):
    without_hard_links(monkeypatch, code)
    path = tmp_path / "seal.json"
    assert _create_exclusive(path, b"first\n") is True and path.read_bytes() == b"first\n"
    assert _create_exclusive(path, b"second\n") is False and path.read_bytes() == b"first\n"
    assert [p.name for p in tmp_path.iterdir()] == ["seal.json"], "no temporary file is left"


def test_a_windows_file_system_without_hard_links_is_also_the_fallback(tmp_path, monkeypatch):
    def link(src, dst, **kwargs):
        error = OSError("Incorrect function")
        error.winerror = 1               # ERROR_INVALID_FUNCTION: what NTFS-less volumes answer to a hard link
        raise error

    monkeypatch.setattr(os, "link", link)
    path = tmp_path / "seal.json"
    assert _create_exclusive(path, b"first\n") is True and _create_exclusive(path, b"second\n") is False and path.read_bytes() == b"first\n"


def test_the_fallback_does_not_depend_on_looking_first(tmp_path, monkeypatch):
    """R4-seal-fallback-toctou: a seal that another process creates after any look is not replaced, because creating is the check."""
    without_hard_links(monkeypatch)
    path = tmp_path / "seal.json"
    path.write_bytes(b"written by the other process\n")
    monkeypatch.setattr(Path, "exists", lambda self: False)                    # a look that says there is nothing, however late the other process wrote
    assert _create_exclusive(path, b"mine\n") is False
    monkeypatch.undo()
    assert path.read_bytes() == b"written by the other process\n" and [p.name for p in tmp_path.iterdir()] == ["seal.json"]


@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EACCES, errno.EIO])
def test_an_error_that_is_not_the_lack_of_hard_links_is_raised_and_nothing_is_created(tmp_path, monkeypatch, code):
    without_hard_links(monkeypatch, code)
    path = tmp_path / "seal.json"
    with pytest.raises(OSError) as caught:
        _create_exclusive(path, b"data\n")
    assert caught.value.errno == code
    assert list(tmp_path.iterdir()) == [], "neither the seal nor a temporary file"


def test_a_failure_while_the_fallback_writes_leaves_no_half_seal(tmp_path, monkeypatch):
    without_hard_links(monkeypatch)
    path = tmp_path / "seal.json"
    real = os.write
    calls = []

    def failing(fd, data):
        calls.append(1)
        if len(calls) == 1:
            return real(fd, bytes(data)[:3])           # a short write, then the disk fills
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "write", failing)
    with pytest.raises(OSError, match="No space"):
        _create_exclusive(path, b"complete document\n")
    monkeypatch.undo()
    assert list(tmp_path.iterdir()) == [], "a seal that could not be written completely does not exist"
    assert _create_exclusive(path, b"complete document\n") is True and path.read_bytes() == b"complete document\n", "and a later try can create it"


def test_with_hard_links_the_seal_is_created_as_before(tmp_path):
    path = tmp_path / "reports" / "seal.json"
    assert _create_exclusive(path, b"one\n") is True and _create_exclusive(path, b"two\n") is False and path.read_bytes() == b"one\n"
    assert [p.name for p in path.parent.iterdir()] == ["seal.json"]


def test_the_launch_seal_is_still_written_once_when_the_file_system_has_no_hard_links(cfg, tmp_path, monkeypatch):
    without_hard_links(monkeypatch)
    path = tmp_path / "seal.json"
    assert write_launch_seal(path, launch_for(cfg)) == "written" and write_launch_seal(path, launch_for(cfg)) == "unchanged"
    sealed = path.read_bytes()
    assert write_launch_seal(path, launch_for(cfg, threshold=0.7)) == "post_seal" and path.read_bytes() == sealed


# --- the part of the test --------------------------------------------------------------------------------------------------------------------------


def seal_with_launch(cfg, tmp_path) -> Path:
    path = tmp_path / "seal.json"
    write_launch_seal(path, launch_for(cfg))
    return path


def another_writer_wins(monkeypatch, path: Path, other: dict) -> None:
    """After this call writes its part of the test, a second writer (that started from the same stale read) replaces the seal with its own result."""
    real = registry.write_atomic

    def write_then_lose(target, data):
        real(target, data)
        document = json.loads(Path(target).read_text(encoding="utf-8"))
        document["test"] = other
        real(target, json.dumps(document, sort_keys=True, indent=1, ensure_ascii=False) + "\n")
        return target

    monkeypatch.setattr(registry, "write_atomic", write_then_lose)


def test_a_writer_whose_result_was_replaced_by_another_one_with_other_values_is_told(cfg, tmp_path, monkeypatch):
    """R3-write-test-seal-race: the first writer finds a different result in the file than the one it wrote and fails (runbook R7); it does not report success."""
    path = seal_with_launch(cfg, tmp_path)
    other = part_of_test(cfg, run_key="run-other", metrics={"model": {"auc": 0.5}, "b2_mode": {"auc": 0.74}, "b5_mode_noon": {"auc": 0.781}})
    another_writer_wins(monkeypatch, path, other)
    with pytest.raises(SealMismatchError, match="R7"):
        write_test_seal(path, part_of_test(cfg))
    assert read_seal(path)["test"]["metrics"]["model"]["auc"] == 0.5, "the file keeps what the other writer put there"


def test_a_second_writer_with_the_same_values_is_no_conflict(cfg, tmp_path, monkeypatch):
    path = seal_with_launch(cfg, tmp_path)
    twin = part_of_test(cfg, run_key="run-twin")                    # the same configuration and the same values, from another run
    another_writer_wins(monkeypatch, path, twin)
    assert write_test_seal(path, part_of_test(cfg)) == "written"
    assert read_seal(path)["test"]["criteria"] == part_of_test(cfg)["criteria"]
    monkeypatch.undo()
    assert write_test_seal(path, part_of_test(cfg)) == "unchanged"


def test_an_ordinary_first_write_still_verifies_and_leaves_one_file(cfg, tmp_path):
    path = seal_with_launch(cfg, tmp_path)
    assert write_test_seal(path, part_of_test(cfg)) == "written" and [p.name for p in tmp_path.iterdir()] == ["seal.json"]
