"""One writer at a time on a project's data folder: a lock file created with ``O_EXCL`` (review of H8, R1-001 and R4-001; decision D82).

The pipeline has a single writer by design: the DAG runs one run and one task at a time, and the command line is one process. ``airflow dags test`` in the debug service
of the compose file runs outside the scheduler, so next to a scheduler that is running the monthly run there could be two processes writing the same warehouse, state
files and exports. Every entry point of the pipeline (``run_task``, ``run_all``, ``replay``) takes this lock before its first write and keeps it until it returns; a
second process is refused before it writes anything.

The file is ``<interim folder>/pipeline.lock`` and says who holds it: the process id, the host, the command, the run and when it took it, plus a token that only its owner
knows. Only the owner removes it, and only if the token is still its own. A lock whose holder is gone (a process killed with ``kill -9``, a power cut) is reported, never
removed by another process: two containers, or a container and the machine that runs it, share the folder through a mount but not their processes, and a process of one
cannot tell whether a process id of the other is alive. A person checks and removes the file (docs/runbook.md, R9). The lock covers the time a process runs: between two
tasks of an Airflow run, which are separate processes, nobody holds it.

Only the standard library is imported, so a process can take the lock without loading the pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

LOCK_NAME = "pipeline.lock"
HOLDER_FIELDS = ("pid", "host", "command", "run_key", "since", "token")
_HELD: dict[Path, list] = {}   # the locks this process holds: path -> [token, depth]; ``replay`` takes the lock and calls ``run_all``, which takes it again


@dataclass(frozen=True)
class Holding:
    """A lock this process holds (what ``release`` needs)."""

    path: Path
    token: str


class LockHeld(RuntimeError):
    """The folder is held by another process (or by a lock nobody can read): nothing was written. ``state`` is ``alive`` (a live process of this host), ``stale`` (a
    process of this host that is no longer running), ``elsewhere`` (another host or container: it cannot be checked from here) or ``unreadable``."""

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


def _windows_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    query_limited_information, still_active, invalid_parameter = 0x1000, 259, 87
    handle = kernel32.OpenProcess(query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() != invalid_parameter   # no such process; any other refusal (access denied) means it exists
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True   # it exists and will not say how it is: not dead for certain
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


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
    if _alive(holder["pid"]):
        return LockHeld(
            f"Otro proceso escribe esta carpeta de datos: {_who(holder)}. Espere a que termine y vuelva a correr; con Airflow, detenga el planificador antes de usar "
            "`airflow dags test` (docs/runbook.md, R8 y R9). No se escribió nada.",
            path=path, holder=holder, state="alive",
        )
    return LockHeld(
        f"El bloqueo {path} quedó de un proceso que ya no está en marcha: {_who(holder)}. Ningún proceso lo borra solo: compruebe que nada más escribe esta carpeta "
        "(tampoco un contenedor que la monte), borre el archivo y, si esa corrida quedó a medias, retómela con `python -m latedel run --run-key <corrida> --from <tarea>` "
        "(docs/runbook.md, R9). No se escribió nada.",
        path=path, holder=holder, state="stale",
    )


def acquire(folder: str | os.PathLike, *, command: str, run_key: str | None = None) -> Holding:
    """Take the lock of `folder` (created if missing) for this process, or raise ``LockHeld`` without writing anything.

    A process that already holds it takes it again (the outer call keeps the file and the inner release only counts down).
    """
    path = _lock_path(folder)
    if path in _HELD:
        _HELD[path][1] += 1
        return Holding(path, _HELD[path][0])
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    record = {
        "pid": os.getpid(), "host": socket.gethostname(), "command": command, "run_key": run_key,
        "since": datetime.now(UTC).isoformat(timespec="seconds"), "token": token,
    }
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError:
        raise _refusal(path, read_holder(path.parent)) from None
    try:
        os.write(handle, (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
    except BaseException:
        os.close(handle)
        path.unlink(missing_ok=True)   # it is this process's own file, not yet a lock anybody reads
        raise
    os.close(handle)
    _HELD[path] = [token, 1]
    return Holding(path, token)


def release(held: Holding) -> None:
    """Give the lock back; the file is removed by the outer holder only, and only while it still carries this process's token (never another's)."""
    entry = _HELD.get(held.path)
    if entry is None or entry[0] != held.token:
        logger.warning("lock %s is not held by this process: nothing to release", held.path)
        return
    entry[1] -= 1
    if entry[1] > 0:
        return
    del _HELD[held.path]
    holder = read_holder(held.path.parent)
    if holder is None or holder.get("token") != held.token:
        logger.warning("lock %s is not this process's any more (removed or taken by another process): left as it is", held.path)
        return
    held.path.unlink(missing_ok=True)
