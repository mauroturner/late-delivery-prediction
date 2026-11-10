"""Download, fingerprint and redirects to allowed hosts only (U04, item 2: the download is untrusted): AC01 and AC02, RF01, RF02, V01, V02."""

from __future__ import annotations

import dataclasses
import errno
import hashlib
import json
import logging
import os
import secrets
import types
import urllib.parse
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from latedel import ingest
from latedel.ingest import (
    MANIFEST_NAME,
    STAGING_ATTEMPTS,
    USER_AGENT,
    DownloadError,
    SourceManifest,
    check_hop,
    download_file,
    fetch_source,
    interim_fingerprint,
    load_source_report,
    read_source,
    read_source_manifest,
    write_source_manifest,
)
from latedel.schema import SourceError
from tests.fixtures.http_server import ALT_HOST as ALT
from tests.fixtures.http_server import FileServer

LOCAL = frozenset({"127.0.0.1"})
BODY = b"a,b,c\r\n1,2,3\r\n" * 300


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


GOOD = sha(BODY)
BAD = "0" * 64


def snapshot(folder: Path) -> dict[str, str]:
    """Relative path -> SHA-256 of every file under `folder` (empty if it does not exist)."""
    if not folder.exists():
        return {}
    return {p.relative_to(folder).as_posix(): sha(p.read_bytes()) for p in sorted(folder.rglob("*")) if p.is_file()}


def get(server: FileServer, path: str, dest: Path, digest: str = GOOD, *, hosts=("127.0.0.1",), redirects: int = 3, cap: int = 10_000_000, chunk: int = 1000,
        timeout: int = 10, insecure=LOCAL):
    return download_file(server.url(path), dest, digest, list(hosts), redirects, cap, chunk, timeout, insecure_hosts=insecure)


# --- check_hop: the rule applied to every address, the first and each redirect ---------------------------------------


@pytest.mark.parametrize(("url", "host"), [
    ("https://data.mendeley.com/public-files/x/file_downloaded", "data.mendeley.com"),
    ("https://bucket.s3.eu-west-1.amazonaws.com/a/b?X-Amz-Signature=abc&X-Amz-Expires=300", "bucket.s3.eu-west-1.amazonaws.com"),
    ("https://DATA.mendeley.com:443/a", "data.mendeley.com"),
])
def test_check_hop_accepts_https_to_a_listed_host(url, host):
    assert check_hop(url, ("data.mendeley.com", ".amazonaws.com")) == host


@pytest.mark.parametrize(("url", "match"), [
    ("http://data.mendeley.com/a", "HTTPS"),
    ("ftp://data.mendeley.com/a", "esquema"),
    ("file:///etc/passwd", "esquema"),
    ("gopher://data.mendeley.com/", "esquema"),
    ("https://evil.example.com/a", "no está en la lista"),
    ("https://data.mendeley.com.evil.com/a", "no está en la lista"),
    ("https://amazonaws.com/a", "no está en la lista"),
    ("https://evilamazonaws.com/a", "no está en la lista"),
    ("https://data.mendeley.com@evil.com/a", "dirección"),
    ("https://evil.com@data.mendeley.com/a", "dirección"),
    ("https://evil.com\\@data.mendeley.com/a", "dirección"),
    ("https://data.mendeley.com/a b", "dirección"),
    ("https://data.mendeley.com/a\r\nX: y", "dirección"),
    ("https://data.mendeley.com:8443/a", "puerto"),
    ("https://data.mendeley.com:80/a", "puerto"),
    ("//data.mendeley.com/a", "esquema"),
    ("", "dirección"),
])
def test_check_hop_refuses_with_rule_v02_and_says_why(url, match):
    with pytest.raises(SourceError, match=rf"^V02:.*{match}") as error:
        check_hop(url, ("data.mendeley.com", ".amazonaws.com"))
    assert error.value.rule_id == "V02" and error.value.problems


def test_check_hop_lets_a_test_use_plain_http_on_named_hosts_only():
    assert check_hop("http://127.0.0.1:8080/x", ("127.0.0.1",), frozenset({"127.0.0.1"})) == "127.0.0.1"
    with pytest.raises(SourceError, match="HTTPS"):
        check_hop("http://127.0.0.2:8080/x", ("127.0.0.1", ALT), frozenset({"127.0.0.1"}))
    with pytest.raises(SourceError, match="HTTPS"):
        check_hop("http://127.0.0.1:8080/x", ("127.0.0.1",))


# --- download_file: the file, the hash and the part (AC01) -------------------------------------------------------------


def test_download_writes_the_file_and_returns_its_hash(tmp_path):
    with FileServer(BODY) as server:
        dest, digest = get(server, "/file.csv", tmp_path / "raw" / "f.csv")
    assert dest == tmp_path / "raw" / "f.csv" and dest.read_bytes() == BODY and digest == GOOD
    assert [p.name for p in (tmp_path / "raw").iterdir()] == ["f.csv"], "no part and no staging file are left"
    assert server.paths() == ["/file.csv"] and [m for m, _p, _h in server.requests] == ["GET"]
    assert server.requests[0][2]["User-Agent"] == USER_AGENT and USER_AGENT.startswith("latedel-pipeline/")


def test_hash_mismatch_leaves_part_and_changes_nothing(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "f.csv").write_bytes(b"the accepted file from before")
    before = snapshot(raw)
    with FileServer(BODY) as server, pytest.raises(SourceError) as error:
        get(server, "/file.csv", raw / "f.csv", BAD)
    assert error.value.rule_id == "V01" and GOOD in str(error.value) and BAD in str(error.value)
    after = snapshot(raw)
    assert after.pop("f.csv.part") == GOOD, "the whole download is kept, to be inspected"
    assert after == before, "the file that was there is untouched and nothing else was written"


def test_hash_mismatch_with_no_previous_file_creates_no_destination(tmp_path):
    with FileServer(BODY) as server, pytest.raises(SourceError, match="V01"):
        get(server, "/file.csv", tmp_path / "raw" / "f.csv", BAD)
    assert sorted(p.name for p in (tmp_path / "raw").iterdir()) == ["f.csv.part"]


def test_an_accepted_download_removes_the_part_an_earlier_failure_kept(tmp_path):
    with FileServer(BODY) as server:
        with pytest.raises(SourceError):
            get(server, "/file.csv", tmp_path / "f.csv", BAD)
        assert (tmp_path / "f.csv.part").exists()
        get(server, "/file.csv", tmp_path / "f.csv")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.csv"]


def test_a_part_that_cannot_be_removed_does_not_fail_an_accepted_download(tmp_path, caplog, monkeypatch):
    (tmp_path / "f.csv.part").write_bytes(b"old")
    real_unlink = Path.unlink

    def refuse(self, *args, **kwargs):
        if self.name.endswith(".part"):
            raise PermissionError("locked")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    with FileServer(BODY) as server, caplog.at_level(logging.WARNING, logger="latedel"):
        dest, digest = get(server, "/file.csv", tmp_path / "f.csv")
    assert dest.read_bytes() == BODY and digest == GOOD
    assert any("could not be removed" in r.getMessage() for r in caplog.records)


def test_a_response_without_content_length_is_accepted_when_the_hash_matches(tmp_path):
    with FileServer(BODY) as server:
        dest, digest = get(server, "/no-length", tmp_path / "f.csv", chunk=100)
    assert dest.read_bytes() == BODY and digest == GOOD


def test_a_truncated_download_is_a_download_error_and_leaves_nothing(tmp_path):
    with FileServer(BODY) as server, pytest.raises(DownloadError, match="bytes"):
        get(server, "/truncated", tmp_path / "raw" / "f.csv")
    assert list((tmp_path / "raw").iterdir()) == []


@pytest.mark.parametrize(("path", "needle"), [("/status/404", "404"), ("/status/500", "500"), ("/nothing-here", "404")])
def test_http_errors_are_download_errors_that_name_the_status(tmp_path, path, needle):
    with FileServer(BODY) as server, pytest.raises(DownloadError, match=needle) as error:
        get(server, path, tmp_path / "f.csv")
    if needle == "404":
        assert "R11" in str(error.value)
    assert not (tmp_path / "f.csv").exists() and not (tmp_path / "f.csv.part").exists()


def test_a_timeout_is_a_download_error(tmp_path):
    with FileServer(BODY, delay_s=3.0) as server, pytest.raises(DownloadError, match="tiempo"):
        get(server, "/hang", tmp_path / "f.csv", timeout=1)
    assert list(tmp_path.iterdir()) == []


def test_a_refused_connection_is_a_download_error(tmp_path):
    with FileServer(BODY) as server:
        url = server.url("/file.csv")
    with pytest.raises(DownloadError, match="conectar"):
        download_file(url, tmp_path / "f.csv", GOOD, ["127.0.0.1"], 3, 10**7, 1000, 2, insecure_hosts=LOCAL)
    assert list(tmp_path.iterdir()) == []


def test_every_transport_failure_is_logged_before_it_is_raised(tmp_path, caplog):
    with FileServer(BODY) as server, caplog.at_level(logging.WARNING, logger="latedel"):
        for path in ("/status/500", "/truncated"):
            with pytest.raises(DownloadError):
                get(server, path, tmp_path / "f.csv")
    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(messages) == 2 and all(m.startswith("download failed") for m in messages)


def test_a_successful_download_logs_no_warning(tmp_path, caplog):
    with FileServer(BODY) as server, caplog.at_level(logging.INFO, logger="latedel"):
        get(server, "/file.csv", tmp_path / "f.csv")
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("download end" in r.getMessage() for r in caplog.records)


# --- the size cap (V02) --------------------------------------------------------------------------------------------------


def test_a_download_above_the_cap_is_refused_before_it_is_written_when_the_length_is_announced(tmp_path):
    with FileServer(BODY) as server, pytest.raises(SourceError, match=r"^V02:.*límite") as error:
        get(server, "/file.csv", tmp_path / "raw" / "f.csv", cap=len(BODY) - 1)
    assert error.value.problems[0]["max_bytes"] == len(BODY) - 1
    assert not (tmp_path / "raw").exists(), "nothing was created"
    with FileServer(BODY) as server:
        dest, _ = get(server, "/file.csv", tmp_path / "ok" / "f.csv", cap=len(BODY))
    assert dest.read_bytes() == BODY, "a body of exactly the cap is accepted"


def test_a_huge_announced_length_is_refused_without_reading_a_byte(tmp_path):
    with FileServer(BODY) as server, pytest.raises(SourceError, match=r"^V02:.*1000000000000"):
        get(server, "/huge", tmp_path / "f.csv")
    assert list(tmp_path.iterdir()) == []


def test_an_endless_body_without_length_is_cut_at_the_cap_and_removed(tmp_path):
    with FileServer(BODY) as server, pytest.raises(SourceError, match=r"^V02:.*supera el límite"):
        get(server, "/endless", tmp_path / "raw" / "f.csv", cap=1_000_000, chunk=65536)
    assert list((tmp_path / "raw").iterdir()) == []


# --- redirects: only to allowed hosts, over HTTPS, a bounded number of times (AC02) ----------------------------------------


def test_redirect_to_unlisted_host_is_refused_before_following(tmp_path):
    with FileServer(BODY) as server:
        target = server.url("/file.csv", host=ALT)
        with pytest.raises(SourceError, match=r"^V02:.*127.0.0.2.*no está en la lista") as error:
            get(server, f"/redirect?to={target}", tmp_path / "raw" / "f.csv", insecure=frozenset({"127.0.0.1", ALT}))
        assert server.paths() == [f"/redirect?to={target}"], "the second address was never requested"
        assert not any(h.startswith(ALT) for h in server.hosts())
    assert error.value.problems[0]["host"] == ALT and error.value.problems[0]["hop"] == 1
    assert not (tmp_path / "raw").exists(), "nothing was created: no folder, no staging file, no part"


def test_redirect_to_a_listed_host_is_followed_and_the_hash_still_decides(tmp_path):
    both = frozenset({"127.0.0.1", ALT})
    with FileServer(BODY) as server:
        target = server.url("/file.csv", host=ALT)
        dest, digest = get(server, f"/redirect?to={target}", tmp_path / "f.csv", hosts=("127.0.0.1", ALT), insecure=both)
        assert dest.read_bytes() == BODY and digest == GOOD
        assert [h.split(":")[0] for h in server.hosts()] == ["127.0.0.1", ALT]
        with pytest.raises(SourceError, match="V01"):
            get(server, f"/redirect?to={target}", tmp_path / "other.csv", BAD, hosts=("127.0.0.1", ALT), insecure=both)


def test_a_redirect_from_a_host_that_may_use_http_to_one_that_may_not_is_refused_even_if_listed(tmp_path):
    """The shape of a downgrade: the next hop is listed, but it is plain http and the host is not one of the test's."""
    with FileServer(BODY) as server:
        target = server.url("/file.csv", host=ALT)
        with pytest.raises(SourceError, match=r"^V02:.*HTTPS"):
            get(server, f"/redirect?to={target}", tmp_path / "f.csv", hosts=("127.0.0.1", ALT), insecure=LOCAL)
        assert len(server.requests) == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(("location", "match"), [
    ("ftp://127.0.0.1/file.csv", "esquema"),
    ("file:///etc/passwd", "esquema"),
    ("http://127.0.0.1@127.0.0.2/file.csv", "dirección"),
    ("http://127.0.0.1\\@127.0.0.2/file.csv", "dirección"),
    ("https://evil.example.com/file.csv", "no está en la lista"),
    ("//127.0.0.2:9/file.csv", "(esquema|no está en la lista)"),
])
def test_every_unsafe_redirect_target_is_refused_and_not_requested(tmp_path, location, match):
    with FileServer(BODY) as server:
        with pytest.raises(SourceError, match=rf"^V02:.*{match}"):
            get(server, "/redirect?to=" + urllib.parse.quote(location, safe=""), tmp_path / "f.csv", insecure=frozenset({"127.0.0.1", ALT}))
        assert len(server.requests) == 1
    assert list(tmp_path.iterdir()) == []


def test_a_relative_redirect_is_resolved_against_the_current_address(tmp_path):
    with FileServer(BODY) as server:
        dest, digest = get(server, "/redirect?to=/file.csv", tmp_path / "f.csv")
        assert server.paths() == ["/redirect?to=/file.csv", "/file.csv"]
    assert digest == GOOD and dest.read_bytes() == BODY


@pytest.mark.parametrize(("length", "limit", "ok"), [(0, 0, True), (3, 3, True), (4, 3, False), (1, 0, False), (10, 3, False)])
def test_the_number_of_redirects_is_bounded(tmp_path, length, limit, ok):
    with FileServer(BODY) as server:
        if ok:
            dest, _ = get(server, f"/chain/{length}", tmp_path / "f.csv", redirects=limit)
            assert dest.read_bytes() == BODY and len(server.requests) == length + 1
        else:
            with pytest.raises(SourceError, match=r"^V02:.*redirecciones") as error:
                get(server, f"/chain/{length}", tmp_path / "f.csv", redirects=limit)
            assert len(server.requests) == limit + 1, "the redirect over the limit is not followed"
            assert error.value.problems[0]["max_redirects"] == limit
            assert list(tmp_path.iterdir()) == []


def test_a_redirect_loop_ends_at_the_limit_and_a_redirect_without_location_is_an_error(tmp_path):
    with FileServer(BODY) as server:
        with pytest.raises(SourceError, match=r"^V02:.*redirecciones"):
            get(server, "/loop", tmp_path / "f.csv", redirects=3)
        assert len(server.requests) == 4
        with pytest.raises(SourceError, match=r"^V02:.*Location"):
            get(server, "/no-location", tmp_path / "f.csv")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_every_redirect_status_is_followed_through_the_same_checks(tmp_path, code):
    with FileServer(BODY) as server:
        dest, _ = get(server, f"/redirect?to=/file.csv&code={code}", tmp_path / "f.csv")
        assert dest.read_bytes() == BODY and server.paths()[-1] == "/file.csv"
        with pytest.raises(SourceError, match=r"^V02:"):
            get(server, f"/redirect?to=ftp://127.0.0.1/x&code={code}", tmp_path / "g.csv")


def test_the_log_names_the_host_of_every_hop(tmp_path, caplog):
    both = frozenset({"127.0.0.1", ALT})
    with FileServer(BODY) as server, caplog.at_level(logging.INFO, logger="latedel"):
        target = server.url("/file.csv", host=ALT)
        get(server, f"/redirect?to={target}", tmp_path / "f.csv", hosts=("127.0.0.1", ALT), insecure=both)
    hops = [r.getMessage() for r in caplog.records if r.getMessage().startswith("download hop")]
    assert len(hops) == 2 and "127.0.0.1" in hops[0] and ALT in hops[1]


# --- the first address is checked before any connection ---------------------------------------------------------------------


@pytest.mark.parametrize(("url", "match"), [
    ("file:///etc/passwd", "esquema"),
    ("ftp://127.0.0.1/x", "esquema"),
    ("http://127.0.0.1:9/x", "HTTPS"),
    ("https://evil.example.com/x", "no está en la lista"),
])
def test_the_first_address_is_checked_before_connecting_and_nothing_is_created(tmp_path, url, match):
    with pytest.raises(SourceError, match=rf"^V02:.*{match}"):
        download_file(url, tmp_path / "raw" / "f.csv", GOOD, ["data.mendeley.com"], 3, 10**7, 1000, 5)
    assert not (tmp_path / "raw").exists()


# --- local faults keep their own type --------------------------------------------------------------------------------------


def test_a_destination_folder_that_cannot_be_created_is_an_os_error_not_a_download_error(tmp_path, caplog):
    (tmp_path / "blocked").write_bytes(b"a file where a folder should be")
    with FileServer(BODY) as server, caplog.at_level(logging.WARNING, logger="latedel"):
        with pytest.raises(OSError) as error:
            get(server, "/file.csv", tmp_path / "blocked" / "sub" / "f.csv")
    assert not isinstance(error.value, (DownloadError, SourceError))
    assert any(r.exc_info for r in caplog.records), "logged with its traceback"
    assert (tmp_path / "blocked").read_bytes() == b"a file where a folder should be"


def test_a_staging_file_that_cannot_be_created_is_an_os_error(tmp_path, monkeypatch, caplog):
    def refuse(path, flags, mode=0o777, **kwargs):
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(os, "open", refuse)
    with FileServer(BODY) as server, caplog.at_level(logging.WARNING, logger="latedel"):
        with pytest.raises(PermissionError):
            get(server, "/file.csv", tmp_path / "f.csv")
    assert any(r.exc_info for r in caplog.records)
    assert list(tmp_path.iterdir()) == []


def test_a_full_disk_while_writing_is_an_os_error_and_removes_the_staging_file(tmp_path, monkeypatch, caplog):
    real_fdopen = os.fdopen

    class Full:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()

        def write(self, data):
            raise OSError(errno.ENOSPC, "no space left on device")

    monkeypatch.setattr(os, "fdopen", lambda fd, mode="r", *a, **k: Full(real_fdopen(fd, mode, *a, **k)))
    with FileServer(BODY) as server, caplog.at_level(logging.WARNING, logger="latedel"):
        with pytest.raises(OSError) as error:
            get(server, "/file.csv", tmp_path / "raw" / "f.csv")
    assert error.value.errno == errno.ENOSPC and not isinstance(error.value, DownloadError)
    assert list((tmp_path / "raw").iterdir()) == []
    assert any("local fault" in r.getMessage() and r.exc_info for r in caplog.records)


def test_the_staging_file_is_created_exclusively_under_a_random_name(tmp_path, monkeypatch):
    descriptor, first = ingest._create_staging(tmp_path / "f.csv")
    os.close(descriptor)
    descriptor, second = ingest._create_staging(tmp_path / "f.csv")
    os.close(descriptor)
    assert first != second and first.parent == tmp_path and first.name.startswith("f.csv.") and first.name.endswith(".staging")
    monkeypatch.setattr(secrets, "token_hex", lambda n: "fixed")
    monkeypatch.setattr(ingest, "STAGING_ATTEMPTS", 3)
    descriptor, _ = ingest._create_staging(tmp_path / "g.csv")
    os.close(descriptor)
    with pytest.raises(FileExistsError, match="3 intentos"):
        ingest._create_staging(tmp_path / "g.csv")
    assert STAGING_ATTEMPTS == 100


def test_two_overlapping_downloads_do_not_share_their_staging_file(tmp_path):
    """A good download that is stopped halfway and a bad one that finishes: the kept part is the bad one, whole, and the accepted file is the good one."""
    with FileServer(BODY) as good, FileServer(BODY + b"extra bytes that change the hash") as bad:
        with pytest.raises(SourceError):
            get(bad, "/file.csv", tmp_path / "f.csv")
        part_before = (tmp_path / "f.csv.part").read_bytes()
        dest, digest = get(good, "/file.csv", tmp_path / "f.csv")
    assert part_before == BODY + b"extra bytes that change the hash" and digest == GOOD and dest.read_bytes() == BODY


# --- the manifest of the source ---------------------------------------------------------------------------------------------


def test_the_manifest_round_trips_and_refuses_anything_else(tmp_path):
    manifest = SourceManifest(mode="local", source=str(tmp_path / "x.csv"), sha256=GOOD, size=12, fetched_at="2026-11-10T10:00:00+00:00", csv_path="x.csv")
    path = tmp_path / "raw" / MANIFEST_NAME
    assert read_source_manifest(path) is None
    write_source_manifest(path, manifest)
    assert read_source_manifest(path) == manifest and [p.name for p in path.parent.iterdir()] == [MANIFEST_NAME]
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["version"] == 1 and set(document) == {"version", "source"}
    for broken in ("not json", "[]", '{"version": 2, "source": {}}', json.dumps({**document, "extra": 1}), json.dumps({"version": 1, "source": {"mode": "x"}})):
        path.write_text(broken, encoding="utf-8")
        with pytest.raises(ValueError, match="manifiesto"):
            read_source_manifest(path)


# --- fetch_source: the first task -----------------------------------------------------------------------------------------------


def ctx_for(cfg, tmp_path, **source):
    paths = dataclasses.replace(cfg.paths, raw_dir=tmp_path / "raw", interim_dir=tmp_path / "interim")
    return types.SimpleNamespace(cfg=dataclasses.replace(cfg, paths=paths, source=dataclasses.replace(cfg.source, **source)))


def http_ctx(cfg, tmp_path, server: FileServer, digest: str, path: str = "/file.csv"):
    return ctx_for(cfg, tmp_path, mode="http", url=server.url(path), allowed_hosts=("127.0.0.1",), accepted_sha256=digest, local_path=None)


def local_ctx(cfg, tmp_path, csv: Path):
    return ctx_for(cfg, tmp_path, mode="local", url="", accepted_sha256="", local_path=csv)


@pytest.fixture(scope="module")
def fixture_bytes(fixture_csv) -> bytes:
    return fixture_csv.read_bytes()


def test_fetch_downloads_checks_builds_and_the_second_run_changes_nothing(cfg, tmp_path, fixture_bytes, fixture_truth):
    with FileServer(fixture_bytes) as server:
        ctx = http_ctx(cfg, tmp_path, server, sha(fixture_bytes))
        first = fetch_source(ctx, insecure_hosts=LOCAL)
        assert first["downloaded"] is True and first["built"] is True and first["sha256"] == sha(fixture_bytes)
        assert first["lines"] == fixture_truth.n_lines and first["orders"] == fixture_truth.n_orders
        assert (tmp_path / "raw" / cfg.source.file_name).read_bytes() == fixture_bytes
        assert sorted(p.name for p in (tmp_path / "interim").iterdir()) == ["interim_manifest.json", "order_lines.parquet", "orders.parquet", "outcomes.parquet"]
        assert read_source_manifest(tmp_path / "raw" / MANIFEST_NAME).sha256 == sha(fixture_bytes)
        assert interim_fingerprint(tmp_path / "interim") == sha(fixture_bytes)
        before = snapshot(tmp_path)
        requests = len(server.requests)
        second = fetch_source(ctx, insecure_hosts=LOCAL)
        assert second["downloaded"] is False and second["built"] is False and second["orders"] == fixture_truth.n_orders
        assert len(server.requests) == requests, "the file was there: nothing is downloaded again"
    assert snapshot(tmp_path) == before, "repeating the task changes no byte"


def test_fetch_stores_the_source_report_next_to_the_streams(cfg, tmp_path, fixture_bytes, fixture_truth):
    with FileServer(fixture_bytes) as server:
        fetch_source(http_ctx(cfg, tmp_path, server, sha(fixture_bytes)), insecure_hosts=LOCAL)
    report = load_source_report(tmp_path / "interim")
    assert report is not None and report.counts == fixture_truth.expected and report.size_bytes == len(fixture_bytes)
    assert report == read_source(tmp_path / "raw" / cfg.source.file_name, cfg)[1]
    assert load_source_report(tmp_path / "nowhere") is None


def test_a_download_with_another_hash_rejects_the_run_and_writes_no_file_but_the_part(cfg, tmp_path, fixture_bytes):
    with FileServer(fixture_bytes) as server:
        with pytest.raises(SourceError) as error:
            fetch_source(http_ctx(cfg, tmp_path, server, BAD), insecure_hosts=LOCAL)
    assert error.value.rule_id == "V01"
    assert sorted(snapshot(tmp_path)) == [f"raw/{cfg.source.file_name}.part"]
    assert not (tmp_path / "interim").exists()


def test_a_file_in_raw_with_another_hash_is_rejected_without_changing_anything_or_downloading(cfg, tmp_path, fixture_bytes):
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / cfg.source.file_name).write_bytes(fixture_bytes + b"tampered\r\n")
    before = snapshot(tmp_path)
    with FileServer(fixture_bytes) as server:
        with pytest.raises(SourceError, match=r"^V01:.*aceptada") as error:
            fetch_source(http_ctx(cfg, tmp_path, server, sha(fixture_bytes)), insecure_hosts=LOCAL)
        assert server.requests == []
    assert error.value.problems[0]["actual"] == sha(fixture_bytes + b"tampered\r\n")
    assert snapshot(tmp_path) == before


def test_a_file_that_is_there_but_has_no_streams_is_read_again_without_downloading(cfg, tmp_path, fixture_bytes):
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / cfg.source.file_name).write_bytes(fixture_bytes)
    with FileServer(fixture_bytes) as server:
        result = fetch_source(http_ctx(cfg, tmp_path, server, sha(fixture_bytes)), insecure_hosts=LOCAL)
        assert server.requests == []
    assert result["downloaded"] is False and result["built"] is True and (tmp_path / "interim" / "orders.parquet").exists()


def test_streams_from_another_source_are_rebuilt(cfg, tmp_path, fixture_bytes, fixture_read):
    frames, report = fixture_read
    ingest.write_interim(frames, report, tmp_path / "interim", "ef" * 32)
    stale = snapshot(tmp_path / "interim")
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / cfg.source.file_name).write_bytes(fixture_bytes)
    with FileServer(fixture_bytes) as server:
        result = fetch_source(http_ctx(cfg, tmp_path, server, sha(fixture_bytes)), insecure_hosts=LOCAL)
    assert result["built"] is True and interim_fingerprint(tmp_path / "interim") == sha(fixture_bytes)
    assert snapshot(tmp_path / "interim") != stale
    # one stream with another fingerprint is enough to rebuild all three
    other = pq.read_table(tmp_path / "interim" / "outcomes.parquet")
    ingest.write_interim(frames, report, tmp_path / "interim", sha(fixture_bytes))
    pq.write_table(other.replace_schema_metadata({**(other.schema.metadata or {}), b"latedel.source_sha256": b"00" * 32}), tmp_path / "interim" / "outcomes.parquet")
    assert interim_fingerprint(tmp_path / "interim") is None


def test_a_hash_valid_file_with_invalid_content_is_rejected_by_the_reader_and_writes_no_stream(cfg, tmp_path, malformed):
    path = malformed("short_row")
    data = path.read_bytes()
    with FileServer(data) as server:
        with pytest.raises(SourceError) as error:
            fetch_source(http_ctx(cfg, tmp_path / "work", server, sha(data)), insecure_hosts=LOCAL)
    assert error.value.rule_id == "V05"
    assert not (tmp_path / "work" / "interim").exists() and not (tmp_path / "work" / "raw" / MANIFEST_NAME).exists()


def test_local_mode_registers_the_first_hash_and_rejects_another(cfg, tmp_path, fixture_csv, malformed):
    csv = tmp_path / "in" / "f.csv"
    csv.parent.mkdir()
    csv.write_bytes(fixture_csv.read_bytes())
    ctx = local_ctx(cfg, tmp_path / "work", csv)
    first = fetch_source(ctx)
    assert first["downloaded"] is False and first["built"] is True and first["csv_path"] == str(csv)
    registered = read_source_manifest(tmp_path / "work" / "raw" / MANIFEST_NAME)
    assert registered.mode == "local" and registered.sha256 == sha(csv.read_bytes())
    again = snapshot(tmp_path / "work")
    assert fetch_source(ctx)["built"] is False and snapshot(tmp_path / "work") == again
    csv.write_bytes(malformed("wrong_hash").read_bytes())
    with pytest.raises(SourceError, match=r"^V01:.*registrada") as error:
        fetch_source(ctx)
    assert error.value.problems[0]["expected"] == registered.sha256
    assert snapshot(tmp_path / "work") == again, "a rejected run changes nothing"
    assert [p.name for p in (tmp_path / "work" / "raw").iterdir()] == [MANIFEST_NAME], "the local file is read where it is, never copied into raw"


def test_local_mode_registers_nothing_when_the_first_file_is_invalid(cfg, tmp_path, malformed):
    csv = tmp_path / "in.csv"
    csv.write_bytes(malformed("bad_date").read_bytes())
    with pytest.raises(SourceError, match="V05"):
        fetch_source(local_ctx(cfg, tmp_path / "work", csv))
    assert snapshot(tmp_path / "work") == {}


def test_local_mode_needs_the_file(cfg, tmp_path):
    with pytest.raises(FileNotFoundError, match="no existe"):
        fetch_source(local_ctx(cfg, tmp_path / "work", tmp_path / "absent.csv"))
    assert snapshot(tmp_path / "work") == {}


def test_a_download_error_is_not_swallowed_by_the_task(cfg, tmp_path, fixture_bytes):
    with FileServer(fixture_bytes) as server:
        with pytest.raises(DownloadError, match="500"):
            fetch_source(http_ctx(cfg, tmp_path, server, sha(fixture_bytes), "/status/500"), insecure_hosts=LOCAL)
    assert snapshot(tmp_path) == {}


def test_the_task_never_enables_plain_http_by_itself(cfg, tmp_path, fixture_bytes):
    with FileServer(fixture_bytes) as server:
        with pytest.raises(SourceError, match=r"^V02:.*HTTPS"):
            fetch_source(http_ctx(cfg, tmp_path, server, sha(fixture_bytes)))
        assert server.requests == []


@pytest.mark.real_data
def test_real_download_from_mendeley_follows_only_allowed_hosts_and_matches_the_pinned_hash(cfg, tmp_path, caplog):
    """The one real download: Mendeley answers 302 to a signed S3 link; every hop is HTTPS to a listed host and the file has the pinned SHA-256."""
    with caplog.at_level(logging.INFO, logger="latedel"):
        dest, digest = download_file(
            cfg.source.url, tmp_path / cfg.source.file_name, cfg.source.accepted_sha256, list(cfg.source.allowed_hosts), cfg.source.max_redirects,
            cfg.source.max_bytes, cfg.source.chunk_bytes, cfg.source.timeout_s,
        )
    assert digest == cfg.source.accepted_sha256 and dest.stat().st_size == 95_910_149
    hops = [r.getMessage() for r in caplog.records if r.getMessage().startswith("download hop")]
    assert len(hops) >= 2 and "data.mendeley.com" in hops[0] and "prod-dcd-datasets-public-files-eu-west-1.s3.eu-west-1.amazonaws.com" in hops[-1]
    print("HOPS:", [h.split(" ")[3] for h in hops])
