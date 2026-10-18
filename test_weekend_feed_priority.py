"""Regression for screenshot on Sunday 18 Oct 2026: >47h stale US500.

A shutdown-weekend broker feed is never a calendar outage and never a LONG/
SHORT position. Missing Google RSS must not indefinitely kill fresh PAPER
research, but unknown macro dates must still block entries.
"""
import pandas as pd
import pytest
import macro_calendar
import market_context


NOW = pd.Timestamp("2026-10-18T20:37:00Z")  # 22:37 Polish Sunday


def candles(now, n=450, step=.08):
    times = pd.date_range(end=now-pd.Timedelta(minutes=1),
                          periods=n, freq="min", tz="UTC")
    values = [7700+i*step for i in range(n)]
    return pd.DataFrame({"Open": values,
                         "High": [v+.2 for v in values],
                         "Low": [v-.2 for v in values],
                         "Close": [v+.1 for v in values]}, index=times)


def opinion(side="LONG", age=0, bars=450):
    return {"side":side, "reason":"sygnał techniczny",
            "reasons":["EMA"], "age_min":age, "bars":bars,
            "reference":7730.5, "score_long":75}


def test_sunday_47h_stale_api_does_not_report_false_bls_failure(monkeypatch):
    def fail_if_macro_called(*args, **kwargs):
        raise AssertionError("Calendar should not run for stale prices")
    monkeypatch.setattr(macro_calendar, "macro_guard", fail_if_macro_called)
    result = market_context.filtered_decision(
        opinion(age=2856.7), candles(NOW-pd.Timedelta(days=2)),
        NOW, (), False, None, ())
    assert result["side"] == "WAIT"
    assert result["market_data_ok"] is False
    assert result["macro_checked"] is False
    assert "weekend" in result["reason"]
    assert "2857 min" in result["reason"]
    assert "kalendarz BLS niedostępny" not in result["reason"]


def test_weekday_stale_feed_distinguishes_api_gap_from_macro_outage():
    monday = pd.Timestamp("2026-10-19T09:30:00Z")
    result = market_context.filtered_decision(
        opinion(age=35), candles(monday-pd.Timedelta(hours=1)),
        monday, (), False, None, ())
    assert result["side"]=="WAIT"
    assert "brak świeżych danych brokera" in result["reason"]
    assert "BLS" not in result["rationale"]


def test_fresh_candles_allow_signal_when_optional_rss_down():
    monday = pd.Timestamp("2026-10-19T15:00:00Z")
    result = market_context.filtered_decision(
        opinion(age=0), candles(monday),
        monday, (), True, None, ())
    assert result["side"] == "LONG"
    assert result["market_data_ok"] is True
    assert "RSS niedostępny" in result["rationale"]
    assert "wsparcie M15" in result["rationale"]


def test_macro_blackout_still_blocks_even_without_rss():
    monday = pd.Timestamp("2026-10-19T15:00:00Z")
    result = market_context.filtered_decision(
        opinion(age=0), candles(monday),
        monday, [(monday,"CPI")], True, None, ())
    assert result["side"] == "WAIT"
    assert "STOP MAKRO" in result["reason"]


def test_unverified_macro_still_blocks_fresh_paper():
    monday = pd.Timestamp("2026-10-19T15:00:00Z")
    result = market_context.filtered_decision(
        opinion(age=0), candles(monday),
        monday, (), False, None, ())
    assert result["side"] == "WAIT"
    assert "kalendarz BLS niedostępny" in result["reason"]


def test_empty_feed_yields_wait_without_spurious_news_failure():
    result = market_context.filtered_decision(
        opinion(age=None,bars=0),pd.DataFrame(),NOW,(),False)
    assert result["side"] == "WAIT"
    assert "brak świec brokera" in result["reason"]
