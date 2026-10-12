"""Prosty program: wykres na żywo + wiadomości (GPT) + sygnał LONG / SHORT / WAIT.

Uruchom:  streamlit run app.py
Klucz OpenAI:  zmienna środowiskowa OPENAI_API_KEY  (albo .streamlit/secrets.toml).
NIE wpisuj klucza w kodzie. To narzędzie edukacyjne, nie porada inwestycyjna.
"""
import math
import os

import feedparser
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf

import store

st.set_page_config(page_title="Live Signal", layout="wide")

SYMBOLS = {"S&P 500 (SPY)": "SPY", "Nasdaq (QQQ)": "QQQ", "Kontrakty S&P (ES=F)": "ES=F"}


def get_secret(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        try:
            value = st.secrets.get(name, "")
        except Exception:
            value = ""
    return str(value).strip()


KEY = get_secret("OPENAI_API_KEY")
ALPACA = (get_secret("ALPACA_KEY_ID"), get_secret("ALPACA_SECRET_KEY"))

with st.sidebar:
    st.header("Ustawienia")
    name = st.selectbox("Instrument", list(SYMBOLS))
    refresh = st.slider("Odświeżanie wykresu (sekundy)", 10, 120, 30)
    use_gpt = st.checkbox("Wiadomości ocenia GPT (płatne, grosze)", value=bool(KEY))
    model = st.text_input("Model OpenAI", "gpt-4o-mini")
    st.caption("Bez GPT użyję darmowego VADER (słabszy, ale 0 zł).")
    source = st.selectbox("Źródło cen", ["Auto", "Yahoo", "Alpaca (IEX, na żywo)"])
    cost = st.number_input("Koszt transakcji (spread) w %", 0.0, 1.0, 0.02, 0.01)
    st.caption("Alpaca: ustaw ALPACA_KEY_ID i ALPACA_SECRET_KEY (darmowe konto paper).")
symbol = SYMBOLS[name]


def yahoo_bars(sym: str) -> pd.DataFrame:
    df = yf.download(sym, period="1d", interval="1m", progress=False, auto_adjust=False)
    if df.empty:
        df = yf.download(sym, period="5d", interval="1m", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Close"])
    idx = pd.DatetimeIndex(df.index)
    df.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    return df


def alpaca_bars(sym: str) -> pd.DataFrame:
    """Świece 1m z darmowego kanału IEX (jedna giełda, ale praktycznie na żywo)."""
    start = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = requests.get(
        f"https://data.alpaca.markets/v2/stocks/{sym}/bars",
        params={"timeframe": "1Min", "start": start, "limit": 400, "sort": "desc",
                "feed": "iex", "adjustment": "raw"},
        headers={"APCA-API-KEY-ID": ALPACA[0], "APCA-API-SECRET-KEY": ALPACA[1]},
        timeout=15,
    )
    r.raise_for_status()
    df = pd.DataFrame(r.json().get("bars") or [])
    if df.empty:
        return df
    df = df.rename(columns={"t": "Date", "o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
    df.index = pd.to_datetime(df.pop("Date"), utc=True)
    return df.sort_index()


@st.cache_data(ttl=20)
def get_prices(sym: str, source: str):
    """Zwraca (świece, nazwa użytego źródła). Alpaca tylko dla akcji/ETF (SPY, QQQ)."""
    want_alpaca = source.startswith("Alpaca") or (source == "Auto" and all(ALPACA))
    if want_alpaca and all(ALPACA) and sym in ("SPY", "QQQ"):
        try:
            df = alpaca_bars(sym)
            if not df.empty:
                return df, "Alpaca IEX"
        except Exception:
            pass  # spadamy na Yahoo
    return yahoo_bars(sym), "Yahoo (może być opóźnione)"


@st.cache_data(ttl=300)
def get_news(sym: str) -> list[str]:
    q = {"SPY": "S&P 500", "QQQ": "Nasdaq", "ES=F": "S&P 500 futures"}.get(sym, sym)
    url = f"https://news.google.com/rss/search?q={q.replace(' ', '+')}+when:1d&hl=en-US&gl=US&ceid=US:en"
    return [e.title for e in feedparser.parse(url).entries[:10]]


@st.cache_data(ttl=600)  # GPT max raz na 10 min = tanio
def news_score(titles: tuple, use_gpt: bool, model: str, key: str):
    """Zwraca (wynik od -1 do +1, krótkie uzasadnienie)."""
    if not titles:
        return 0.0, "Brak wiadomości"
    if use_gpt and key:
        try:
            import json
            from openai import OpenAI

            resp = OpenAI(api_key=key).chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": (
                        "Oceniasz nagłówki pod kątem wpływu na indeks S&P 500 w najbliższych godzinach. "
                        'Odpowiedz TYLKO JSON: {"score": liczba od -1 (bardzo spadkowe) do 1 (bardzo wzrostowe), '
                        '"reason": "jedno krótkie zdanie po polsku"}. Jeśli nagłówki są neutralne, score ~ 0.')},
                    {"role": "user", "content": "\n".join(titles)},
                ],
            )
            data = json.loads(resp.choices[0].message.content)
            return max(-1.0, min(1.0, float(data["score"]))), str(data.get("reason", ""))
        except Exception as exc:
            return 0.0, f"GPT niedostępny ({type(exc).__name__}) — wiadomości pominięte"
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        sia = SentimentIntensityAnalyzer()
        return sum(sia.polarity_scores(t)["compound"] for t in titles) / len(titles), "VADER (darmowy)"
    except Exception:
        return 0.0, "Brak analizy wiadomości"


def make_signal(df: pd.DataFrame, news: float):
    close = df["Close"].astype(float)
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    trend = (ema9.iloc[-1] / ema21.iloc[-1] - 1) * 100
    score = 0.6 * math.tanh(trend / 0.03) + 0.4 * news
    age = (pd.Timestamp.now(tz="UTC") - (df.index[-1] + pd.Timedelta(minutes=1))).total_seconds() / 60
    if len(df) < 30:
        return "WAIT", score, trend, age, "Za mało danych"
    if age > 5:
        return "WAIT", score, trend, age, f"Dane starsze o {age:.0f} min (rynek zamknięty lub opóźnienie)"
    if score > 0.35:
        return "LONG", score, trend, age, "Trend w górę + wiadomości sprzyjają"
    if score < -0.35:
        return "SHORT", score, trend, age, "Trend w dół + wiadomości sprzyjają"
    return "WAIT", score, trend, age, "Brak wyraźnej przewagi — nie graj"


@st.fragment(run_every=refresh)
def live():
    df, used = get_prices(symbol, source)
    if df.empty:
        st.warning("Brak danych z Yahoo. Spróbuj za chwilę.")
        return
    titles = tuple(get_news(symbol))
    news, why = news_score(titles, use_gpt, model, KEY)
    side, score, trend, age, note = make_signal(df, news)
    now = pd.Timestamp.now(tz="UTC")
    store.settle(symbol, df, now, cost)
    store.log_signal(symbol, side, float(df["Close"].iloc[-1]), now)

    color = {"LONG": "#16a34a", "SHORT": "#dc2626", "WAIT": "#6b7280"}[side]
    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(f"<h1 style='color:{color};margin:0'>{side}</h1>", unsafe_allow_html=True)
    c2.metric("Cena", f"{df['Close'].iloc[-1]:.2f}")
    c3.metric("Wynik (-1…+1)", f"{score:+.2f}")
    c4.metric("Wiadomości", f"{news:+.2f}")
    st.caption(f"{note} • trend EMA9/21: {trend:+.3f}% • wiek danych: {age:.1f} min • źródło: {used}")

    d = df.tail(120)
    fig = go.Figure(go.Candlestick(x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"], name=symbol))
    for span, col in ((9, "#2563eb"), (21, "#f59e0b")):
        fig.add_trace(go.Scatter(x=d.index, y=df["Close"].ewm(span=span, adjust=False).mean().tail(120),
                                 name=f"EMA{span}", line=dict(color=col, width=1.5)))
    fig.update_layout(height=520, xaxis_rangeslider_visible=False, margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, width="stretch")

    st.subheader("Skuteczność sygnałów (rozliczone po czasie, z kosztem)")
    table = store.stats(symbol)
    if table.empty:
        st.info("Jeszcze nic nie rozliczono. Sygnały zapisują się, gdy strona jest otwarta.")
    else:
        st.dataframe(table, width="stretch")
        if table["rozliczone"].min() < 100:
            st.caption("⚠️ Mniej niż 100 rozliczeń na horyzont — wynik to jeszcze przypadek, nie dowód.")
    st.download_button("Pobierz dziennik (CSV)", store.load(symbol).to_csv(index=False).encode(),
                       "dziennik_sygnalow.csv", "text/csv")

    with st.expander(f"Wiadomości — {why}"):
        for t in titles:
            st.write("•", t)


st.title("Live Signal")
live()
st.warning("Wynik to prosty wskaźnik techniczny + sentyment, NIE prawdopodobieństwo zysku. Nie graj za prawdziwe pieniądze bez testów na historii.")
