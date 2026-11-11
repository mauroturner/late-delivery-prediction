"""H9: the writer lock of a project's data folder (review of H8, R1-001 and R4-001; decision D82).

One process writes a data folder at a time. The lock is a file created with ``O_EXCL`` in the interim folder; it says who holds it (the process, the host, the command and
the run) and only its owner removes it. A lock whose holder is gone is reported, never deleted by another process: a person decides. These tests use the module alone; the
pipeline's refusals, with a second process, are in ``tests/integration/test_pipeline_hardening.py``.
"""

from __future__ import annotations

import json
import logging
import os
import socket

import pytest

from latedel.lock import LOCK_NAME, LockHeld, acquire, read_holder, release


def write_lock(folder, **holder) -> bytes:
    """A lock file as another process would have left it; returns its bytes."""
    record = {"pid": os.getpid(), "host": socket.gethostname(), "command": "replay", "run_key": "other", "since": "2026-11-11T12:00:00+00:00", "token": "f" * 32}
    record.update(holder)
    data = (json.dumps(record) + "\n").encode("utf-8")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / LOCK_NAME).write_bytes(data)
    return data


def test_the_lock_says_who_holds_it_and_its_owner_removes_it(tmp_path):
    folder = tmp_path / "interim"   # created by the lock when it is missing, as on a first run
    held = acquire(folder, command="task fetch_source", run_key="r1")
    holder = read_holder(folder)
    assert holder is not None and {k: holder[k] for k in ("pid", "host", "command", "run_key")} == {
        "pid": os.getpid(), "host": socket.gethostname(), "command": "task fetch_source", "run_key": "r1",
    }
    assert holder["since"] and len(holder["token"]) == 32
    release(held)
    assert not (folder / LOCK_NAME).exists() and read_holder(folder) is None


def test_the_same_process_takes_the_lock_again_and_only_the_outer_release_removes_it(tmp_path):
    """``replay`` holds the lock and calls ``run_all``, which takes it again: the inner call does not refuse its own process, and the file stays until the outer one ends."""
    outer = acquire(tmp_path, command="replay", run_key=None)
    inner = acquire(tmp_path, command="run", run_key="r1")
    release(inner)
    assert read_holder(tmp_path)["command"] == "replay"
    release(outer)
    assert not (tmp_path / LOCK_NAME).exists()


def test_a_lock_held_by_a_live_process_of_this_host_is_refused_and_left_as_it_is(tmp_path):
    data = write_lock(tmp_path)   # this test's own process is alive, and the lock is not one this process took through acquire
    with pytest.raises(LockHeld) as refused:
        acquire(tmp_path, command="run", run_key="r2")
    message = str(refused.value)
    assert refused.value.state == "alive" and refused.value.holder["run_key"] == "other"
    assert "Otro proceso escribe esta carpeta de datos" in message and str(os.getpid()) in message and "replay" in message and "other" in message
    assert (tmp_path / LOCK_NAME).read_bytes() == data


def test_a_lock_from_another_host_is_refused_with_the_host_to_check(tmp_path):
    """A container and the machine that runs it share ``data/`` through a mount but not their processes: from here it cannot be known whether that process still runs."""
    data = write_lock(tmp_path, host="a1b2c3d4e5f6", pid=7)
    with pytest.raises(LockHeld) as refused:
        acquire(tmp_path, command="run", run_key="r2")
    message = str(refused.value)
    assert refused.value.state == "elsewhere" and "a1b2c3d4e5f6" in message and "no se puede saber" in message and "docs/runbook.md" in message
    assert (tmp_path / LOCK_NAME).read_bytes() == data


@pytest.mark.parametrize("content", [b"", b"{not json", b"[1, 2]\n", b'{"pid": "x"}\n'])
def test_a_lock_that_cannot_be_read_is_refused_and_not_removed(tmp_path, content):
    """A process that dies between creating the file and writing it leaves it empty: still a lock, whose holder nobody can tell."""
    (tmp_path / LOCK_NAME).write_bytes(content)
    with pytest.raises(LockHeld) as refused:
        acquire(tmp_path, command="run", run_key="r2")
    assert refused.value.state == "unreadable" and refused.value.holder is None and "no se puede leer" in str(refused.value)
    assert (tmp_path / LOCK_NAME).read_bytes() == content


def test_release_never_removes_a_lock_that_is_not_its_own(tmp_path, caplog):
    """If someone removed this process's lock by hand and another process took the folder, the release of the first must leave the second one's lock in place."""
    held = acquire(tmp_path, command="run", run_key="r1")
    data = write_lock(tmp_path, pid=4242, token="a" * 32, run_key="r9")
    with caplog.at_level(logging.WARNING, logger="latedel.lock"):
        release(held)
    assert (tmp_path / LOCK_NAME).read_bytes() == data
    assert any("not this process's" in r.getMessage() for r in caplog.records)
