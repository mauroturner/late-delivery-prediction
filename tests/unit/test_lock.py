"""H9: the writer lock of a project's data folder (review of H8, R1-001 and R4-001; decision D82).

One process writes a data folder at a time. The lock is a file created with ``O_EXCL`` in the interim folder; it says who holds it (the process, the host, the command and
the run) and only its owner removes it. A lock whose holder is gone is reported, never deleted by another process: a person decides. These tests use the module alone; the
pipeline's refusals, with a second process, are in ``tests/integration/test_pipeline_hardening.py``.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from latedel import lock as lock_module
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


# --- the review of H9 (its eight advisories) and B2 of the evaluation of eef9bea (decision D87) -----------------------------------------------------------


def lock_file_cannot_be_removed(monkeypatch) -> None:
    """Removing the lock file fails every time, as on Windows while another process has it open (``PermissionError``, WinError 32); no pause between the tries."""
    real = Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name == LOCK_NAME:
            raise PermissionError(13, "The process cannot access the file because it is being used by another process", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(lock_module, "RELEASE_PAUSE_S", 0, raising=False)


def test_release_waits_for_a_reader_that_closes_the_file_and_then_removes_the_lock(tmp_path):
    """B2: on Windows a file that another process has open cannot be removed (WinError 32). A refused writer that reads who holds the lock, an operator or a scanner made
    the release raise ``PermissionError``: a run that worked exited 1 and left its lock behind. The release tries again while the reader closes the file."""
    held = acquire(tmp_path, command="run", run_key="r1")
    reader = open(tmp_path / LOCK_NAME, "rb")   # noqa: SIM115 - kept open on purpose, as another process would
    closer = threading.Timer(0.2, reader.close)
    closer.start()
    try:
        release(held)
    finally:
        closer.join()
        reader.close()
    assert not (tmp_path / LOCK_NAME).exists()


def test_a_release_that_cannot_remove_the_lock_logs_a_warning_and_never_raises(tmp_path, monkeypatch, caplog):
    held = acquire(tmp_path, command="run", run_key="r1")
    lock_file_cannot_be_removed(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="latedel.lock"):
        release(held)
    assert (tmp_path / LOCK_NAME).exists()   # left for a person, as a lock of a killed process is
    assert any("could not remove the lock" in r.getMessage() and "runbook R9" in r.getMessage() for r in caplog.records)


@pytest.mark.skipif(not (sys.platform == "win32" or sys.platform.startswith("linux")), reason="the start time of a process is read on Windows and Linux only")
def test_a_live_process_that_got_the_holder_s_id_again_is_not_taken_for_the_holder(tmp_path):
    """R4-pid-reuse-false-alive: a lock left by a killed process whose id the system gave since to an unrelated live process was reported as «Otro proceso escribe…
    Espere», and nobody would ever remove it. The lock says when its holder started; a live process with that id that started at another moment is reported as stale."""
    data = write_lock(tmp_path, started=1)   # this process's id, with a start time that is not this process's
    with pytest.raises(LockHeld) as refused:
        acquire(tmp_path, command="run", run_key="r2")
    assert refused.value.state == "stale" and "ya no está en marcha" in str(refused.value) and "otro proceso" in str(refused.value)
    assert (tmp_path / LOCK_NAME).read_bytes() == data


@pytest.mark.skipif(not (sys.platform == "win32" or sys.platform.startswith("linux")), reason="the start time of a process is read on Windows and Linux only")
def test_the_lock_says_when_its_holder_started_and_that_holder_is_still_reported_alive(tmp_path):
    held = acquire(tmp_path / "a", command="run", run_key="r1")
    started = read_holder(tmp_path / "a")["started"]
    release(held)
    assert isinstance(started, int) and started > 0
    write_lock(tmp_path / "b", started=started)
    with pytest.raises(LockHeld) as refused:
        acquire(tmp_path / "b", command="run", run_key="r2")
    assert refused.value.state == "alive"


def test_while_replay_holds_the_lock_the_file_says_the_run_in_progress_and_then_the_outer_record_again(tmp_path):
    """R4-replay-lock-run-key-granularity: during a replay the lock said ``replay`` and no run, so a refused writer could not tell which run to wait for or resume."""
    outer = acquire(tmp_path, command="replay", run_key=None)
    first = read_holder(tmp_path)
    inner = acquire(tmp_path, command="run", run_key="r1")
    during = read_holder(tmp_path)
    release(inner)
    after = read_holder(tmp_path)
    release(outer)
    assert (during["command"], during["run_key"]) == ("run", "r1") and (during["pid"], during["token"]) == (first["pid"], first["token"])
    assert after == first
    assert list(tmp_path.iterdir()) == []   # the file is gone and no temporary file of a rewrite is left


def rewrite_cannot_write(monkeypatch) -> None:
    """The temporary file of a rewrite cannot be created, as on a full disk (``ENOSPC``)."""
    def no_space(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(lock_module.tempfile, "mkstemp", no_space)


def test_a_nested_take_whose_rewrite_fails_never_leaves_the_lock_behind(tmp_path, monkeypatch, caplog):
    """R4-nested-acquire-permanent-lock-leak (review of B2): the inner take kept its record in this process's bookkeeping before the rewrite raised, so the outer release
    only rewrote the file and the lock outlived the replay, refusing every later writer until a person removed it. A rewrite only says who holds the lock: when it fails,
    the take still happens, a warning says so, and the outer release removes the file."""
    outer = acquire(tmp_path, command="replay", run_key=None)
    rewrite_cannot_write(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="latedel.lock"):
        try:
            inner = acquire(tmp_path, command="run", run_key="r1")
        except OSError:
            inner = None   # the take failed: nothing to give back
    monkeypatch.undo()
    if inner is not None:
        release(inner)
    release(outer)
    assert list(tmp_path.iterdir()) == []
    assert inner is not None
    assert any("could not rewrite the lock" in r.getMessage() for r in caplog.records)


def test_a_nested_release_whose_rewrite_fails_never_raises_and_the_outer_release_removes_the_lock(tmp_path, monkeypatch, caplog):
    """R4-rewrite-unguarded-raise (review of B2): the inner release of a replay rewrote the file with no guard, so a write error left the release raising, which B2 says
    it never does, and replaced the outcome of the run."""
    outer = acquire(tmp_path, command="replay", run_key=None)
    inner = acquire(tmp_path, command="run", run_key="r1")
    rewrite_cannot_write(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="latedel.lock"):
        release(inner)
    monkeypatch.undo()
    release(outer)
    assert list(tmp_path.iterdir()) == []
    assert any("could not rewrite the lock" in r.getMessage() for r in caplog.records)


def test_another_thread_of_the_same_process_is_refused_like_another_process(tmp_path):
    """R3-003: the registry of the locks this process holds had no thread guard, so a second thread took the folder as if it were the holder's own nested call."""
    held = acquire(tmp_path, command="run", run_key="r1")
    caught: list[LockHeld] = []

    def other() -> None:
        try:
            acquire(tmp_path, command="task score", run_key="r2")
        except LockHeld as error:
            caught.append(error)

    worker = threading.Thread(target=other)
    worker.start()
    worker.join()
    release(held)
    assert len(caught) == 1, "the second thread was not refused"
    assert caught[0].state == "thread" and "Otro hilo de este mismo proceso" in str(caught[0]) and "r1" in str(caught[0])
    assert not (tmp_path / LOCK_NAME).exists()


def test_a_lock_released_between_the_failed_create_and_the_read_is_taken_on_a_second_try(tmp_path, monkeypatch):
    """R4-lock-toctou-false-unreadable: a lock released by its holder between this process's failed create and its read of the holder was reported as unreadable, and
    the message told the operator to delete it. One more try of the create takes the folder, which is free."""
    real, creates = os.open, []

    def racing_open(path, flags, *args, **kwargs):
        if Path(path).name == LOCK_NAME:
            creates.append(path)
            if len(creates) == 1:
                raise FileExistsError(17, "File exists", str(path))   # the holder's file was there, and it is gone when it is read
        return real(path, flags, *args, **kwargs)

    monkeypatch.setattr(lock_module.os, "open", racing_open)
    held = acquire(tmp_path, command="run", run_key="r1")
    monkeypatch.undo()
    assert read_holder(tmp_path)["run_key"] == "r1" and len(creates) == 2
    release(held)
    assert not (tmp_path / LOCK_NAME).exists()


@pytest.mark.skipif(sys.platform != "win32", reason="the liveness check of Windows")
def test_the_windows_liveness_check_tells_a_running_child_from_one_that_ended():
    """R3-001: ``_windows_alive`` asks the system (``OpenProcess`` and ``GetExitCodeProcess``) because ``os.kill(pid, 0)`` ends a process on Windows; it had no test."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import os, sys; print(os.getpid(), flush=True); sys.stdin.readline()"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        pid = int(child.stdout.readline())   # the interpreter's own id: a virtual environment's python.exe starts it as a child of its own
        assert lock_module._windows_alive(pid) and lock_module._alive(pid)
        os.kill(pid, signal.SIGTERM)   # TerminateProcess of this child only, by its id
        child.wait(timeout=60)
        deadline = time.perf_counter() + 10
        while lock_module._windows_alive(pid) and time.perf_counter() < deadline:
            time.sleep(0.05)
        assert not lock_module._windows_alive(pid) and not lock_module._alive(pid)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=60)
        child.stdin.close()
        child.stdout.close()
