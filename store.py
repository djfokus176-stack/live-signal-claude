"""Dziennik sygnałów: zapis (LONG/SHORT) i uczciwe rozliczenie po czasie.

Zasady: wejście = otwarcie PIERWSZEJ świecy PO sygnale; wyjście = zamknięcie
pierwszej świecy kończącej się w/po czasie horyzontu; koszt odejmowany z góry.
Brak świecy (luka w danych) = BRAK_DANYCH, nigdy zgadywanie.
"""
import os
import sqlite3
from contextlib import contextmanager

import pandas as pd

DB = os.getenv("SIGNAL_DB", "signals.db")
HORIZONS = (5, 15, 30, 60)  # minuty
MAX_GAP = pd.Timedelta(minutes=3)


@contextmanager
def db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.execute(
        """create table if not exists signals(
            id integer primary key autoincrement, symbol text, side text,
            horizon integer, created_utc text, price_ref real, target_utc text,
            status text default 'OCZEKUJE', entry_utc text, exit_utc text,
            entry_price real, exit_price real, gross_pct real, net_pct real)"""
    )
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def log_signal(symbol, side, price, now, cooldown_min=15) -> int:
    """Zapisuje sygnał na wszystkich horyzontach, max raz na cooldown dla danej strony."""
    if side not in ("LONG", "SHORT"):
        return 0
    now = pd.Timestamp(now)
    with db() as c:
        last = c.execute(
            "select max(created_utc) from signals where symbol=? and side=?", (symbol, side)
        ).fetchone()[0]
        if last and now - pd.Timestamp(last) < pd.Timedelta(minutes=cooldown_min):
            return 0
        for h in HORIZONS:
            c.execute(
                "insert into signals(symbol,side,horizon,created_utc,price_ref,target_utc) values (?,?,?,?,?,?)",
                (symbol, side, h, now.isoformat(), float(price), (now + pd.Timedelta(minutes=h)).isoformat()),
            )
    return len(HORIZONS)


def settle(symbol, bars: pd.DataFrame, now, cost_pct=0.02) -> int:
    """Rozlicza oczekujące sygnały na podstawie świec (index = początek świecy, UTC)."""
    if bars.empty:
        return 0
    now = pd.Timestamp(now)
    done = 0
    with db() as c:
        rows = c.execute(
            "select id,created_utc,horizon,side from signals where symbol=? and status='OCZEKUJE'",
            (symbol,),
        ).fetchall()
        for sid, created, hz, side in rows:
            created = pd.Timestamp(created)
            later = bars.loc[bars.index > created]
            if later.empty:
                if now - created > pd.Timedelta(hours=6):
                    c.execute("update signals set status='BRAK_DANYCH' where id=?", (sid,))
                    done += 1
                continue
            entry_t = later.index[0]
            if entry_t - created > MAX_GAP:
                c.execute("update signals set status='BRAK_DANYCH' where id=?", (sid,))
                done += 1
                continue
            need = entry_t + pd.Timedelta(minutes=hz)
            if now < need + pd.Timedelta(minutes=2):
                continue
            ex = bars.loc[bars.index + pd.Timedelta(minutes=1) >= need]
            if ex.empty or ex.index[0] + pd.Timedelta(minutes=1) - need > MAX_GAP:
                if now - need > pd.Timedelta(hours=6) or not ex.empty:
                    c.execute("update signals set status='BRAK_DANYCH' where id=?", (sid,))
                    done += 1
                continue
            p_in, p_out = float(later["Open"].iloc[0]), float(ex["Close"].iloc[0])
            move = (p_out / p_in - 1) * 100
            gross = move if side == "LONG" else -move
            net = gross - cost_pct
            c.execute(
                """update signals set status=?, entry_utc=?, exit_utc=?, entry_price=?,
                   exit_price=?, gross_pct=?, net_pct=? where id=?""",
                ("TRAFIONY" if gross > 0 else "BLEDNY", entry_t.isoformat(), ex.index[0].isoformat(),
                 p_in, p_out, gross, net, sid),
            )
            done += 1
    return done


def load(symbol=None) -> pd.DataFrame:
    with db() as c:
        q = "select * from signals" + (" where symbol=?" if symbol else "") + " order by id desc"
        return pd.read_sql_query(q, c, params=(symbol,) if symbol else None)


def stats(symbol=None) -> pd.DataFrame:
    df = load(symbol)
    df = df[df["status"].isin(["TRAFIONY", "BLEDNY"])]
    if df.empty:
        return pd.DataFrame()
    g = df.groupby("horizon")
    out = pd.DataFrame({
        "rozliczone": g.size(),
        "trafione_%": g["gross_pct"].apply(lambda s: round((s > 0).mean() * 100, 1)),
        "śr_netto_%": g["net_pct"].mean().round(4),
        "suma_netto_%": g["net_pct"].sum().round(3),
    })
    out.index = [f"{h} min" for h in out.index]
    return out
