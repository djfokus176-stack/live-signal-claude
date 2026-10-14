"""PAPER position and news scan audit tests, no live API or brokerage access."""
import pandas as pd
import pytest
import store
from paper_positions import positions, counts_today


@pytest.fixture(autouse=True)
def clean_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", str(tmp_path / "paper.sqlite"))


def prices(start="2026-10-14T14:00:00Z", n=120):
    index=pd.date_range(start,periods=n,freq="min",tz="UTC")
    close=[100+i*.03 for i in range(n)]
    return pd.DataFrame({"Open":close,"High":close,"Low":close,
                         "Close":close},index=index)


def test_auto_trade_entry_appears_before_target_exit():
    now=pd.Timestamp("2026-10-14T14:10:10Z")
    assert store.log_signal("SPY","Yahoo","LONG",100.3,now,origin="AUTO")==6
    candles=prices()
    checked=now+pd.Timedelta(minutes=2)
    store.settle("SPY","Yahoo",candles,checked)
    h15=store.load("SPY","Yahoo")
    h15=h15[h15["horizon"]==15].iloc[0]
    assert h15["status"]=="OCZEKUJE"
    assert pd.notna(h15["entry_utc"])
    assert pd.notna(h15["entry_price"])
    assert pd.isna(h15["exit_utc"])
    board,totals=positions(store.load("SPY","Yahoo"),15)
    assert totals["all"]==1
    assert totals["done"]==0
    assert board.iloc[0]["Status"]=="OTWARTA PAPER"
    later=now+pd.Timedelta(minutes=16)
    store.settle("SPY","Yahoo",candles,later)
    board,totals=positions(store.load("SPY","Yahoo"),15)
    assert totals["all"]==totals["done"]==1
    assert board.iloc[0]["Status"] in ("PLUS","MINUS","ZERO")
    assert pd.notna(board.iloc[0]["Cena wyjścia"])
    assert pd.notna(board.iloc[0]["Netto %"])


def test_short_has_positive_points_when_price_falls():
    ts=pd.Timestamp("2026-10-14T14:10:10Z")
    store.log_signal("SPY","Yahoo","SHORT",100.3,ts,cost_pct=0,origin="AUTO")
    bars=prices()
    bars[["Open","High","Low","Close"]]=200-bars[["Open","High","Low","Close"]]
    store.settle("SPY","Yahoo",bars,ts+pd.Timedelta(minutes=16))
    board,totals=positions(store.load("SPY","Yahoo"),15)
    assert totals["plus"]==1
    assert board.iloc[0]["Punkty"]>0
    assert board.iloc[0]["Netto %"]>0


def test_wait_news_context_is_logged_without_new_trade():
    now=pd.Timestamp("2026-10-14T19:40:00Z")
    store.log_scan("CAPITAL_US500","Capital.com DEMO",now,"AUTO",
                   {"side":"WAIT","score_long":50,"reason":"EMA neutral",
                    "bars":120,"age_min":.5,"reference":None},
                   "WAIT",news_score=-.3,
                   news_titles=("News one","News two"))
    scans=store.load_scans("CAPITAL_US500")
    assert len(scans)==1
    assert scans.iloc[0]["news_score"]==pytest.approx(-.3)
    assert scans.iloc[0]["news_count"]==2
    assert store.load(model=None).empty
    count=counts_today(scans,now)
    assert count=={"auto":1,"manual":0,"wait":1,"saved":0,"all":1}


def test_each_scan_is_audited_but_only_one_forecast():
    now=pd.Timestamp("2026-10-14T15:00:00Z")
    opinion={"side":"LONG","score_long":80,"reason":"2 wskazniki",
             "bars":100,"age_min":.4,"reference":7780}
    for i in range(2):
        t=now+pd.Timedelta(minutes=i)
        n=store.log_signal("CAPITAL_US500","Capital.com DEMO",
                           "LONG",7780,t,origin="AUTO")
        store.log_scan("CAPITAL_US500","Capital.com DEMO",t,"AUTO",
                       opinion,"ZAPISANO" if n else "BLOKADA")
    scans=store.load_scans("CAPITAL_US500")
    assert len(scans)==2
    assert len(store.load("CAPITAL_US500"))==6
    assert counts_today(scans,now)["saved"]==1


def test_no_history_means_no_fabricated_success():
    board,totals=positions(pd.DataFrame(),15)
    assert board.empty
    assert totals["done"]==0
    assert totals["avg"] is None
    assert counts_today(pd.DataFrame(),pd.Timestamp("2026-10-14T10:00:00Z"))["all"]==0
