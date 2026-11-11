"""The documentation figures (U24, plan section 2), drawn from the site files of the fixture run: files, data and failures."""

from __future__ import annotations

import json
import shutil

import matplotlib.image as mpimg
import pytest

from latedel import figures

EXPECTED = ["split.png", "mode_rates.png", "noon_cutoff.png", "leakage.png", "methods.png", "cost_curve.png", "alerts.png"]
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def site(fixture_site) -> dict:
    return figures.load_site(fixture_site)


@pytest.fixture(scope="module")
def drawn(site) -> dict:
    return figures.draw_all(site)


@pytest.fixture(scope="module")
def written(fixture_site, tmp_path_factory):
    """The seven files written once from the site files of the fixture into a folder that held a figure of an earlier run: ``(folder, paths, {name: bytes})``.
    The tests that compare a drawing with another one compare with these bytes, so each draws once instead of twice."""
    out = tmp_path_factory.mktemp("written") / "figures"
    out.mkdir()
    (out / "split.png").write_bytes(b"old")   # a figure of an earlier run is replaced, not appended to
    paths = figures.make_figures(fixture_site, out)
    return out, paths, {p.name: p.read_bytes() for p in paths}


def test_figures_written_from_fixture_outputs(written):
    out, paths, _bytes = written
    assert [p.name for p in paths] == EXPECTED == list(figures.FIGURE_NAMES)
    assert sorted(p.name for p in out.iterdir()) == sorted(EXPECTED)   # nothing else, not even a temporary file
    for path in paths:
        assert path.parent == out
        assert path.read_bytes()[:8] == PNG_SIGNATURE
        height, width = mpimg.imread(path).shape[:2]
        assert width >= 900 and height >= 450   # readable in a README at full width


def test_drawing_twice_gives_the_same_files(fixture_site, written, tmp_path):
    first = [written[2][name] for name in EXPECTED]                                       # the first drawing, by the module fixture
    second = [p.read_bytes() for p in figures.make_figures(fixture_site, tmp_path / "b")]
    assert first == second   # a versioned figure changes only when the data does


def test_a_figure_changes_when_its_data_change(fixture_site, written, tmp_path):
    changed = tmp_path / "site"
    shutil.copytree(fixture_site, changed)
    document = json.loads((changed / "alerts.json").read_text(encoding="utf-8"))
    document["monthly"][-1]["alerts"] += 100
    (changed / "alerts.json").write_text(json.dumps(document), encoding="utf-8")
    original = written[2]                                                                 # the drawing of the unchanged files
    new = {p.name: p.read_bytes() for p in figures.make_figures(changed, tmp_path / "b")}
    assert {name for name in EXPECTED if original[name] != new[name]} == {"alerts.png"}


# --- the data behind each figure -----------------------------------------------------------------------------------------------------------


def test_the_split_figure_draws_the_seven_blocks_with_their_dates(drawn, site):
    [ax] = drawn["split.png"].axes
    rows = site["algorithms"]["split"]
    assert len(rows) == 7 and [" ".join(t.get_text().split()) for t in ax.get_yticklabels()] == [r["label_es"] for r in rows]   # the labels are wrapped
    from datetime import date

    lefts = [p.get_x() for p in ax.patches]
    widths = [p.get_width() for p in ax.patches]
    assert lefts == [date.fromisoformat(r["start"]).toordinal() for r in rows]
    assert widths == [date.fromisoformat(r["end"]).toordinal() + 1 - date.fromisoformat(r["start"]).toordinal() for r in rows]


def test_the_mode_figure_draws_the_late_rate_of_each_mode_and_greys_a_hidden_cell(drawn, site):
    [ax] = drawn["mode_rates.png"].axes
    rows = site["algorithms"]["mode_rates"]
    assert len(rows) == 4
    assert [p.get_height() for p in ax.patches] == [0.0 if r["suppressed"] else r["train_late_rate"] for r in rows]
    assert [t.get_text().split("\n")[0] for t in ax.get_xticklabels()] == [r["mode_es"] for r in rows]


def test_the_noon_figure_draws_only_the_hours_that_the_site_publishes_and_the_cut_at_noon(drawn, site):
    [ax] = drawn["noon_cutoff.png"].axes
    rows = [r for r in site["algorithms"]["hour_profile"] if r["mode"] == "Same Day"]
    shown = [r for r in rows if not r["suppressed"]]
    curve, cut = ax.lines
    assert list(curve.get_xdata()) == [r["hour"] for r in shown] and list(curve.get_ydata()) == [r["train_late_rate"] for r in shown]
    assert list(cut.get_xdata()) == [12, 12] and f"{len(rows) - len(shown)} de 24" in ax.get_xlabel()


def test_the_leakage_figure_draws_the_auc_of_each_measured_column_next_to_the_mode_alone(drawn, site):
    [ax] = drawn["leakage.png"].axes
    rows = [c for c in site["leakage"]["columns"] if c["auc_with_mode_valid"] is not None]
    assert len(rows) >= 7
    assert [p.get_width() for p in ax.patches] == [r["auc_with_mode_valid"] for r in rows]
    assert [t.get_text() for t in ax.get_yticklabels()] == [r["column_es"] for r in rows]
    [reference] = ax.lines
    assert list(reference.get_xdata()) == [site["leakage"]["reference"]["auc_mode_only_valid"]] * 2


def test_the_methods_figure_draws_the_three_methods_of_the_test_for_the_auc_and_the_net_benefit(drawn, site):
    left, right = drawn["methods.png"].axes
    rows = [m for m in site["algorithms"]["methods"] if m["scope"] == "test"]
    assert [m["method"] for m in rows] == ["model", "b2_mode", "b5_mode_noon"]
    assert [p.get_height() for p in left.patches] == [m["auc"] for m in rows]
    assert [p.get_height() for p in right.patches] == [m["net_benefit_per_1000"] for m in rows]


def test_the_cost_figure_draws_the_curve_of_each_method_and_marks_the_assumed_break_even(drawn, site):
    [ax] = drawn["cost_curve.png"].axes
    rows = site["algorithms"]["cost_curve"]
    assert len(rows) == 17
    model, b2, b5, assumed = ax.lines
    for line, key in ((model, "nb_model"), (b2, "nb_b2_mode"), (b5, "nb_b5_mode_noon")):
        assert list(line.get_xdata()) == [r["break_even"] for r in rows] and list(line.get_ydata()) == [r[key] for r in rows], key
    assert list(assumed.get_xdata()) == [site["algorithms"]["threshold"]["break_even"]] * 2


def test_the_alert_figure_draws_the_orders_and_alerts_and_the_late_orders_of_each_visible_month(drawn, site):
    left, right = drawn["alerts.png"].axes
    rows = [r for r in site["alerts"]["monthly"] if not r["suppressed"]]
    assert rows
    without, with_alert = left.patches[: len(rows)], left.patches[len(rows):]
    assert [p.get_height() for p in with_alert] == [r["alerts"] for r in rows]
    assert [p.get_height() for p in without] == [r["orders"] - r["alerts"] for r in rows]
    expected, observed = right.lines
    assert list(expected.get_ydata()) == [r["late_expected"] for r in rows] and list(observed.get_ydata()) == [r["late_observed"] for r in rows]
    assert [t.get_text() for t in left.get_xticklabels()] == [r["month"] for r in rows]


# --- before the test is evaluated, with a hidden cell, and failures ---------------------------------------------------------------------------------


def test_before_the_test_is_evaluated_the_figures_of_its_results_say_so_instead_of_staying_empty(pipeline_two_runs, tmp_path):
    early = pipeline_two_runs / "outputs" / "site"
    algorithms = json.loads((early / "algorithms.json").read_text(encoding="utf-8"))
    assert algorithms["methods"] == [] and algorithms["cost_curve"] == []   # the site shows nothing of the test before it is evaluated (D17)
    made = figures.draw_all(figures.load_site(early))
    for name in ("methods.png", "cost_curve.png"):
        assert {" ".join(t.get_text().split()) for ax in made[name].axes for t in ax.texts} == {figures.PENDING}, name   # the text is wrapped to the width of the panel
    assert made["mode_rates.png"].axes[0].patches and made["alerts.png"].axes[0].patches   # what exists is still drawn
    assert len(figures.make_figures(early, tmp_path / "out")) == 7


def test_a_hidden_cell_is_a_gap_and_never_a_number(site, fixture_site, tmp_path):
    hidden = json.loads(json.dumps(site["algorithms"]))
    for row in hidden["mode_rates"][:1]:
        row.update(suppressed=True, train_orders=None, train_late_rate=None)
    for row in hidden["hour_profile"]:
        if row["mode"] == "Same Day" and row["hour"] in (3, 4):
            row.update(suppressed=True, train_orders=None, train_late_rate=None)
    made = figures.draw_all({**site, "algorithms": hidden})
    assert made["mode_rates.png"].axes[0].patches[0].get_height() == 0.0 and any(t.get_text() == "celda oculta" for t in made["mode_rates.png"].axes[0].texts)
    drawn_hours = list(made["noon_cutoff.png"].axes[0].lines[0].get_xdata())
    assert 3 not in drawn_hours and 4 not in drawn_hours


def test_nothing_is_written_if_a_figure_fails_or_a_site_file_is_missing(fixture_site, tmp_path, monkeypatch):
    out = tmp_path / "figures"

    def boom(_site):
        raise RuntimeError("boom")

    monkeypatch.setattr(figures, "draw_cost_curve", boom)
    with pytest.raises(RuntimeError, match="boom"):
        figures.make_figures(fixture_site, out)
    assert not out.exists()
    monkeypatch.undo()
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(FileNotFoundError, match=r"algorithms\.json.*python -m latedel run"):
        figures.make_figures(bare, out)
    assert not out.exists()
