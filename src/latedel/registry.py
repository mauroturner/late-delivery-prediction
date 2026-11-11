"""The frozen model: its artifact, loaded only when the bytes still have the registered SHA-256 (M03), and the seal of the launch and of the test (D17, D30).

**Artifact.** ``pickle`` runs code when it loads, so a file that someone replaced must never reach it. ``load_artifact`` reads the bytes of the file **once**,
computes their SHA-256, compares it with the one registered, and only then hands *those same bytes* to ``pickle.loads``: there is no window between checking and
loading, and nothing is unpickled from a file whose fingerprint is not the registered one. The fingerprint protects against a file that was replaced or damaged; it
does not make ``pickle`` safe against someone who can rewrite the file and the registry together (decision D30).

**Seal.** ``reports/model_seal.json`` is versioned and written in two parts, each once: ``launch`` (the configuration of the model, the threshold, the costs and
the fingerprint of the scores of V2) when the model is trained, and ``test`` (the criteria and metrics) at the first evaluation of the test. ``config_sha256``
is the SHA-256 of the canonical JSON of ``launch`` without ``run_key``, ``valid_v2_scores_sha256`` and itself. A run with another configuration than the sealed
one (another threshold, other costs, other variables, another model) is ``post_seal``: it is reported apart and never replaces what was sealed (M05). A sealed
file is never rewritten with other values; a rebuild with the same configuration that does not reproduce the sealed values fails (runbook R7).

Some keys that ``config_sha256`` does not hash change values of the part of the test: the evaluation applies them, not the launch (the grids of the threshold and
of the sensitivity, the bootstrap, the bins of the calibration, the noon cut of B5). ``unhashed_inputs`` names them for each sealed value. A test whose every value
that only the fingerprinted configuration decides is reproduced, and that differs only where such a key acts, is another configuration and is ``post_seal`` too; a
difference anywhere else is a failed reproduction (B4 of the evaluation of ``eef9bea``, decision D89). ``config_sha256`` and the seal stay as they are.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import logging
import os
import pickle
import re
import tempfile
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np
import pandas as pd

from latedel.config import PipelineConfig
from latedel.files import write_atomic
from latedel.model import Candidate

logger = logging.getLogger(__name__)

PICKLE_PROTOCOL = 5
SEAL_VERSION = 1
SEAL_DECIMALS = 6                     # a repetition must reproduce the sealed values rounded to this many decimals
_SHA256 = re.compile(r"[0-9a-f]{64}")
LAUNCH_KEYS = (
    "run_key", "as_of", "source_sha256", "model_version", "feature_set", "params", "calibration", "threshold", "costs", "split", "criteria", "valid_v2_scores_sha256",
    "config_sha256",
)
UNHASHED_LAUNCH_KEYS = ("run_key", "valid_v2_scores_sha256", "config_sha256")   # what config_sha256 leaves out
TEST_KEYS = ("run_key", "as_of", "config_sha256", "criteria", "metrics")


class ArtifactMismatchError(RuntimeError):
    """M03: the artifact does not have the registered SHA-256; it was not deserialized (docs/runbook.md, R6)."""


class SealError(RuntimeError):
    """The seal file cannot be read as a seal, or what is asked of it is not allowed."""


class SealMismatchError(SealError):
    """A repetition with the sealed configuration did not reproduce the sealed values (docs/runbook.md, R7)."""


# --- the artifact --------------------------------------------------------------------------------------------------------------------------------


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


# --- the fingerprint of the configuration --------------------------------------------------------------------------------------------------------


class HasChosen(Protocol):
    chosen: Candidate


def canonical_sha256(document: Any) -> str:
    """SHA-256 of the canonical JSON of `document` (sorted keys, no spaces, ASCII)."""
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def config_fingerprint(cfg: PipelineConfig, selection: HasChosen, threshold: float, *, source_sha256: str) -> tuple[dict, str]:
    """``(body, config_sha256)``: what the seal of the launch says about the configuration, and the SHA-256 of that body.

    The body has the calculation date of the launch, the source, the model version, the feature set and the parameters of the selected candidate, the
    calibration, the threshold, the costs, the dates of the split and the preregistered criteria. It leaves out the run, the fingerprint of the scores of V2 and
    the fingerprint itself, so that the same configuration gives the same fingerprint in any run.
    """
    chosen = selection.chosen
    split = {key: (value.isoformat() if isinstance(value, date) else value) for key, value in vars(cfg.split).items()}
    body = {
        "as_of": cfg.deliveries.launch_date.isoformat(), "source_sha256": source_sha256, "model_version": cfg.model.model_version, "feature_set": chosen.feature_set,
        "params": {
            "learning_rate": chosen.learning_rate, "max_leaf_nodes": chosen.max_leaf_nodes, "min_samples_leaf": chosen.min_samples_leaf,
            "l2_regularization": cfg.model.l2_regularization, "max_iter": cfg.model.max_iter,
        },
        "calibration": cfg.model.calibration, "threshold": float(threshold),
        "costs": {"alert_cost": cfg.costs.alert_cost, "late_cost": cfg.costs.late_cost, "effect": cfg.costs.effect},
        "split": split, "criteria": dict(vars(cfg.criteria)),
    }
    return body, canonical_sha256(body)


def valid_v2_scores_fingerprint(valid_v2: pd.DataFrame) -> str:
    """SHA-256 of the risks of V2 rounded to 6 decimals, one ``order_id:risk`` line per order, sorted by ``order_id``."""
    ordered = valid_v2.sort_values("order_id")
    text = "".join(f"{int(i)}:{float(r):.6f}\n" for i, r in zip(ordered["order_id"], ordered["risk"], strict=True))
    return hashlib.sha256(text.encode("ascii")).hexdigest()


# --- the seal ------------------------------------------------------------------------------------------------------------------------------------


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def read_seal(seal_path: Path) -> dict | None:
    """The seal at `seal_path`, or None if there is no file. A file that is not a seal is a ``SealError``: it is never taken for an absent one."""
    path = Path(seal_path)
    if not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SealError(f"el sello {path.name} no se puede leer: {error}") from error
    if not isinstance(document, dict) or document.get("version") != SEAL_VERSION or not set(document) <= {"version", "launch", "test"}:
        raise SealError(f"el sello {path.name} no tiene la forma de la versión {SEAL_VERSION}")
    launch = document.get("launch")
    if launch is None:
        raise SealError(f"el sello {path.name} no tiene la parte del lanzamiento")
    if not isinstance(launch, dict) or not _is_sha256(launch.get("config_sha256")) or not _is_sha256(launch.get("valid_v2_scores_sha256")):
        raise SealError(f"la parte del lanzamiento del sello {path.name} no tiene sus huellas")
    test = document.get("test")
    if test is not None and (not isinstance(test, dict) or not _is_sha256(test.get("config_sha256"))):
        raise SealError(f"la parte de la prueba del sello {path.name} no tiene su huella de configuración")
    return document


def seal_state(seal_path: Path, config_sha256: str) -> Literal["none", "match", "post_seal"]:
    """``none`` if there is no seal, ``match`` if its configuration is `config_sha256` and ``post_seal`` if it is another one (M05)."""
    document = read_seal(seal_path)
    if document is None:
        return "none"
    return "match" if document["launch"]["config_sha256"] == config_sha256 else "post_seal"


def _dump(document: dict) -> str:
    return json.dumps(document, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


# The mode of the seal file, the same one ``tempfile.mkstemp`` gives the temporary file of the main path: for its owner only. Enforced where the mode bits are the access
# control (POSIX). On Windows ``os.open`` takes only the read-only bit of the mode, so the file gets the access list of its folder, on both paths alike (H4, R1-001);
# the creation of a seal logs that caveat where it applies (``_say_who_can_read``, H6).
SEAL_MODE = 0o600

# What the system answers to a hard link on a file system that has none (FAT and exFAT, some network shares). Any other error of ``os.link`` is a real error.
_NO_HARD_LINKS = frozenset({errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.ENOSYS})
_WINDOWS_NO_HARD_LINKS = frozenset({1, 50})      # ERROR_INVALID_FUNCTION and ERROR_NOT_SUPPORTED


def _lacks_hard_links(error: OSError) -> bool:
    return error.errno in _NO_HARD_LINKS or getattr(error, "winerror", None) in _WINDOWS_NO_HARD_LINKS


def _mode_is_access_control() -> bool:
    """True where the mode bits of a file decide who can read it (POSIX); on Windows ``os.open`` and ``mkstemp`` take only the read-only bit, and the access list of the
    folder decides."""
    return os.name == "posix"


def _say_who_can_read(path: Path) -> None:
    """The caveat of ``SEAL_MODE`` made observable (review of H4, R1-001): where the mode is not the access control, the creation of a seal says so, with the folder whose
    access list decides who reads it."""
    if not _mode_is_access_control():
        logger.warning(
            "seal %s: the owner-only mode 0o%o is not enforced on this system (os.name %s); who can read it is decided by the access list of the folder %s (runbook R7)",
            path.name, SEAL_MODE, os.name, path.parent,
        )


def _create_exclusive(path: Path, data: bytes) -> bool:
    """Create `path` with `data` only if it does not exist, all or nothing: True if it was created, False if a file was already there.

    The data is written to a temporary file and hard-linked to `path`, which fails if `path` exists and never shows half a file. On a file system without hard links
    the creation itself is the check (``O_CREAT | O_EXCL``, see ``_create_without_link``); an error of ``os.link`` that is not the lack of hard links is raised.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        try:
            os.link(name, path)             # fails if `path` exists, and `path` never shows half a file
            _say_who_can_read(path)
            return True
        except FileExistsError:
            return False
        except OSError as error:
            if not _lacks_hard_links(error):
                raise
        created = _create_without_link(path, data)
        if created:
            _say_who_can_read(path)
        return created
    finally:
        Path(name).unlink(missing_ok=True)


def _create_without_link(path: Path, data: bytes) -> bool:
    """The creation for a file system without hard links: the exclusive create is the check, so a seal that another process writes at any moment is never replaced.

    Unlike the hard link, the file exists while it is being written; if the write fails the half file is removed, and a reader that looks in between gets
    ``SealError`` (a seal that cannot be read is never taken for an absent one), not a wrong seal.
    """
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), SEAL_MODE)
    except FileExistsError:
        return False
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(descriptor, view) :]
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        Path(path).unlink(missing_ok=True)
        raise
    os.close(descriptor)
    return True


def _check_launch(launch: Mapping[str, Any]) -> None:
    missing = [key for key in LAUNCH_KEYS if key not in launch]
    if missing or set(launch) - set(LAUNCH_KEYS):
        raise SealError(f"la parte del lanzamiento debe tener exactamente las claves {list(LAUNCH_KEYS)}; faltan {missing}, sobran {sorted(set(launch) - set(LAUNCH_KEYS))}")
    if not _is_sha256(launch["config_sha256"]) or not _is_sha256(launch["valid_v2_scores_sha256"]):
        raise SealError("las huellas de la parte del lanzamiento deben ser SHA-256 de 64 dígitos hexadecimales")
    body = {key: value for key, value in launch.items() if key not in UNHASHED_LAUNCH_KEYS}
    if canonical_sha256(body) != launch["config_sha256"]:
        raise SealError("config_sha256 no es la huella del contenido de la parte del lanzamiento")


def write_launch_seal(seal_path: Path, launch: Mapping[str, Any]) -> Literal["written", "unchanged", "post_seal"]:
    """Write the part of the launch of the seal, once. Returns what happened.

    ``written``: there was no seal and now it has `launch`. ``unchanged``: the seal already has this configuration and the same fingerprint of the scores of V2
    (nothing is rewritten). ``post_seal``: the seal has another configuration, which is left as it is. The same configuration with other scores of V2 is a
    ``SealMismatchError`` (runbook R7).
    """
    _check_launch(launch)
    path = Path(seal_path)
    document = {"version": SEAL_VERSION, "launch": dict(launch)}
    if read_seal(path) is None and _create_exclusive(path, _dump(document).encode("utf-8")):
        logger.info("seal: launch part written to %s (config %s)", path.name, launch["config_sha256"][:12])
        return "written"
    sealed = read_seal(path)["launch"]
    if sealed["config_sha256"] != launch["config_sha256"]:
        logger.warning("seal: the configuration %s is not the sealed one (%s): post_seal", launch["config_sha256"][:12], sealed["config_sha256"][:12])
        return "post_seal"
    if sealed["valid_v2_scores_sha256"] != launch["valid_v2_scores_sha256"]:
        raise SealMismatchError(
            f"M05: la configuración es la sellada pero los puntajes de V2 no se reproducen: la huella sellada es {sealed['valid_v2_scores_sha256']} y esta corrida "
            f"da {launch['valid_v2_scores_sha256']} (docs/runbook.md, R7)"
        )
    return "unchanged"


# --- what config_sha256 does not cover --------------------------------------------------------------------------------------------------------------------

_THRESHOLD_GRID = ("[costs] threshold_min", "[costs] threshold_max", "[costs] threshold_step")    # B5's own threshold and the thresholds of the sensitivity, on V2
_SENSITIVITY = ("[costs] sensitivity_min", "[costs] sensitivity_max", "[costs] sensitivity_step")
_BOOTSTRAP = ("[evaluation] bootstrap_resamples", "[evaluation] bootstrap_seed")
_BINS = ("[evaluation] calibration_bins",)
_NOON = ("[features] noon_hour",)
_UNHASHED_CRITERIA: dict[str, tuple[str, ...]] = {
    "C02.ci_low": _BOOTSTRAP, "C02.ci_high": _BOOTSTRAP, "C02.passed": _BOOTSTRAP,
    "C03.value": _NOON, "C03.ci_low": _NOON + _BOOTSTRAP, "C03.ci_high": _NOON + _BOOTSTRAP, "C03.passed": _NOON + _BOOTSTRAP,
    "C05.baseline_value": _NOON + _THRESHOLD_GRID, "C05.passed": _NOON + _THRESHOLD_GRID,
    "C06.value": _BINS, "C06.passed": _BINS,
    "C09.value": _SENSITIVITY + _THRESHOLD_GRID, "C09.baseline_value": _SENSITIVITY, "C09.passed": _SENSITIVITY + _THRESHOLD_GRID,
}
_B5_CUT = ("threshold", "flagged_share", "precision", "recall", "nb_total", "nb_per_1000")       # what B5's own threshold changes
_SAME_FOR_EVERY_METHOD = ("orders", "late_rate", "topk_k")                                        # the block and the k alerts of B2: not B5's


def unhashed_inputs(path: str) -> tuple[str, ...]:
    """The keys outside ``config_sha256`` that change the sealed value at `path` (``C03.ci_low``, ``b5_mode_noon.auc``): empty for a value that only the fingerprinted
    configuration, the data and the model decide, where a difference is a failed reproduction (runbook R7)."""
    if path in _UNHASHED_CRITERIA:
        return _UNHASHED_CRITERIA[path]
    method, _, metric = path.partition(".")
    if method == "b5_mode_noon" and metric not in _SAME_FOR_EVERY_METHOD:
        return _NOON + (_BINS if metric == "ece" else ()) + (_THRESHOLD_GRID if metric in _B5_CUT else ())
    return _BINS if metric == "ece" else ()


_ABSENT = object()


def _flat_test(part: Mapping[str, Any]) -> dict[str, Any]:
    """The values of a part of the test by path: ``<criterion>.<field>`` and ``<method>.<metric>``, rounded to ``SEAL_DECIMALS``."""
    flat: dict[str, Any] = {}
    for entry in part.get("criteria") or []:
        flat.update({f"{entry.get('id')}.{key}": value for key, value in entry.items() if key != "id"})
    for method, values in (part.get("metrics") or {}).items():
        flat.update({f"{method}.{metric}": value for metric, value in (values or {}).items()})
    return _rounded(flat)


def differing_values(sealed: Mapping[str, Any], test: Mapping[str, Any]) -> list[str]:
    """The paths whose values differ, to 6 decimals, between two parts of the test (a value present in one only differs too)."""
    a, b = _flat_test(sealed), _flat_test(test)
    return sorted(path for path in set(a) | set(b) if a.get(path, _ABSENT) != b.get(path, _ABSENT))


def _rounded(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        return None if np.isnan(value) else round(value, SEAL_DECIMALS)
    if isinstance(value, Mapping):
        return {str(key): _rounded(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rounded(item) for item in value]
    raise SealError(f"un valor del sello no es JSON: {type(value).__name__}")


def write_test_seal(seal_path: Path, test: Mapping[str, Any]) -> Literal["written", "unchanged", "post_seal"]:
    """Add the part of the test to the seal, once. Returns what happened.

    ``written``: the seal had only the launch and now has `test` too, the launch exactly as it was. ``unchanged``: it already has a test part whose criteria and
    metrics, rounded to 6 decimals, are these. ``post_seal``: `test` was computed with another configuration than the sealed one (another ``config_sha256``, or the
    same one with values that differ only where a key outside it acts, ``unhashed_inputs``); the seal is left as it is. Any other difference is a
    ``SealMismatchError`` that names the values (runbook R7).
    """
    missing = [key for key in TEST_KEYS if key not in test]
    if missing or set(test) - set(TEST_KEYS):
        raise SealError(f"la parte de la prueba debe tener exactamente las claves {list(TEST_KEYS)}; faltan {missing}, sobran {sorted(set(test) - set(TEST_KEYS))}")
    if not _is_sha256(test["config_sha256"]):
        raise SealError("config_sha256 de la parte de la prueba debe ser un SHA-256 de 64 dígitos hexadecimales")
    _rounded(test)                            # refuses a value that is not JSON before anything is compared or written
    path = Path(seal_path)
    document = read_seal(path)
    if document is None:
        raise SealError("no hay sello: la parte de la prueba se escribe después de la del lanzamiento")
    if test["config_sha256"] != document["launch"]["config_sha256"]:
        logger.warning("seal: the test was evaluated with another configuration than the sealed one: post_seal")
        return "post_seal"
    sealed = document.get("test")
    if sealed is not None:
        differ = differing_values(sealed, test)
        if not differ:
            return "unchanged"
        failed = [path for path in differ if not unhashed_inputs(path)]
        if failed:
            raise SealMismatchError(
                "la evaluación de la prueba con la configuración sellada no reproduce los valores sellados, redondeados a 6 decimales: "
                f"{', '.join(failed[:6])}{' y otros' if len(failed) > 6 else ''} (docs/runbook.md, R7)"
            )
        keys = list(dict.fromkeys(key for path in differ for key in unhashed_inputs(path)))
        logger.warning(
            "seal: the test differs from the sealed one only in %s, which %s change and config_sha256 does not cover: another configuration, not a failed "
            "reproduction; stored as post_seal and the seal is left as it is", ", ".join(differ), ", ".join(keys),
        )
        return "post_seal"
    write_atomic(path, _dump({**document, "test": dict(test)}))
    # The flow has one writer (D61), but the read above, the decision and this write are not one step: a second writer that started from the same read and wrote another
    # result would have replaced this one without a word. Read it back: if the file does not say what was written, say so.
    kept = (read_seal(path) or {}).get("test") or {}
    if _rounded({k: kept.get(k) for k in ("criteria", "metrics")}) != _rounded({k: test[k] for k in ("criteria", "metrics")}):
        logger.error("seal: the test part written by run %s was replaced by the one of run %s with other values (runbook R7)", test["run_key"], kept.get("run_key"))
        raise SealMismatchError("otra escritura reemplazó la parte de la prueba mientras se escribía la de esta corrida: lo que consta en el sello no es lo que esta corrida calculó (docs/runbook.md, R7)")
    logger.info("seal: test part written to %s", path.name)
    return "written"
