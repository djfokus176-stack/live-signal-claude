"""Reproduce the real Streamlit screenshot: V6 UI but stale V5 store and BLS WAIT.

These regressions must fail if only sidebar text changes without reloading
the actual model, and must never invent a new trade on an unavailable feed.
"""
import pandas as pd
import requests
import streamlit.testing.v1 as sttest

import store
import macro_calendar


def test_2026_calendar_offline_does_not_permanently_block_all_hours(monkeypatch):
    def blocked(*args, **kwargs):
        raise requests.HTTPError("Upstream BLS blocked")
    monkeypatch.setattr(macro_calendar.requests, "get", blocked)
    when = pd.Timestamp("2026-10-15T20:21:00Z")  # 22:21 Polish
    events, ready = macro_calendar.load_official_calendar(now=when)
    assert ready
    assert not macro_calendar.macro_guard(when, events, ready)[0]
    # It still blocks within 15 minutes of the actual PPI release.
    release = pd.Timestamp("2026-10-15T12:30:00Z")
    stopped, text = macro_calendar.macro_guard(release, events, ready)
    assert stopped
    assert "Producer Price" in text


def test_redeploy_refreshes_stale_model_and_macro_from_memory(monkeypatch, tmp_path):
    # This is exactly what a previous Cloud screenshot showed: app V6 banner
    # but in-memory store.MODEL=V5 and macro calendar from before fallback.
    monkeypatch.setenv("SIGNAL_DB", str(tmp_path / "runtime.sqlite"))
    monkeypatch.setattr(store, "MODEL", "ema-rsi-v5-auto-paper")
    monkeypatch.setattr(macro_calendar, "CALENDAR_ENGINE_VERSION",
                        "OLD_BLS_SINGLE_SOURCE")
    app = sttest.AppTest.from_file("app.py", default_timeout=50).run()
    assert not app.exception
    assert store.MODEL == "ema-rsi-mtf-macro-v6"
    assert macro_calendar.CALENDAR_ENGINE_VERSION == (
        "bls-official-plus-2026-snapshot-v3"
    )
