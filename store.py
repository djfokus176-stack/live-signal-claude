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
MODEL = "ema-rsi-v5-auto-paper"


@contextmanager
def db():
    # Four Streamlit fragments may read/write at once. Give writes time to
    # acquire the lock, and use WAL where supported by the filesystem.
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        # Some hosted filesystems don't support WAL; normal SQLite still works.
        pass
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
        gross_pct REAL, net_pct REAL,
        origin TEXT NOT NULL DEFAULT 'MANUAL'
    )""")
    # Old SQLite files were created before the origin provenance column.
    existing_cols = {item[1] for item in conn.execute("PRAGMA table_info(signals)")}
    if "origin" not in existing_cols:
        conn.execute("ALTER TABLE signals ADD COLUMN origin TEXT NOT NULL DEFAULT 'MANUAL'")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_journal_lookup
                    ON signals(symbol, source, model, status)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_scans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_utc TEXT NOT NULL,
        symbol TEXT NOT NULL,
        source TEXT NOT NULL,
        model TEXT NOT NULL,
        origin TEXT NOT NULL,
        direction TEXT NOT NULL,
        result TEXT NOT NULL,
        note TEXT NOT NULL,
        score_long INTEGER,
        reference_price REAL,
        closed_bars INTEGER,
        age_minutes REAL,
        news_score REAL,
        news_count INTEGER NOT NULL DEFAULT 0,
        news_sample TEXT NOT NULL DEFAULT ''
    )""")
    # A Streamlit hot update may retain an older SQLite schema. Reconcile
    # additive columns before indexing/inserting instead of failing forever.
    scan_columns = {item[1] for item in conn.execute("PRAGMA table_info(paper_scans)")}
    scan_defaults = {
        "created_utc": "TEXT NOT NULL DEFAULT ''",
        "symbol": "TEXT NOT NULL DEFAULT ''",
        "source": "TEXT NOT NULL DEFAULT ''",
        "model": "TEXT NOT NULL DEFAULT ''",
        "origin": "TEXT NOT NULL DEFAULT 'MANUAL'",
        "direction": "TEXT NOT NULL DEFAULT 'WAIT'",
        "result": "TEXT NOT NULL DEFAULT 'WAIT'",
        "note": "TEXT NOT NULL DEFAULT ''",
        "score_long": "INTEGER",
        "reference_price": "REAL",
        "closed_bars": "INTEGER",
        "age_minutes": "REAL",
        "news_score": "REAL",
        "news_count": "INTEGER NOT NULL DEFAULT 0",
        "news_sample": "TEXT NOT NULL DEFAULT ''",
    }
    for column, declaration in scan_defaults.items():
        if column not in scan_columns:
            conn.execute(f"ALTER TABLE paper_scans ADD COLUMN {column} {declaration}")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_scans_today
                    ON paper_scans(created_utc, symbol, model)""")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def log_signal(symbol, source, side, price, now, cost_pct=0.08,
               model=MODEL, cooldown_min=15, origin="MANUAL"):
    if side not in ("LONG", "SHORT"):
        return 0
    if origin not in ("AUTO", "MANUAL"):
        raise ValueError("Nieznane pochodzenie testu PAPER")
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
            WHERE symbol=? AND source=? AND model=?""",
            (symbol, source, model)).fetchone()[0]
        if last and now - pd.Timestamp(last) < pd.Timedelta(minutes=cooldown_min):
            return 0
        for hz in HORIZONS:
            c.execute("""INSERT INTO signals
                (symbol, source, model, side, horizon, created_utc,
                 reference_price, target_utc, cost_pct, origin)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (symbol, source, model, side, hz, now.isoformat(), float(price),
                 (now + pd.Timedelta(minutes=hz)).isoformat(), float(cost_pct), origin))
    return len(HORIZONS)


def settle(symbol, source, bars, now, model=MODEL):
    """Reconcile simulated OPEN and CLOSED positions using only finished M1 bars.

    The FIRST observed valid next-minute open is the hypothetical PAPER entry.
    Record the entry as soon as that candle closes, not only when target time
    has elapsed. Exit uses the candle close reached by the target horizon.
    Capital DEMO uses ASK on long entry / BID on short entry and vice versa.
    If original entry/exit candles are missing, NEVER fabricate a trade.
    """
    if bars is None or bars.empty:
        return 0
    now = pd.Timestamp(now).tz_convert("UTC")
    bars = bars.copy().sort_index()
    bars.index = pd.to_datetime(bars.index, utc=True)
    bars = bars[~bars.index.duplicated(keep="last")]
    bars = bars.loc[bars.index + pd.Timedelta(minutes=1) <= now]
    if bars.empty:
        return 0
    done = 0
    has_spread = all(key in bars.columns
                     for key in ("OpenBid", "OpenAsk", "CloseBid", "CloseAsk"))
    with db() as c:
        if model is None:
            rows = c.execute("""SELECT id, created_utc, horizon, side, cost_pct,
                                       entry_utc, entry_price
                FROM signals WHERE symbol=? AND source=? AND status='OCZEKUJE'""",
                (symbol, source)).fetchall()
        else:
            rows = c.execute("""SELECT id, created_utc, horizon, side, cost_pct,
                                       entry_utc, entry_price
                FROM signals WHERE symbol=? AND source=? AND model=?
                    AND status='OCZEKUJE'""",
                (symbol, source, model)).fetchall()
        for signal_id, created_str, hz, side, cost_pct, known_entry, known_price in rows:
            created = pd.Timestamp(created_str).tz_convert("UTC")
            target = created + pd.Timedelta(minutes=hz)
            entry_col = ("OpenAsk" if side == "LONG" else "OpenBid") if has_spread else "Open"
            exit_col = ("CloseBid" if side == "LONG" else "CloseAsk") if has_spread else "Close"

            if known_entry and known_price is not None:
                entry_t = pd.Timestamp(known_entry).tz_convert("UTC")
                p_in = float(known_price)
            else:
                possible_entries = bars.loc[bars.index > created]
                if possible_entries.empty:
                    if now - target >= MISSING_AFTER:
                        c.execute("UPDATE signals SET status='BRAK_DANYCH' WHERE id=?",
                                  (signal_id,))
                        done += 1
                    continue
                entry_t = possible_entries.index[0]
                if entry_t >= target or entry_t - created > MAX_GAP:
                    if now >= target:
                        c.execute("UPDATE signals SET status='BRAK_DANYCH' WHERE id=?",
                                  (signal_id,))
                        done += 1
                    continue
                p_in = float(bars.loc[entry_t, entry_col])
                if not pd.notna(p_in) or not 0 < p_in < float("inf"):
                    c.execute("UPDATE signals SET status='BRAK_DANYCH' WHERE id=?",
                              (signal_id,))
                    done += 1
                    continue
                # Only a closed entry candle confirms the virtual fill.
                c.execute("""UPDATE signals SET entry_utc=?, entry_price=?
                             WHERE id=? AND status='OCZEKUJE'""",
                          (entry_t.isoformat(), p_in, signal_id))

            if now < target:
                continue
            possible_exits = bars.loc[(bars.index >= entry_t) &
                (bars.index + pd.Timedelta(minutes=1) >= target)]
            if possible_exits.empty:
                if now - target >= MISSING_AFTER:
                    c.execute("UPDATE signals SET status='BRAK_DANYCH' WHERE id=?",
                              (signal_id,))
                    done += 1
                continue
            exit_t = possible_exits.index[0]
            if exit_t + pd.Timedelta(minutes=1) - target > MAX_GAP:
                c.execute("UPDATE signals SET status='BRAK_DANYCH' WHERE id=?",
                          (signal_id,))
                done += 1
                continue
            p_out = float(bars.loc[exit_t, exit_col])
            if not pd.notna(p_out) or not 0 < p_out < float("inf"):
                c.execute("UPDATE signals SET status='BRAK_DANYCH' WHERE id=?",
                          (signal_id,))
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


def log_scan(symbol, source, now, origin, opinion, result, news_score=None,
             news_titles=(), model=MODEL):
    """Audit each AUTO or MANUAL market scan, even when it decides WAIT.

    Never includes login/API credentials; no trading or order placement.
    """
    import math
    if origin not in ("AUTO", "MANUAL") or result not in (
        "ZAPISANO", "BLOKADA", "WAIT", "BLAD_ZAPISU"):
        raise ValueError("Nieznany status skanu PAPER")
    now = pd.Timestamp(now)
    if now.tzinfo is None:
        raise ValueError("Czas skanu musi mieć strefę")
    direction = str(opinion.get("side", "WAIT"))
    if direction not in ("LONG", "SHORT", "WAIT"):
        raise ValueError("Niepoprawna decyzja")
    score = opinion.get("score_long")
    ref = opinion.get("reference")
    age = opinion.get("age_min")
    def finite_or_none(x):
        if x is None:
            return None
        x = float(x)
        return x if math.isfinite(x) else None
    titles = [str(x).replace("\n", " ").strip()[:180]
              for x in tuple(news_titles)[:3]]
    note = str(opinion.get("reason", ""))[:600]
    with db() as c:
        c.execute("""INSERT INTO paper_scans
            (created_utc, symbol, source, model, origin, direction, result,
             note, score_long, reference_price, closed_bars, age_minutes,
             news_score, news_count, news_sample)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now.tz_convert("UTC").isoformat(), symbol, source, model, origin,
             direction, result, note, finite_or_none(score),
             finite_or_none(ref), int(opinion.get("bars") or 0),
             finite_or_none(age), finite_or_none(news_score),
             len(tuple(news_titles)), " | ".join(titles)))


def load_scans(symbol=None, model=None):
    filters, args = [], []
    if symbol:
        filters.append("symbol=?")
        args.append(symbol)
    if model is not None:
        filters.append("model=?")
        args.append(model)
    clause = " WHERE " + " AND ".join(filters) if filters else ""
    with db() as c:
        return pd.read_sql_query("SELECT * FROM paper_scans" + clause +
                                 " ORDER BY created_utc DESC, id DESC",
                                 c, params=args)

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


# Restore only files exported by this app. This provides a manual fallback
# when ephemeral Streamlit Community Cloud SQLite is reset. No new API account.
BACKUP_COLUMNS = (
    "symbol", "source", "model", "side", "horizon", "created_utc",
    "reference_price", "target_utc", "cost_pct", "status",
    "entry_utc", "exit_utc", "entry_price", "exit_price",
    "gross_pct", "net_pct", "origin",
)


def restore_csv_backup(content):
    """Merge a previously exported, schema-validated CSV (no double counting).

    It is an optional manual recovery, NOT continuous cloud persistence.
    All entries are validated before the atomic SQLite transaction.
    """
    import io
    import math
    if not isinstance(content, bytes) or len(content) > 5_000_000 or not content:
        raise ValueError("Plik CSV musi mieć od 1 B do 5 MB")
    try:
        frame = pd.read_csv(io.BytesIO(content), dtype={"created_utc": "string",
                                                        "target_utc": "string"})
    except (ValueError, UnicodeDecodeError, pd.errors.ParserError) as exc:
        raise ValueError("Nie można odczytać kopii CSV dziennika") from exc
    if frame.empty:
        return 0
    # Older V2-V4 exports have no origin column; those were manual tests.
    if not set(BACKUP_COLUMNS[:-1]).issubset(frame.columns) or len(frame) > 10000:
        raise ValueError("To nie jest eksport dziennika Claude lub plik jest za duży")
    if "origin" not in frame.columns:
        frame["origin"] = "MANUAL"
    accepted_status = {"OCZEKUJE", "TRAFIONY", "BLEDNY", "REMIS", "BRAK_DANYCH"}
    accepted_sources = {
        "SPY": {"Yahoo", "Alpaca IEX"},
        "QQQ": {"Yahoo", "Alpaca IEX"},
        "ES=F": {"Yahoo"},
        "CAPITAL_US500": {"Capital.com DEMO"},
    }
    def number(value, nullable=False):
        if pd.isna(value) or str(value).strip() == "":
            if nullable:
                return None
            raise ValueError("Brak wymaganej liczby w CSV")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("Niepoprawna liczba w CSV")
        return value

    prepared = []
    for _, row in frame.iterrows():
        vals = {col: row[col] for col in BACKUP_COLUMNS}
        sym = str(vals["symbol"])
        src = str(vals["source"])
        if src not in accepted_sources.get(sym, set()):
            raise ValueError("Nieznany instrument albo źródło w kopii")
        model = str(vals["model"])
        if not model.startswith(("ema-rsi-click-", "ema9-21-news-", "ema-rsi-v5-")):
            raise ValueError("Niezgodna wersja modelu w kopii")
        side = str(vals["side"])
        status = str(vals["status"])
        hz = int(number(vals["horizon"]))
        if side not in ("LONG", "SHORT") or hz not in HORIZONS or status not in accepted_status:
            raise ValueError("Nieprawidłowy kierunek, horyzont lub status w kopii")
        if str(vals["origin"]) not in ("AUTO", "MANUAL"):
            raise ValueError("Nieznane pochodzenie sygnału PAPER w kopii")
        created = pd.Timestamp(vals["created_utc"])
        target = pd.Timestamp(vals["target_utc"])
        if created.tzinfo is None or target.tzinfo is None:
            raise ValueError("Kopie muszą zawierać godziny UTC ze strefą")
        if abs((target-created).total_seconds() - 60*hz) > 1:
            raise ValueError("Horyzont kopii jest niezgodny z terminem rozliczenia")
        ref = number(vals["reference_price"])
        cost = number(vals["cost_pct"])
        if ref <= 0 or not 0 <= cost <= 2:
            raise ValueError("Niepoprawna cena lub koszt w kopii")
        for field in ("entry_utc", "exit_utc"):
            if not pd.isna(vals[field]) and str(vals[field]).strip():
                t = pd.Timestamp(vals[field])
                if t.tzinfo is None:
                    raise ValueError("Czas wykonania w kopii musi być UTC")
            else:
                vals[field] = None
        for field in ("entry_price", "exit_price", "gross_pct", "net_pct"):
            vals[field] = number(vals[field], nullable=True)
        if status in {"TRAFIONY", "BLEDNY", "REMIS"}:
            net = vals["net_pct"]
            if net is None or (
                (status == "TRAFIONY" and net <= 0) or
                (status == "BLEDNY" and net >= 0) or
                (status == "REMIS" and abs(net) > 1e-8)
            ):
                raise ValueError("Status w kopii jest niezgodny z wynikiem netto")
        vals["horizon"] = hz
        vals["reference_price"] = ref
        vals["cost_pct"] = cost
        prepared.append(tuple(vals[col] for col in BACKUP_COLUMNS))

    inserted = 0
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        for values in prepared:
            symbol, source, model, side, hz, created = values[:6]
            already = c.execute(
                """SELECT 1 FROM signals
                   WHERE symbol=? AND source=? AND model=? AND side=?
                     AND horizon=? AND created_utc=? LIMIT 1""",
                (symbol, source, model, side, hz, created)).fetchone()
            if already:
                continue
            cols = ", ".join(BACKUP_COLUMNS)
            params = ", ".join("?" for _ in BACKUP_COLUMNS)
            c.execute(f"INSERT INTO signals ({cols}) VALUES ({params})", values)
            inserted += 1
    return inserted
