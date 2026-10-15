"""Macro/news genuinely veto trades; M15/H1 are resampled from closed M1."""
import pandas as pd
from macro_calendar import parse_bls_ics, macro_guard
from market_context import market_structure, filtered_decision
from simple_ledger import trade_table


def ohlc(now, n=450, slope=.09):
    idx=pd.date_range(end=now-pd.Timedelta(minutes=1),periods=n,
                      freq="min",tz="UTC")
    opens=[7500+slope*i for i in range(n)]
    return pd.DataFrame({"Open":opens,"High":[x+.2 for x in opens],
                         "Low":[x-.2 for x in opens],
                         "Close":[x+0.05 for x in opens]},index=idx)


def base(side="LONG"):
    return {"side":side,"score_long":75,"age_min":0,"bars":450,
            "reason":"EMA+RSI", "reasons":["EMA9 > EMA21"],
            "reference":7510.0}


def test_bls_cpi_date_and_15_minute_window_in_utc():
    example="""BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=America/New_York:20261015T083000
SUMMARY:Consumer Price Index
END:VEVENT
END:VCALENDAR"""
    events=parse_bls_ics(example)
    assert len(events)==1
    event,_=events[0]
    assert event==pd.Timestamp("2026-10-15T12:30:00Z")
    assert macro_guard(event-pd.Timedelta(minutes=15),events,True)[0]
    assert macro_guard(event+pd.Timedelta(minutes=15),events,True)[0]
    assert not macro_guard(event+pd.Timedelta(minutes=16),events,True)[0]


def test_macro_calendar_unavailable_means_wait():
    assert macro_guard(pd.Timestamp("2026-10-15T09:00:00Z"),[],False)[0]


def test_h1_m15_use_completed_candles_and_support_resistance():
    now=pd.Timestamp("2026-10-15T18:00:00Z")
    context=market_structure(ohlc(now),now)
    assert context["ready"]
    assert context["h1"]==1
    assert context["support"] < context["resistance"]
    assert "H1:" in context["reason"]


def test_bad_news_physically_changes_long_to_wait():
    now=pd.Timestamp("2026-10-15T18:00:00Z")
    outcome=filtered_decision(base("LONG"),ohlc(now),now,[],True,
                              -.75,["US inflation jumps above forecasts"])
    assert outcome["side"]=="WAIT"
    assert "LONG odrzucony" in outcome["rationale"]
    assert "(-0.75)" in outcome["reason"]


def test_good_context_allows_long_and_explanation_mentions_all_sources():
    now=pd.Timestamp("2026-10-15T18:00:00Z")
    outcome=filtered_decision(base("LONG"),ohlc(now),now,[],True,
                              .35,["Wall Street gains amid rate optimism"])
    assert outcome["side"]=="LONG"
    assert "H1:" in outcome["rationale"]
    assert "wsparcie M15" in outcome["rationale"]
    assert "RSS" in outcome["rationale"]
    assert "publikacji" in outcome["rationale"]


def test_macro_release_blocks_both_sides():
    now=pd.Timestamp("2026-10-15T18:00:00Z")
    for side in ("LONG","SHORT"):
        outcome=filtered_decision(base(side),ohlc(now),now,
                                  [(now,"CPI")],True,.4,["Positive news"])
        assert outcome["side"]=="WAIT"
        assert "STOP MAKRO" in outcome["rationale"]


def test_net_points_counts_one_forecast_not_six_horizons():
    ts=pd.Timestamp("2026-10-15T17:00:00Z")
    rows=[]
    for hz in (1,3,5,15,30,60):
        rows.append({"horizon":hz,"created_utc":ts.isoformat(),
          "model":"ema-rsi-mtf-macro-v6", "side":"LONG","entry_price":7800.2,
          "exit_price":7804.0,"cost_pct":.02,"status":"TRAFIONY",
          "rationale":"H1 wzrostowy; M15 wyższe dołki; RSS +0.3"})
    table,scores=trade_table(pd.DataFrame(rows))
    assert len(table)==1
    assert scores["wins"]==1 and scores["losses"]==0
    # Spread is already included in bid/ask prices, extra 0.02% costs 1.56004pts.
    assert scores["points"]==2.24
    assert scores["winrate"]==100.0
    assert "RSS" in table.iloc[0]["Dlaczego taki typ?"]
