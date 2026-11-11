"""H5: the advisories of the native review of U20 (the site files): the examples that published one order's whole combination, a hidden cell that a published total
minus the shown cells gives back, the statuses translated in two places, the kinds of the duplicate and constant columns split by position, the counts of ``by_market`` and
``by_mode`` that no test pinned, and five files written one by one that could be left half old and half new.

Every claim was checked against the code first; the ones that were defects have a test that failed before the change, the ones that were only missing tests pass. The
expected values are written here (by hand or from the Parquet files), never computed with the module under test. The files are written only into temporary folders.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from latedel import schema, site_export
from latedel.schema import PERSONAL_COLUMNS
from latedel.site_export import MANIFEST_FILE, SITE_FILES, SiteExportError, export_site, export_summary, verify_site
from latedel.warehouse import connect
from tests.contract.test_site_export import META, approx

DETAILS = ("order_hour", "real_days", "month", "weekday_es", "n_lines", "market")   # what an example may publish beyond its cell, its risk, its alert and its outcome


@pytest.fixture(scope="module")
def frames(star_export) -> dict[str, pd.DataFrame]:
    return {p.stem: pd.read_parquet(p) for p in star_export.out_dir.glob("*.parquet")}


@pytest.fixture(scope="module")
def site(fixture_site, star_export) -> Path:
    assert star_export.meta == META
    return fixture_site


@pytest.fixture(scope="module")
def older_site(star_export, tmp_path_factory) -> Path:
    """Another export of the same warehouse, with a minimum of 120 orders per cell: the files that hide cells have other bytes than the ones of ``site``."""
    folder = tmp_path_factory.mktemp("older_site")
    export_with(star_export, folder, 120)
    return folder


def documents(folder: Path) -> dict[str, dict]:
    return {name: json.loads((folder / name).read_text(encoding="utf-8")) for name in SITE_FILES}


def export_with(star_export, folder: Path, minimum: int) -> dict[str, dict]:
    cfg = replace(star_export.cfg, site=replace(star_export.cfg.site, min_cell_orders=minimum))
    export_site(star_export.conn, folder, META, cfg)
    return documents(folder)


# --- R1-examples-quasi-id: an example is one of at least min_cell_orders orders for everything it publishes ------------------------------------------------


@pytest.fixture(scope="module")
def every_detail(star_export, tmp_path_factory) -> list[dict]:
    """The examples with a minimum of one order: nothing is withheld, each example is its order's own values (what U20 published whatever the minimum)."""
    return export_with(star_export, tmp_path_factory.mktemp("every_detail"), 1)["algorithms.json"]["examples"]


def scored_test_orders(frames) -> pd.DataFrame:
    """The closed, scored orders of the test block with what an example publishes, at the precision it is published (the risk at two decimals, the month)."""
    order = frames["fact_order"]
    test = order[(order["block"] == "test") & (order["outcome_status"] == "closed") & order["risk"].notna()].copy()
    test["market"] = test["geo_key"].map(frames["dim_geo"].set_index("geo_key")["market"])
    test["weekday_es"] = test["order_date_key"].map(frames["dim_date"].set_index("date_key")["weekday_es"])
    test["month"] = test["order_date_key"].map(lambda k: f"{int(k) // 10000:04d}-{int(k) // 100 % 100:02d}")
    test["risk2"] = test["risk"].map(lambda r: round(float(r), 2))
    test["alert"] = test["is_alert"].astype(bool)
    test["late"] = test["is_late"].astype(bool)
    return test


def sharing(test: pd.DataFrame, example: dict, noon: int = 12) -> int:
    """How many orders of the test have every attribute that the example publishes: its mode (and its half of the day for Same Day), its risk, its alert, its outcome
    and each detail it does not leave null."""
    mask = (test["mode_key"] == example["mode"]) & (test["risk2"] == example["risk"]) & (test["alert"] == example["is_alert"]) & (test["late"] == (example["outcome"] == "tarde"))
    if example["mode"] == "Same Day":
        mask &= (test["order_hour"] < noon) if "antes" in example["rule_es"] else (test["order_hour"] >= noon)
    for name in DETAILS:
        if example[name] is not None:
            mask &= test[name] == example[name]
    return int(mask.sum())


def test_an_example_publishes_only_what_at_least_the_minimum_of_orders_of_the_test_share(star_export, site, frames, every_detail):
    """The example of a cell was one real order with its market, weekday, month, hour, lines and real days: a combination that one order alone may have. Now each
    published example is shared, in everything it says, by at least ``min_cell_orders`` closed orders of the test."""
    test, minimum = scored_test_orders(frames), star_export.cfg.site.min_cell_orders
    assert len(every_detail) == 5 and all(e[name] is not None for e in every_detail for name in DETAILS)
    alone = [e["rule_es"] for e in every_detail if sharing(test, e) < minimum]
    assert alone, "the fixture has examples whose whole combination fewer than the minimum of orders share"
    examples = documents(site)["algorithms.json"]["examples"]
    assert examples and [e["label"] for e in examples] == [f"Pedido {c}" for c in "ABCDE"][: len(examples)]
    for example in examples:
        assert sharing(test, example) >= minimum, (example["label"], {name: example[name] for name in DETAILS})
    assert all(any(e[name] is None for name in DETAILS) for e in examples if e["rule_es"] in alone)


def without_details(example: dict) -> dict:
    return {**example, **dict.fromkeys(DETAILS)}


def test_each_detail_is_kept_in_order_while_the_example_stays_one_of_the_minimum(star_export, site, frames, every_detail):
    """The rule written again from the Parquet files: a cell whose order, without its details, fewer than the minimum share has no example; otherwise the details are
    tried in a fixed order (the hour, the real days, the month, the weekday, the lines, the market) and one is published if the orders that share it and everything
    kept before it are at least the minimum. What is published is the order's own value, and the letters follow in order."""
    test, minimum = scored_test_orders(frames), star_export.cfg.site.min_cell_orders
    expected = []
    for full in every_detail:                     # every cell of B5 whose example exists with a minimum of one, in the fixed order
        if sharing(test, without_details(full)) < minimum:
            continue
        kept = without_details(full)
        for name in DETAILS:
            trial = {**kept, name: full[name]}
            if sharing(test, trial) >= minimum:
                kept = trial
        expected.append({**kept, "label": f"Pedido {'ABCDE'[len(expected)]}"})
    assert documents(site)["algorithms.json"]["examples"] == expected


def test_with_a_minimum_of_one_an_example_is_its_order_as_before(frames, every_detail):
    """The previous behaviour, where nothing is withheld: the lower-median order of each cell with every detail (``expected_examples`` of ``test_site_export``)."""
    from tests.contract.test_site_export import expected_examples

    want = expected_examples(frames, minimum=1)
    got = [(e["mode"], e["month"], e["order_hour"], e["real_days"], e["n_lines"], e["risk"]) for e in every_detail]
    assert got == [(w["mode"], w["month"], w["hour"], w["real_days"], w["n_lines"], approx(w["risk"], 2)) for w in want]


# --- R1-differencing: no hidden cell is a published total minus the shown cells -------------------------------------------------------------------------


def recovered(docs: dict[str, dict]) -> list[tuple[str, str, int]]:
    """What an outsider gets by subtraction from the published files alone: for each breakdown whose total is published somewhere (in the same file or another), a
    breakdown with exactly one hidden cell gives that cell as the total minus the shown ones; a breakdown whose own total is hidden but whose cells are all shown gives
    that total. Each is ``(breakdown, cell, value)``."""
    summary, algorithms, alerts = docs["summary.json"], docs["algorithms.json"], docs["alerts.json"]
    found: list[tuple[str, str, int]] = []

    def subtract(name: str, cells: list[dict], key: str, label: str, total: int | None) -> None:
        if total is None:
            return
        hidden = [c for c in cells if c["suppressed"]]
        if len(hidden) == 1:
            found.append((name, str(hidden[0][label]), total - sum(c[key] for c in cells if not c["suppressed"])))

    def total(cells: list[dict], key: str) -> int | None:
        return None if any(c["suppressed"] for c in cells) else sum(c[key] for c in cells)

    split = algorithms["split"]
    subtract("split", split, "orders", "block", summary["headline"]["orders_task"])
    train = next(b for b in split if b["block"] == "train")
    subtract("mode_rates", algorithms["mode_rates"], "train_orders", "mode", None if train["suppressed"] else train["orders"])
    for mode in algorithms["mode_rates"]:
        hours = [h for h in algorithms["hour_profile"] if h["mode"] == mode["mode"]]
        if mode["suppressed"]:
            if not any(h["suppressed"] for h in hours):
                found.append(("mode_rates", mode["mode"], sum(h["train_orders"] for h in hours)))
        else:
            subtract(f"hour_profile {mode['mode']}", hours, "train_orders", "hour", mode["train_orders"])
    if algorithms["calibration"]:
        subtract("calibration", algorithms["calibration"], "orders", "bin", summary["headline"]["test_orders"])
    scored = [t for t in (total(alerts["by_mode"], "orders"), total(alerts["by_market"], "orders"), total(alerts["monthly"], "orders")) if t is not None]
    for name, cells, label in (("monthly", alerts["monthly"], "month"), ("by_mode", alerts["by_mode"], "mode"), ("by_market", alerts["by_market"], "market")):
        subtract(name, cells, "orders", label, scored[0] if scored else None)
    return found


def true_orders(frames) -> dict[tuple[str, str], int]:
    """The orders of each cell of the breakdowns of alerts.json and of the mode rates, from the Parquet files: ``{(breakdown, cell): orders}``."""
    track = frames["fact_tracking_monthly"]
    order = frames["fact_order"]
    scored = order[order["risk"].notna()]
    train = order[(order["block"] == "train") & (order["outcome_status"] == "closed")]
    out = {("monthly", f"{int(k) // 100:04d}-{int(k) % 100:02d}"): int(n) for k, n in track.groupby("month_key")["orders_scored"].sum().items()}
    out |= {("by_mode", str(m)): int(n) for m, n in track.groupby("mode_key")["orders_scored"].sum().items()}
    out |= {("by_market", str(m)): int(n) for m, n in scored["geo_key"].map(frames["dim_geo"].set_index("geo_key")["market"]).value_counts().items()}
    out |= {("mode_rates", str(m)): int(n) for m, n in train["mode_key"].value_counts().items()}
    return out


@pytest.mark.parametrize("minimum", [60, 100, 120])
def test_no_hidden_cell_is_a_published_total_minus_the_shown_cells_of_its_breakdown(star_export, frames, tmp_path, minimum):
    """At these minima the fixture has breakdowns with exactly one cell under the minimum (a month, a mode, a market): that cell was the total of the scored orders, or
    of the training, minus the cells shown. With the secondary suppression the smallest shown cell of such a breakdown is hidden too, and the subtraction finds nothing."""
    docs = export_with(star_export, tmp_path / "site", minimum)
    assert recovered(docs) == [], "hidden cells that the published totals give back"
    counts = true_orders(frames)
    where = {"monthly": ("alerts.json", "month"), "by_mode": ("alerts.json", "mode"), "by_market": ("alerts.json", "market"), "mode_rates": ("algorithms.json", "mode")}
    secondary = [(name, cell[label]) for name, (file, label) in where.items() for cell in docs[file][name] if cell["suppressed"] and counts.get((name, str(cell[label])), 0) >= minimum]
    assert secondary, "a cell with at least the minimum of orders is hidden to protect another one (without it the subtraction gives that one back)"
    for name, (file, label) in where.items():
        for cell in docs[file][name]:
            if not cell["suppressed"]:
                assert counts[(name, str(cell[label]))] >= minimum, (name, cell[label])


# --- R3-by-market-late-rate-untested and R3-by-mode-alerts-precision-recall-untested: the counts of alerts.json on a case written by hand ---------------------


TRACKING = [   # month_key, mode, orders_scored, alerts, closed_orders, closed_alerts, alerts_late, late_closed, late_missed, late_expected, cancelled_orders, alerts_on_cancelled
    (201703, "First Class", 30, 20, 25, 18, 15, 20, 5, 22.5, 1, 0), (201704, "First Class", 15, 10, 12, 9, 6, 8, 2, 9.0, 0, 1),
    (201703, "Second Class", 22, 11, 20, 10, 4, 9, 5, 10.5, 0, 0), (201704, "Second Class", 8, 1, 6, 1, 1, 3, 2, 3.0, 0, 0),
    (201703, "Standard Class", 25, 0, 20, 0, 0, 6, 6, 8.0, 0, 0), (201704, "Standard Class", 10, 0, 8, 0, 0, 2, 2, 3.0, 0, 0),
    (201703, "Same Day", 15, 5, 12, 4, 3, 3, 0, 4.0, 0, 0), (201704, "Same Day", 10, 2, 8, 2, 1, 2, 1, 2.0, 0, 0),
]


def hand_case(star_export, tmp_path, markets: dict[str, tuple[int, int, int, int]]):
    """A copy of the exported warehouse whose scored orders and follow-up are replaced by a case written by hand: ``markets`` gives, for each market, the scored orders,
    the alerts among them, the closed ones and the late ones among those; the follow-up is ``TRACKING``."""
    db = tmp_path / "hand.sqlite"
    shutil.copy(star_export.db, db)
    conn = connect(db)
    geo = dict(conn.execute("SELECT market, MIN(geo_key) FROM star_dim_geo GROUP BY market").fetchall())
    conn.execute("DELETE FROM star_fact_order")
    order_id = 1
    for market, (scored, alerts, closed, late) in markets.items():
        for i in range(scored):
            is_closed = i < closed
            conn.execute(
                "INSERT INTO star_fact_order (order_id, order_date_key, mode_key, geo_key, order_hour, n_lines, block, regime, outcome_status, real_days, is_late, risk, is_alert) "
                "VALUES (?, 20171015, 'Standard Class', ?, 9, 1, 'post_break', 'post_break', ?, ?, ?, 0.5, ?)",
                (order_id, geo[market], "closed" if is_closed else "open", 5 if is_closed else None, (1 if i < late else 0) if is_closed else None, 1 if i < alerts else 0),
            )
            order_id += 1
    conn.execute("DELETE FROM star_fact_tracking_monthly")
    conn.executemany(
        "INSERT INTO star_fact_tracking_monthly (month_key, mode_key, orders_scored, alerts, closed_orders, closed_alerts, alerts_late, late_closed, late_missed, late_expected, "
        "cancelled_orders, alerts_on_cancelled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", TRACKING)
    return conn


def alerts_of_the_hand_case(star_export, tmp_path, markets) -> dict:
    conn = hand_case(star_export, tmp_path, markets)
    try:
        export_site(conn, tmp_path / "site", META, star_export.cfg)
    finally:
        conn.close()
    return documents(tmp_path / "site")["alerts.json"]


def test_by_market_counts_the_scored_orders_and_hides_the_late_rate_of_a_market_with_few_closed_ones(star_export, tmp_path):
    alerts = alerts_of_the_hand_case(star_export, tmp_path, {"Africa": (30, 12, 24, 6), "Europe": (25, 5, 10, 3), "LATAM": (4, 1, 4, 2), "Pacific Asia": (6, 2, 5, 1)})
    by_market = {m["market"]: m for m in alerts["by_market"]}
    assert by_market["Africa"] == {"market": "Africa", "suppressed": False, "orders": 30, "alerts": 12, "late_rate_observed": 0.25}             # 6 late of 24 closed
    assert by_market["Europe"] == {"market": "Europe", "suppressed": False, "orders": 25, "alerts": 5, "late_rate_observed": None}             # 10 closed, under 20
    for small in ("LATAM", "Pacific Asia"):                                                                                                 # 4 and 6 scored: hidden
        assert by_market[small] == {"market": small, "suppressed": True, "orders": None, "alerts": None, "late_rate_observed": None}


def test_by_mode_sums_the_follow_up_of_each_mode_over_the_months(star_export, tmp_path):
    alerts = alerts_of_the_hand_case(star_export, tmp_path, {"Africa": (30, 12, 24, 6), "Europe": (25, 5, 10, 3)})
    by_mode = {m["mode"]: {k: m[k] for k in ("suppressed", "orders", "alerts", "precision", "recall")} for m in alerts["by_mode"]}
    assert by_mode == {
        "First Class": {"suppressed": False, "orders": 45, "alerts": 30, "precision": 0.778, "recall": 0.75},       # 21 late of 27 closed alerts; 21 of 21 + 7 missed
        "Second Class": {"suppressed": False, "orders": 30, "alerts": 12, "precision": 0.455, "recall": 0.417},     # 5 of 11; 5 of 5 + 7
        "Standard Class": {"suppressed": False, "orders": 35, "alerts": 0, "precision": None, "recall": 0.0},       # no closed alert; 0 of 0 + 8
        "Same Day": {"suppressed": False, "orders": 25, "alerts": 7, "precision": 0.667, "recall": 0.8},           # 4 of 6; 4 of 4 + 1
    }
    assert {m["month"]: (m["orders"], m["alerts"]) for m in alerts["monthly"]} == {"2017-03": (92, 36), "2017-04": (43, 13)}
    assert alerts["cancelled"] == {"alerts_on_later_cancelled": 1, "share_of_alerts": approx(1 / 49, 3)}


def test_a_breakdown_with_one_hidden_cell_hides_its_smallest_shown_cell_too(star_export, tmp_path):
    """One small market (LATAM, 4 orders) would be the total of the scored orders minus the other markets; Europe, the smallest shown market, is hidden with it."""
    alerts = alerts_of_the_hand_case(star_export, tmp_path, {"Africa": (30, 12, 24, 6), "Europe": (25, 5, 10, 3), "LATAM": (4, 1, 4, 2), "Pacific Asia": (26, 8, 21, 7)})
    by_market = {m["market"]: m for m in alerts["by_market"]}
    assert {m: c["suppressed"] for m, c in by_market.items()} == {"Africa": False, "Europe": True, "LATAM": True, "Pacific Asia": False}
    assert by_market["Europe"] == {"market": "Europe", "suppressed": True, "orders": None, "alerts": None, "late_rate_observed": None}
    assert by_market["Pacific Asia"] == {"market": "Pacific Asia", "suppressed": False, "orders": 26, "alerts": 8, "late_rate_observed": approx(7 / 21, 3)}


# --- R2-1: one table of the statuses, used --------------------------------------------------------------------------------------------------------------


def test_the_status_of_a_rule_is_its_worst_one_in_english_and_an_unknown_status_is_refused(star_export, tmp_path):
    db = tmp_path / "w.sqlite"
    shutil.copy(star_export.db, db)
    conn = connect(db)
    try:
        conn.execute("UPDATE star_fact_validation SET status = 'falla' WHERE rule_id = 'M05' AND run_key = 'run-L2017-01-07'")   # a blocking rule of a loaded run that failed
        export_site(conn, tmp_path / "site", META, star_export.cfg)
        rules = {r["rule_id"]: r["status"] for r in documents(tmp_path / "site")["validation_rules.json"]["rules"]}
        assert (rules["M05"], rules["V11"], rules["V20"], rules["V01"]) == ("fail", "warn", "pass", "not_evaluated")
        conn.execute("UPDATE star_fact_validation SET status = 'quizás' WHERE rule_id = 'V20' AND run_key = 'run-L2017-01-07'")
        before = {p.name: p.read_bytes() for p in (tmp_path / "site").iterdir()}
        with pytest.raises(SiteExportError, match=r"V20.*quizás"):
            export_site(conn, tmp_path / "site", META, star_export.cfg)
        assert {p.name: p.read_bytes() for p in (tmp_path / "site").iterdir()} == before, "nothing is written"
    finally:
        conn.close()


# --- R2-2 and R3-column-kind-split-by-position: the kind of a column comes from its name ---------------------------------------------------------------------


KINDS_BY_HAND = {
    "Sales per customer": "duplicate", "Order Item Product Price": "duplicate", "Product Card Id": "duplicate", "Product Category Id": "duplicate",
    "Product Status": "constant", "Product Description": "constant", "Product Image": "constant", "Late_delivery_risk": "target",
}


def test_each_column_that_is_not_an_input_has_the_kind_of_its_name(site):
    columns = {c["column"]: c["kind"] for c in documents(site)["leakage.json"]["columns"]}
    assert {name: columns[name] for name in KINDS_BY_HAND} == KINDS_BY_HAND
    assert {name for name, kind in columns.items() if kind == "personal"} == set(PERSONAL_COLUMNS)


def fresh_site_export():
    """The module ``latedel.site_export`` executed again, apart from the one the other tests use (``sys.modules`` is not touched)."""
    spec = importlib.util.find_spec("latedel.site_export")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reordering_the_lists_of_the_schema_changes_no_kind(monkeypatch):
    """The kinds were the first four and the last three of one tuple of ``schema.py``: reordering it relabelled columns without a failure anywhere."""
    for name in ("DUPLICATE_OR_CONSTANT_COLUMNS", "DUPLICATE_COLUMNS", "CONSTANT_COLUMNS"):
        if hasattr(schema, name):
            monkeypatch.setattr(schema, name, tuple(reversed(getattr(schema, name))))
    fresh = fresh_site_export()
    assert {name: fresh.COLUMN_KINDS[name] for name in KINDS_BY_HAND} == KINDS_BY_HAND


# --- R3-write-not-transactional-across-files: the five files are one export, which a manifest written last says --------------------------------------------


def test_the_manifest_is_written_last_and_names_each_file_with_its_size_and_sha256(star_export, tmp_path, monkeypatch):
    written: list[str] = []
    real = site_export.write_atomic

    def recording(path, data):
        written.append(Path(path).name)
        return real(path, data)

    monkeypatch.setattr(site_export, "write_atomic", recording)
    paths = export_site(star_export.conn, tmp_path / "site", META, star_export.cfg)
    assert [p.name for p in paths] == list(SITE_FILES) and written == [*SITE_FILES, MANIFEST_FILE]
    manifest = json.loads((tmp_path / "site" / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert {k: manifest[k] for k in ("schema_version", "run_key", "as_of", "source_sha256")} == {
        "schema_version": 1, "run_key": META.run_key, "as_of": META.as_of, "source_sha256": META.source_sha256}
    assert sorted(manifest["files"]) == sorted(SITE_FILES)
    for name in SITE_FILES:
        data = (tmp_path / "site" / name).read_bytes()
        assert manifest["files"][name] == {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}, name
    assert verify_site(tmp_path / "site") == []


@pytest.mark.parametrize("failing_write", range(1, len(SITE_FILES) + 2))
def test_a_failure_in_the_middle_of_the_writes_leaves_a_set_that_is_refused_never_a_mix_that_is_read(star_export, site, older_site, tmp_path, monkeypatch, failing_write):
    """An export over an older one fails at its n-th write (the five files, then the manifest): the folder is the older export whole, which verifies, or a mix that
    ``verify_site`` refuses. It is never a mix that verifies; the next export writes the new set whole."""
    folder = tmp_path / "site"
    shutil.copytree(older_site, folder)
    older = {p.name: p.read_bytes() for p in folder.iterdir()}
    real, calls = site_export.write_atomic, {"n": 0}

    def disk_full_at_the_nth(path, data):
        calls["n"] += 1
        if calls["n"] == failing_write:
            raise OSError("no space left on the device")
        return real(path, data)

    with monkeypatch.context() as failing:
        failing.setattr(site_export, "write_atomic", disk_full_at_the_nth)
        with pytest.raises(OSError, match="no space"):
            export_site(star_export.conn, folder, META, star_export.cfg)
    now = {p.name: p.read_bytes() for p in folder.iterdir()}
    assert sorted(now) == sorted([*SITE_FILES, MANIFEST_FILE]), "no temporary file is left"
    if any(now[name] != older[name] for name in SITE_FILES):
        assert verify_site(folder), f"a mix of the old and the new files verifies after a failure at write {failing_write}"
    else:
        assert now == older and verify_site(folder) == []
    if failing_write == 3:
        export_site(star_export.conn, folder, META, star_export.cfg)
        assert verify_site(folder) == [] and all((folder / name).read_bytes() == (site / name).read_bytes() for name in SITE_FILES)


@pytest.mark.parametrize("damage", ["a byte of a file", "a file missing", "the manifest missing", "the manifest unreadable", "two files swapped", "a file of another export"])
def test_the_set_is_refused_when_a_file_is_not_the_one_the_manifest_names(site, older_site, tmp_path, damage):
    folder = tmp_path / "site"
    shutil.copytree(site, folder)
    if damage == "a byte of a file":
        data = bytearray((folder / "alerts.json").read_bytes())
        data[10] ^= 1
        (folder / "alerts.json").write_bytes(bytes(data))
    elif damage == "a file missing":
        (folder / "leakage.json").unlink()
    elif damage == "the manifest missing":
        (folder / MANIFEST_FILE).unlink()
    elif damage == "the manifest unreadable":
        (folder / MANIFEST_FILE).write_text("{not json", encoding="utf-8")
    elif damage == "two files swapped":
        a, b = (folder / "alerts.json").read_bytes(), (folder / "summary.json").read_bytes()
        (folder / "alerts.json").write_bytes(b)
        (folder / "summary.json").write_bytes(a)
    else:
        assert (older_site / "algorithms.json").read_bytes() != (site / "algorithms.json").read_bytes()
        shutil.copyfile(older_site / "algorithms.json", folder / "algorithms.json")
    problems = verify_site(folder)
    named = {"a byte of a file": "alerts.json", "a file missing": "leakage.json", "the manifest missing": MANIFEST_FILE, "the manifest unreadable": MANIFEST_FILE,
             "two files swapped": "alerts.json", "a file of another export": "algorithms.json"}[damage]
    assert problems and all(isinstance(p, str) for p in problems) and any(named in p for p in problems), (damage, problems)


def test_the_summary_alone_rewrites_the_manifest_and_refuses_a_folder_that_is_not_one_export(star_export, site, tmp_path):
    folder = tmp_path / "site"
    shutil.copytree(site, folder)
    star_export.conn.execute("UPDATE pipeline_runs SET status = 'ok' WHERE run_key = ?", (META.run_key,))
    try:
        export_summary(star_export.conn, folder, META, star_export.cfg)
        assert verify_site(folder) == [] and json.loads((folder / "summary.json").read_text(encoding="utf-8"))["status"] == "ok"
        (folder / "alerts.json").write_bytes((folder / "alerts.json").read_bytes() + b" ")
        before = {p.name: p.read_bytes() for p in folder.iterdir()}
        with pytest.raises(SiteExportError, match=r"alerts\.json.*export"):
            export_summary(star_export.conn, folder, META, star_export.cfg)
        assert {p.name: p.read_bytes() for p in folder.iterdir()} == before, "the summary does not bless a mix with a new manifest"
    finally:
        star_export.conn.execute("UPDATE pipeline_runs SET status = 'running' WHERE run_key = ?", (META.run_key,))


def test_the_figures_refuse_site_files_that_are_not_one_export_and_write_nothing(site, tmp_path):
    from latedel import figures

    folder = tmp_path / "site"
    shutil.copytree(site, folder)
    (folder / "alerts.json").write_bytes((folder / "alerts.json").read_bytes() + b"\n")
    with pytest.raises(FileNotFoundError, match=r"alerts\.json.*python -m latedel run"):
        figures.make_figures(folder, tmp_path / "figures")
    assert not (tmp_path / "figures").exists()


def test_the_export_task_reads_the_site_files_back_and_fails_if_they_are_not_one_export(pipeline_interrupted, fixture_csv, tmp_path, monkeypatch):
    from latedel import pipeline
    from tests.conftest import relocate

    config = relocate(pipeline_interrupted, tmp_path / "p", fixture_csv)
    monkeypatch.setattr(pipeline, "verify_site", lambda folder: ["alerts.json no es el que nombra manifest.json"])
    with pytest.raises(pipeline.ExportError, match=r"sitio.*alerts\.json"):
        pipeline.run_task("export", "r1", config)
