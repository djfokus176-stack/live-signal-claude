"""SQLite PAPER journal. All times UTC. Source and model versions are isolated.
This module never sends orders. Prices are simulated candle executions.
"""
import os
import sqlite3
from contextlib import contextmanager

import pandas as pd

DB = os.getenv("SIGNAL_DB", "signals_test.db")
HORIZONS = (1, 3, 5, 15, 30, 60)
MAX_GAP = pd.Timedelta(minutes=3)
MISSING_AFTER = pd.Timedelta(hours=6)
MODEL = "ema-rsi-click-v4-audit"


@contextmanager
def db():
    conn = sqlite3.connect(DB, timeout=15)
    conn.execute("""CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        source TEXT NOT NULL,
        model TEXT NOT NULL,
        side TEXT NOT NULL,
        horizon INTEGER NOT NULL,
        created_utc TEXT NOT NULL,
        reference_price REAL NOT NULL,
        target_utc TEXT NOT NULL,
        cost_pct REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'OCZEKUJE',
        entry_utc TEXT, exit_utc TEXT,
        entry_price REAL, exit_price REAL,
        gross_pct REAL, net_pct REAL
    )""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_journal_lookup
                    ON signals(symbol, source, model, status)""")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def log_signal(symbol, source, side, price, now, cost_pct=0.08,
               model=MODEL, cooldown_min=15):
    if side not in ("LONG", "SHORT"):
        return 0
    now = pd.Timestamp(now).tz_convert("UTC")
    import math
    if not math.isfinite(float(price)) or float(price) <= 0:
        raise ValueError("Cena referencyjna musi być dodatnia i skończona")
    if not math.isfinite(float(cost_pct)) or not 0 <= float(cost_pct) <= 2:
        raise ValueError("Niepoprawny dodatkowy koszt 0–2%")
    with db() as c:
        # Serialize the cooldown check and six inserts as one write transaction,
        # also when two browser sessions click at nearly the same time.
        c.execute("BEGIN IMMEDIATE")
        last = c.execute("""SELECT MAX(created_utc) FROM signals
            WHERE symbol=? AND source=? AND model=? AND side=?""",
            (symbol, source, model, side)).fetchone()[0]
        if last and now - pd.Timestamp(last) < pd.Timedelta(minutes=cooldown_min):
            return 0
        for hz in HORIZONS:
            c.execute("""INSERT INTO signals
                (symbol, source, model, side, horizon, created_utc,
                 reference_price, target_utc, cost_pct)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (symbol, source, model, side, hz, now.isoformat(), float(price),
                 (now + pd.Timedelta(minutes=hz)).isoformat(), float(cost_pct)))
    return len(HORIZONS)


def settle(symbol, source, bars, now, model=MODEL):
    """Settle only from *already closed* 1-minute candles, never future data.
    The target is measured from the signal timestamp, not from the fill.
    For broker bid/ask candles, spread is included by using executable sides.
    `cost_pct` is additional round-trip transaction cost, fixed at signal time.
    """
    if bars.empty:
        return 0
    now = pd.Timestamp(now).tz_convert("UTC")
    bars = bars.copy().sort_index()
    bars = bars[~bars.index.duplicated(keep="last")]
    bars = bars.loc[bars.index + pd.Timedelta(minutes=1) <= now]
    if bars.empty:
        return 0
    done = 0
    with db() as c:
        rows = c.execute("""SELECT id, created_utc, horizon, side, cost_pct
            FROM signals WHERE symbol=? AND source=? AND model=? AND status='OCZEKUJE'""",
            (symbol, source, model)).fetchall()
        for signal_id, created_str, hz, side, cost_pct in rows:
            created = pd.Timestamp(created_str)
            target = created + pd.Timedelta(minutes=hz)
            if now < target:
                continue
            possible_entries = bars.loc[bars.index > created]
            if possible_entries.empty:
                if now - target >= MISSING_AFTER:
                    c.execute("UPDATE signals SET status='BRAK_DANYCH' WHERE id=?", (signal_id,))
                    done += 1
                continue
            entry_t = possible_entries.index[0]
            # Never fabricate a position that opens only after its planned exit.
            if entry_t >= target or entry_t - created > MAX_GAP:
                c.execute("UPDATE signals SET status='BRAK_DANYCH' WHERE id=?", (signal_id,))
                done += 1
                continue
            possible_exits = bars.loc[(bars.index >= entry_t) &
                (bars.index + pd.Timedelta(minutes=1) >= target)]
            if possible_exits.empty:
                if now - target >= MISSING_AFTER:
                    c.execute("UPDATE signals SET status='BRAK_DANYCH' WHERE id=?", (signal_id,))
                    done += 1
                continue
            exit_t = possible_exits.index[0]
            if (exit_t + pd.Timedelta(minutes=1) - target) > MAX_GAP:
                c.execute("UPDATE signals SET status='BRAK_DANYCH' WHERE id=?", (signal_id,))
                done += 1
                continue
            # Capital.com CFD: execution at ask for buys and bid for sells.
            has_spread = all(x in bars.columns for x in
                ("OpenBid", "OpenAsk", "CloseBid", "CloseAsk"))
            if has_spread:
                entry_col = "OpenAsk" if side == "LONG" else "OpenBid"
                exit_col = "CloseBid" if side == "LONG" else "CloseAsk"
            else:
                entry_col, exit_col = "Open", "Close"
            p_in = float(bars.loc[entry_t, entry_col])
            p_out = float(bars.loc[exit_t, exit_col])
            if p_in <= 0 or p_out <= 0:
                c.execute("UPDATE signals SET status='BRAK_DANYCH' WHERE id=?", (signal_id,))
                done += 1
                continue
            gross = ((p_out / p_in - 1) if side == "LONG" else
                     (p_in - p_out) / p_in) * 100
            net = gross - cost_pct
            status = "TRAFIONY" if net > 0 else "BLEDNY" if net < 0 else "REMIS"
            c.execute("""UPDATE signals SET
                status=?, entry_utc=?, exit_utc=?, entry_price=?, exit_price=?,
                gross_pct=?, net_pct=? WHERE id=?""",
                (status, entry_t.isoformat(), exit_t.isoformat(), p_in,
                 p_out, gross, net, signal_id))
            done += 1
    return done


def load(symbol=None, source=None, model=MODEL):
    # model=None is an explicit all-version historical export;
    # main performance table always uses only the current model.
    filters = []
    args = []
    if model is not None:
        filters.append("model=?")
        args.append(model)
    if symbol:
        filters.append("symbol=?")
        args.append(symbol)
    if source:
        filters.append("source=?")
        args.append(source)
    where_clause = " WHERE " + " AND ".join(filters) if filters else ""
    with db() as c:
        return pd.read_sql_query("SELECT * FROM signals" + where_clause +
                " ORDER BY id DESC", c, params=args)


def stats(symbol=None, source=None, model=MODEL):
    df = load(symbol, source, model)
    df = df[df["status"].isin(["TRAFIONY", "BLEDNY", "REMIS"])]
    if df.empty:
        return pd.DataFrame()
    grouped = df.groupby("horizon", sort=True)
    result = pd.DataFrame({
        "rozliczone": grouped.size(),
        "trafione_netto_%": grouped["net_pct"].apply(
            lambda s: round((s > 0).mean() * 100, 1)),
        "śr_wynik_netto_%": grouped["net_pct"].mean().round(4),
        "suma_punktów_%": grouped["net_pct"].sum().round(3),
    })
    result.index = [f"{i} min" for i in result.index]
    return result


def newest_horizon_coverage(frame, expected=HORIZONS):
    """Report recorded horizons without backfilling past predictions.

    Different app hot-reloads can leave a running module with a previous
    horizon configuration. Reporting the discrepancy is safer than adding
    old 1m/3m signals retrospectively.
    """
    ordered = tuple(int(h) for h in expected)
    if frame is None or frame.empty:
        return {"created_utc": None, "expected": ordered,
                "observed": (), "missing": ordered}
    created = frame["created_utc"].max()
    group = frame.loc[frame["created_utc"] == created]
    observed = tuple(sorted({int(x) for x in group["horizon"]}))
    missing = tuple(h for h in ordered if h not in observed)
    return {"created_utc": created, "expected": ordered,
            "observed": observed, "missing": missing}
