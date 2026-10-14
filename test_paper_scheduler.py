"""15-minute cadence tests; no timers or network calls."""
import pandas as pd
from paper_scheduler import auto_scan_due


def test_first_active_run_is_due():
    now = pd.Timestamp("2026-10-14T10:45:00Z")
    assert auto_scan_due(None, now)


def test_does_not_double_log_on_repeated_30_second_reruns():
    now = pd.Timestamp("2026-10-14T10:45:00Z")
    assert not auto_scan_due(now, now + pd.Timedelta(seconds=30))
    assert not auto_scan_due(now, now + pd.Timedelta(minutes=14, seconds=59))
    assert auto_scan_due(now, now + pd.Timedelta(minutes=15))


def test_clock_backwards_and_utc_timezones():
    now = pd.Timestamp("2026-10-14T10:45:00Z")
    assert not auto_scan_due(now, now - pd.Timedelta(minutes=3))
    assert auto_scan_due(now, pd.Timestamp("2026-10-14 12:00", tz="Europe/Warsaw"))


def test_naive_now_fails_closed():
    import pytest
    with pytest.raises(ValueError):
        auto_scan_due(None, pd.Timestamp("2026-10-14 10:00"))
