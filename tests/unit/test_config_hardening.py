"""H1: the advisories of the native review of U01 that change the configuration (R1-broad-host-suffix-allowlist, R3-001, R3-002, R3-003, R3-mode-fallback).

Each test fails on the code of U01 for the reason the advisory gives. The rest of the advisories of U01 (hash pinning, absolute paths, the hook of the marks) are
recorded in ``docs/review-advisories.md`` with their resolution.
"""

from __future__ import annotations

import dataclasses
import types

import pytest

from latedel.config import host_allowed, load_config
from latedel.ingest import check_hop, fetch_source
from latedel.schema import SourceError
from tests.unit.test_config import refused, variant

BUCKET = "prod-dcd-datasets-public-files-eu-west-1.s3.eu-west-1.amazonaws.com"
SIGNED = f"https://{BUCKET}/8gx2fvg2k6/72784be5-36d3-44fe-b75d-0edbf1999f65?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=300&X-Amz-Signature=abc"


# --- R1-broad-host-suffix-allowlist: the S3 host is the one bucket that Mendeley redirects to --------------------------------------------


def test_the_default_hosts_are_the_dataset_host_and_the_one_bucket_it_redirects_to(cfg):
    assert cfg.source.allowed_hosts == ("data.mendeley.com", BUCKET)
    assert not any(entry.startswith(".") for entry in cfg.source.allowed_hosts), "no suffix: nothing under amazonaws.com is admitted by its name"


@pytest.mark.parametrize("host", [
    "attacker.s3.eu-west-1.amazonaws.com", "other-bucket.s3.eu-west-1.amazonaws.com", "x.amazonaws.com", "amazonaws.com", "s3.eu-west-1.amazonaws.com",
    f"{BUCKET}.evil.com", f"evil-{BUCKET}", f"x.{BUCKET}", "prod-dcd-datasets-public-files-us-east-1.s3.us-east-1.amazonaws.com", "data.mendeley.com.evil.com",
])
def test_no_other_amazon_host_is_admitted_by_the_default_configuration(cfg, host):
    assert host_allowed(host, cfg.source.allowed_hosts) is False
    with pytest.raises(SourceError, match=r"^V02:.*no está en la lista"):
        check_hop(f"https://{host}/file?X-Amz-Signature=abc", cfg.source.allowed_hosts)


def test_the_observed_redirect_chain_passes_the_default_configuration_without_the_network(cfg):
    """The deterministic half of the real download test: Mendeley's first address and the signed S3 address it redirects to, as observed on 2026-11-10."""
    assert check_hop(cfg.source.url, cfg.source.allowed_hosts) == "data.mendeley.com"
    assert check_hop(SIGNED, cfg.source.allowed_hosts) == BUCKET
    assert host_allowed(BUCKET.upper(), cfg.source.allowed_hosts) is True and host_allowed(BUCKET + ".", cfg.source.allowed_hosts) is True


# --- R3-001: a model version is a folder name, and '.' and '..' are not names -----------------------------------------------------------------------


@pytest.mark.parametrize("value", [".", "..", "...", ".hidden", "v1.", "v1..", "v1 ", " v1", "", "v/1", "v\\\\1", "../v1", "v1:x", "a*b", "CON.txt "])
def test_a_model_version_must_be_a_plain_folder_name(tmp_path, value):
    refused(tmp_path, r"\[model\] model_version", snippet=f'[model]\nmodel_version = "{value}"\n')


@pytest.mark.parametrize("value", ["v1", "v1.2", "2026-11-10", "v1_final", "m-3", "0", "V1.2.3-rc1"])
def test_ordinary_model_versions_are_accepted(tmp_path, value):
    assert load_config(variant(tmp_path, snippet=f'[model]\nmodel_version = "{value}"\n')).model.model_version == value


# --- R3-002 and R3-003: the seeds are numbers that numpy and scikit-learn accept ---------------------------------------------------------------------


@pytest.mark.parametrize(("section", "key"), [("model", "random_state"), ("evaluation", "bootstrap_seed")])
def test_a_seed_must_be_an_integer_from_zero_to_two_to_the_32_minus_one(tmp_path, section, key):
    for bad in ("-1", "-100", str(2**32), str(2**63)):
        refused(tmp_path, rf"\[{section}\] {key}", snippet=f"[{section}]\n{key} = {bad}\n")
    refused(tmp_path, rf"\[{section}\] {key}.*un entero", snippet=f"[{section}]\n{key} = 1.5\n")
    refused(tmp_path, rf"\[{section}\] {key}.*un entero", snippet=f"[{section}]\n{key} = true\n")
    refused(tmp_path, rf"\[{section}\] {key}.*un entero", snippet=f'[{section}]\n{key} = "7"\n')
    for good in (0, 1, 42, 2**32 - 1):
        assert getattr(getattr(load_config(variant(tmp_path, snippet=f"[{section}]\n{key} = {good}\n")), section), key) == good


def test_every_other_integer_of_the_model_and_evaluation_sections_still_has_its_own_check(tmp_path):
    """The seeds were the only two integers of these sections with no check: the others keep theirs (and the tests of U01)."""
    refused(tmp_path, r"\[model\] max_iter", snippet="[model]\nmax_iter = 0\n")
    refused(tmp_path, r"\[evaluation\] bootstrap_resamples", snippet="[evaluation]\nbootstrap_resamples = 0\n")
    refused(tmp_path, r"\[evaluation\] roc_points", snippet="[evaluation]\nroc_points = 1\n")


# --- R3-mode-fallback-untested: an unknown source mode is refused with its name, by the configuration and by the task ------------------------------------


@pytest.mark.parametrize("mode", ["ftp", "HTTP", "Local", "", "http ", "s3"])
def test_an_unknown_source_mode_is_refused_by_the_configuration_with_its_name(tmp_path, mode):
    refused(tmp_path, rf"\[source\] mode.*'http' o 'local'.*{mode!r}", snippet=f'[source]\nmode = "{mode}"\n')


def test_the_task_refuses_a_mode_that_is_neither_http_nor_local_instead_of_treating_it_as_local(cfg, tmp_path, fixture_csv):
    paths = dataclasses.replace(cfg.paths, raw_dir=tmp_path / "raw", interim_dir=tmp_path / "interim")
    source = dataclasses.replace(cfg.source, mode="ftp", local_path=fixture_csv, url="", accepted_sha256="")      # built without load_config: its check is skipped
    ctx = types.SimpleNamespace(cfg=dataclasses.replace(cfg, paths=paths, source=source))
    with pytest.raises(ValueError, match=r"\[source\] mode.*'ftp'"):
        fetch_source(ctx)
    assert not (tmp_path / "raw").exists() and not (tmp_path / "interim").exists(), "nothing was read and nothing was written"
