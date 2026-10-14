"""Regression tests for Streamlit Cloud scan-log storage across hot updates."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import pytest
import store


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", str(tmp_path / "cloud.sqlite"))


def opinion(side="WAIT"):
    return {"side": side, "reason": "2 wskaźniki bez przewagi",
            "score_long": 62, "reference": 7715.8,
            "bars": 450, "age_min": 1.3}


def test_partial_old_scan_schema_migrates_in_place():
    # Simulate an actual Streamlit Cloud DB created by an earlier deployment:
    # old paper_scans table had no model, no origin and no RSS columns.
    with sqlite3.connect(store.DB) as conn:
        conn.execute("""CREATE TABLE paper_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            source TEXT NOT NULL,
            direction TEXT NOT NULL,
            result TEXT NOT NULL)""")
        conn.execute("""INSERT INTO paper_scans
                       (created_utc, symbol, source, direction, result)
                       VALUES (?, ?, ?, ?, ?)""",
                     ("2026-10-14T19:00:00+00:00",
                      "CAPITAL_US500", "Capital.com DEMO", "WAIT", "WAIT"))
    now = pd.Timestamp("2026-10-14T21:01:17Z")
    assert store.log_scan("CAPITAL_US500", "Capital.com DEMO", now,
                          "MANUAL", opinion(), "WAIT", news_titles=("headline",))
    saved = store.load_scans("CAPITAL_US500")
    assert len(saved) == 2
    assert saved.iloc[0]["model"] == store.MODEL
    assert saved.iloc[0]["news_count"] == 1


def test_same_scan_does_not_duplicate_on_retry():
    now = pd.Timestamp("2026-10-14T21:01:17Z")
    for _ in range(3):
        assert store.log_scan("CAPITAL_US500", "Capital.com DEMO", now,
                              "MANUAL", opinion(), "WAIT")
    assert len(store.load_scans("CAPITAL_US500")) == 1


def test_two_streamlit_sessions_do_not_duplicate_scan_record():
    now = pd.Timestamp("2026-10-14T21:01:17Z")
    def write(_):
        return store.log_scan("CAPITAL_US500", "Capital.com DEMO", now,
                              "AUTO", opinion("LONG"), "ZAPISANO")
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(write, range(3)))
    assert results == [True] * 3
    df = store.load_scans("CAPITAL_US500")
    assert len(df) == 1
    assert df.iloc[0]["direction"] == "LONG"


def test_capital_cfd_paper_test_and_wait_audit_both_saved():
    now = pd.Timestamp("2026-10-14T21:01:17Z")
    assert store.log_signal("CAPITAL_US500", "Capital.com DEMO",
                            "LONG", 7715.8, now, origin="AUTO") == 6
    assert store.log_scan("CAPITAL_US500", "Capital.com DEMO", now,
                          "AUTO", opinion("LONG"), "ZAPISANO")
    later = now + pd.Timedelta(minutes=15)
    assert store.log_scan("CAPITAL_US500", "Capital.com DEMO", later,
                          "AUTO", opinion("WAIT"), "WAIT")
    assert len(store.load("CAPITAL_US500")) == 6
    assert len(store.load_scans("CAPITAL_US500")) == 2
