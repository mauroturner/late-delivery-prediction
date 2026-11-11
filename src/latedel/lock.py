"""One writer at a time on a project's data folder: a lock file created with ``O_EXCL`` (review of H8, R1-001 and R4-001; decisions D82 and D87).

The pipeline has a single writer by design: the DAG runs one run and one task at a time, and the command line is one process. ``airflow dags test`` in the debug service
of the compose file runs outside the scheduler, so next to a scheduler that is running the monthly run there could be two processes writing the same warehouse, state
files and exports. Every entry point of the pipeline (``run_task``, ``run_all``, ``replay``) takes this lock before its first write and keeps it until it returns; a
second process is refused before it writes anything.

The file is ``<interim folder>/pipeline.lock`` and says who holds it: the process id and when that process started (so that a process id the system gave again to another
process is not taken for the holder), the host, the command, the run and when it took it, plus a token that only its owner knows. Only the owner removes it, and only if
the token is still its own. A lock whose holder is gone (a process killed with ``kill -9``, a power cut) is reported, never removed by another process: two containers, or a
container and the machine that runs it, share the folder through a mount but not their processes, and a process of one cannot tell whether a process id of the other is
alive. A person checks and removes the file (docs/runbook.md, R9). The lock covers the time a process runs: between two tasks of an Airflow run, which are separate
processes, nobody holds it.

The process that holds the lock may take it again (``replay`` holds it and calls ``run_all``, which takes it for each run): the file then says the inner command and run,
and the outer record comes back when the inner call gives it back. Only the thread that took it may take it again; another thread of the same process is refused like
another process. Giving the lock back never raises: on Windows a file that another process has open (a refused writer reading who holds it, an operator, an antivirus)
cannot be removed for a moment, so the removal is tried again for a short while and, if the file is still there, a warning says so and the caller's own outcome (its
result, or its error) stands (review of the evaluation of ``eef9bea``, B2).

Only the standard library is imported, so a process can take the lock without loading the pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

LOCK_NAME = "pipeline.lock"
HOLDER_FIELDS = ("pid", "host", "command", "run_key", "since", "token")   # ``started`` is written too, but a lock left by an earlier version has none
RELEASE_ATTEMPTS = 40          # tries of the removal (or of the rewrite of a nested record) while another process has the file open
RELEASE_PAUSE_S = 0.05         # between two tries: two seconds in all


@dataclass
class _Entry:
    """A lock this process holds: its token, the thread that took it and the records it has said, the outer one first."""

    token: str
    thread: int
    records: list[dict] = field(default_factory=list)


_HELD: dict[Path, _Entry] = {}
_REGISTRY = threading.Lock()   # the check of ``_HELD`` and the creation of the file are one step for the threads of this process


@dataclass(frozen=True)
class Holding:
    """A lock this process holds (what ``release`` needs)."""

    path: Path
    token: str


class LockHeld(RuntimeError):
    """The folder is held by another process (or by a lock nobody can read): nothing was written. ``state`` is ``alive`` (a live process of this host), ``stale`` (a
    process of this host that is no longer running, or whose id now belongs to another process), ``elsewhere`` (another host or container: it cannot be checked from
    here), ``thread`` (another thread of this same process) or ``unreadable``."""

    def __init__(self, message: str, *, path: Path, holder: dict | None, state: str) -> None:
        super().__init__(message)
        self.path, self.holder, self.state = path, holder, state


def _lock_path(folder: str | os.PathLike) -> Path:
    return Path(os.path.abspath(folder)) / LOCK_NAME


def read_holder(folder: str | os.PathLike) -> dict | None:
    """Who holds the lock of `folder`, as the file says; None if there is no lock or it cannot be read (empty, not JSON, a field missing or of the wrong type)."""
    try:
        holder = json.loads(_lock_path(folder).read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(holder, dict) or not all(k in holder for k in HOLDER_FIELDS) or not isinstance(holder["pid"], int) or not isinstance(holder["host"], str):
        return None
    return holder


# --- whether the holder still runs -------------------------------------------------------------------------------------------------------------


def _windows_kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(ctypes.c_ulonglong)] * 4)]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return ctypes, wintypes, kernel32


_QUERY_LIMITED_INFORMATION, _STILL_ACTIVE, _INVALID_PARAMETER = 0x1000, 259, 87


def _windows_alive(pid: int) -> bool:
    ctypes, wintypes, kernel32 = _windows_kernel32()
    handle = kernel32.OpenProcess(_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() != _INVALID_PARAMETER   # no such process; any other refusal (access denied) means it exists
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True   # it exists and will not say how it is: not dead for certain
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _windows_start(pid: int) -> int | None:
    ctypes, _wintypes, kernel32 = _windows_kernel32()
    handle = kernel32.OpenProcess(_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        created, ended, kernel, user = (ctypes.c_ulonglong() for _ in range(4))
        if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(ended), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return int(created.value)
    finally:
        kernel32.CloseHandle(handle)


def _process_start(pid: int) -> int | None:
    """When the process `pid` of this host started, as the system counts it (100-ns steps since 1601 on Windows, clock ticks since the boot on Linux), or None where that
    cannot be told (no such process, another system). It tells a process from a later one that the system gave the same id (review of H9, R4-pid-reuse-false-alive)."""
    if pid <= 0:
        return None
    try:
        if sys.platform == "win32":
            return _windows_start(pid)
        if sys.platform.startswith("linux"):
            return int(Path(f"/proc/{pid}/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()[19])   # field 22, starttime; the name in parentheses may hold spaces
    except (OSError, ValueError, IndexError):
        return None
    return None


def _alive(pid: int) -> bool:
    """Whether a process of this host with this id is running. ``os.kill(pid, 0)`` is not a test on Windows (it ends the process), so Windows asks the system."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _same_process(holder: dict) -> bool:
    """Whether the live process with the holder's id is the one that took the lock: it is, unless the lock says when its holder started and that process started at
    another moment. A lock with no start time (an earlier version), or a system that cannot tell, keeps the answer of ``_alive``."""
    started = holder.get("started")
    if not isinstance(started, int) or isinstance(started, bool):
        return True
    now = _process_start(holder["pid"])
    return now is None or now == started


# --- messages ----------------------------------------------------------------------------------------------------------------------------------------


def _who(holder: dict) -> str:
    run = f"corrida {holder['run_key']}" if holder.get("run_key") else "sin corrida todavía"
    return f"`{holder['command']}` ({run}), proceso {holder['pid']} en {holder['host']}, desde {holder['since']}"


def _refusal(path: Path, holder: dict | None) -> LockHeld:
    if holder is None:
        return LockHeld(
            f"El bloqueo {path} existe pero no se puede leer quién lo tiene: puede haberlo dejado un proceso que murió al crearlo. Si ningún proceso de latedel escribe "
            "esta carpeta de datos (ni en este equipo ni en un contenedor que la monte), bórrelo y vuelva a correr (docs/runbook.md, R9). No se escribió nada.",
            path=path, holder=None, state="unreadable",
        )
    if holder["host"] != socket.gethostname():
        return LockHeld(
            f"Un proceso de otro equipo o contenedor tiene esta carpeta de datos: {_who(holder)}. Desde aquí no se puede saber si sigue en marcha: compruébelo en "
            f"{holder['host']} (con Docker, `docker compose ps` y los registros de la tarea) y borre {path} solo si ya terminó (docs/runbook.md, R9). No se escribió nada.",
            path=path, holder=holder, state="elsewhere",
        )
    alive = _alive(holder["pid"])
    if alive and _same_process(holder):
        return LockHeld(
            f"Otro proceso escribe esta carpeta de datos: {_who(holder)}. Espere a que termine y vuelva a correr; con Airflow, detenga el planificador antes de usar "
            "`airflow dags test` (docs/runbook.md, R8 y R9). No se escribió nada.",
            path=path, holder=holder, state="alive",
        )
    reused = f" (el número {holder['pid']} lo tiene ahora otro proceso, que empezó después)" if alive else ""
    return LockHeld(
        f"El bloqueo {path} quedó de un proceso que ya no está en marcha: {_who(holder)}{reused}. Ningún proceso lo borra solo: compruebe que nada más escribe esta "
        "carpeta (tampoco un contenedor que la monte), borre el archivo y, si esa corrida quedó a medias, retómela con "
        "`python -m latedel run --run-key <corrida> --from <tarea>` (docs/runbook.md, R9). No se escribió nada.",
        path=path, holder=holder, state="stale",
    )


def _thread_refusal(path: Path, entry: _Entry) -> LockHeld:
    holder = entry.records[-1]
    return LockHeld(
        f"Otro hilo de este mismo proceso escribe esta carpeta de datos: {_who(holder)}. Un proceso escribe una carpeta de datos desde un solo hilo a la vez: espere a que "
        "ese hilo termine (docs/runbook.md, R9). No se escribió nada.",
        path=path, holder=dict(holder), state="thread",
    )


# --- the file ----------------------------------------------------------------------------------------------------------------------------------------


def _encoded(record: dict) -> bytes:
    return (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")


def _create(path: Path, record: dict) -> bool:
    """Create the lock with `record`, all or nothing: True if it was created, False if a lock was already there."""
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError:
        return False
    try:
        os.write(handle, _encoded(record))
    except BaseException:
        os.close(handle)
        path.unlink(missing_ok=True)   # it is this process's own file, not yet a lock anybody reads
        raise
    os.close(handle)
    return True


def _retrying(action, what: str, path: Path) -> bool:
    """Do `action` (a removal or a replacement of the lock file), trying again for ``RELEASE_ATTEMPTS`` × ``RELEASE_PAUSE_S`` while the system refuses it because another
    process has the file open (Windows); True if it was done. Never raises an ``OSError``: the lock is bookkeeping of the caller, whose outcome must stand."""
    for attempt in range(RELEASE_ATTEMPTS):
        try:
            action()
            return True
        except PermissionError:   # WinError 32 or 5: another process has the file open; it closes it in a moment
            if attempt + 1 < RELEASE_ATTEMPTS:
                time.sleep(RELEASE_PAUSE_S)
        except OSError:
            break
    logger.warning(
        "could not %s the lock %s after %.1f s (another process may have it open): %s", what, path, RELEASE_ATTEMPTS * RELEASE_PAUSE_S,
        "remove it by hand once nothing writes the folder (runbook R9)" if what == "remove" else "it keeps saying the previous command",
        exc_info=True,
    )
    return False


def _rewrite(path: Path, record: dict) -> None:
    """Say `record` in the lock this process holds, replacing the file whole (a reader sees the old record or the new one, never half a file)."""
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=LOCK_NAME + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(_encoded(record))
        _retrying(lambda: os.replace(name, path), "rewrite", path)
    finally:
        Path(name).unlink(missing_ok=True)


# --- taking and giving back --------------------------------------------------------------------------------------------------------------------------


def acquire(folder: str | os.PathLike, *, command: str, run_key: str | None = None) -> Holding:
    """Take the lock of `folder` (created if missing) for this process, or raise ``LockHeld`` without writing anything.

    The thread that holds it may take it again: the file then says the inner `command` and `run_key` until the inner release, which brings the outer record back; the file
    is removed by the outer release only. Another thread of this process is refused.
    """
    path = _lock_path(folder)
    with _REGISTRY:
        entry = _HELD.get(path)
        if entry is not None:
            if entry.thread != threading.get_ident():
                raise _thread_refusal(path, entry)
            record = {**entry.records[-1], "command": command, "run_key": run_key, "since": datetime.now(UTC).isoformat(timespec="seconds")}
            entry.records.append(record)
            _rewrite(path, record)   # the run in progress, not only the outer command (review of H9, R4-replay-lock-run-key-granularity)
            return Holding(path, entry.token)
        path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(16)
        record = {
            "pid": os.getpid(), "started": _process_start(os.getpid()), "host": socket.gethostname(), "command": command, "run_key": run_key,
            "since": datetime.now(UTC).isoformat(timespec="seconds"), "token": token,
        }
        if not _create(path, record):
            holder = read_holder(path.parent)
            if holder is not None or path.exists() or not _create(path, record):   # gone between the create and the read: its holder released it; one try more
                raise _refusal(path, holder if holder is not None else read_holder(path.parent)) from None
        _HELD[path] = _Entry(token, threading.get_ident(), [record])
        return Holding(path, token)


def release(held: Holding) -> None:
    """Give the lock back. An inner release brings the outer record back; the outer one removes the file, only while it still carries this process's token (never
    another's). It never raises: a file it cannot remove is logged as a warning and left for a person (runbook R9)."""
    with _REGISTRY:
        entry = _HELD.get(held.path)
        if entry is None or entry.token != held.token:
            logger.warning("lock %s is not held by this process: nothing to release", held.path)
            return
        entry.records.pop()
        if entry.records:
            _rewrite(held.path, entry.records[-1])
            return
        del _HELD[held.path]
    holder = read_holder(held.path.parent)
    if holder is None or holder.get("token") != held.token:
        logger.warning("lock %s is not this process's any more (removed or taken by another process): left as it is", held.path)
        return
    _retrying(lambda: held.path.unlink(missing_ok=True), "remove", held.path)
