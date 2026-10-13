import pandas as pd
import pytest
import store
from data_sources import capital_to_bars, closed_bars


def bars(start, n, step=0.01, p0=100.0):
    idx = pd.date_range(start, periods=n, freq="min", tz="UTC")
    close = [p0 + i * step for i in range(n)]
    return pd.DataFrame({"Open": [c - step for c in close], "Close": close,
                         "High": close, "Low": [c - step for c in close]}, index=idx)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", str(tmp_path / "test.db"))


def test_roundtrip_long_and_cooldown():
    b = bars("2026-10-12 14:00", 120)
    ts = pd.Timestamp("2026-10-12 14:10:10", tz="UTC")
    assert store.log_signal("SPY", "Yahoo", "LONG", 100.1, ts, cost_pct=0.005) == 6
    assert store.log_signal("SPY", "Yahoo", "LONG", 100.1, ts + pd.Timedelta(minutes=5)) == 0
    assert store.log_signal("SPY", "Yahoo", "WAIT", 100.1, ts) == 0
    assert store.settle("SPY", "Yahoo", b, ts + pd.Timedelta(minutes=3)) == 1
    assert store.settle("SPY", "Yahoo", b, ts + pd.Timedelta(minutes=70)) == 5
    df = store.load("SPY", "Yahoo")
    assert (df["status"] == "TRAFIONY").all()
    assert (df["net_pct"] < df["gross_pct"]).all()
    assert not store.stats("SPY", "Yahoo").empty


def test_cost_turns_gross_winner_into_net_loser():
    b = bars("2026-10-12 14:00", 120, step=0.001)
    ts = pd.Timestamp("2026-10-12 14:10:10", tz="UTC")
    store.log_signal("SPY", "Yahoo", "LONG", 100, ts, cost_pct=0.08)
    store.settle("SPY", "Yahoo", b, ts + pd.Timedelta(minutes=70))
    df = store.load("SPY", "Yahoo")
    assert (df["gross_pct"] > 0).all()
    assert (df["net_pct"] < 0).all()
    assert set(df["status"]) == {"BLEDNY"}


def test_source_isolation_and_late_gap():
    b = bars("2026-10-12 14:00", 60)
    ts = pd.Timestamp("2026-10-12 14:10:10", tz="UTC")
    store.log_signal("SPY", "Yahoo", "SHORT", 100, ts)
    store.log_signal("SPY", "Alpaca IEX", "SHORT", 100, ts)
    assert len(store.load("SPY", "Yahoo")) == 6
    assert len(store.load("SPY", "Alpaca IEX")) == 6
    store.settle("SPY", "Yahoo", b, ts + pd.Timedelta(minutes=70))
    assert set(store.load("SPY", "Alpaca IEX")["status"]) == {"OCZEKUJE"}
    next_day = pd.Timestamp("2026-10-13 14:10:10", tz="UTC")
    store.log_signal("QQQ", "Yahoo", "LONG", 100, next_day)
    store.settle("QQQ", "Yahoo", b, next_day + pd.Timedelta(hours=7))
    assert set(store.load("QQQ")["status"]) == {"BRAK_DANYCH"}


def test_ignores_future_bars_during_settlement():
    b = bars("2026-10-12 14:00", 120)
    ts = pd.Timestamp("2026-10-12 14:10:10", tz="UTC")
    store.log_signal("SPY", "Yahoo", "LONG", 100, ts)
    # b contains candles from the future; these must not be read.
    assert store.settle("SPY", "Yahoo", b, ts + pd.Timedelta(seconds=30)) == 0
    assert set(store.load("SPY")["status"]) == {"OCZEKUJE"}


def test_cfds_bid_ask_spread_included():
    ts = pd.Timestamp("2026-10-12 14:10:10", tz="UTC")
    idx = pd.date_range("2026-10-12 14:00", periods=90, freq="min", tz="UTC")
    b = pd.DataFrame({"Open": 100.01, "Close": 100.01,
                      "OpenBid": 100.00, "OpenAsk": 100.02,
                      "CloseBid": 100.00, "CloseAsk": 100.02}, index=idx)
    store.log_signal("CAPITAL_US500", "Capital.com DEMO", "LONG", 100.01, ts, cost_pct=0)
    store.settle("CAPITAL_US500", "Capital.com DEMO", b,
                 ts + pd.Timedelta(minutes=70))
    df = store.load("CAPITAL_US500")
    assert set(df["status"]) == {"BLEDNY"}
    assert (df["net_pct"] < 0).all()


def test_closed_candles_filter_and_capital_parser():
    now = pd.Timestamp("2026-10-12 14:10:30", tz="UTC")
    b = bars("2026-10-12 14:00", 20)
    assert closed_bars(b, now).index[-1] == pd.Timestamp("2026-10-12 14:09", tz="UTC")
    payload = {"prices": [{
        "snapshotTimeUTC": "2026-10-12T14:01:00",
        "openPrice": {"bid": 99, "ask": 101},
        "highPrice": {"bid": 100, "ask": 102},
        "lowPrice": {"bid": 98, "ask": 100},
        "closePrice": {"bid": 99.5, "ask": 101.5},
    }]}
    df = capital_to_bars(payload, now=pd.Timestamp("2026-10-12 14:03", tz="UTC"))
    assert df["Open"].iloc[0] == pytest.approx(100)
    assert df["CloseBid"].iloc[0] == pytest.approx(99.5)


def test_newest_horizon_coverage_reports_missing_not_fake_results():
    ts = pd.Timestamp("2026-10-13T16:46:17Z")
    assert store.log_signal("CAPITAL_US500", "Capital.com DEMO",
                            "LONG", 7800, ts) == 6
    existing = store.load("CAPITAL_US500", "Capital.com DEMO")
    full = store.newest_horizon_coverage(existing)
    assert full["observed"] == (1, 3, 5, 15, 30, 60)
    assert full["missing"] == ()
    # Simulate old browser session having recorded only four horizons.
    truncated = existing[existing["horizon"].isin([5, 15, 30, 60])]
    missing = store.newest_horizon_coverage(truncated)
    assert missing["observed"] == (5, 15, 30, 60)
    assert missing["missing"] == (1, 3)
    assert len(store.load("CAPITAL_US500", "Capital.com DEMO")) == 6
