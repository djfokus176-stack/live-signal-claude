"""Today-only PAPER dashboard tests: no invented signals or success claims."""
import pandas as pd
from daily_report import today_activity


def entries(created, side="LONG", symbol="CAPITAL_US500",
            model="ema-rsi-v5-auto-paper", origin="AUTO",
            horizons=(1, 3, 5, 15, 30, 60)):
    return pd.DataFrame([
        {
            "created_utc": created, "symbol": symbol,
            "source": "Capital.com DEMO", "model": model,
            "side": side, "horizon": h, "origin": origin,
            "status": "TRAFIONY" if h == 5 else "OCZEKUJE",
            "net_pct": 0.12 if h == 5 else None
        } for h in horizons
    ])


def test_six_horizons_count_as_one_forecast():
    now = pd.Timestamp("2026-10-14T19:30:00Z")
    df = entries("2026-10-14T16:45:12+00:00")
    result = today_activity(df, now)
    assert len(result) == 1
    assert result.iloc[0]["Kierunek"] == "LONG"
    assert result.iloc[0]["Sposób"] == "AUTO"
    assert result.iloc[0]["5 min"] == "TRAFIONY (+0.1200%)"
    assert result.iloc[0]["1 min"] == "OCZEKUJE"


def test_polish_today_not_utc_today_across_midnight():
    # 23:30 UTC on Oct 13 is already Oct 14 in Poland.
    now = pd.Timestamp("2026-10-14T06:00:00Z")
    newer = entries("2026-10-13T23:30:00+00:00")
    older = entries("2026-10-13T20:30:00+00:00", side="SHORT")
    result = today_activity(pd.concat([newer, older]), now)
    assert len(result) == 1
    assert result.iloc[0]["Godzina PL"].startswith("14.10.2026")
    assert result.iloc[0]["Kierunek"] == "LONG"


def test_old_four_horizons_show_missing_as_dash_not_wins():
    now = pd.Timestamp("2026-10-14T19:30:00Z")
    df = entries("2026-10-14T16:45:12+00:00", model="ema-rsi-click-v3",
                 origin="MANUAL", horizons=(5, 15, 30, 60))
    result = today_activity(df, now)
    assert len(result) == 1
    assert result.iloc[0]["1 min"] == "—"
    assert result.iloc[0]["3 min"] == "—"
    assert result.iloc[0]["Sposób"] == "RĘCZNY"
    assert result.iloc[0]["Wersja"] == "ema-rsi-click-v3"


def test_separate_forecasts_and_versions_never_collapsed():
    now = pd.Timestamp("2026-10-14T19:30:00Z")
    ts = "2026-10-14T16:45:12+00:00"
    old = entries(ts, model="ema-rsi-click-v3")
    new = entries(ts, model="ema-rsi-v5-auto-paper")
    df = pd.concat([old, new], ignore_index=True)
    result = today_activity(df, now)
    assert len(result) == 2


def test_empty_journal_does_not_invent_forecast():
    now = pd.Timestamp("2026-10-14T19:30:00Z")
    assert today_activity(pd.DataFrame(), now).empty


def test_naive_clock_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        today_activity(pd.DataFrame(), pd.Timestamp("2026-10-14 19:30"))
