"""The peak memory with the name of its counter (RNF03, decision D29)."""

from __future__ import annotations

import sys
import types

import pytest

from latedel import sysinfo
from latedel.sysinfo import MemoryPeaks, peak_memory


def fake_resource(monkeypatch, ru_maxrss: int) -> None:
    module = types.SimpleNamespace(RUSAGE_SELF=0, getrusage=lambda who: types.SimpleNamespace(ru_maxrss=ru_maxrss))
    monkeypatch.setitem(sys.modules, "resource", module)


def test_the_real_peak_of_this_process_names_its_counter():
    peaks = peak_memory()
    assert isinstance(peaks, MemoryPeaks) and peaks.working_set_mb > 10
    if sys.platform == "win32":
        assert peaks.counter == "PeakWorkingSetSize" and peaks.commit_mb is not None and peaks.commit_mb > 0   # the committed figure exists on Windows
    else:
        assert peaks.counter == "ru_maxrss" and peaks.commit_mb is None
    assert peak_memory().working_set_mb >= peaks.working_set_mb   # a peak never falls


def test_on_linux_the_counter_is_ru_maxrss_in_kilobytes_and_there_is_no_committed_figure(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    fake_resource(monkeypatch, 2048)    # KiB
    assert peak_memory() == MemoryPeaks(2.0, None, "ru_maxrss")


def test_on_macos_ru_maxrss_is_in_bytes(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_resource(monkeypatch, 3 * 1024 * 1024)
    assert peak_memory() == MemoryPeaks(3.0, None, "ru_maxrss")


def test_a_system_that_reports_nothing_is_an_error_and_not_a_zero(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    fake_resource(monkeypatch, 0)
    with pytest.raises(OSError, match="pico de memoria"):
        peak_memory()


def test_the_windows_counters_are_read_with_declared_argument_types():
    """Without ``argtypes`` and ``restype`` the call fails with ``OverflowError`` on 64 bits (D29): reading the counters twice, from two threads of calls, must work."""
    if sys.platform != "win32":
        pytest.skip("Windows only")
    first, second = sysinfo._windows_peaks(), sysinfo._windows_peaks()
    assert first[0] > 0 and first[1] > 0 and second[0] >= first[0] and second[1] >= first[1]
