"""H1: the advisories of the native review of U04 that change or pin the download and the task (R4-no-overall-deadline, R4-no-duration-metric,
R3-open-non200-untested, R4-fetch-source-toctou, R3-concurrent-staging-untested, R3-mode-fallback-untested for the task).

The local server has a route that drips the body (``/drip``): a source that is slow but never silent for longer than a read timeout, which only an overall
deadline stops.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import re
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

from latedel import ingest
from latedel.config import load_config
from latedel.ingest import DownloadDeadlineError, DownloadError, download_file, fetch_source, interim_fingerprint
from tests.fixtures.http_server import FileServer
from tests.unit.test_config import refused, variant
from tests.unit.test_fetch import BODY, GOOD, LOCAL, get, http_ctx, snapshot

DRIP = "/drip?step=100&pause=0.05"           # 42 pieces of 100 bytes, about 2 s in all


def leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.rglob("*") if p.is_file())


# --- R4-no-overall-deadline: an overall time limit for the download, with its own error ---------------------------------------------------------------


def test_a_download_that_takes_longer_than_the_overall_deadline_is_refused_with_its_own_error_and_removes_its_staging_file(tmp_path, caplog):
    with FileServer(BODY) as server, caplog.at_level(logging.INFO, logger="latedel"):
        started = time.monotonic()
        with pytest.raises(DownloadDeadlineError) as error:
            download_file(server.url(DRIP), tmp_path / "raw" / "f.csv", GOOD, ["127.0.0.1"], 3, 10**7, 1000, 30, max_seconds=0.5, insecure_hosts=LOCAL)
        elapsed = time.monotonic() - started
    assert isinstance(error.value, DownloadError), "it is a transport failure: the task can be retried"
    assert elapsed < 1.9, "it stopped long before the 2 s that the whole body takes, and long before the 30 s read timeout"
    assert re.search(r"tiempo máximo de 0\.5 s", str(error.value)) and "bytes" in str(error.value)
    assert leftovers(tmp_path) == [], "no destination, no part and no staging file"
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1 and warnings[0].startswith("download failed") and re.search(r"\d+\.\d s", warnings[0])


def test_a_slow_download_inside_the_deadline_succeeds_and_the_log_says_how_long_it_took(tmp_path, caplog):
    with FileServer(BODY) as server, caplog.at_level(logging.INFO, logger="latedel"):
        dest, digest = download_file(server.url(DRIP), tmp_path / "f.csv", GOOD, ["127.0.0.1"], 3, 10**7, 1000, 30, max_seconds=30, insecure_hosts=LOCAL)
    assert dest.read_bytes() == BODY and digest == GOOD and leftovers(tmp_path) == ["f.csv"]
    ends = [r.getMessage() for r in caplog.records if r.getMessage().startswith("download end")]
    assert len(ends) == 1
    seconds = float(re.search(r"in (\d+\.\d) s", ends[0]).group(1))
    assert 1.5 <= seconds <= 10, ends[0]


def test_without_a_deadline_the_download_behaves_as_before(tmp_path):
    with FileServer(BODY) as server:
        dest, digest = download_file(server.url(DRIP), tmp_path / "f.csv", GOOD, ["127.0.0.1"], 3, 10**7, 1000, 30, insecure_hosts=LOCAL)
    assert dest.read_bytes() == BODY and digest == GOOD


def test_the_deadline_also_stops_a_server_that_does_not_answer(tmp_path):
    with FileServer(BODY, delay_s=3.0) as server:
        started = time.monotonic()
        with pytest.raises(DownloadDeadlineError, match="tiempo máximo"):
            download_file(server.url("/hang"), tmp_path / "f.csv", GOOD, ["127.0.0.1"], 3, 10**7, 1000, 30, max_seconds=0.4, insecure_hosts=LOCAL)
        assert time.monotonic() - started < 2.5
    assert leftovers(tmp_path) == []


def test_the_deadline_covers_the_redirects_too(tmp_path):
    """The first hop answers at once with a redirect and the second one hangs: the time the first took counts, and the call ends at the deadline."""
    with FileServer(BODY, delay_s=3.0) as server:
        started = time.monotonic()
        with pytest.raises(DownloadDeadlineError, match="tiempo máximo"):
            download_file(server.url("/redirect?to=/hang"), tmp_path / "f.csv", GOOD, ["127.0.0.1"], 3, 10**7, 1000, 30, max_seconds=0.5, insecure_hosts=LOCAL)
        assert time.monotonic() - started < 2.5
        assert server.paths() == ["/redirect?to=/hang", "/hang"]
    assert leftovers(tmp_path) == []


def test_a_timeout_that_fires_just_before_the_deadline_is_the_deadline_and_one_far_from_it_is_not(tmp_path, monkeypatch):
    """Socket timeouts can fire a few milliseconds early: a hop cut by the time it was given by the deadline is reported as the deadline."""
    import urllib.request

    def cut_after(seconds: float):
        def fake_open(self, request, data=None, timeout=None):
            time.sleep(seconds)
            raise TimeoutError("timed out")

        return fake_open

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", cut_after(0.27))
    with pytest.raises(DownloadDeadlineError, match="tiempo máximo de 0.3 s"):
        download_file("https://data.mendeley.com/x", tmp_path / "f.csv", GOOD, ["data.mendeley.com"], 3, 10**7, 1000, 30, max_seconds=0.3)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", cut_after(0.0))
    with pytest.raises(DownloadError, match="Se agotó el tiempo de espera de 30 s") as error:
        download_file("https://data.mendeley.com/x", tmp_path / "f.csv", GOOD, ["data.mendeley.com"], 3, 10**7, 1000, 30, max_seconds=5)
    assert not isinstance(error.value, DownloadDeadlineError), "a read timeout far from the deadline stays a read timeout"
    assert leftovers(tmp_path) == []


def test_a_deadline_that_is_already_spent_requests_nothing(tmp_path):
    with FileServer(BODY) as server:
        with pytest.raises(DownloadDeadlineError):
            download_file(server.url("/chain/3"), tmp_path / "f.csv", GOOD, ["127.0.0.1"], 3, 10**7, 1000, 30, max_seconds=1e-9, insecure_hosts=LOCAL)
        assert server.requests == [], "a spent deadline stops before the first hop"
    assert leftovers(tmp_path) == []


def test_a_deadline_that_is_a_refused_address_does_not_hide_the_rule(tmp_path):
    from latedel.schema import SourceError

    with pytest.raises(SourceError, match=r"^V02:.*HTTPS"):
        download_file("http://127.0.0.1:9/x", tmp_path / "f.csv", GOOD, ["127.0.0.1"], 3, 10**7, 1000, 30, max_seconds=0.0001)


def test_the_task_passes_the_configured_deadline_and_a_deadline_writes_nothing(cfg, tmp_path):
    with FileServer(BODY) as server:
        ctx = http_ctx(cfg, tmp_path, server, GOOD, DRIP)
        ctx.cfg = dataclasses.replace(ctx.cfg, source=dataclasses.replace(ctx.cfg.source, max_seconds=0.5, chunk_bytes=1000))
        with pytest.raises(DownloadDeadlineError):
            fetch_source(ctx, insecure_hosts=LOCAL)
    assert snapshot(tmp_path) == {}


def test_the_task_reports_how_long_the_download_took(cfg, tmp_path, fixture_csv):
    data = fixture_csv.read_bytes()
    with FileServer(data) as server:
        result = fetch_source(http_ctx(cfg, tmp_path, server, hashlib.sha256(data).hexdigest()), insecure_hosts=LOCAL)
        again = fetch_source(http_ctx(cfg, tmp_path, server, hashlib.sha256(data).hexdigest()), insecure_hosts=LOCAL)
    assert result["downloaded"] is True and 0 <= result["download_seconds"] < 30
    assert again["downloaded"] is False and again["download_seconds"] == 0.0


@pytest.mark.parametrize(("snippet", "bad"), [("max_seconds = 0", "0"), ("max_seconds = -5", "-5"), ("max_seconds = inf", "inf"), ("max_seconds = nan", "nan"),
                                              ('max_seconds = "60"', "60")])
def test_the_configured_deadline_must_be_a_positive_finite_number(tmp_path, snippet, bad):
    refused(tmp_path, r"\[source\] max_seconds", snippet=f"[source]\n{snippet}\n")
    assert bad


def test_the_default_deadline_is_a_positive_number_of_seconds(cfg, tmp_path):
    assert cfg.source.max_seconds == 900.0
    assert load_config(variant(tmp_path, snippet="[source]\nmax_seconds = 30\n")).source.max_seconds == 30.0


# --- R3-open-non200-untested: a 2xx that is not 200 is not a download -----------------------------------------------------------------------------------


@pytest.mark.parametrize("code", [201, 202, 203, 204, 206])
def test_a_success_status_other_than_200_is_a_download_error_that_names_it_and_writes_nothing(tmp_path, caplog, code):
    with FileServer(BODY) as server, caplog.at_level(logging.WARNING, logger="latedel"):
        with pytest.raises(DownloadError, match=rf"HTTP {code} y se esperaba 200"):
            get(server, f"/status/{code}", tmp_path / "raw" / "f.csv")
        assert server.paths() == [f"/status/{code}"]
    assert leftovers(tmp_path) == [] and not (tmp_path / "raw").exists()
    assert [r.getMessage()[:15] for r in caplog.records if r.levelno >= logging.WARNING] == ["download failed"]


def test_the_task_does_not_accept_a_partial_content_answer_either(cfg, tmp_path):
    with FileServer(BODY) as server:
        with pytest.raises(DownloadError, match="HTTP 206"):
            fetch_source(http_ctx(cfg, tmp_path, server, GOOD, "/status/206"), insecure_hosts=LOCAL)
    assert snapshot(tmp_path) == {}


# --- R4-fetch-source-toctou: the stored report can disappear between two reads; the task rebuilds instead of failing with AttributeError --------------------


@pytest.mark.parametrize("vanishes_at", [1, 2, 3])
def test_a_stored_report_that_is_not_there_when_read_makes_the_task_rebuild_not_fail(cfg, tmp_path, fixture_csv, monkeypatch, vanishes_at):
    data = fixture_csv.read_bytes()
    with FileServer(data) as server:
        ctx = http_ctx(cfg, tmp_path, server, hashlib.sha256(data).hexdigest())
        first = fetch_source(ctx, insecure_hosts=LOCAL)
        assert first["built"] is True
        real, calls = ingest.load_source_report, {"n": 0}

        def flaky(interim_dir):
            calls["n"] += 1
            return None if calls["n"] == vanishes_at else real(interim_dir)

        monkeypatch.setattr(ingest, "load_source_report", flaky)
        second = fetch_source(ctx, insecure_hosts=LOCAL)
    assert calls["n"] == 1, "the stored report is read once per run, together with the fingerprint: there is no second read to race with"
    assert second["orders"] == first["orders"] and second["lines"] == first["lines"] and second["task_orders"] == first["task_orders"]
    monkeypatch.undo()
    assert interim_fingerprint(tmp_path / "interim") == hashlib.sha256(data).hexdigest(), "whatever happened, the streams end up consistent"


# --- R3-concurrent-staging-untested: two overlapping calls on one destination -------------------------------------------------------------------------


@pytest.mark.parametrize(("first", "expected"), [
    ("good", ["f.csv", "f.csv.part"]),      # the accepted download finishes first: the other one then keeps its whole body as the part
    ("bad", ["f.csv"]),                     # the other one finishes first: the accepted one then supersedes the part that it left (documented in U04)
])
def test_two_downloads_of_one_destination_at_the_same_time_never_mix_their_bytes(tmp_path, first, expected):
    """One call gets the accepted body, the other a body with another hash, both at once to the same destination. Whatever the order in which they finish,
    the accepted file is the verified one, whole; a part, if there is one, is the other body, whole; neither leaves a staging file. (Two calls that both
    succeed replace each other with the same verified bytes; that case is not asserted because Windows may refuse the second rename while the first file is
    being replaced.)

    The order is set by events, not by time (R3-concurrent-staging-timing-flake): each server sends half of its body and waits at a gate; the test waits until
    both calls are in the middle of their download, opens the gate of the one that must finish first, waits for it to end, and only then opens the other."""
    from latedel.schema import SourceError

    wrong = BODY + b"another body, another hash"
    outcomes: dict[str, object] = {}

    def run(name: str, url: str) -> None:
        try:
            outcomes[name] = download_file(url, tmp_path / "raw" / "f.csv", GOOD, ["127.0.0.1"], 3, 10**7, 1000, 30, max_seconds=60, insecure_hosts=LOCAL)
        except Exception as error:  # noqa: BLE001 - the outcome is what the test inspects
            outcomes[name] = error

    with FileServer(BODY) as good, FileServer(wrong) as bad:
        servers = {"good": good, "bad": bad}
        gates = {"good": good.gate("g"), "bad": bad.gate("b")}
        threads = {name: threading.Thread(target=run, args=(name, servers[name].url(f"/gated?id={name[0]}"))) for name in servers}
        for thread in threads.values():
            thread.start()
        assert all(gate.started.wait(timeout=20) for gate in gates.values()), "both downloads are in the middle of their body"
        for name in (first, "bad" if first == "good" else "good"):
            gates[name].release.set()
            threads[name].join(timeout=30)
            assert not threads[name].is_alive(), f"{name} finished before the other one was let go"
    assert outcomes["good"][1] == GOOD and isinstance(outcomes["bad"], SourceError) and outcomes["bad"].rule_id == "V01"
    raw = tmp_path / "raw"
    assert (raw / "f.csv").read_bytes() == BODY, "the accepted file is the good body, complete"
    if "f.csv.part" in expected:
        assert (raw / "f.csv.part").read_bytes() == wrong, "the kept part is the other body, complete"
    assert leftovers(raw) == expected, "no staging file is left by either"
    assert hashlib.sha256((raw / "f.csv").read_bytes()).hexdigest() == GOOD


def test_the_staging_names_of_two_calls_never_collide(tmp_path):
    import os

    names = set()
    for _ in range(50):
        descriptor, path = ingest._create_staging(tmp_path / "f.csv")
        os.close(descriptor)
        names.add(path.name)
    assert len(names) == 50 and all(n.endswith(".staging") for n in names)


# --- H2: the advisories of the native review of H1 ----------------------------------------------------------------------------------------------------


# R4-deadline-error-missing-hop-context: the message of the deadline says which host was in progress

from tests.fixtures.http_server import ALT_HOST  # noqa: E402

BOTH = frozenset({"127.0.0.1", ALT_HOST})


def test_the_deadline_during_a_redirect_chain_names_the_hop_and_the_host_that_used_the_time(tmp_path):
    """The first hop (127.0.0.1) answers at once with a redirect to the second host (127.0.0.2), which hangs: the error says that the second hop was waited for."""
    with FileServer(BODY, delay_s=3.0) as server:
        target = urllib.parse.quote(server.url("/hang", host=ALT_HOST), safe="")
        with pytest.raises(DownloadDeadlineError) as error:
            download_file(server.url(f"/redirect?to={target}"), tmp_path / "f.csv", GOOD, ["127.0.0.1", ALT_HOST], 3, 10**7, 1000, 30, max_seconds=0.5, insecure_hosts=BOTH)
    text = str(error.value)
    assert f"salto 2 ({ALT_HOST})" in text and "127.0.0.1)" not in text, text
    assert "tiempo máximo de 0.5 s" in text and "docs/runbook.md, R2" in text
    assert leftovers(tmp_path) == []


def test_the_deadline_while_the_body_is_being_received_names_the_host_and_the_bytes(tmp_path):
    with FileServer(BODY) as server:
        with pytest.raises(DownloadDeadlineError) as error:
            download_file(server.url(DRIP), tmp_path / "f.csv", GOOD, ["127.0.0.1"], 3, 10**7, 1000, 30, max_seconds=0.5, insecure_hosts=LOCAL)
    assert re.search(r"tras recibir \d+ bytes de 127\.0\.0\.1", str(error.value)), str(error.value)
    assert "salto" not in str(error.value)


def test_a_deadline_already_spent_names_the_hop_that_was_not_requested(tmp_path):
    with FileServer(BODY) as server:
        with pytest.raises(DownloadDeadlineError, match=r"salto 1 \(127\.0\.0\.1\)"):
            download_file(server.url("/file.csv"), tmp_path / "f.csv", GOOD, ["127.0.0.1"], 3, 10**7, 1000, 30, max_seconds=1e-9, insecure_hosts=LOCAL)
        assert server.requests == []


def test_the_error_without_a_host_is_the_one_it_was_before(tmp_path):
    """The previous message, with no place: what ``_deadline_error`` says when it is not told one."""
    assert str(ingest._deadline_error(5.0)) == (
        "La descarga superó el tiempo máximo de 5 s (source.max_seconds); se interrumpió y no se guardó nada. "
        "Si el origen es lento, se puede subir ese límite; si no responde, ver docs/runbook.md, R2."
    )
    assert str(ingest._deadline_error(5.0, 12)) == (
        "La descarga superó el tiempo máximo de 5 s (source.max_seconds) tras recibir 12 bytes; se interrumpió y no se guardó nada. "
        "Si el origen es lento, se puede subir ese límite; si no responde, ver docs/runbook.md, R2."
    )


# R3-single-bucket-host-allowlist-fragility: a host that is not on the list says what to do if the source moved


def test_a_redirect_to_a_host_that_is_not_on_the_list_says_where_to_look_if_the_bucket_changed(tmp_path):
    from latedel.schema import SourceError

    with FileServer(BODY) as server:
        target = urllib.parse.quote(server.url("/file.csv", host=ALT_HOST), safe="")
        with pytest.raises(SourceError) as error:
            download_file(server.url(f"/redirect?to={target}"), tmp_path / "f.csv", GOOD, ["127.0.0.1"], 3, 10**7, 1000, 30, insecure_hosts=BOTH)
    text = str(error.value)
    assert text.startswith("V02:") and f"el host '{ALT_HOST}'" in text and "no está en la lista de hosts permitidos" in text
    assert "[source] allowed_hosts" in text and "docs/runbook.md, R2 y R11" in text
    assert error.value.problems[0]["host"] == ALT_HOST and leftovers(tmp_path) == []


# R3-deadline-slack-timer-assumption: the slack is pinned against the timer, with the numbers that were measured


def test_the_deadline_slack_covers_more_than_two_ticks_of_the_coarsest_timer_and_stays_small():
    """Windows ticks every 15.6 ms. Measured on the development machine over 172 socket timeouts of 0.02 to 1 s: none fired early, all 4 to 29 ms late (a
    lateness is never misread: a timeout after the deadline is the deadline). The slack of 50 ms is more than three ticks, and short enough that a timeout
    that really was a read timeout, far from the deadline, stays one."""
    tick = 0.015625
    assert 2 * tick < ingest.DEADLINE_SLACK_S <= 0.1
