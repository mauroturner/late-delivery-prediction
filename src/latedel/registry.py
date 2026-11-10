"""The artifact of the frozen model: saved with its SHA-256 and loaded only when the bytes still have it (M03, decision D30, runbook R6).

``pickle`` runs code when it loads, so a file that someone replaced must never reach it. ``load_artifact`` reads the bytes of the file **once**, computes their
SHA-256, compares it with the one registered, and only then hands *those same bytes* to ``pickle.loads``: there is no window between checking and loading, and
nothing is unpickled from a file whose fingerprint is not the registered one. The fingerprint protects against a file that was replaced or damaged; it does not
make ``pickle`` safe against someone who can rewrite the file and the registry together (decision D30).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import pickle
import re
from pathlib import Path
from typing import Any

from latedel.files import write_atomic

logger = logging.getLogger(__name__)

PICKLE_PROTOCOL = 5
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ArtifactMismatchError(RuntimeError):
    """M03: the artifact does not have the registered SHA-256; it was not deserialized (docs/runbook.md, R6)."""


def artifact_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_artifact(model: Any, path: Path) -> str:
    """Serialize `model` with the standard library's ``pickle`` (protocol 5), write it atomically to `path` and return the SHA-256 of the bytes written.

    If the model cannot be serialized, nothing is written and a file already at `path` is left as it was.
    """
    data = pickle.dumps(model, protocol=PICKLE_PROTOCOL)
    write_atomic(Path(path), data)
    return artifact_sha256(data)


def load_artifact(path: Path, expected_sha256: str) -> Any:
    """The model saved at `path`, deserialized only if its bytes have the SHA-256 `expected_sha256` (M03).

    The file is read once; the fingerprint is computed over those bytes and, if equal, the same bytes are deserialized. Otherwise ``ArtifactMismatchError`` is
    raised before any byte reaches ``pickle``. A fingerprint that is not 64 lower-case hexadecimal digits is a ``ValueError`` and the file is not even read.
    """
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise ValueError("la huella esperada del artefacto debe ser un SHA-256 de 64 dígitos hexadecimales en minúsculas")
    data = Path(path).read_bytes()
    actual = artifact_sha256(data)
    if not hmac.compare_digest(actual, expected_sha256):
        logger.error("M03: artifact %s has sha256 %s and %s was registered; not loaded", Path(path).name, actual, expected_sha256)
        raise ArtifactMismatchError(
            f"M03: el artefacto {Path(path).name} tiene la huella {actual} y la registrada es {expected_sha256}: no se deserializó. "
            "Se restaura el archivo registrado o se vuelve a entrenar desde el lanzamiento (docs/runbook.md, R6)"
        )
    return pickle.loads(data)  # noqa: S301 - only bytes whose SHA-256 is the registered one (D30)
