"""Read-only, deterministic PAPER opinion, independent of presentation and news feeds.

This is an uncalibrated research heuristic, NOT a probability or financial advice.
Only completed broker/market bars passed by the caller are used.
"""
import math

import pandas as pd


def evaluate(bars: pd.DataFrame, now: pd.Timestamp) -> dict:
    now = pd.Timestamp(now)
    if now.tzinfo is None:
        raise ValueError("Czas analizy musi być UTC (timezone-aware)")
    now = now.tz_convert("UTC")
    result = {
        "side": "WAIT", "score_long": 50, "confidence": None,
        "age_min": None, "reason": "Brak potwierdzonego kierunku",
        "reasons": [], "reference": None, "rsi": None,
        "momentum_pct": None, "ema_diff_pct": None, "bars": 0,
    }
    if bars is None or len(bars) < 60 or "Close" not in bars:
        result["reason"] = "Potrzebujemy co najmniej 60 zamkniętych świec M1."
        return result
    close = pd.to_numeric(bars["Close"], errors="coerce").dropna()
    close = close.loc[~close.index.duplicated(keep="last")].sort_index()
    close.index = pd.to_datetime(close.index, utc=True)
    close = close.loc[close.index + pd.Timedelta(minutes=1) <= now]
    # A closed-minute close must be a valid executable price, not NaN/inf/zero.
    close = close.loc[close.gt(0) & close.lt(float("inf"))]
    result["bars"] = len(close)
    if len(close) < 60:
        result["reason"] = "Brak 60 poprawnych, zakończonych świec."
        return result

    age = (now - (close.index[-1] + pd.Timedelta(minutes=1))).total_seconds()/60
    result["age_min"] = age
    if not math.isfinite(age) or age < -0.02 or age > 2.5:
        result["reason"] = "Świece są nieaktualne albo nie zostały jeszcze zakończone. Bez gry."
        return result
    # Do not mix pre-weekend/previous-session bars with the fresh few bars.
    # A 60-bar EMA/RSI lookback must comprise 60 consecutive M1 candles.
    window = close.tail(60)
    gaps = window.index.to_series().diff().dropna()
    if (gaps != pd.Timedelta(minutes=1)).any():
        result["reason"] = ("Przerwa w historii minutowej (np. sesja/awaria). "
                            "Czekamy na 60 kolejnych, zamkniętych świec.")
        return result
    ema9 = close.ewm(span=9, adjust=False).mean().iloc[-1]
    ema21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
    diff = (ema9 / ema21 - 1) * 100
    delta = close.diff().fillna(0)
    gains = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean().iloc[-1]
    losses = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean().iloc[-1]
    rsi = 100 if losses == 0 and gains > 0 else 50 if losses == 0 else 100 - 100/(1+gains/losses)
    momentum = (close.iloc[-1]/close.iloc[-6] - 1) * 100
    if not all(math.isfinite(x) for x in (diff, rsi, momentum)):
        result["reason"] = "Niepoprawne wskaźniki — brak bezpiecznej decyzji."
        return result

    trend_vote = 1 if diff > 0.012 else -1 if diff < -0.012 else 0
    momentum_vote = 1 if momentum > 0.025 else -1 if momentum < -0.025 else 0
    rsi_vote = 1 if 52 <= rsi < 72 else -1 if 28 < rsi <= 48 else 0
    votes = (trend_vote, momentum_vote, rsi_vote)
    sum_votes = sum(votes)
    # Direction requires two agreeing independent trend / momentum / RSI conditions.
    side = "LONG" if sum_votes >= 2 else "SHORT" if sum_votes <= -2 else "WAIT"
    # This is a *directional index*, not a probability of success.
    directional_index = min(95, max(5, round(50 + 12 * sum_votes + 7 * math.tanh(diff/0.035))))
    explanations = [
        "EMA9 ponad EMA21" if diff > 0 else "EMA9 poniżej EMA21" if diff < 0 else "EMA bez kierunku",
        f"RSI(14): {rsi:.1f}",
        f"Momentum 5 min: {momentum:+.3f}%",
    ]
    result.update({
        "side": side,
        "score_long": directional_index,
        "reference": float(close.iloc[-1]),
        "rsi": round(rsi, 1),
        "ema_diff_pct": round(float(diff), 4),
        "momentum_pct": round(float(momentum), 4),
        "reason": ("Zbieżność co najmniej 2 wskaźników — sygnał badawczy."
                   if side != "WAIT" else "Wskaźniki nie potwierdzają wspólnego kierunku."),
        "reasons": explanations,
    })
    return result
