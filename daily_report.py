"""Read-only summary of actual PAPER forecasts recorded in local SQLite.

Not a performance estimate: one forecast can have six overlapping horizons.
WAIT and unrecorded scans are deliberately excluded because they were not
persisted. Date boundaries use Polish local time, not UTC.
"""
from __future__ import annotations

import math

import pandas as pd

HORIZONS = (1, 3, 5, 15, 30, 60)
COLUMNS = (
    "Godzina PL", "Instrument", "Źródło", "Wersja",
    "Kierunek", "Sposób", "1 min", "3 min", "5 min",
    "15 min", "30 min", "60 min",
)


def _result(row):
    state = str(row["status"])
    if state == "OCZEKUJE":
        return "OCZEKUJE"
    if state == "BRAK_DANYCH":
        return "BRAK DANYCH"
    if state not in ("TRAFIONY", "BLEDNY", "REMIS"):
        return "NIEZNANY"
    try:
        score = float(row["net_pct"])
    except (TypeError, ValueError):
        return "NIEPOTWIERDZONY"
    if not math.isfinite(score):
        return "NIEPOTWIERDZONY"
    return f"{state} ({score:+.4f}%)"


def today_activity(frame: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """One row per recorded forecast, across versions and selected markets."""
    now = pd.Timestamp(now)
    if now.tzinfo is None:
        raise ValueError("Current time must be timezone-aware")
    if frame is None or frame.empty:
        return pd.DataFrame(columns=COLUMNS)
    required = {"created_utc", "symbol", "source", "model", "side",
                "horizon", "status", "net_pct"}
    if not required.issubset(frame.columns):
        raise ValueError("Missing columns in PAPER journal")

    rows = frame.copy()
    created = pd.to_datetime(rows["created_utc"], utc=True, errors="coerce")
    rows["when_pl"] = created.dt.tz_convert("Europe/Warsaw")
    local_day = now.tz_convert("Europe/Warsaw").date()
    rows = rows.loc[rows["when_pl"].dt.date == local_day].copy()
    if rows.empty:
        return pd.DataFrame(columns=COLUMNS)
    if "origin" not in rows.columns:
        rows["origin"] = "MANUAL"

    prepared = []
    group_fields = ("created_utc", "symbol", "source", "model", "side")
    for _, group in rows.groupby(list(group_fields), sort=False, dropna=False):
        first = group.iloc[0]
        origin = ("AUTO" if "AUTO" in set(group["origin"].astype(str))
                  else "RĘCZNY")
        item = {
            "Godzina PL": first["when_pl"].strftime("%d.%m.%Y %H:%M:%S"),
            "Instrument": str(first["symbol"]),
            "Źródło": str(first["source"]),
            "Wersja": str(first["model"]),
            "Kierunek": str(first["side"]),
            "Sposób": origin,
        }
        for horizon in HORIZONS:
            matches = group[group["horizon"] == horizon]
            item[f"{horizon} min"] = (
                _result(matches.iloc[-1]) if not matches.empty else "—"
            )
        prepared.append((first["when_pl"], item))
    prepared.sort(key=lambda item: item[0], reverse=True)
    return pd.DataFrame([item for _, item in prepared], columns=COLUMNS)
