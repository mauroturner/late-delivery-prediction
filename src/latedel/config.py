"""Pipeline configuration: one TOML file, typed, immutable and checked key by key.

``config/pipeline.toml`` holds every default. A file may start from another one with a top-level
``extends = "<path>"`` (``config/pipeline.quick.toml`` does). Every relative path in the configuration
(``[paths]`` and ``source.local_path``) is resolved against the project folder, never against the working
directory (decision D24): ``$LATEDEL_HOME`` when it is set, otherwise the parent of the folder that holds the
main configuration file. A relative ``$LATEDEL_CONFIG`` is taken from the project folder too: ``$LATEDEL_HOME`` when
it is set, otherwise the folder of this package's project (A2 of the evaluation of ``eef9bea``, decision D90).

Anything unknown, missing, mistyped or inconsistent is a ``ValueError`` that names the key. That includes a key the
chosen ``source.mode`` would ignore: an option that is accepted and silently not used is the defect that projects 01 and
02 both had, so here it is refused.
"""

from __future__ import annotations

import codecs
import dataclasses
import math
import os
import re
import tomllib
import types
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = DEFAULT_PROJECT_ROOT / "config" / "pipeline.toml"

ENV_CONFIG = "LATEDEL_CONFIG"
ENV_HOME = "LATEDEL_HOME"
FEATURE_SET_NAMES = ("f0", "f1", "f2")

_SHA256 = re.compile(r"[0-9a-f]{64}")
# a folder name that is the same on every system: it starts with a letter, a digit, "_" or "-" (so "." and ".." are not names, and nothing is hidden),
# has dots only between other characters, and does not end in a dot (Windows drops it, so "v1." and "v1" would be one folder)
_FOLDER_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*")
MAX_SEED = 2**32 - 1   # the largest seed that numpy's legacy generators and scikit-learn accept
_HOST_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?")
_TYPE_NAMES = {
    bool: "verdadero o falso", int: "un entero", float: "un número", str: "un texto", date: "una fecha (AAAA-MM-DD)", Path: "una ruta",
}


@dataclass(frozen=True)
class SourceConfig:
    mode: str
    url: str
    allowed_hosts: tuple[str, ...]
    max_redirects: int
    local_path: Path | None
    accepted_sha256: str
    file_name: str
    encoding: str
    max_bytes: int
    chunk_bytes: int
    timeout_s: int
    max_seconds: float


@dataclass(frozen=True)
class DeliveriesConfig:
    first_order_date: date
    launch_date: date
    last_order_date: date
    close_date: date


@dataclass(frozen=True)
class SplitConfig:
    train_start: date
    train_end: date
    purge_days: int
    valid_start: date
    valid_v2_start: date
    valid_end: date
    test_start: date
    test_end: date
    post_break_start: date
    post_break_end: date


@dataclass(frozen=True)
class FeaturesConfig:
    candidate_sets: tuple[str, ...]
    noon_hour: int


@dataclass(frozen=True)
class ModelConfig:
    learning_rate: tuple[float, ...]
    max_leaf_nodes: tuple[int, ...]
    min_samples_leaf: tuple[int, ...]
    l2_regularization: float
    max_iter: int
    random_state: int
    calibration: str
    model_version: str


@dataclass(frozen=True)
class CostsConfig:
    alert_cost: float
    late_cost: float
    effect: float
    threshold_min: float
    threshold_max: float
    threshold_step: float
    sensitivity_min: float
    sensitivity_max: float
    sensitivity_step: float


@dataclass(frozen=True)
class EvaluationConfig:
    bootstrap_resamples: int
    bootstrap_seed: int
    calibration_bins: int
    roc_points: int
    importance_repeats: int


@dataclass(frozen=True)
class CriteriaConfig:
    c06_max_ece: float
    c06_max_bias: float
    leak_tripwire_auc: float


@dataclass(frozen=True)
class SiteConfig:
    max_total_bytes: int
    min_cell_orders: int


@dataclass(frozen=True)
class PathsConfig:
    raw_dir: Path
    interim_dir: Path
    warehouse: Path
    models_dir: Path
    outputs_dir: Path
    site_dir: Path
    reports_dir: Path
    seal: Path
    dashboard_html: Path
    readme: Path
    figures_dir: Path


@dataclass(frozen=True)
class PipelineConfig:
    source: SourceConfig
    deliveries: DeliveriesConfig
    split: SplitConfig
    features: FeaturesConfig
    model: ModelConfig
    costs: CostsConfig
    evaluation: EvaluationConfig
    criteria: CriteriaConfig
    site: SiteConfig
    paths: PathsConfig
    project_root: Path


# --- hosts: shared by the configuration check and the download (V02) ------------------------------------------------


def url_host(url: str) -> str | None:
    """The host of an ``http(s)`` URL, lower case and without a trailing dot, or None if the URL is not one the download may follow.

    A URL with user information (``user@host``), a backslash, a space or a control character is refused as a whole: those are the
    spellings in which two parsers can read different hosts from the same text, so the host that was checked would not be the
    host that is connected to.
    """
    if not isinstance(url, str) or not url or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F or ch == "\\" for ch in url):
        return None
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or "@" in parts.netloc or not parts.hostname:
        return None
    del port
    return parts.hostname.lower().rstrip(".")


def host_allowed(host: str, allowed_hosts: Sequence[str]) -> bool:
    """Whether `host` is one of `allowed_hosts`: an exact name, or a suffix when the entry starts with a dot (``.amazonaws.com``)."""
    host = host.lower().rstrip(".")
    if not host:
        return False
    for entry in allowed_hosts:
        entry = entry.lower()
        if entry.startswith("."):
            if host.endswith(entry) and len(host) > len(entry):
                return True
        elif host == entry:
            return True
    return False


# --- loading --------------------------------------------------------------------------------------------------------


def _is_optional_path(annotation: Any) -> bool:
    origin = get_origin(annotation)
    return origin in (Union, types.UnionType) and set(get_args(annotation)) == {Path, type(None)}


def _resolve(value: str, root: Path, where: str) -> Path:
    if not value.strip():
        raise ValueError(f"{where}: se esperaba una ruta, se recibió un texto vacío")
    path = Path(value)
    if path.is_absolute():
        return path  # an absolute path is explicit: it may point anywhere on purpose
    joined = Path(os.path.normpath(root / path))
    base = Path(os.path.normpath(root))
    if joined != base and base not in joined.parents:
        raise ValueError(
            f"{where}: la ruta relativa {value!r} sale de la carpeta del proyecto ({base}); "
            "use una ruta absoluta si el destino está fuera a propósito"
        )
    return joined


def _coerce(value: Any, annotation: Any, where: str, root: Path) -> Any:
    """Convert a TOML value to the annotated type or raise ValueError naming `where`."""
    if _is_optional_path(annotation):
        if isinstance(value, str):
            return _resolve(value, root, where) if value.strip() else None
        raise ValueError(f"{where}: se esperaba una ruta escrita como texto, se recibió {value!r}")
    origin = get_origin(annotation)
    if origin is tuple:
        (element, _ellipsis) = get_args(annotation)
        if not isinstance(value, list):
            raise ValueError(f"{where}: se esperaba una lista, se recibió {type(value).__name__}")
        return tuple(_coerce(v, element, where, root) for v in value)
    if annotation is bool:
        ok = isinstance(value, bool)
    elif annotation is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif annotation is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        value = float(value) if ok else value
    elif annotation is str:
        ok = isinstance(value, str)
    elif annotation is date:
        ok = isinstance(value, date) and not isinstance(value, datetime)
    elif annotation is Path:
        if isinstance(value, str):
            return _resolve(value, root, where)
        ok = False
    else:  # pragma: no cover - only reachable by adding an unsupported annotation
        raise TypeError(f"{where}: unsupported annotation {annotation!r}")
    if not ok:
        raise ValueError(f"{where}: se esperaba {_TYPE_NAMES.get(annotation, annotation)}, se recibió {value!r}")
    return value


def _build_section(cls: type, data: Any, name: str, root: Path) -> Any:
    if not isinstance(data, dict):
        raise ValueError(f"[{name}]: se esperaba una tabla, se recibió {type(data).__name__}")
    hints = get_type_hints(cls)
    fields = [f.name for f in dataclasses.fields(cls)]
    unknown = sorted(set(data) - set(fields))
    if unknown:
        raise ValueError(f"[{name}]: clave desconocida {unknown[0]!r}")
    missing = [f for f in fields if f not in data]
    if missing:
        raise ValueError(f"[{name}]: falta la clave {missing[0]!r}")
    return cls(**{f: _coerce(data[f], hints[f], f"[{name}] {f}", root) for f in fields})


# --- consistency checks ---------------------------------------------------------------------------------------------


def _positive(value: float, where: str) -> None:
    if not value > 0:
        raise ValueError(f"{where}: se esperaba un valor mayor que 0, se recibió {value!r}")


def _seed(value: int, where: str) -> None:
    if not 0 <= value <= MAX_SEED:
        raise ValueError(f"{where}: se esperaba un entero de 0 a {MAX_SEED}, se recibió {value!r}")


def _fraction(value: float, where: str, *, closed_high: bool = False) -> None:
    if not (0 < value <= 1 if closed_high else 0 < value < 1):
        interval = "(0, 1]" if closed_high else "(0, 1)"
        raise ValueError(f"{where}: se esperaba un valor en {interval}, se recibió {value!r}")


def _check_host_entry(entry: str) -> None:
    where = "[source] allowed_hosts"
    if entry != entry.lower() or not entry.strip():
        raise ValueError(f"{where}: se esperaba un nombre de host en minúscula y sin espacios, se recibió {entry!r}")
    name = entry[1:] if entry.startswith(".") else entry
    labels = name.split(".")
    if not all(_HOST_LABEL.fullmatch(label) for label in labels):
        raise ValueError(f"{where}: {entry!r} no es un nombre de host (sin esquema, puerto ni ruta)")
    if entry.startswith(".") and len(labels) < 2:
        raise ValueError(f"{where}: el sufijo {entry!r} es demasiado amplio; use al menos dos etiquetas, como '.amazonaws.com'")


def _check_source(source: SourceConfig) -> None:
    if source.mode not in ("http", "local"):
        raise ValueError(f"[source] mode: se esperaba 'http' o 'local', se recibió {source.mode!r}")
    if not source.allowed_hosts:
        raise ValueError("[source] allowed_hosts: se esperaba al menos un host permitido")
    for entry in source.allowed_hosts:
        _check_host_entry(entry)
    if source.mode == "local":
        if source.local_path is None:
            raise ValueError("[source] local_path: con mode = 'local' hace falta la ruta del CSV (el modo rápido usa data/quick/dataco_fixture.csv)")
        if source.accepted_sha256:
            raise ValueError("[source] accepted_sha256: con mode = 'local' se registra la primera huella que se ve y esta clave se ignoraría; déjela vacía")
        if source.url:
            raise ValueError(
                "[source] url: con mode = 'local' se lee local_path y esta clave se ignoraría; déjela vacía (el archivo de modo rápido debe decir "
                'url = "" para no heredar la dirección del origen real)'
            )
    else:
        if not source.url.startswith("https://"):
            raise ValueError(f"[source] url: se esperaba una URL https://, se recibió {source.url!r}")
        host = url_host(source.url)
        if host is None:
            raise ValueError(f"[source] url: no es una dirección que la descarga pueda seguir (sin usuario, espacios ni barras invertidas): {source.url!r}")
        if not host_allowed(host, source.allowed_hosts):
            raise ValueError(f"[source] url: el host {host!r} no está en allowed_hosts {list(source.allowed_hosts)}")
        if not _SHA256.fullmatch(source.accepted_sha256):
            raise ValueError("[source] accepted_sha256: con mode = 'http' hacen falta los 64 caracteres hexadecimales en minúscula del CSV aceptado")
        if source.local_path is not None:
            raise ValueError("[source] local_path: con mode = 'http' esta clave se ignoraría; déjela vacía")
    if source.max_redirects < 0:
        raise ValueError(f"[source] max_redirects: se esperaba 0 o más, se recibió {source.max_redirects!r}")
    name = source.file_name
    if not name or "/" in name or "\\" in name or name in (".", "..") or Path(name).is_absolute():
        raise ValueError(f"[source] file_name: se esperaba un nombre de archivo sin ruta, se recibió {name!r}")
    try:
        codecs.lookup(source.encoding)
    except LookupError as error:
        raise ValueError(f"[source] encoding: {source.encoding!r} no es una codificación conocida") from error
    _positive(source.max_bytes, "[source] max_bytes")
    _positive(source.chunk_bytes, "[source] chunk_bytes")
    _positive(source.timeout_s, "[source] timeout_s")
    if not math.isfinite(source.max_seconds) or source.max_seconds <= 0:
        raise ValueError(f"[source] max_seconds: se esperaba un número de segundos mayor que 0 y finito (el tiempo máximo de toda la descarga), se recibió {source.max_seconds!r}")


def _check_dates(cfg: PipelineConfig) -> None:
    d, s = cfg.deliveries, cfg.split
    if not d.first_order_date < d.launch_date < d.last_order_date < d.close_date:
        raise ValueError("[deliveries]: first_order_date, launch_date, last_order_date y close_date deben estar en orden creciente")
    _positive(s.purge_days, "[split] purge_days")
    gap = timedelta(days=s.purge_days)
    ordered = (
        ("train_start", s.train_start, "train_end", s.train_end),
        ("valid_start", s.valid_start, "valid_v2_start", s.valid_v2_start),
        ("valid_v2_start", s.valid_v2_start, "valid_end", s.valid_end),
        ("test_start", s.test_start, "test_end", s.test_end),
        ("post_break_start", s.post_break_start, "post_break_end", s.post_break_end),
    )
    for first, a, second, b in ordered:
        if not a < b if (first, second) == ("valid_start", "valid_v2_start") else not a <= b:
            raise ValueError(f"[split] {first}: se esperaba que {first} ({a}) fuera anterior a {second} ({b}) y los bloques en orden")
    if s.valid_start != s.train_end + gap + timedelta(days=1):
        raise ValueError(
            f"[split] valid_start: la purga entre el entrenamiento y la validación es de purge_days = {s.purge_days} días, "
            f"así que valid_start debía ser {s.train_end + gap + timedelta(days=1)} y es {s.valid_start}"
        )
    if s.test_start != s.valid_end + gap + timedelta(days=1):
        raise ValueError(
            f"[split] test_start: la purga entre la validación y la prueba es de purge_days = {s.purge_days} días, "
            f"así que test_start debía ser {s.valid_end + gap + timedelta(days=1)} y es {s.test_start}"
        )
    if s.post_break_start != s.test_end + timedelta(days=1):
        raise ValueError(f"[split] post_break_start: debía ser el día siguiente a test_end ({s.test_end + timedelta(days=1)}) y es {s.post_break_start}")
    if s.train_start < d.first_order_date:
        raise ValueError(f"[split] train_start: no puede ser anterior a first_order_date de [deliveries] ({d.first_order_date})")
    if d.launch_date != s.test_start - timedelta(days=1):
        raise ValueError(f"[deliveries] launch_date: debía ser el día anterior al inicio de la prueba ({s.test_start - timedelta(days=1)}) y es {d.launch_date}")
    if s.post_break_end > d.last_order_date:
        raise ValueError("[split] post_break_end: no puede ser posterior a last_order_date de [deliveries]")


def _check(cfg: PipelineConfig) -> None:
    _check_source(cfg.source)
    _check_dates(cfg)

    f = cfg.features
    if not f.candidate_sets or len(set(f.candidate_sets)) != len(f.candidate_sets):
        raise ValueError(f"[features] candidate_sets: se esperaba una lista no vacía y sin repetidos, se recibió {list(f.candidate_sets)}")
    for name in f.candidate_sets:
        if name not in FEATURE_SET_NAMES:
            raise ValueError(f"[features] candidate_sets: {name!r} no es un conjunto conocido ({', '.join(FEATURE_SET_NAMES)})")
    if not 1 <= f.noon_hour <= 23:
        raise ValueError(f"[features] noon_hour: se esperaba una hora de 1 a 23, se recibió {f.noon_hour!r}")

    m = cfg.model
    for key in ("learning_rate", "max_leaf_nodes", "min_samples_leaf"):
        grid = getattr(m, key)
        where = f"[model] {key}"
        if not grid or len(set(grid)) != len(grid):
            raise ValueError(f"{where}: se esperaba una lista no vacía y sin repetidos, se recibió {list(grid)}")
        for value in grid:
            _positive(value, where)
    if any(v < 2 for v in m.max_leaf_nodes):
        raise ValueError(f"[model] max_leaf_nodes: se esperaban valores de 2 o más, se recibió {list(m.max_leaf_nodes)}")
    if m.l2_regularization < 0:
        raise ValueError(f"[model] l2_regularization: se esperaba 0 o más, se recibió {m.l2_regularization!r}")
    _positive(m.max_iter, "[model] max_iter")
    _seed(m.random_state, "[model] random_state")
    if m.calibration != "isotonic":
        raise ValueError(f"[model] calibration: el único método implementado es 'isotonic' (D13), se recibió {m.calibration!r}")
    if not _FOLDER_NAME.fullmatch(m.model_version) or m.model_version.endswith("."):
        raise ValueError(
            f"[model] model_version: se esperaba un nombre de carpeta simple (letras, números, «_», «-» y puntos entre otros caracteres; sin espacios, separadores "
            f"de ruta ni punto al empezar o al terminar, y distinto de «.» y «..»), se recibió {m.model_version!r}"
        )

    c = cfg.costs
    for key in ("alert_cost", "late_cost"):
        _positive(getattr(c, key), f"[costs] {key}")
    _fraction(c.effect, "[costs] effect", closed_high=True)
    for key in ("threshold_min", "threshold_max", "sensitivity_min", "sensitivity_max"):
        _fraction(getattr(c, key), f"[costs] {key}")
    if not c.threshold_min < c.threshold_max:
        raise ValueError(f"[costs] threshold_min: se esperaba threshold_min < threshold_max, se recibió {c.threshold_min!r} y {c.threshold_max!r}")
    _positive(c.threshold_step, "[costs] threshold_step")
    if not c.sensitivity_min <= c.sensitivity_max:
        raise ValueError(f"[costs] sensitivity_min: se esperaba sensitivity_min <= sensitivity_max, se recibió {c.sensitivity_min!r} y {c.sensitivity_max!r}")
    _positive(c.sensitivity_step, "[costs] sensitivity_step")

    e = cfg.evaluation
    _positive(e.bootstrap_resamples, "[evaluation] bootstrap_resamples")
    _seed(e.bootstrap_seed, "[evaluation] bootstrap_seed")
    if e.calibration_bins < 2:
        raise ValueError(f"[evaluation] calibration_bins: se esperaba 2 o más, se recibió {e.calibration_bins!r}")
    if e.roc_points < 2:
        raise ValueError(f"[evaluation] roc_points: se esperaba 2 o más, se recibió {e.roc_points!r}")
    _positive(e.importance_repeats, "[evaluation] importance_repeats")

    k = cfg.criteria
    _fraction(k.c06_max_ece, "[criteria] c06_max_ece")
    _fraction(k.c06_max_bias, "[criteria] c06_max_bias")
    if not 0.5 < k.leak_tripwire_auc <= 1:
        raise ValueError(f"[criteria] leak_tripwire_auc: se esperaba un valor en (0,5, 1], se recibió {k.leak_tripwire_auc!r}")

    _positive(cfg.site.max_total_bytes, "[site] max_total_bytes")
    _positive(cfg.site.min_cell_orders, "[site] min_cell_orders")


# --- reading --------------------------------------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], top: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in top.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_toml(path: Path, _seen: tuple[Path, ...] = ()) -> dict[str, Any]:
    """Read a TOML file, following ``extends`` (relative to the file) and merging over its base."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"no se encontró el archivo de configuración: {path}")
    resolved = path.resolve()
    if resolved in _seen:
        raise ValueError(f"los archivos de configuración se extienden entre sí en un ciclo en {path}")
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    parent = data.pop("extends", None)
    if parent is None:
        return data
    if not isinstance(parent, str):
        raise ValueError(f"{path}: extends debe ser una ruta escrita como texto")
    parent_path = Path(parent)
    if not parent_path.is_absolute():
        parent_path = path.parent / parent_path
    return _deep_merge(_read_toml(parent_path, (*_seen, resolved)), data)


def _project_root(config_path: Path) -> Path:
    """``$LATEDEL_HOME``, or the parent of the folder that holds the main configuration file (D24)."""
    home = os.environ.get(ENV_HOME)
    if home:
        root = Path(home).resolve()
        if not root.is_dir():
            raise ValueError(f"{ENV_HOME}: {home!r} no es una carpeta existente")
        return root
    return config_path.resolve().parent.parent


def load_config(path: Path | None = None) -> PipelineConfig:
    """Load the configuration.

    `path` is the main file; without it, ``$LATEDEL_CONFIG`` and then ``config/pipeline.toml`` of the
    project. Relative paths are resolved against the project folder, so the result does not depend on the
    working directory; that includes a relative ``$LATEDEL_CONFIG``, taken from ``$LATEDEL_HOME`` or, without it,
    from this package's project. Anything unknown, missing, mistyped or inconsistent is a ``ValueError`` naming the key.
    """
    if path is None:
        env = os.environ.get(ENV_CONFIG)
        path = Path(env) if env else DEFAULT_CONFIG
        if not path.is_absolute():   # the working directory may be another copy's folder: never read its configuration in silence
            path = Path(os.environ.get(ENV_HOME) or DEFAULT_PROJECT_ROOT) / path
    path = Path(path)
    data = _read_toml(path)
    root = _project_root(path)

    sections = [f.name for f in dataclasses.fields(PipelineConfig) if f.name != "project_root"]
    hints = get_type_hints(PipelineConfig)
    unknown = sorted(set(data) - set(sections))
    if unknown:
        raise ValueError(f"sección desconocida [{unknown[0]}]")
    missing = [s for s in sections if s not in data]
    if missing:
        raise ValueError(f"falta la sección [{missing[0]}]")
    built = {name: _build_section(hints[name], data[name], name, root) for name in sections}
    cfg = PipelineConfig(**built, project_root=root)
    _check(cfg)
    return cfg
