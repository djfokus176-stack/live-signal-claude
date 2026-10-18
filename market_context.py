"""Multi-timeframe market structure using *completed broker M1 candles only*.

M15/H1 bars are resampled from the SAME Capital.com bid/ask midquotes (or
selected equity data source), never from a visually separate TradingView
widget. Incomplete periods and bars with missing minutes are not used.
"""
import pandas as pd


def _resample_closed(bars, minutes, now):
    b = bars.loc[:, ["Open", "High", "Low", "Close"]].copy().sort_index()
    b.index = pd.to_datetime(b.index, utc=True)
    b = b.loc[b.index + pd.Timedelta(minutes=1) <= now]
    frequency = f"{minutes}min"
    agg = b.resample(frequency, origin="start_day").agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last"})
    count = b["Close"].resample(frequency, origin="start_day").count()
    agg = agg.loc[count == minutes]
    agg = agg.loc[agg.index + pd.Timedelta(minutes=minutes) <= now]
    return agg


def market_structure(bars, now):
    now = pd.Timestamp(now).tz_convert("UTC")
    if (bars is None or bars.empty or
            not {"Open","High","Low","Close"}.issubset(bars.columns)):
        return {"ready": False, "reason": "Brak pełnych OHLC do M15/H1"}
    m15 = _resample_closed(bars, 15, now)
    h1 = _resample_closed(bars, 60, now)
    if len(m15) < 12 or len(h1) < 6:
        return {"ready": False,
                "reason": (f"Niepełna historia trendów M15/H1 "
                           f"({len(m15)} M15, {len(h1)} H1)")}
    recent_h1 = h1.tail(6)
    # Completed H1 rising/falling trend based on slope, not the most
    # recently *unfinished* hourly candle.
    h1_slope = float(recent_h1["Close"].iloc[-1] -
                     recent_h1["Close"].iloc[0])
    threshold = float(recent_h1["Close"].iloc[-1]) * 0.00025
    h1_dir = 1 if h1_slope > threshold else -1 if h1_slope < -threshold else 0
    a, b = m15.iloc[-6:-3], m15.iloc[-3:]
    rise = b["High"].max() > a["High"].max() and b["Low"].min() > a["Low"].min()
    fall = b["High"].max() < a["High"].max() and b["Low"].min() < a["Low"].min()
    m15_dir = 1 if rise else -1 if fall else 0
    # Support and resistance only from completed *preceding* M15 bars.
    historical = m15.iloc[-13:-1]
    support = float(historical["Low"].min())
    resistance = float(historical["High"].max())
    last = float(m15["Close"].iloc[-1])
    return {
        "ready": True, "h1": h1_dir, "m15": m15_dir,
        "support": support, "resistance": resistance,
        "close_m15": last, "bars_m15": len(m15), "bars_h1": len(h1),
        "reason": (f"H1: {'wzrostowy' if h1_dir>0 else 'spadkowy' if h1_dir<0 else 'boczny'}; "
                   f"M15: {'wyższe szczyty/dołki' if m15_dir>0 else 'niższe szczyty/dołki' if m15_dir<0 else 'bez potwierdzonej struktury'}; "
                   f"wsparcie M15 {support:.1f}, opór M15 {resistance:.1f}")
    }


def filtered_decision(base, bars, now, events, calendar_ok,
                      news_score=None, news_titles=()):
    from macro_calendar import macro_guard
    answer = dict(base)
    answer["reasons"] = list(answer.get("reasons") or ())
    def veto(message):
        answer["side"] = "WAIT"
        answer["reason"] = message
        answer["reasons"].append(message)
    # Broker candles are a precondition for ANY macro/news decision. When
    # Capital.com is closed over the weekend, calendar fetching is skipped
    # and calendar_ok=False is intentional. Do not misdiagnose that as a BLS
    # outage or compute H1/M15 from stale Friday prices.
    age = answer.get("age_min")
    if (age is None or age < -0.02 or age > 2.5 or
            int(answer.get("bars") or 0) < 60):
        when = pd.Timestamp(now).tz_convert("Europe/Warsaw")
        weekend = when.weekday() == 5 or (when.weekday() == 6 and when.hour < 23)
        detail = (f"ostatnia świeca sprzed {age:.0f} min"
                  if age is not None else "brak świec brokera")
        message = (
            "WAIT: weekend / oczekiwanie na wznowienie notowań Capital.com "
            if weekend else "WAIT: brak świeżych danych brokera "
        ) + f"({detail}); nie analizujemy nieaktualnych cen"
        answer["market_data_ok"] = False
        answer["macro_checked"] = False
        veto(message)
        answer["rationale"] = "; ".join(answer["reasons"])[:1200]
        return answer
    blocked, macro_note = macro_guard(now, events, calendar_ok)
    structure = market_structure(bars, now)
    answer["market_data_ok"] = True
    answer["macro_checked"] = True
    answer["reasons"].append(macro_note)
    answer["reasons"].append(structure["reason"])
    # Important: this is the ACTUAL trade-entry veto, not just UI text.
    if blocked:
        veto(macro_note)
    elif not structure["ready"]:
        veto(structure["reason"])
    elif answer["side"] == "LONG" and (
        structure["h1"] <= 0 or structure["m15"] < 0
    ):
        veto("LONG odrzucony: H1 nie rośnie lub M15 ma spadkową strukturę")
    elif answer["side"] == "SHORT" and (
        structure["h1"] >= 0 or structure["m15"] > 0
    ):
        veto("SHORT odrzucony: H1 nie spada lub M15 ma wzrostową strukturę")
    # News VADER is an *imperfect tone* proxy, not a CPI surprise
    # measurement. We use it only to veto opposing trades, never
    # generate a LONG or SHORT from headlines alone.
    if news_score is not None:
        value = max(-1.0, min(1.0, float(news_score)))
        news_text = f"Ton nagłówków RSS (VADER) {value:+.2f}"
        if news_titles:
            news_text += "; przykład: " + str(news_titles[0])[:110]
        answer["reasons"].append(news_text)
        if answer["side"] == "LONG" and value <= -0.25:
            veto(f"LONG odrzucony: negatywne wiadomości ({value:+.2f})")
        elif answer["side"] == "SHORT" and value >= +0.25:
            veto(f"SHORT odrzucony: pozytywne wiadomości ({value:+.2f})")
    else:
        # The economic-release guard still vetoes entry ±15 min and whenever
        # its schedule cannot be verified. RSS is *optional*; a temporary
        # Google News outage must not kill all paper research for days.
        answer["reasons"].append(
            "RSS niedostępny — brak filtra tonu wiadomości; "
            "obowiązują kalendarz makro i potwierdzenia M15/H1")
        answer["news_checked"] = False
    answer["rationale"] = "; ".join(answer["reasons"])[:1200]
    if answer["side"] != "WAIT":
        answer["reason"] = f"{answer['side']}: M15/H1 + świece + wiadomości + kalendarz potwierdzone"
    return answer
