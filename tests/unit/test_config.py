"""Configuration (U01): paths against the project folder, the http mode's guarantees, and every key refused by name (RNF04, AC29, AC30)."""

from __future__ import annotations

import dataclasses
import re
from datetime import date
from pathlib import Path

import pytest

from latedel.config import (
    DEFAULT_CONFIG,
    ENV_CONFIG,
    ENV_HOME,
    PipelineConfig,
    host_allowed,
    load_config,
    url_host,
)

PROJECT = Path(__file__).resolve().parents[2]
HASH = "fa6d022ed437155e1a2f0378710602848703c8a7f203f7ff5d77805bf8480aa6"


def variant(tmp_path: Path, snippet: str = "", replace: tuple[str, str] | None = None, name: str = "pipeline.toml") -> Path:
    """A configuration inside `tmp_path/config/`: the real file with one textual replacement, or an ``extends`` of it plus `snippet`."""
    folder = tmp_path / "config"
    folder.mkdir(exist_ok=True)
    target = folder / name
    if replace is not None:
        text = DEFAULT_CONFIG.read_text(encoding="utf-8")
        assert replace[0] in text, f"{replace[0]!r} is not in the default configuration"
        target.write_text(text.replace(*replace, 1), encoding="utf-8")
    else:
        target.write_text(f'extends = "{DEFAULT_CONFIG.as_posix()}"\n{snippet}', encoding="utf-8")
    return target


def refused(tmp_path: Path, match: str, snippet: str = "", replace: tuple[str, str] | None = None) -> None:
    with pytest.raises(ValueError, match=match):
        load_config(variant(tmp_path, snippet, replace))


# --- the default configuration -------------------------------------------------------------------------------------


def test_default_configuration_has_the_values_of_the_plan(cfg):
    assert cfg.source.mode == "http" and cfg.source.accepted_sha256 == HASH
    assert cfg.source.allowed_hosts == ("data.mendeley.com", "prod-dcd-datasets-public-files-eu-west-1.s3.eu-west-1.amazonaws.com") and cfg.source.max_redirects == 3
    assert cfg.source.max_bytes == 128 * 1024 * 1024 and cfg.source.encoding == "cp1252" and cfg.source.local_path is None
    assert cfg.deliveries.launch_date == date(2017, 1, 7) and cfg.deliveries.close_date == date(2018, 2, 7)
    assert (cfg.split.train_end, cfg.split.valid_start, cfg.split.test_start, cfg.split.post_break_start) == (
        date(2016, 6, 30), date(2016, 7, 8), date(2017, 1, 8), date(2017, 10, 1))
    assert (cfg.costs.alert_cost, cfg.costs.late_cost, cfg.costs.effect) == (5.0, 20.0, 0.5)
    assert cfg.model.learning_rate == (0.05, 0.1) and cfg.model.calibration == "isotonic" and cfg.criteria.leak_tripwire_auc == 0.95
    assert cfg.site.min_cell_orders == 20 and cfg.features.noon_hour == 12 and isinstance(cfg, PipelineConfig)


def test_the_configuration_is_immutable(cfg):
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.costs.alert_cost = 1.0  # type: ignore[misc]


# --- paths (D24, RNF04) --------------------------------------------------------------------------------------------


def test_relative_paths_resolve_against_project_root_from_another_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(PROJECT / "config" / "pipeline.toml")
    assert cfg.project_root == PROJECT
    for field in dataclasses.fields(cfg.paths):
        path = getattr(cfg.paths, field.name)
        assert path.is_absolute() and PROJECT in path.parents, field.name
    assert cfg.paths.warehouse == PROJECT / "data" / "warehouse" / "latedel.sqlite"
    assert cfg.paths.seal == PROJECT / "reports" / "model_seal.json"


def test_quick_configuration_keeps_every_output_under_data_quick(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(PROJECT / "config" / "pipeline.quick.toml")
    quick = PROJECT / "data" / "quick"
    assert cfg.source.mode == "local" and cfg.source.local_path == quick / "dataco_fixture.csv"
    assert cfg.source.url == "" and cfg.source.accepted_sha256 == ""
    for field in dataclasses.fields(cfg.paths):
        assert quick in getattr(cfg.paths, field.name).parents, field.name
    assert cfg.model.learning_rate == (0.1,) and cfg.evaluation.bootstrap_resamples == 200
    assert cfg.deliveries == load_config().deliveries, "the quick run replays the same calendar"


def test_a_relative_path_cannot_leave_the_project_folder(tmp_path):
    refused(tmp_path, r"\[paths\] raw_dir.*sale de la carpeta del proyecto", replace=('raw_dir = "data/raw"', 'raw_dir = "../elsewhere"'))
    refused(tmp_path, r"\[paths\] seal.*sale de la carpeta", replace=('seal = "reports/model_seal.json"', 'seal = "data/../../x.json"'))


def test_a_dotdot_that_stays_inside_the_project_is_normalised_and_an_absolute_path_is_kept(tmp_path):
    path = variant(tmp_path, replace=('raw_dir = "data/raw"', 'raw_dir = "data/quick/../raw"'))
    assert load_config(path).paths.raw_dir == tmp_path / "data" / "raw"
    outside = (tmp_path / "outside").as_posix()
    path = variant(tmp_path, replace=('raw_dir = "data/raw"', f'raw_dir = "{outside}"'))
    assert load_config(path).paths.raw_dir == Path(outside)


def test_an_empty_path_is_refused(tmp_path):
    refused(tmp_path, r"\[paths\] raw_dir.*texto vacío", replace=('raw_dir = "data/raw"', 'raw_dir = ""'))


def test_latedel_home_and_latedel_config_are_honoured(tmp_path, monkeypatch):
    home = tmp_path / "elsewhere"
    home.mkdir()
    monkeypatch.setenv(ENV_HOME, str(home))
    assert load_config(DEFAULT_CONFIG).paths.warehouse == home.resolve() / "data" / "warehouse" / "latedel.sqlite"
    monkeypatch.setenv(ENV_HOME, str(tmp_path / "does-not-exist"))
    with pytest.raises(ValueError, match=ENV_HOME):
        load_config(DEFAULT_CONFIG)
    monkeypatch.delenv(ENV_HOME)
    monkeypatch.setenv(ENV_CONFIG, str(PROJECT / "config" / "pipeline.quick.toml"))
    assert load_config().source.mode == "local"


# --- http mode (RF01, RF02, V01, V02) ------------------------------------------------------------------------------


def test_http_mode_requires_https_url_listed_host_and_hash(tmp_path):
    good = "https://data.mendeley.com/public-files/datasets/8gx2fvg2k6/files/72784be5-36d3-44fe-b75d-0edbf1999f65/file_downloaded"
    assert load_config(variant(tmp_path, snippet=f'[source]\nurl = "{good}"\n')).source.url == good
    refused(tmp_path, r"\[source\] url.*https://", snippet='[source]\nurl = "http://data.mendeley.com/x"\n')
    refused(tmp_path, r"\[source\] url.*https://", snippet='[source]\nurl = "ftp://data.mendeley.com/x"\n')
    refused(tmp_path, r"\[source\] url.*no está en allowed_hosts", snippet='[source]\nurl = "https://evil.example.com/x"\n')
    refused(tmp_path, r"\[source\] url.*no está en allowed_hosts", snippet='[source]\nurl = "https://amazonaws.com/x"\n')
    refused(tmp_path, r"\[source\] url.*no es una dirección", snippet='[source]\nurl = "https://evil.example.com@data.mendeley.com/x"\n')
    refused(tmp_path, r"\[source\] accepted_sha256.*64 caracteres", snippet='[source]\naccepted_sha256 = ""\n')
    refused(tmp_path, r"\[source\] accepted_sha256.*64 caracteres", snippet=f'[source]\naccepted_sha256 = "{HASH.upper()}"\n')
    refused(tmp_path, r"\[source\] accepted_sha256.*64 caracteres", snippet=f'[source]\naccepted_sha256 = "{HASH[:-1]}"\n')


def test_http_mode_refuses_a_local_path_it_would_ignore(tmp_path):
    refused(tmp_path, r"\[source\] local_path.*se ignoraría", snippet='[source]\nlocal_path = "data/raw/x.csv"\n')


def test_local_mode_needs_its_path_and_refuses_the_keys_it_would_ignore(tmp_path):
    local = '[source]\nmode = "local"\nurl = ""\naccepted_sha256 = ""\n'
    refused(tmp_path, r"\[source\] local_path.*hace falta", snippet=local)
    assert load_config(variant(tmp_path, snippet=local + 'local_path = "data/x.csv"\n')).source.local_path == tmp_path / "data" / "x.csv"
    refused(tmp_path, r"\[source\] accepted_sha256.*se ignoraría", snippet=f'[source]\nmode = "local"\nurl = ""\nlocal_path = "x.csv"\naccepted_sha256 = "{HASH}"\n')
    refused(tmp_path, r"\[source\] url.*se ignoraría", snippet='[source]\nmode = "local"\naccepted_sha256 = ""\nlocal_path = "x.csv"\n')


def test_mode_must_be_http_or_local(tmp_path):
    refused(tmp_path, r"\[source\] mode", snippet='[source]\nmode = "ftp"\n')


@pytest.mark.parametrize("value", [
    '["data.mendeley.com", "HTTPS.example.com"]',   # upper case
    '["https://data.mendeley.com"]',                # a scheme
    '["data.mendeley.com:443"]',                    # a port
    '["data.mendeley.com/path"]',                   # a path
    '[".com"]',                                     # a suffix that is a whole top-level domain
    '["."]',
    '[]',
    '[""]',
    '["bad host.com"]',
])
def test_allowed_hosts_entries_are_checked(tmp_path, value):
    refused(tmp_path, r"\[source\] allowed_hosts", snippet=f"[source]\nallowed_hosts = {value}\n")


def test_allowed_hosts_accepts_exact_names_and_dotted_suffixes(tmp_path):
    cfg = load_config(variant(tmp_path, snippet='[source]\nallowed_hosts = ["data.mendeley.com", ".s3.amazonaws.com", "files.example.org"]\n'))
    assert cfg.source.allowed_hosts == ("data.mendeley.com", ".s3.amazonaws.com", "files.example.org")


@pytest.mark.parametrize(("host", "expected"), [
    ("data.mendeley.com", True),
    ("DATA.Mendeley.COM", True),
    ("data.mendeley.com.", True),
    ("mendeley.com", False),
    ("evil.data.mendeley.com", False),
    ("data.mendeley.com.evil.com", False),
    ("bucket.s3.amazonaws.com", True),
    ("x.amazonaws.com", True),
    ("amazonaws.com", False),
    ("evilamazonaws.com", False),
    ("evil-amazonaws.com", False),
    ("amazonaws.com.evil.com", False),
    ("", False),
])
def test_host_allowed_matches_exact_names_and_dotted_suffixes_only(host, expected):
    assert host_allowed(host, ("data.mendeley.com", ".amazonaws.com")) is expected


@pytest.mark.parametrize(("url", "host"), [
    ("https://data.mendeley.com/a/b?x=1#f", "data.mendeley.com"),
    ("HTTPS://Data.Mendeley.COM:443/a", "data.mendeley.com"),
    ("http://127.0.0.1:8080/x", "127.0.0.1"),
    ("https://data.mendeley.com./a", "data.mendeley.com"),
    ("https://evil.com@data.mendeley.com/a", None),       # user information
    ("https://data.mendeley.com@evil.com/a", None),
    ("https://evil.com\\@data.mendeley.com/a", None),     # a backslash: two parsers read different hosts
    ("https://data.mendeley.com\\.evil.com/a", None),
    ("https://data.mendeley.com /a", None),               # whitespace
    ("https://data.mendeley.com/a\r\nHost: evil.com", None),
    ("ftp://data.mendeley.com/a", None),
    ("file:///etc/passwd", None),
    ("//data.mendeley.com/a", None),
    ("https://:443/a", None),
    ("https://data.mendeley.com:notaport/a", None),
    ("", None),
])
def test_url_host_reads_one_unambiguous_host_or_refuses_the_url(url, host):
    assert url_host(url) == host


# --- mistakes in the file are named --------------------------------------------------------------------------------


def test_unknown_key_is_rejected_with_its_name(tmp_path):
    refused(tmp_path, r"\[costs\].*'alert_costs'", snippet="[costs]\nalert_costs = 1.0\n")


def test_unknown_section_missing_section_and_missing_key_are_named(tmp_path):
    refused(tmp_path, r"sección desconocida \[extras\]", snippet="[extras]\nx = 1\n")
    text = DEFAULT_CONFIG.read_text(encoding="utf-8")
    without_site = re.sub(r"\[site\].*?(?=\[paths\])", "", text, flags=re.S)
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "a.toml").write_text(without_site, encoding="utf-8")
    with pytest.raises(ValueError, match=r"falta la sección \[site\]"):
        load_config(tmp_path / "config" / "a.toml")
    refused(tmp_path, r"\[costs\]: falta la clave 'effect'", replace=("effect = 0.5\n", ""))


@pytest.mark.parametrize(("snippet", "match"), [
    ("[costs]\nalert_cost = \"5\"\n", r"\[costs\] alert_cost.*un número"),
    ("[costs]\nalert_cost = true\n", r"\[costs\] alert_cost.*un número"),
    ("[model]\nmax_iter = 300.5\n", r"\[model\] max_iter.*un entero"),
    ("[model]\nlearning_rate = 0.1\n", r"\[model\] learning_rate.*una lista"),
    ("[model]\nlearning_rate = [\"a\"]\n", r"\[model\] learning_rate.*un número"),
    ("[split]\ntrain_end = \"2016-06-30\"\n", r"\[split\] train_end.*una fecha"),
    ("[split]\ntrain_end = 2016-06-30T10:00:00\n", r"\[split\] train_end.*una fecha"),
    ("[paths]\nraw_dir = 3\n", r"\[paths\] raw_dir.*ruta"),
    ("[source]\nmax_redirects = false\n", r"\[source\] max_redirects.*un entero"),
])
def test_a_value_of_the_wrong_type_is_named(tmp_path, snippet, match):
    refused(tmp_path, match, snippet=snippet)


def test_extends_cycle_and_missing_base_are_refused(tmp_path):
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "a.toml").write_text('extends = "b.toml"\n', encoding="utf-8")
    (folder / "b.toml").write_text('extends = "a.toml"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="ciclo"):
        load_config(folder / "a.toml")
    (folder / "c.toml").write_text('extends = "nothing.toml"\n', encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        load_config(folder / "c.toml")
    with pytest.raises(FileNotFoundError):
        load_config(folder / "absent.toml")


# --- numeric bounds: every limit has a test with its invalid values ----------------------------------------------


@pytest.mark.parametrize(("section", "key", "bad"), [
    ("source", "max_bytes", 0), ("source", "chunk_bytes", 0), ("source", "timeout_s", -1), ("source", "max_redirects", -1),
    ("split", "purge_days", 0),
    ("model", "max_iter", 0), ("model", "l2_regularization", -1.0),
    ("costs", "alert_cost", 0.0), ("costs", "alert_cost", -5.0), ("costs", "late_cost", 0.0), ("costs", "late_cost", -1.0),
    ("costs", "effect", 0.0), ("costs", "effect", 1.5), ("costs", "effect", -0.5),
    ("costs", "threshold_step", 0.0), ("costs", "threshold_min", 0.0), ("costs", "threshold_max", 1.0),
    ("costs", "sensitivity_min", 0.0), ("costs", "sensitivity_max", 1.0), ("costs", "sensitivity_step", 0.0),
    ("evaluation", "bootstrap_resamples", 0), ("evaluation", "calibration_bins", 1), ("evaluation", "roc_points", 1),
    ("evaluation", "importance_repeats", 0),
    ("criteria", "c06_max_ece", 0.0), ("criteria", "c06_max_ece", 1.0), ("criteria", "c06_max_bias", 0.0), ("criteria", "leak_tripwire_auc", 0.5),
    ("criteria", "leak_tripwire_auc", 1.2),
    ("site", "max_total_bytes", 0), ("site", "min_cell_orders", 0),
    ("features", "noon_hour", 0), ("features", "noon_hour", 24),
])
def test_numeric_bounds_reject_their_invalid_values_naming_the_key(tmp_path, section, key, bad):
    refused(tmp_path, rf"\[{section}\] {key}", snippet=f"[{section}]\n{key} = {bad}\n")


def test_threshold_and_sensitivity_ranges_must_be_ordered(tmp_path):
    refused(tmp_path, r"\[costs\] threshold_min", snippet="[costs]\nthreshold_min = 0.6\nthreshold_max = 0.5\n")
    refused(tmp_path, r"\[costs\] sensitivity_min", snippet="[costs]\nsensitivity_min = 0.8\nsensitivity_max = 0.2\n")


def test_the_edges_of_the_valid_ranges_are_accepted(tmp_path):
    ok = "[costs]\neffect = 1.0\nsensitivity_min = 0.5\nsensitivity_max = 0.5\n[model]\nl2_regularization = 0.0\n[source]\nmax_redirects = 0\n"
    cfg = load_config(variant(tmp_path, snippet=ok))
    assert cfg.costs.effect == 1.0 and cfg.model.l2_regularization == 0.0 and cfg.source.max_redirects == 0


@pytest.mark.parametrize(("snippet", "match"), [
    ("[model]\nlearning_rate = []\n", r"\[model\] learning_rate.*no vacía"),
    ("[model]\nmax_leaf_nodes = [15, 15]\n", r"\[model\] max_leaf_nodes.*sin repetidos"),
    ("[model]\nmax_leaf_nodes = [1]\n", r"\[model\] max_leaf_nodes.*2 o más"),
    ("[model]\nmin_samples_leaf = [0]\n", r"\[model\] min_samples_leaf"),
    ("[model]\nlearning_rate = [0.1, -0.1]\n", r"\[model\] learning_rate"),
    ("[model]\ncalibration = \"sigmoid\"\n", r"\[model\] calibration.*isotonic"),
    ("[model]\nmodel_version = \"v 1\"\n", r"\[model\] model_version"),
    ("[model]\nmodel_version = \"../v1\"\n", r"\[model\] model_version"),
    ("[features]\ncandidate_sets = []\n", r"\[features\] candidate_sets"),
    ("[features]\ncandidate_sets = [\"f0\", \"f3\"]\n", r"\[features\] candidate_sets.*f3"),
    ("[features]\ncandidate_sets = [\"f0\", \"f0\"]\n", r"\[features\] candidate_sets"),
    ("[source]\nencoding = \"nonsense-9\"\n", r"\[source\] encoding"),
    ("[source]\nfile_name = \"../x.csv\"\n", r"\[source\] file_name"),
    ("[source]\nfile_name = \"\"\n", r"\[source\] file_name"),
])
def test_grids_names_and_codecs_are_checked(tmp_path, snippet, match):
    refused(tmp_path, match, snippet=snippet)


# --- dates: the split, its purges and the calendar of the deliveries (D08, D09) -------------------------------------


@pytest.mark.parametrize(("snippet", "match"), [
    ("[split]\ntrain_end = 2016-07-02\n", r"\[split\] valid_start.*purge_days = 7"),
    ("[split]\npurge_days = 5\n", r"\[split\] valid_start.*purge_days = 5"),
    ("[split]\nvalid_end = 2016-12-30\n", r"\[split\] test_start.*purge_days"),
    ("[split]\ntest_end = 2017-09-29\n", r"\[split\] post_break_start"),
    ("[split]\nvalid_v2_start = 2016-07-08\n", r"\[split\] valid_start"),
    ("[split]\nvalid_v2_start = 2017-01-01\n", r"\[split\] valid_v2_start"),
    ("[split]\ntrain_start = 2014-12-31\n", r"\[split\] train_start"),
    ("[split]\ntrain_start = 2016-07-01\n", r"\[split\] train_start"),
    ("[split]\npost_break_end = 2018-02-01\n", r"\[split\] post_break_end"),
    ("[deliveries]\nlaunch_date = 2017-01-06\n", r"\[deliveries\] launch_date"),
    ("[deliveries]\nclose_date = 2018-01-31\n", r"\[deliveries\]: first_order_date"),
    ("[deliveries]\nfirst_order_date = 2017-02-01\n", r"\[deliveries\]: first_order_date"),
])
def test_split_dates_must_be_in_order_with_purges_of_purge_days(tmp_path, snippet, match):
    refused(tmp_path, match, snippet=snippet)


def test_every_split_date_is_used_by_the_check(tmp_path):
    """Moving any one date of [split] is refused: none is accepted and then ignored."""
    moved_dates = {
        "train_start": "2014-01-01", "train_end": "2016-06-29", "valid_start": "2016-07-09", "valid_v2_start": "2016-11-01", "valid_end": "2017-01-01",
        "test_start": "2017-01-09", "test_end": "2017-09-29", "post_break_start": "2017-10-02", "post_break_end": "2018-02-05",
    }
    for key, moved in moved_dates.items():
        if key == "valid_v2_start":
            continue  # the one date that may move inside V1..V2 without breaking a purge; its own bounds are tested above
        with pytest.raises(ValueError, match=r"\[split\]|\[deliveries\]"):
            load_config(variant(tmp_path, snippet=f"[split]\n{key} = {moved}\n"))
    assert load_config(variant(tmp_path, snippet="[split]\nvalid_v2_start = 2016-11-01\n")).split.valid_v2_start == date(2016, 11, 1)
