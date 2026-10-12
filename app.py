"""Independent PAPER research app: no trading or brokerage order access."""
import math
import os

import feedparser
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import data_sources as ds
import store

st.set_page_config(page_title="Live Signal AI Test", layout="wide")

INSTRUMENTS = {
    "S&P 500 ETF (SPY)": "SPY",
    "Nasdaq ETF (QQQ)": "QQQ",
    "S&P 500 futures (ES=F)": "ES=F",
    "S&P 500 CFD (Capital.com DEMO)": "CAPITAL_US500",
}


def secret(name):
    v = os.environ.get(name, "")
    if not v:
        try:
            v = st.secrets.get(name, "")
        except Exception:
            v = ""
    return str(v).strip()


ALPACA_KEY, ALPACA_SECRET = secret("ALPACA_KEY_ID"), secret("ALPACA_SECRET_KEY")
OPENAI_KEY = secret("OPENAI_API_KEY")
CAPITAL_CREDS = tuple(secret(k) for k in
    ("CAPITAL_API_KEY", "CAPITAL_IDENTIFIER", "CAPITAL_PASSWORD", "CAPITAL_EPIC"))

with st.sidebar:
    st.header("Live Signal AI Test")
    instrument = st.selectbox("Instrument", list(INSTRUMENTS))
    symbol = INSTRUMENTS[instrument]
    if symbol == "CAPITAL_US500":
        source_choice = "Capital.com DEMO"
        st.caption("Tylko Capital.com DEMO; nie mieszamy SPY z CFD. US500 można analizować także rano, jeśli broker prowadzi handel.")
    elif symbol == "ES=F":
        source_choice = "Yahoo"
        st.caption("Kontrakt ES=F w tej wersji: tylko Yahoo.")
    else:
        source_choice = st.selectbox("Źródło notowań",
            ["Auto (Alpaca → Yahoo)", "Yahoo", "Alpaca IEX"])
    refresh = st.slider("Odświeżanie (sek.)", 20, 120, 30, step=10)
    cost = st.number_input("Dodatkowy koszt ROUND TRIP (% ceny)",
                           min_value=0.0, max_value=2.0, value=0.08, step=0.01,
                           help="SPY/QQQ: uproszczony koszt transakcyjny. CFD: spread z bid/ask jest już liczony, tutaj dodatkowa prowizja i poślizg.")
    use_gpt = st.checkbox("Analiza nagłówków przez OpenAI (możliwy koszt API)",
                          value=False, disabled=not bool(OPENAI_KEY))
    model = st.text_input("Model OpenAI", value="gpt-4o-mini",
                          disabled=not use_gpt)
    st.caption("Test PAPER. Prawdziwe zlecenia nie są wysyłane.")


@st.cache_data(ttl=20, show_spinner=False)
def cached_prices(sym, wanted):
    if wanted == "Yahoo":
        return ds.yahoo_bars(sym), "Yahoo", ""
    if wanted == "Alpaca IEX":
        if not all((ALPACA_KEY, ALPACA_SECRET)):
            return pd.DataFrame(), "Alpaca IEX", "Brak kluczy Alpaca w Secrets."
        try:
            return ds.alpaca_bars(sym, ALPACA_KEY, ALPACA_SECRET), "Alpaca IEX", ""
        except Exception as exc:
            return pd.DataFrame(), "Alpaca IEX", f"Błąd Alpaca: {type(exc).__name__}"
    if all((ALPACA_KEY, ALPACA_SECRET)):
        try:
            bars = ds.alpaca_bars(sym, ALPACA_KEY, ALPACA_SECRET)
            if not bars.empty:
                return bars, "Alpaca IEX", ""
        except Exception as exc:
            warning = f"Alpaca ({type(exc).__name__}) nie działa; przełączono na Yahoo."
        else:
            warning = "Alpaca nie zwróciła świec; przełączono na Yahoo."
    else:
        warning = "Bez kluczy Alpaca: wykorzystywane jest Yahoo."
    return ds.yahoo_bars(sym), "Yahoo", warning


def capital_client():
    """Keep session tokens per browser session; do not expose credentials in UI."""
    if not all(CAPITAL_CREDS):
        raise ValueError("Brakuje jednego z 4 wpisów CAPITAL_* w Streamlit Secrets")
    fingerprint = CAPITAL_CREDS
    if st.session_state.get("capital_fingerprint") != fingerprint:
        st.session_state["capital_client"] = ds.CapitalDemo(*CAPITAL_CREDS)
        st.session_state["capital_fingerprint"] = fingerprint
    return st.session_state["capital_client"]


def safe_capital_error(exc):
    if isinstance(exc, ds.CapitalAPIError):
        msg = str(exc)
        if exc.status in (401, 403) and exc.stage == "logowanie":
            msg += ". Sprawdź DEMO, login i HASŁO UTWORZONE DLA KLUCZA API (nie zwykłe hasło konta)."
        elif exc.status in (400, 404) and exc.stage in ("świece 1 min", "sprawdzanie EPIC"):
            msg += ". Zweryfikuj CAPITAL_EPIC na koncie DEMO (np. US500)."
        elif exc.status == 429:
            msg += ". Limit API; nie odświeżaj na siłę, poczekaj."
        elif exc.status is not None and exc.status >= 500:
            msg += ". Możliwa awaria API Capital.com; spróbuj później."
        return msg
    if isinstance(exc, ValueError):
        return "Ustawienia Capital.com DEMO: brak wymaganych pól lub nieprawidłowy EPIC."
    return f"Capital DEMO — problem połączenia ({type(exc).__name__}); szczegóły bez danych konta."


def get_prices(sym, wanted):
    if sym != "CAPITAL_US500":
        return cached_prices(sym, wanted)
    try:
        return capital_client().bars(), "Capital.com DEMO", ""
    except Exception as exc:
        return pd.DataFrame(), "Capital.com DEMO", safe_capital_error(exc)


@st.cache_data(ttl=300)
def get_news(sym):
    phrase = {"SPY": "S&P 500", "QQQ": "Nasdaq",
              "ES=F": "S&P 500 futures", "CAPITAL_US500": "S&P 500"}[sym]
    from urllib.parse import quote_plus
    url = ("https://news.google.com/rss/search?q=" +
           quote_plus(phrase + " when:1d") + "&hl=en-US&gl=US&ceid=US:en")
    return tuple(e.title for e in feedparser.parse(url).entries[:10])


@st.cache_data(ttl=600, show_spinner=False)
def news_score(titles, sym, enabled, model):
    if not titles:
        return 0.0, "Brak nagłówków"
    if enabled and OPENAI_KEY:
        try:
            import json
            from openai import OpenAI
            response = OpenAI(api_key=OPENAI_KEY).chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content":
                        "Oceń tylko krótkoterminowy możliwy wpływ tych nagłówków na "
                        f"{sym}. To nie jest prognoza. Zwróć JSON z score (-1..1) "
                        "i reason (jedno zdanie po polsku). Neutralne: 0."},
                    {"role": "user", "content": "\n".join(titles)},
                ], timeout=20)
            payload = json.loads(response.choices[0].message.content)
            return max(-1.0, min(1.0, float(payload["score"]))), str(payload.get("reason", ""))
        except Exception as exc:
            return 0.0, "Analiza GPT niedostępna: " + type(exc).__name__
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        vader = SentimentIntensityAnalyzer()
        value = sum(vader.polarity_scores(t)["compound"] for t in titles) / len(titles)
        return float(value), "VADER (darmowy; uproszczony sentyment)"
    except Exception:
        return 0.0, "Brak oceny wiadomości"


def make_signal(bars, sentiment, now):
    close = bars["Close"].astype(float)
    if len(close) < 30:
        return "WAIT", 0.0, 0.0, None, "Za mało świec"
    ema9 = close.ewm(span=9, adjust=False).mean().iloc[-1]
    ema21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
    trend = (ema9 / ema21 - 1) * 100
    score = 0.6 * math.tanh(trend / 0.03) + 0.4 * sentiment
    age = (now - (bars.index[-1] + pd.Timedelta(minutes=1))).total_seconds() / 60
    if age > 5 or age < -0.01:
        return "WAIT", score, trend, age, "Brak świeżych, zamkniętych świec; bez sygnału"
    if score > 0.35:
        return "LONG", score, trend, age, "Wzrostowe EMA + ocena nagłówków (heurystyka)"
    if score < -0.35:
        return "SHORT", score, trend, age, "Spadkowe EMA + ocena nagłówków (heurystyka)"
    return "WAIT", score, trend, age, "Brak wystarczającego sygnału"


st.title("Live Signal AI Test — osobny eksperyment")
st.caption("Nie jest połączony z poprzednim SP500 Signal AI Pro. Osobny dziennik oraz oddzielne instrumenty/źródła.")


@st.fragment(run_every=refresh)
def live():
    now = pd.Timestamp.now(tz="UTC")
    try:
        candles, actual_source, source_note = get_prices(symbol, source_choice)
    except Exception as exc:
        st.error("Nie udało się pobrać cen: " + type(exc).__name__)
        return
    if source_note:
        st.warning(source_note)
    if symbol == "CAPITAL_US500":
        with st.expander("Diagnostyka Capital.com DEMO (bez ujawniania kluczy)"):
            st.caption("Przycisk testuje połączenie z DEMO i szczegóły instrumentu. "
                       "Pokazuje tylko kod HTTP, nazwę rynku i kwotowania. Nie wysyła zleceń.")
            if st.button("Sprawdź logowanie i instrument US500", key="capital_probe"):
                try:
                    details = capital_client().market_details()
                    st.success(f"DEMO OK · EPIC: {details['epic']} · instrument: "
                               f"{details['name']} · stan rynku: {details['market_status']}")
                    st.write(f"Bid: {details['bid']} · Ask: {details['ask']} "
                             f"· czas broker UTC: {details['update_time']}")
                except Exception as exc:
                    st.error(safe_capital_error(exc))
            st.info("Jeżeli logowanie zwraca HTTP 401/403: CAPITAL_PASSWORD oznacza "
                    "osobne hasło nadane przy tworzeniu klucza API. HTTP 400/404 przy świecach "
                    "może oznaczać błędny EPIC. HTTP 429 to limit zapytań. "
                    "Tylko konto DEMO.")
    if candles.empty:
        st.info("Brak świec. Sprawdź źródło danych lub godziny sesji.")
        return
    candles = ds.closed_bars(candles, now=now)
    if candles.empty:
        st.info("Brak zakończonych świec minutowych.")
        return
    titles = get_news(symbol)
    sentiment, reason = news_score(titles, symbol, use_gpt, model)
    side, score, trend, age, note = make_signal(candles, sentiment, now)

    store.settle(symbol, actual_source, candles, now)
    stored = store.log_signal(symbol, actual_source, side,
                              float(candles["Close"].iloc[-1]), now, cost)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Sygnał PAPER", side)
    col2.metric("Cena (mid dla CFD)", f"{candles['Close'].iloc[-1]:,.2f}")
    col3.metric("Ocena (nie prawdopodobieństwo)", f"{score:+.2f}")
    col4.metric("Wiadomości", f"{sentiment:+.2f}")
    st.caption(f"{note} | EMA9/21: {trend:+.3f}% | wiek świecy: {age:.1f} min | źródło: {actual_source} | zapisane: {stored} wiersze")

    graph_data = candles.tail(120)
    fig = go.Figure(go.Candlestick(x=graph_data.index,
        open=graph_data["Open"], high=graph_data["High"],
        low=graph_data["Low"], close=graph_data["Close"], name=symbol))
    for span, color in ((9, "#2563eb"), (21, "#f59e0b")):
        fig.add_trace(go.Scatter(x=graph_data.index,
            y=candles["Close"].ewm(span=span, adjust=False).mean().tail(120),
            name=f"EMA{span}", line=dict(color=color, width=1.5)))
    fig.update_layout(height=450, xaxis_rangeslider_visible=False,
                      margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, width="stretch")

    st.subheader("Rozliczenia sygnałów — PAPER")
    st.caption("Wynik po kosztach; spread CFD liczony z bid/ask. Sumy procentów to suma obserwacji, NIE wynik portfela ani ROI. Sygnały na horyzontach mogą się pokrywać.")
    summary = store.stats(symbol, actual_source)
    if summary.empty:
        st.info("Brak rozliczeń w wybranym źródle danych.")
    else:
        st.dataframe(summary, width="stretch")
        if summary["rozliczone"].min() < 100:
            st.warning("Mniej niż 100 rozliczeń na horyzont — zbyt mała próba na ocenę metody.")
    journal = store.load(symbol, actual_source)
    st.download_button("Pobierz dziennik CSV", journal.to_csv(index=False).encode("utf-8-sig"),
        file_name="live_signal_test_journal.csv", mime="text/csv")
    with st.expander(f"Wiadomości — {reason}"):
        for headline in titles:
            st.write("•", headline)


live()
st.warning("Tylko badania PAPER. Nie wysyła zleceń. Zbieranie nowych sygnałów wymaga aktywnej sesji Streamlit; SQLite w Community Cloud nie zapewnia trwałości po restarcie.")
