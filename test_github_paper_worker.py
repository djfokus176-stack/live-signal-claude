"""Offline regression tests for the durable scheduled public futures experiment."""
from pathlib import Path

import pandas as pd

import github_paper_worker as worker


def market(now, step=0.4, count=130):
    now = pd.Timestamp(now)
    idx = pd.date_range(end=now - pd.Timedelta(minutes=1),
                        periods=count, freq="min", tz="UTC")
    close = [4500 + i * step for i in range(count)]
    return pd.DataFrame({"Open": close, "High": close,
                         "Low": close, "Close": close}, index=idx)


def test_first_scan_commits_six_horizons_and_provenance(tmp_path):
    now = pd.Timestamp("2026-10-14T12:00:00Z")
    output = worker.run_once(market(now), now, tmp_path)
    assert output["instrument"] == "ES=F"
    assert output["feed_kind"] == "PUBLIC_FUTURES_PROXY_NOT_CAPITAL_US500"
    assert output["side"] == "LONG"
    assert output["new_forecasts"] == 1
    assert output["new_horizon_rows"] == 6
    assert output["resolved_horizon_rows"] == 0
    frame = pd.read_csv(worker.week_file(now, tmp_path))
    assert len(frame) == 6
    assert set(frame["horizon"]) == {1, 3, 5, 15, 30, 60}
    assert set(frame["origin"]) == {"AUTO"}
    assert set(frame["symbol"]) == {"ES=F"}
    assert "CAPITAL" not in frame["source"].to_string()


def test_repeated_run_does_not_double_count_and_can_settle(tmp_path):
    now = pd.Timestamp("2026-10-14T12:00:00Z")
    first = worker.run_once(market(now), now, tmp_path)
    later = now + pd.Timedelta(minutes=15)
    second = worker.run_once(market(later), later, tmp_path)
    assert second["new_forecasts"] == 1
    assert second["newly_settled_rows"] >= 1
    frame = pd.read_csv(worker.week_file(now, tmp_path))
    assert frame["created_utc"].nunique() == 2
    assert len(frame) == 12
    original_bytes = worker.week_file(now, tmp_path).read_bytes()
    again = worker.run_once(market(later), later, tmp_path)
    assert again["new_forecasts"] == 0
    assert worker.week_file(now, tmp_path).read_bytes() == original_bytes


def test_weekly_rollover_keeps_preceding_week_for_settlement(tmp_path):
    sunday = pd.Timestamp("2026-10-18T23:55:00Z")
    monday = sunday + pd.Timedelta(minutes=15)
    worker.run_once(market(sunday), sunday, tmp_path)
    old_path = worker.week_file(sunday, tmp_path)
    assert old_path.is_file()
    output = worker.run_once(market(monday), monday, tmp_path)
    assert output["newly_settled_rows"] >= 1
    assert worker.week_file(monday, tmp_path).is_file()
    original = pd.read_csv(old_path)
    assert "TRAFIONY" in set(original["status"])


def test_missing_price_feed_never_invents_trade(tmp_path):
    now = pd.Timestamp("2026-10-14T12:00:00Z")
    empty = worker.run_once(pd.DataFrame(), now, tmp_path)
    assert empty["status"] == "NO_MARKET_DATA"
    assert empty["new_forecasts"] == 0
    assert len(list(Path(tmp_path).glob("*.csv"))) == 0
    stale = worker.run_once(
        market(now - pd.Timedelta(hours=1)), now, tmp_path)
    assert stale["new_forecasts"] == 0
    assert stale["side"] == "WAIT"
    assert len(list(Path(tmp_path).glob("*.csv"))) == 0
