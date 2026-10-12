import pandas as pd
import store


def bars(start, n, step=0.01, p0=100.0):
    idx = pd.date_range(start, periods=n, freq="min", tz="UTC")
    close = [p0 + i * step for i in range(n)]
    return pd.DataFrame({"Open": [c - step for c in close], "Close": close}, index=idx)


def test_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", str(tmp_path / "t.db"))
    b = bars("2026-10-12 14:00", 120)
    now = pd.Timestamp("2026-10-12 14:10", tz="UTC")
    assert store.log_signal("SPY", "LONG", 100.1, now) == 4
    assert store.log_signal("SPY", "LONG", 100.1, now + pd.Timedelta(minutes=5)) == 0  # cooldown
    assert store.log_signal("SPY", "WAIT", 100.1, now) == 0
    assert store.settle("SPY", b, now + pd.Timedelta(minutes=3)) == 0  # za wcześnie
    store.settle("SPY", b, now + pd.Timedelta(minutes=70))
    df = store.load("SPY")
    assert set(df["status"]) == {"TRAFIONY"}  # rosnące ceny, LONG
    assert (df["net_pct"] < df["gross_pct"]).all()
    assert not store.stats("SPY").empty


def test_short_loses_and_gap(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", str(tmp_path / "t.db"))
    b = bars("2026-10-12 14:00", 60)
    now = pd.Timestamp("2026-10-12 14:10", tz="UTC")
    store.log_signal("SPY", "SHORT", 100.1, now)
    store.settle("SPY", b, now + pd.Timedelta(minutes=70))
    st = dict(zip(store.load()["horizon"], store.load()["status"]))
    assert st[5] == "BLEDNY" and st[15] == "BLEDNY"
    # luka: brak świec po sygnale -> BRAK_DANYCH dopiero po 6 h
    now2 = pd.Timestamp("2026-10-13 14:10", tz="UTC")
    store.log_signal("QQQ", "LONG", 100, now2)
    store.settle("QQQ", b, now2 + pd.Timedelta(hours=7))
    assert set(store.load("QQQ")["status"]) == {"BRAK_DANYCH"}
