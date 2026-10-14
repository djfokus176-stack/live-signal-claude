"""Independent, zero-credential scheduled S&P 500 futures PAPER research.

Runs via GitHub Actions, not Streamlit. The ES=F Yahoo futures feed is a
DIFFERENT INSTRUMENT from Capital.com US500 CFD. It is never substituted for
Capital.com in live app signals or claimed to be a broker quote.

One weekly public CSV stores PAPER forecast horizon records. The previous
week is also reloaded to permit settlement across Sunday/Monday boundaries.
Never sends orders, calls paid AI APIs or uses a brokerage account.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

import pandas as pd

import data_sources
import store
from signal_logic import evaluate

SYMBOL = "ES=F"
SOURCE = "Yahoo"
MODEL = "ema-rsi-v5-auto-futures-github"
EXTRA_COST_PCT = 0.08
OUTPUT_DIR = Path("data/auto_futures")
STATUS_FILE = OUTPUT_DIR / "status.json"


def week_file(created_time: pd.Timestamp, directory: Path = OUTPUT_DIR) -> Path:
    ts = pd.Timestamp(created_time)
    if ts.tzinfo is None:
        raise ValueError("Scan time must have timezone")
    year, week, _ = ts.tz_convert("UTC").isocalendar()
    return directory / f"ES_F_{year}-W{week:02d}.csv"


def restore_recent_weeks(now: pd.Timestamp, directory: Path):
    paths = (week_file(now - pd.Timedelta(days=7), directory),
             week_file(now, directory))
    for path in dict.fromkeys(paths):
        if path.is_file():
            store.restore_csv_backup(path.read_bytes())


def save_recent_weeks(directory: Path):
    data = store.load(SYMBOL, SOURCE, MODEL)
    if data.empty:
        return []
    data["week"] = data["created_utc"].map(
        lambda value: week_file(pd.Timestamp(value), directory).name
    )
    changed = []
    for filename, group in data.groupby("week", sort=True):
        path = directory / filename
        group = group.drop(columns=["week"])
        group = group.loc[:, list(store.BACKUP_COLUMNS)]
        group = group.sort_values(["created_utc", "horizon", "side"])
        csv = group.to_csv(index=False, float_format="%.10f").encode("utf-8-sig")
        if path.is_file() and path.read_bytes() == csv:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(csv)
        os.replace(tmp, path)
        changed.append(path)
    return changed


def run_once(bars: pd.DataFrame, now: pd.Timestamp,
             directory: Path = OUTPUT_DIR) -> dict:
    """Run one independent scheduled tick; reproducible with mocked candles."""
    now = pd.Timestamp(now)
    if now.tzinfo is None:
        raise ValueError("Scan time must be timezone-aware UTC")
    now = now.tz_convert("UTC")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    # GitHub runners are temporary: load persisted week records to a fresh
    # short-lived SQLite DB, then settle and save results back to public CSV.
    with tempfile.TemporaryDirectory(prefix="claude-paper-") as temp:
        former_db = store.DB
        store.DB = str(Path(temp) / "research.sqlite")
        try:
            restore_recent_weeks(now, directory)
            closed = (data_sources.closed_bars(bars, now)
                      if bars is not None and not bars.empty else pd.DataFrame())
            if not closed.empty:
                # Old positions may be settled even if current tick is WAIT.
                settled = store.settle(SYMBOL, SOURCE, closed, now, model=MODEL)
            else:
                settled = 0
            decision = evaluate(closed, now)
            count = 0
            if decision["side"] in ("LONG", "SHORT") and decision["reference"] is not None:
                count = store.log_signal(
                    SYMBOL, SOURCE, decision["side"], decision["reference"],
                    now, EXTRA_COST_PCT, model=MODEL, origin="AUTO",
                    cooldown_min=15)
            changed_files = save_recent_weeks(directory)
            logged = store.load(SYMBOL, SOURCE, MODEL)
            resolved = logged[logged["status"].isin(
                ("TRAFIONY", "BLEDNY", "REMIS"))]
            result = {
                "checked_utc": now.isoformat(),
                "instrument": SYMBOL,
                "source": SOURCE,
                "feed_kind": "PUBLIC_FUTURES_PROXY_NOT_CAPITAL_US500",
                "model": MODEL,
                "data_age_min": round(decision["age_min"], 3)
                                if decision["age_min"] is not None else None,
                "closed_m1_bars": int(decision["bars"]),
                "side": decision["side"],
                "reason": decision["reason"],
                "score_long_index_not_probability": decision["score_long"],
                "new_forecasts": 1 if count else 0,
                "new_horizon_rows": count,
                "newly_settled_rows": settled,
                "active_week_horizon_rows": len(logged),
                "resolved_horizon_rows": len(resolved),
                "saved_files": [p.name for p in changed_files],
                "status": "RECORDED" if count else
                          "WAIT_OR_COOLDOWN" if not closed.empty else
                          "NO_MARKET_DATA",
            }
        finally:
            store.DB = former_db
    status_path = directory / STATUS_FILE.name
    content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    status_path.write_text(content, encoding="utf-8")
    return result


def main():
    now = pd.Timestamp.now(tz="UTC")
    try:
        bars = data_sources.yahoo_bars(SYMBOL)
        result = run_once(bars, now, OUTPUT_DIR)
    except Exception as exc:
        # Never write traceback/credentials to an openly browsable status file.
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        result = {
            "checked_utc": now.isoformat(), "instrument": SYMBOL,
            "source": SOURCE, "model": MODEL,
            "feed_kind": "PUBLIC_FUTURES_PROXY_NOT_CAPITAL_US500",
            "status": "ERROR", "error_type": type(exc).__name__,
            "new_forecasts": 0,
        }
        STATUS_FILE.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        # Fail the workflow visibly. A future run can retry automatically.
        print("PAPER data feed/storage failed:", type(exc).__name__)
        raise
    print("PAPER futures check:", result["status"],
          result["side"], "new forecasts:", result["new_forecasts"],
          "resolved:", result["resolved_horizon_rows"])


if __name__ == "__main__":
    main()
