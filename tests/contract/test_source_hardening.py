"""H1: the advisories of the native review of U03 (R3-negative-day-counts-not-rejected, R3-max-problem-rows-untested, R3-write-interim-not-cross-file-atomic).

Negative day counts are a defect of the file (V05); the cap of problem rows is proven at its edges; and the three interim files are one set: written with a
manifest of their hashes last, and read only if every file is the one the manifest names (a failure in the middle of a write leaves a set that is refused,
never a mix that is read).
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from latedel import ingest
from latedel.files import sha256_file
from latedel.ingest import (
    INTERIM_MANIFEST,
    MAX_PROBLEM_ROWS,
    InterimError,
    interim_fingerprint,
    read_interim,
    read_source,
    write_interim,
)
from latedel.schema import SourceError
from tests.fixtures.make_fixture import ENCODING, HEADER, _parse, _serialise

SHA_A, SHA_B = "ab" * 32, "cd" * 32
DAY_COLUMNS = ("Days for shipping (real)", "Days for shipment (scheduled)")


def column(name: str) -> int:
    return HEADER.index(name)


def edited(fixture_csv: Path, tmp_path: Path, change, name: str = "edited.csv") -> Path:
    table = _parse(fixture_csv.read_bytes())
    change(table)
    target = tmp_path / name
    target.write_bytes(_serialise(table))
    return target


def many_rows(fixture_csv: Path, tmp_path: Path, count: int, damage, name: str = "many.csv") -> Path:
    """The header and `count` copies of a valid row, each one damaged by `damage(row, i)`."""
    table = _parse(fixture_csv.read_bytes())
    rows = []
    for i in range(count):
        row = list(table[1])
        damage(row, i)
        rows.append(row)
    target = tmp_path / name
    target.write_bytes(_serialise([table[0], *rows]))
    return target


# --- R3-negative-day-counts-not-rejected ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", DAY_COLUMNS)
@pytest.mark.parametrize("value", ["-1", "-0", "-30", "-99999"])
def test_a_negative_day_count_is_refused_by_v05_naming_the_column_the_line_and_what_is_wrong(cfg, fixture_csv, tmp_path, name, value):
    def change(table):
        table[9][column(name)] = value

    with pytest.raises(SourceError) as error:
        read_source(edited(fixture_csv, tmp_path, change), cfg)
    assert error.value.rule_id == "V05"
    message = str(error.value)
    assert name in message and "negativ" in message and "línea 10" in message and value in message
    (problem,) = error.value.problems
    assert (problem["kind"], problem["column"], problem["count"]) == ("negative", name, 1) and problem["examples"] == [{"line": 10, "value": value}]


def test_zero_and_positive_day_counts_still_pass_and_other_negative_numbers_are_still_allowed(fixture_read):
    outcomes, orders = fixture_read[0].outcomes, fixture_read[0].orders
    assert outcomes["real_days"].min() == 0 and orders["scheduled_days"].min() == 0 and outcomes["real_days"].max() >= 6, "0 and positive day counts are read"
    assert (outcomes["benefit_total"] < 0).any(), "a negative benefit is a valid number: only the day counts must not be negative"


def test_several_negative_day_counts_are_counted_together_with_the_other_problems_of_v05(cfg, fixture_csv, tmp_path):
    def change(table):
        table[4][column(DAY_COLUMNS[0])] = "-2"
        table[6][column(DAY_COLUMNS[0])] = "-3"
        table[8][column(DAY_COLUMNS[1])] = "-1"
        table[10][column("order date (DateOrders)")] = "13/45/2017 99:99"

    with pytest.raises(SourceError) as error:
        read_source(edited(fixture_csv, tmp_path, change), cfg)
    found = {(p["kind"], p["column"]): p["count"] for p in error.value.problems}
    assert found == {("negative", DAY_COLUMNS[0]): 2, ("negative", DAY_COLUMNS[1]): 1, ("column", "order date (DateOrders)"): 1}
    assert "2 valor(es) negativo(s)" in str(error.value)


# --- R3-max-problem-rows-untested ----------------------------------------------------------------------------------------------------------------


def bad_date(row, i):
    row[column("order date (DateOrders)")] = "13/45/2017 99:99"


def test_the_scan_stops_at_the_cap_of_problem_rows_and_says_so(cfg, fixture_csv, tmp_path):
    path = many_rows(fixture_csv, tmp_path, MAX_PROBLEM_ROWS + 50, bad_date)
    with pytest.raises(SourceError) as error:
        read_source(path, cfg)
    assert error.value.rule_id == "V05"
    (problem,) = error.value.problems
    assert problem["count"] == MAX_PROBLEM_ROWS, "the scan stopped at the cap, not at the end of the file"
    assert str(error.value).endswith(f"Se dejó de revisar tras {MAX_PROBLEM_ROWS} filas con problemas.")
    assert len(problem["examples"]) == ingest.EXAMPLES


def test_below_the_cap_every_problem_row_is_counted_and_the_message_has_no_suffix(cfg, fixture_csv, tmp_path):
    with pytest.raises(SourceError) as error:
        read_source(many_rows(fixture_csv, tmp_path, MAX_PROBLEM_ROWS - 1, bad_date), cfg)
    assert error.value.problems[0]["count"] == MAX_PROBLEM_ROWS - 1 and "Se dejó de revisar" not in str(error.value)


def test_exactly_the_cap_is_reported_as_the_cap_was_reached(cfg, fixture_csv, tmp_path):
    with pytest.raises(SourceError) as error:
        read_source(many_rows(fixture_csv, tmp_path, MAX_PROBLEM_ROWS, bad_date), cfg)
    assert error.value.problems[0]["count"] == MAX_PROBLEM_ROWS and "Se dejó de revisar" in str(error.value)


def test_every_kind_of_unreadable_row_counts_towards_the_cap(cfg, fixture_csv, tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "MAX_PROBLEM_ROWS", 4)
    table = _parse(fixture_csv.read_bytes())
    short, wrong_date, wrong_int = list(table[1])[:-1], list(table[1]), list(table[1])
    wrong_date[column("order date (DateOrders)")] = "x"
    wrong_int[column("Order Id")] = "x"
    text = io.StringIO(newline="")
    writer = csv.writer(text, lineterminator="\r\n")
    writer.writerow(table[0])
    for row in (short, wrong_date, wrong_int, short, wrong_date, wrong_int, short):
        writer.writerow(row)
    path = tmp_path / "mixed.csv"
    path.write_bytes(text.getvalue().encode(ENCODING))
    with pytest.raises(SourceError) as error:
        read_source(path, cfg)
    assert sum(p["count"] for p in error.value.problems) == 4 and "Se dejó de revisar tras 4 filas" in str(error.value)


def test_problems_of_the_other_rules_are_never_cut_by_the_cap(cfg, fixture_csv, tmp_path):
    """V06 (an unmasked email) and V07 (a repeated item id) are counted without a cap: the cap is for rows that cannot be read."""
    count = 3 * MAX_PROBLEM_ROWS
    path = many_rows(fixture_csv, tmp_path, count, lambda row, i: row.__setitem__(column("Customer Email"), "someone@example.com"))
    with pytest.raises(SourceError) as error:
        read_source(path, cfg)
    assert error.value.rule_id == "V06" and error.value.problems[0]["count"] == count and "Se dejó de revisar" not in str(error.value)
    assert "someone@example.com" not in str(error.value) and "someone@example.com" not in json.dumps(error.value.problems)


# --- R3-write-interim-not-cross-file-atomic ------------------------------------------------------------------------------------------------------


def hashes(folder: Path) -> dict[str, str]:
    return {name: sha256_file(folder / f"{name}.parquet") for name in ("orders", "order_lines", "outcomes")}


def failing_after(monkeypatch, number: int) -> list[str]:
    """Make the `number`-th atomic write of ``ingest`` fail with an I/O error (the writes are: orders, order_lines, outcomes, manifest); return the names written."""
    real, written = ingest.atomic_path, []

    def wrapper(target):
        written.append(Path(target).name)
        if len(written) == number:
            raise OSError(5, "injected I/O error")
        return real(target)

    monkeypatch.setattr(ingest, "atomic_path", wrapper)
    return written


def test_the_manifest_of_hashes_is_written_last_and_names_each_file(fixture_read, tmp_path, monkeypatch):
    frames, report = fixture_read
    written = failing_after(monkeypatch, 99)
    write_interim(frames, report, tmp_path, SHA_A)
    assert written == ["orders.parquet", "order_lines.parquet", "outcomes.parquet", INTERIM_MANIFEST]
    document = json.loads((tmp_path / INTERIM_MANIFEST).read_text(encoding="utf-8"))
    assert document == {"version": 1, "source_sha256": SHA_A, "files": hashes(tmp_path)}
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([INTERIM_MANIFEST, "orders.parquet", "order_lines.parquet", "outcomes.parquet"])
    assert interim_fingerprint(tmp_path) == SHA_A


@pytest.mark.parametrize(("failing_write", "old_set_survives"), [(1, True), (2, False), (3, False), (4, False)])
def test_a_failure_in_the_middle_of_the_writes_leaves_a_set_that_is_refused_never_a_mix_that_is_read(fixture_read, tmp_path, monkeypatch, failing_write, old_set_survives):
    frames, report = fixture_read
    write_interim(frames, report, tmp_path, SHA_A)
    old = hashes(tmp_path)
    failing_after(monkeypatch, failing_write)
    with pytest.raises(OSError, match="injected"):
        write_interim(frames, report, tmp_path, SHA_B)
    monkeypatch.undo()
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")], "no temporary file is left"
    if old_set_survives:
        assert interim_fingerprint(tmp_path) == SHA_A and hashes(tmp_path) == old, "nothing was replaced: the old set is whole"
        assert read_interim(tmp_path)[1] == report
    else:
        assert interim_fingerprint(tmp_path) is None, "some files are new and some old: the set does not exist"
        with pytest.raises(InterimError, match="no coincide|no es"):
            read_interim(tmp_path)
    write_interim(frames, report, tmp_path, SHA_B)
    assert interim_fingerprint(tmp_path) == SHA_B and read_interim(tmp_path)[1] == report, "the next write repairs it"


def test_a_failure_while_writing_the_first_set_leaves_no_set(fixture_read, tmp_path, monkeypatch):
    frames, report = fixture_read
    failing_after(monkeypatch, 3)
    with pytest.raises(OSError, match="injected"):
        write_interim(frames, report, tmp_path, SHA_A)
    monkeypatch.undo()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["order_lines.parquet", "orders.parquet"]
    assert interim_fingerprint(tmp_path) is None
    with pytest.raises(InterimError):
        read_interim(tmp_path)


@pytest.mark.parametrize("damage", ["change_bytes", "delete_file", "delete_manifest", "garbage_manifest", "other_version", "extra_file_listed", "missing_file_listed", "swap_files"])
def test_the_set_is_refused_when_any_file_is_not_the_one_the_manifest_names(fixture_read, tmp_path, damage):
    frames, report = fixture_read
    write_interim(frames, report, tmp_path, SHA_A)
    manifest = tmp_path / INTERIM_MANIFEST
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if damage == "change_bytes":
        (tmp_path / "outcomes.parquet").write_bytes((tmp_path / "outcomes.parquet").read_bytes() + b"\x00")
    elif damage == "delete_file":
        (tmp_path / "order_lines.parquet").unlink()
    elif damage == "delete_manifest":
        manifest.unlink()
    elif damage == "garbage_manifest":
        manifest.write_text("not json", encoding="utf-8")
    elif damage == "other_version":
        manifest.write_text(json.dumps({**document, "version": 2}), encoding="utf-8")
    elif damage == "extra_file_listed":
        manifest.write_text(json.dumps({**document, "files": {**document["files"], "extra": "00"}}), encoding="utf-8")
    elif damage == "missing_file_listed":
        manifest.write_text(json.dumps({**document, "files": {k: v for k, v in document["files"].items() if k != "orders"}}), encoding="utf-8")
    else:
        a, b = tmp_path / "orders.parquet", tmp_path / "outcomes.parquet"
        first, second = a.read_bytes(), b.read_bytes()
        a.write_bytes(second)
        b.write_bytes(first)
    assert interim_fingerprint(tmp_path) is None
    with pytest.raises(InterimError):
        read_interim(tmp_path)


def test_a_fingerprint_that_the_manifest_and_the_files_disagree_about_is_refused(fixture_read, tmp_path):
    frames, report = fixture_read
    write_interim(frames, report, tmp_path, SHA_A)
    document = json.loads((tmp_path / INTERIM_MANIFEST).read_text(encoding="utf-8"))
    (tmp_path / INTERIM_MANIFEST).write_text(json.dumps({**document, "source_sha256": SHA_B}), encoding="utf-8")
    assert interim_fingerprint(tmp_path) is None


def test_a_set_that_is_read_back_is_the_set_that_was_written(fixture_read, tmp_path):
    frames, report = fixture_read
    write_interim(frames, report, tmp_path, SHA_A)
    back, back_report = read_interim(tmp_path)
    assert back_report == report
    for name in ("orders", "order_lines", "outcomes"):
        assert len(getattr(back, name)) == len(getattr(frames, name))
    assert back.orders["order_id"].tolist() == frames.orders["order_id"].tolist() and back.outcomes["real_days"].tolist() == frames.outcomes["real_days"].tolist()


# --- H2, R4-interim-rehash-every-run: what the check of the manifest costs on the real set --------------------------------------------------------------


@pytest.mark.real_data
def test_the_check_of_the_interim_manifest_on_the_real_set_costs_milliseconds_not_seconds(cfg, real_read, tmp_path):
    """The three files are hashed whole on every run (decision D50). Measured on the development machine: the real set is 5.8 MB (orders 1.0, lines 2.9,
    outcomes 1.8) and the check takes about 5 ms against 5.3 s to read the CSV and 0.1 s to read the three files; it grows with the size of the set at the rate
    of SHA-256 (about 1 GB/s here). The bound below is a hundred times what was measured: it fails if the check ever turns into a real cost."""
    import time

    frames, report = real_read
    write_interim(frames, report, tmp_path, cfg.source.accepted_sha256)
    size = sum(p.stat().st_size for p in tmp_path.glob("*.parquet"))
    runs = []
    for _ in range(5):
        started = time.perf_counter()
        assert ingest._interim_problem(tmp_path) is None
        runs.append(time.perf_counter() - started)
    median = sorted(runs)[len(runs) // 2]
    print(f"REAL interim set {size / 1e6:.2f} MB, manifest check median {median * 1000:.1f} ms")
    assert size < 10_000_000 and median < 0.5
