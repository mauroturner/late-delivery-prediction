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


@pytest.mark.parametrize("winerror", [1, 50])       # ERROR_INVALID_FUNCTION and ERROR_NOT_SUPPORTED: the two answers of a volume without hard links
def test_a_windows_file_system_without_hard_links_is_also_the_fallback(tmp_path, monkeypatch, winerror):
    def link(src, dst, **kwargs):
        error = OSError("Incorrect function")
        error.winerror = winerror
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


# --- H4: the advisories of the native review of H3 -----------------------------------------------------------------------------------------------


def test_a_windows_error_that_is_not_the_lack_of_hard_links_is_raised_and_nothing_is_created(tmp_path, monkeypatch):
    """R3-001: of the Windows codes only 1 and 50 are the lack of hard links; a full disk (112) is an error, not a reason to fall back."""
    def link(src, dst, **kwargs):
        error = OSError("There is not enough space on the disk")
        error.winerror = 112
        raise error

    monkeypatch.setattr(os, "link", link)
    with pytest.raises(OSError, match="enough space"):
        _create_exclusive(tmp_path / "seal.json", b"first\n")
    assert list(tmp_path.iterdir()) == []


def test_the_fallback_creates_the_seal_with_the_owner_only_mode_of_the_main_path(tmp_path, monkeypatch):
    """R1-001: the temporary file of the main path is created by ``mkstemp`` with 0600; the file of the fallback asked for 0644 and was readable by every local user."""
    modes = []
    real_open = os.open

    def recording_open(path, flags, mode=0o777, **kwargs):
        modes.append(mode)
        return real_open(path, flags, mode, **kwargs)

    without_hard_links(monkeypatch)
    monkeypatch.setattr(os, "open", recording_open)
    path = tmp_path / "seal.json"
    assert _create_exclusive(path, b"first\n") is True
    assert modes and set(modes) == {0o600}, "every file the creation opens (the temporary one of mkstemp and the seal itself) is for its owner only"
    if os.name == "posix":                                    # on Windows the mode bits of os.open only decide if the file is read-only: the access is the ACL of the folder
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name != "posix", reason="on Windows the mode bits are not the access control")
def test_the_main_path_and_the_fallback_leave_the_same_mode(tmp_path, monkeypatch):
    main, fallback = tmp_path / "main.json", tmp_path / "fallback.json"
    assert _create_exclusive(main, b"x\n") is True
    without_hard_links(monkeypatch)
    assert _create_exclusive(fallback, b"x\n") is True
    assert main.stat().st_mode & 0o777 == fallback.stat().st_mode & 0o777 == 0o600


def test_a_lost_update_of_the_test_part_is_logged_with_both_runs_where_it_is_detected(cfg, tmp_path, monkeypatch, caplog):
    """R4-001: detection is the design (one writer, D61; exclusion would leave an orphan lock, D64), but the point of detection says who wrote what."""
    import logging

    path = seal_with_launch(cfg, tmp_path)
    other = part_of_test(cfg, run_key="run-other", metrics={"model": {"auc": 0.5}, "b2_mode": {"auc": 0.74}, "b5_mode_noon": {"auc": 0.781}})
    another_writer_wins(monkeypatch, path, other)
    with caplog.at_level(logging.ERROR, logger="latedel.registry"), pytest.raises(SealMismatchError, match="R7"):
        write_test_seal(path, part_of_test(cfg, run_key="run-mine"))
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR and r.name == "latedel.registry"]
    assert len(errors) == 1 and "run-mine" in errors[0] and "run-other" in errors[0] and "R7" in errors[0]
    assert read_seal(path)["test"]["run_key"] == "run-other", "the file keeps what the other writer put there, as before"


# --- H6 (review of H4) ---------------------------------------------------------------------------------------------------------------------------------


def test_where_the_owner_only_mode_is_not_the_access_control_the_creation_of_the_seal_says_so(tmp_path, monkeypatch, caplog):
    """R1-001 of the review of H4: the Windows caveat of the owner-only mode lived only in a comment. Where the mode bits are not the access control, creating the seal
    logs it with the folder whose access list decides who reads it; on both paths, and once per created seal (a seal that was already there logs nothing)."""
    import logging

    monkeypatch.setattr(registry, "_mode_is_access_control", lambda: False)
    with caplog.at_level(logging.WARNING, logger="latedel.registry"):
        assert _create_exclusive(tmp_path / "main.json", b"x\n") is True
        assert _create_exclusive(tmp_path / "main.json", b"y\n") is False
        without_hard_links(monkeypatch)
        assert _create_exclusive(tmp_path / "fallback.json", b"x\n") is True
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and r.name == "latedel.registry"]
    assert len(warnings) == 2 and all("0o600" in w and str(tmp_path) in w and "R7" in w for w in warnings), warnings
    assert "main.json" in warnings[0] and "fallback.json" in warnings[1]


def test_where_the_mode_is_the_access_control_nothing_is_logged(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.setattr(registry, "_mode_is_access_control", lambda: True)
    with caplog.at_level(logging.WARNING, logger="latedel.registry"):
        assert _create_exclusive(tmp_path / "seal.json", b"x\n") is True
    assert [r for r in caplog.records if r.name == "latedel.registry"] == []


def test_the_mode_is_the_access_control_on_posix_only():
    assert registry._mode_is_access_control() is (os.name == "posix")


def test_the_log_of_a_lost_update_names_the_run_that_wrote_and_the_run_that_was_kept_each_in_its_role(cfg, tmp_path, monkeypatch, caplog):
    """R3-weak-log-attribution-assertion: the test of H4 only checked that both runs appear; swapping them would still pass. The roles are checked here word by word."""
    import logging
    import re

    path = seal_with_launch(cfg, tmp_path)
    other = part_of_test(cfg, run_key="run-other", metrics={"model": {"auc": 0.5}, "b2_mode": {"auc": 0.74}, "b5_mode_noon": {"auc": 0.781}})
    another_writer_wins(monkeypatch, path, other)
    with caplog.at_level(logging.ERROR, logger="latedel.registry"), pytest.raises(SealMismatchError):
        write_test_seal(path, part_of_test(cfg, run_key="run-mine"))
    [message] = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR and r.name == "latedel.registry"]
    assert re.search(r"written by run run-mine was replaced by the one of run run-other", message), message
