"""Offline unit checks for Claude V3 click-to-analyse logic, no API keys."""
import pandas as pd
from signal_logic import evaluate


def candles(now, step, n=130, start=100.0):
    index = pd.date_range(now - pd.Timedelta(minutes=n), periods=n, freq="min", tz="UTC")
    close = [start + i*step for i in range(n)]
    return pd.DataFrame({"Open": close, "Close": close}, index=index)


def test_direction_long_and_no_probability():
    now = pd.Timestamp("2026-10-13T15:00:00Z")
    outcome = evaluate(candles(now, 0.08), now)
    assert outcome["side"] == "LONG"
    assert outcome["age_min"] == 0
    assert outcome["confidence"] is None
    assert 0 <= outcome["score_long"] <= 100
    assert outcome["rsi"] is not None


def test_direction_short():
    now = pd.Timestamp("2026-10-13T15:00:00Z")
    assert evaluate(candles(now, -0.08), now)["side"] == "SHORT"


def test_flat_is_wait():
    now = pd.Timestamp("2026-10-13T15:00:00Z")
    assert evaluate(candles(now, 0.0), now)["side"] == "WAIT"


def test_stale_data_is_wait_even_when_trending():
    now = pd.Timestamp("2026-10-13T15:00:00Z")
    assert evaluate(candles(now - pd.Timedelta(minutes=10), 0.08), now)["side"] == "WAIT"


def test_empty_or_short_history_is_wait():
    now = pd.Timestamp("2026-10-13T15:00:00Z")
    assert evaluate(pd.DataFrame(), now)["side"] == "WAIT"
    assert evaluate(candles(now, 0.08, n=20), now)["side"] == "WAIT"


def test_future_bar_cannot_change_direction():
    now = pd.Timestamp("2026-10-13T15:00:00Z")
    history = candles(now, 0.08)
    future = pd.DataFrame({"Close": [-1000.0], "Open": [-1000.0]},
                          index=[now + pd.Timedelta(minutes=1)])
    base = evaluate(history, now)
    changed = evaluate(pd.concat([history, future]), now)
    assert changed["side"] == base["side"]
    assert changed["reference"] == base["reference"]


def test_gap_with_fresh_last_bar_must_wait():
    now = pd.Timestamp("2026-10-13T15:00:00Z")
    history = candles(now, 0.08)
    # Remove one M1 candle close to the current price - price is fresh,
    # but the minimum 60-minute lookback is discontinuous.
    history = history.drop(history.index[-12])
    opinion = evaluate(history, now)
    assert opinion["side"] == "WAIT"
    assert "Przerwa" in opinion["reason"]
    assert opinion["age_min"] == 0


def test_nan_infinite_zero_closes_not_allowed():
    now = pd.Timestamp("2026-10-13T15:00:00Z")
    for bad_value in (0, -1, float("nan"), float("inf")):
        history = candles(now, 0.08)
        history.iloc[-1, history.columns.get_loc("Close")] = bad_value
        result = evaluate(history, now)
        assert result["side"] == "WAIT"
