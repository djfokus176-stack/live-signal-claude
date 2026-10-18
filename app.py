"""Independent PAPER research app: no trading or brokerage order access."""
import json
import os
import importlib

import feedparser
import pandas as pd
import streamlit.components.v1 as components
import streamlit as st

import data_sources as ds
import store
import macro_calendar
import market_context
from runtime_compat import ensure_store_api, safe_failure_id

# Protect Streamlit hot deploys: updated app.py sometimes keeps the previous
# in-memory imported store/macro modules and Streamlit cache. Check *actual*
# algorithms, not only a common API compatibility stamp.
EXPECTED_STORE_MODEL = "ema-rsi-mtf-macro-v6"
EXPECTED_CALENDAR_VERSION = "bls-official-plus-2026-snapshot-v3"

def ensure_live_modules():
    if (getattr(store, "MODEL", None) != EXPECTED_STORE_MODEL or
            not callable(getattr(store, "log_scan", None))):
        importlib.invalidate_caches()
        importlib.reload(store)
    if getattr(macro_calendar, "CALENDAR_ENGINE_VERSION", None) != EXPECTED_CALENDAR_VERSION:
        importlib.invalidate_caches()
        importlib.reload(macro_calendar)
    if (store.MODEL != EXPECTED_STORE_MODEL or
            macro_calendar.CALENDAR_ENGINE_VERSION != EXPECTED_CALENDAR_VERSION):
        raise RuntimeError("NIEZGODNE_WERSJE_MODULOW")
    return store.MODEL, macro_calendar.CALENDAR_ENGINE_VERSION

ensure_live_modules()

import paper_dashboard
from signal_logic import evaluate
from daily_report import today_activity
from paper_scheduler import auto_scan_due
import simple_ledger

st.set_page_config(page_title="CLAUDE LIVE SIGNAL • PAPER", page_icon="📈", layout="wide")

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


st.markdown("""
<style>
[data-testid="stAppViewContainer"] {background:#0b1020;}
[data-testid="stSidebar"] {background:#171d2d;}
.block-container {padding-top:1.0rem;max-width:1900px;padding-left:1.4rem;padding-right:1.4rem;}
[data-testid="stMetric"] {background:#18243a;border:1px solid #30425b;border-radius:12px;padding:12px;}
[data-testid="stMetricValue"] {font-size:1.65rem;}
</style>
""", unsafe_allow_html=True)

TV_SYMBOLS = {
    "CAPITAL_US500": "CAPITALCOM:US500",
    "SPY": "AMEX:SPY",
    "QQQ": "NASDAQ:QQQ",
    "ES=F": "CME_MINI:ES1!",
}

with st.sidebar:
    st.markdown("### 🤖 CLAUDE LIVE SIGNAL")
    st.caption("PROGRAM 2 · PAPER · V6: M15/H1 + wiadomości + makro")
    st.divider()
    # CFD first: US500 is traded during many morning hours as well as US session.
    names = ["S&P 500 CFD (Capital.com DEMO)", "S&P 500 ETF (SPY)",
             "Nasdaq ETF (QQQ)", "S&P 500 futures (ES=F)"]
    instrument = st.selectbox("Co obserwujemy?", names, index=0)
    symbol = INSTRUMENTS[instrument]
    interval = st.selectbox("Świece na wykresie", ["1m", "3m", "5m", "15m", "30m", "60m"], index=2)
    source_choice = "Capital.com DEMO" if symbol == "CAPITAL_US500" else (
        "Yahoo" if symbol == "ES=F" else st.selectbox("Dane do analizy bota",
            ["Auto (Alpaca → Yahoo)", "Alpaca IEX", "Yahoo"]))
    refresh = st.slider("Odświeżanie analizy (sek.)", 20, 120, 30, step=10)
    auto_paper = st.toggle("Automatyczne testy PAPER co 15 min", value=True,
                           help="Skanuje i zapisuje tylko gdy karta Streamlit "
                                "pozostaje aktywna. Nie wysyła zleceń.")
    st.caption("AUTO: bez kosztów OpenAI, wyłącznie świeże zamknięte świece. "
               "Po zamknięciu karty testy nie zbierają się dalej. "
               "Wyłącz przełącznikiem, aby korzystać tylko ręcznie.")
    cost = st.number_input("Koszt dodatkowy ROUND TRIP (%)", min_value=0.0,
                           max_value=2.0, value=0.08, step=0.01)
    st.caption("Dla CFD spread bid/ask jest już uwzględniany w teście. "
               "Ten koszt to dodatkowe opłaty i poślizg.")
    st.caption("Bez hasła właściciela i bez płatnych analiz AI. "
               "Wszystkie pozycje są wyłącznie PAPER.")
    use_gpt = False
    model = "gpt-4o-mini"

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
    import requests
    response = requests.get(url, timeout=8)
    response.raise_for_status()
    return tuple(e.title for e in feedparser.parse(response.content).entries[:10])


@st.cache_data(ttl=900, show_spinner=False)
def official_calendar(engine_version):
    # Version argument prevents reusing cached "BLS unavailable" results
    # saved by an earlier deployment of the app.
    return macro_calendar.load_official_calendar()


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



def tv_widget(tv_symbol, interval):
    """TradingView iframe owns displayed chart; broker bars are used only by model."""
    interval_code = {"1m": "1", "3m": "3", "5m": "5",
                     "15m": "15", "30m": "30", "60m": "60"}[interval]
    options = {
        "autosize": False, "width": "100%", "height": 650,
        "symbol": tv_symbol, "interval": interval_code,
        "timezone": "Europe/Warsaw", "theme": "dark", "locale": "pl",
        "style": "1", "backgroundColor": "#10192c",
        "gridColor": "rgba(255,255,255,.05)",
        "allow_symbol_change": False, "hide_top_toolbar": False,
        "hide_side_toolbar": False, "save_image": False,
        "withdateranges": True, "calendar": False,
    }
    html = """<style>
      html,body{background:#10192c;padding:0;margin:0;width:100%;height:100%}
      .tradingview-widget-container{height:684px;width:100%;}
      .tradingview-widget-container__widget{height:650px;width:100%;}
      .tradingview-widget-copyright{padding-top:5px;font:12px sans-serif;color:#8da8c7}
    </style>
    <div class="tradingview-widget-container">
      <div class="tradingview-widget-container__widget"></div>
      <div class="tradingview-widget-copyright">
        <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer"
           style="color:#96c8ff">Wykres TradingView — może mieć opóźnienie</a>
      </div>
      <script type="text/javascript" async
       src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js"
       >__OPTIONS__</script>
    </div>""".replace("__OPTIONS__", json.dumps(options))
    components.html(html, height=696, scrolling=False)


@st.fragment(run_every=refresh)
def bot_and_log():
    try:
        ensure_live_modules()
    except Exception as exc:
        st.error("Nie udało się zsynchronizować wersji modelu i kalendarza. "
                 f"Błąd: {type(exc).__name__}. "
                 "Nie otwieramy hipotetycznej pozycji na niezgodnym kodzie.")
        return
    now = pd.Timestamp.now(tz="UTC")
    try:
        candles, actual_source, warning = get_prices(symbol, source_choice)
        if warning:
            st.warning(warning)
    except Exception:
        candles, actual_source = pd.DataFrame(), source_choice
        st.error("Nie udało się pobrać danych bota. Wykres TradingView działa niezależnie.")
    if candles is None:
        candles = pd.DataFrame()
    candles = ds.closed_bars(candles, now) if not candles.empty else candles
    base_opinion = evaluate(candles, now)
    # LIVE model uses these macro/news values BEFORE any entry is considered.
    titles, headline_score = (), None
    try:
        if base_opinion.get("age_min") is not None and (
                0 <= base_opinion["age_min"] <= 2.5):
            titles = tuple(get_news(symbol))
            if titles:
                headline_score, _ = news_score(titles, symbol, False, "")
    except Exception:
        titles, headline_score = (), None
    try:
        if (base_opinion.get("bars", 0) >= 60 and
                base_opinion.get("age_min") is not None and
                0 <= base_opinion["age_min"] <= 2.5):
            official_events, calendar_ok = official_calendar(EXPECTED_CALENDAR_VERSION)
        else:
            official_events, calendar_ok = (), False
    except Exception:
        official_events, calendar_ok = (), False
    opinion = market_context.filtered_decision(
        base_opinion, candles, now, official_events, calendar_ok,
        headline_score, titles)
    st.caption("Model: zamknięte M1 + trend H1 + struktura M15 + "
               "wiadomości RSS + filtr publikacji USA ±15 minut.")
    st.caption(f"Bieżąca wersja: {store.MODEL} · wymagane horyzonty PAPER: "
               f'{", ".join(str(h) for h in store.HORIZONS)} min · '
               f'{opinion["bars"]} zamkniętych świec M1.')
    age = opinion.get("age_min")
    data_recent = age is not None and 0 <= age <= 2.5
    history_ok = data_recent and opinion["bars"] >= 60 and not (
        "Przerwa" in opinion["reason"])
    local_now = now.tz_convert("Europe/Warsaw")
    weekend_hint = (local_now.weekday() == 5 or
                    (local_now.weekday() == 6 and local_now.hour < 23))
    status_label = ("Gotowe do analizy" if history_ok else
                    "Świece świeże, historia niepełna" if data_recent else
                    "Przerwa weekendowa / stare notowania" if weekend_hint else
                    "Brak świeżych danych brokera")
    st.metric("Stan danych", status_label)
    st.metric("Wiek świecy", f"{age:.1f} min" if age is not None else "—")
    if not data_recent:
        if candles is not None and not candles.empty:
            last_closed_pl = (pd.Timestamp(candles.index[-1]) +
                              pd.Timedelta(minutes=1)).tz_convert(
                                  "Europe/Warsaw").strftime("%d.%m.%Y %H:%M")
            st.caption(f"Ostatnia potwierdzona świeca Capital.com: "
                       f"{last_closed_pl} czasu polskiego. "
                       "Wykres TradingView może mieć inne notowania.")
        st.warning("Brak świeżych cen Capital.com: "
                   "nie uruchamiamy nowych transakcji PAPER. "
                   "To nie jest błąd kalendarza BLS. "
                   "Po wznowieniu rynku bot musi zebrać nowe świece.")
    elif opinion["side"] == "WAIT" and not history_ok:
        st.warning(opinion["reason"])
    st.session_state["claude_last_source_" + symbol] = actual_source
    if not candles.empty:
        # Passive settlement is safe; no forecast is silently logged.
        try:
            store.settle(symbol, actual_source, candles, now, model=None)
        except Exception:
            st.warning("Rozliczanie chwilowo niedostępne (baza).")
    if symbol == "CAPITAL_US500" and candles.empty:
        st.caption("Nie ma potwierdzonego odczytu US500 przez API. Sprawdź "
                   "diagnostykę Capital.com poniżej. Nie analizujemy cen TV jako cen brokera.")

    clicked = st.button("🤖 ANALIZUJ TERAZ — LONG czy SHORT?",
                        use_container_width=True, type="primary")
    current_key = f"{symbol}|{actual_source}"
    auto_slot = f"claude_v5_auto_{symbol}_{actual_source}"
    last_auto_scan = st.session_state.get(auto_slot)
    auto_due = bool(auto_paper and not clicked and
                    auto_scan_due(last_auto_scan, now))
    if clicked or auto_due:
        if auto_due:
            # Only mark the scan as due once for this user session.
            # Never claim 24/7 collection or durable market background work.
            st.session_state[auto_slot] = now.isoformat()
        origin = "MANUAL" if clicked else "AUTO"
        result = dict(opinion)
        saved = 0
        issue = ""
        if result["side"] in ("LONG", "SHORT") and result["reference"] is not None:
            try:
                saved = store.log_signal(symbol, actual_source, result["side"],
                                         result["reference"], now, cost,
                                         origin=origin,
                                         rationale=result.get("rationale", ""))
                if saved == 0:
                    issue = ("Blokada 15 min od poprzedniego zapisu na "
                             "tym instrumencie — brak nowego testu.")
            except Exception:
                issue = "Błąd zapisu bazy — niczego nie uznajemy za zapisane."
        else:
            issue = ("WAIT — bez prognozy LONG/SHORT i bez nowego testu. "
                     + result["reason"])
        # RSS, VADER and macro data were evaluated BEFORE deciding the
        # direction. Log exactly the context used at the entry decision.
        audit_result = ("ZAPISANO" if saved else
                        "BLAD_ZAPISU" if issue.startswith("Błąd") else
                        "BLOKADA" if result["side"] != "WAIT" else "WAIT")
        try:
            # One more compatibility check: fragments may run without a full
            # page rerun, preserving the old imported module across a deploy.
            ensure_store_api(store).log_scan(
                symbol, actual_source, now, origin, result, audit_result,
                news_score=headline_score, news_titles=titles)
        except Exception as exc:
            st.warning(
                "Historia skanów: zapis niedostępny "
                f"({safe_failure_id(exc)}). "
                "Pozycja PAPER, jeśli została zapisana, pozostaje w dzienniku. "
                "Ta informacja nie potwierdza pomyślnego zapisu skanu.")
            if auto_due:
                # Retry a failed AUTO audit after ~2min, not after 15min.
                st.session_state[auto_slot] = (
                    now - pd.Timedelta(minutes=13)).isoformat()
        st.session_state["claude_news_context"] = {
            "key": current_key, "count": len(titles),
            "score": headline_score, "titles": titles[:3],
        }
        st.session_state["last_bot_v5"] = {
            "key": current_key, "analysis": result, "time": now.isoformat(),
            "saved": saved, "message": issue, "origin": origin,
            "model": store.MODEL,
        }

    if auto_paper:
        st.caption("🟢 AUTO PAPER włączony — skan co 15 minut, TYLKO "
                   "gdy strona pozostaje aktywna.")
        last_scan = st.session_state.get(auto_slot)
        if last_scan:
            last_pl = pd.Timestamp(last_scan).tz_convert(
                "Europe/Warsaw").strftime("%H:%M:%S")
            st.caption(f"Ostatni skan AUTO tej sesji: {last_pl} (czas polski)")
    else:
        st.caption("AUTO PAPER wyłączony — tylko przycisk ręczny.")

    previous = st.session_state.get("last_bot_v5")
    if (previous and previous.get("key") == current_key and
            previous.get("model") == store.MODEL):
        assessed = previous["analysis"]
        direction = assessed["side"]
        origin_text = "AUTO" if previous["origin"] == "AUTO" else "RĘCZNY"
        time_pl = pd.Timestamp(previous["time"]).tz_convert("Europe/Warsaw").strftime("%H:%M:%S")
        if previous["saved"] and direction == "LONG":
            st.success(f"🟢 LONG — NOWY test {origin_text} PAPER zapisany · {time_pl}")
        elif previous["saved"] and direction == "SHORT":
            st.error(f"🔴 SHORT — NOWY test {origin_text} PAPER zapisany · {time_pl}")
        elif direction in ("LONG", "SHORT"):
            st.info(f"{direction} — analiza {origin_text} {time_pl}; NIE zapisano nowego testu")
        else:
            st.warning(f"🟡 WAIT — skan {origin_text}, bez testowego wejścia · {time_pl}")
        st.metric("Indeks kierunku LONG (nie % trafień)",
                  f'{assessed["score_long"]}/100')
        st.write(assessed["reason"])
        if assessed["reference"] is not None:
            st.caption(f'Cena odniesienia u źródła bota: {assessed["reference"]:,.2f}. '
                       "To nie jest cena wykonania transakcji.")
        for line in assessed["reasons"]:
            st.caption("• " + line)
        if previous["saved"]:
            st.success(f'Zapisano {previous["saved"]}/{len(store.HORIZONS)} horyzontów '
                       f'do rozliczenia. To jeden test kierunku, nie '
                       f'{previous["saved"]} niezależnych transakcji.')
        if previous["message"]:
            st.info(previous["message"])
        st.caption("Wynik ostatniej analizy (ręcznej lub AUTO), "
                   "nie każdej aktualizacji wykresu.")
    else:
        st.info("Bot wykonuje test AUTO co 15 minut przy otwartej stronie; "
                "możesz także kliknąć przycisk. "
                "Zapisywane są tylko świeże LONG/SHORT.")
    st.caption("Ocena nie jest prawdopodobieństwem wygranej. "
               "System nie ma jeszcze potwierdzonej przewagi po kosztach.")


st.title("CLAUDE LIVE SIGNAL — PROGRAM 2")
st.caption("US500 · sygnały badawcze PAPER · bez logowania właściciela")
simple_ledger.render(symbol)
st.divider()

chart_col, assistant_col = st.columns([3.0, 1.3], gap="medium")
with chart_col:
    tv_widget(TV_SYMBOLS[symbol], interval)
    st.caption("Wykres dostarcza TradingView; bot korzysta z innego, wskazanego w menu źródła "
               "danych. Ceny i godziny aktualizacji mogą się różnić.")
with assistant_col:
    st.subheader("Twój bot")
    bot_and_log()

st.divider()
st.subheader("📆 Typy z dzisiaj — PROGRAM 2")
st.caption("Wyłącznie faktycznie zapisane analizy PAPER ze wszystkich "
           "wersji bota w tej lokalnej bazie. Godzina polska. "
           "Nie zawiera niezapisanych WAIT ani prognoz z GitHuba.")

@st.fragment(run_every=refresh)
def today_panel():
    try:
        all_rows = store.load(model=None)
        now_today = pd.Timestamp.now(tz="Europe/Warsaw")
        today = today_activity(all_rows, now_today)
        st.caption(f'Dzień w Polsce: {now_today.strftime("%d.%m.%Y")} · '
                   "źródło: lokalny dziennik Streamlit")
        forecast_count = len(today)
        horizons = ["1 min", "3 min", "5 min", "15 min", "30 min", "60 min"]
        outcomes = today[horizons].stack() if forecast_count else pd.Series(dtype=str)
        won = int(outcomes.astype(str).str.startswith("TRAFIONY").sum())
        lost = int(outcomes.astype(str).str.startswith("BLEDNY").sum())
        done = int(outcomes.astype(str).str.startswith(
            ("TRAFIONY", "BLEDNY", "REMIS")).sum())
        a, b, c, d = st.columns(4)
        a.metric("Zapisane prognozy", forecast_count)
        b.metric("Rozliczone horyzonty", done)
        c.metric("Trafione", won)
        d.metric("Błędne", lost)
        if today.empty:
            st.info("W tej lokalnej bazie nie ma dziś zapisanych "
                    "prognoz LONG/SHORT. To nie dowodzi, że bot nie skanował: "
                    "mógł wydać WAIT albo lokalna baza mogła zniknąć "
                    "przy restarcie Streamlit.")
        else:
            st.dataframe(today, use_container_width=True,
                         hide_index=True)
            st.download_button("⬇️ Pobierz dzisiejsze typy CSV",
                               data=today.to_csv(index=False).encode("utf-8-sig"),
                               file_name="claude_typy_dzisiaj.csv", mime="text/csv",
                               use_container_width=False)
        st.caption("1 zapisany LONG/SHORT = 1 prognoza i maksymalnie "
                   "6 powiązanych horyzontów. To nie są niezależne zakłady "
                   "ani rzeczywiste ROI. Starsze modele są widoczne, "
                   "ale ich skuteczności nie sumujemy z V5.")
    except Exception:
        st.warning("Nie udało się odczytać dzisiejszych zapisów z lokalnej "
                   "bazy. Nie przedstawiamy niepotwierdzonych typów.")

today_panel()

@st.fragment(run_every=refresh)
def paper_position_audit():
    current_source = st.session_state.get(
        "claude_last_source_" + symbol,
        "Capital.com DEMO" if symbol == "CAPITAL_US500" else "Yahoo")
    paper_dashboard.render(symbol, current_source)

paper_position_audit()

st.divider()
st.subheader("📒 Dziennik i rozliczenia — tylko PROGRAM 2")
st.caption("Zapisujemy godzinę sygnału i kierunek. Rozliczamy netto na "
           "1, 3, 5, 15, 30 i 60 minutach. W przypadku braku danych wynik "
           "pozostaje niepotwierdzony. Sumy procentów nie są ROI portfela.")

@st.fragment(run_every=refresh)
def journal_panel():
    # Record histories are isolated by symbol, price source and model version.
    source = ("Capital.com DEMO" if symbol == "CAPITAL_US500"
              else "Yahoo" if source_choice == "Yahoo" or symbol == "ES=F"
              else "Alpaca IEX" if source_choice == "Alpaca IEX"
              else (st.session_state.get("claude_last_source_" + symbol, "Alpaca IEX")))
    df = store.load(symbol, source)
    settled = df[df["status"].isin(["TRAFIONY", "BLEDNY", "REMIS"])] if not df.empty else df
    total = len(settled)
    wins = int((settled["status"] == "TRAFIONY").sum()) if total else 0
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Zapisane analizy", df["created_utc"].nunique() if not df.empty else 0)
    c2.metric("Rozliczone horyzonty", total)
    c3.metric("Trafione", f"{wins}/{total}")
    c4.metric("Średnia netto", f'{settled["net_pct"].mean():+.4f}%' if total else "—")
    st.caption(f"Wersja dziennika: {store.MODEL} · źródło: {source} · "
               f'oczekiwane horyzonty jednej analizy: {", ".join(str(h) for h in store.HORIZONS)} min.')
    if not df.empty:
        origin_counts = df.groupby("origin")["created_utc"].nunique().to_dict()
        st.caption("Zapisane analizy V5: AUTO "
                   f'{origin_counts.get("AUTO", 0)} · RĘCZNE '
                   f'{origin_counts.get("MANUAL", 0)}.')
        latest = store.newest_horizon_coverage(df)
        local_time = pd.Timestamp(latest["created_utc"]).tz_convert(
            "Europe/Warsaw").strftime("%d.%m.%Y %H:%M:%S")
        st.caption(f'Ostatnia zapisana analiza: {local_time} (czas polski) · '
                   f'{len(latest["observed"])}/{len(latest["expected"])} '
                   "horyzontów obecnych w bazie.")
        if latest["missing"]:
            st.warning(
                "NIEPEŁNY ZAPIS: dla ostatniej prognozy brakuje horyzontów "
                + ", ".join(str(h) for h in latest["missing"])
                + " min. Nie dopisujemy ich po fakcie, ponieważ zmieniałoby to "
                "historyczne rozliczenia. Następny zapis należy sprawdzić "
                "po ponownym uruchomieniu aplikacji."
            )
    if not total:
        st.info("Jeszcze brak rozliczonych testów w tej wersji algorytmu i źródle.")
    else:
        st.dataframe(store.stats(symbol, source), use_container_width=True)
        if total < 100:
            st.warning("Zbyt mała próba do oceny skuteczności (minimum 100 to dopiero wstęp).")
    if not df.empty:
        st.dataframe(df[["created_utc", "origin", "side", "horizon", "status", "net_pct"]],
                     use_container_width=True, hide_index=True)
    st.download_button("⬇️ Pobierz aktualny dziennik CSV",
                       data=df.to_csv(index=False).encode("utf-8-sig"),
                       file_name="claude_paper_v5.csv", mime="text/csv",
                       use_container_width=False)
    # Never mix V3 and V4 outcomes in the same success percentage.
    all_history = store.load(symbol, source, model=None)
    old = all_history.loc[all_history["model"] != store.MODEL]
    if not old.empty:
        with st.expander("📚 Historia starszych wersji algorytmu (osobno)"):
            st.caption("Nie łączymy skuteczności historycznych wersji z V5. "
                       "Poprzednie sygnały w lokalnej bazie pozostały.")
            st.dataframe(old[["model", "created_utc", "side",
                              "horizon", "status", "net_pct"]],
                         use_container_width=True, hide_index=True)
    st.download_button("⬇️ Pobierz WSZYSTKIE wersje z dostępnej bazy",
                       data=all_history.to_csv(index=False).encode("utf-8-sig"),
                       file_name="claude_paper_all_versions.csv",
                       mime="text/csv", use_container_width=False)
    with st.expander("💾 Przywróć dziennik z pobranego CSV — bez nowego konta"):
        st.caption("Opcjonalna kopia ratunkowa po restarcie Streamlit. "
                   "Wybierz tylko plik CSV pobrany wcześniej z tego programu. "
                   "Powtarzany import nie dubluje testów. To nie jest "
                   "automatyczna, trwała baza.")
        restored = st.file_uploader("Wybierz wcześniej pobrany plik CSV",
                                    type=["csv"], key="restore_claude_csv")
        if restored is not None and st.button("Sprawdź i przywróć dane z CSV",
                                             key="restore_claude_confirm"):
            try:
                restored_count = store.restore_csv_backup(restored.getvalue())
                st.session_state["claude_restore_notice"] = (
                    f"Przywrócono {restored_count} brakujących wierszy "
                    "(istniejące wpisy bez zmian).")
                st.rerun(scope="fragment")
            except (ValueError, TypeError, OverflowError) as exc:
                st.error(f"Nieprawidłowy plik kopii: {exc}")
            except Exception:
                st.error("Nie udało się przywrócić bazy; nie dopisujemy "
                         "niezweryfikowanych wpisów.")
        if st.session_state.get("claude_restore_notice"):
            st.success(st.session_state["claude_restore_notice"])



journal_panel()

st.divider()
st.subheader("🗃️ GitHub PAPER — przygotowane archiwum futures")
st.warning("Skaner GitHub jest przygotowany, ale **harmonogram NIE JEST AKTYWNY**. "
           "GitHub odrzucił konfigurację cyklicznych wywołań; "
           "nie twierdzimy, że skanuje rynek po zamknięciu strony.")
st.caption("Przygotowany oddzielny model ES=F (Yahoo futures, NIE Capital.com US500 CFD). "
           "Bez kont, kluczy i wydatków OpenAI. "
           "Publiczne CSV pojawią się dopiero po udanym uruchomieniu skanera.")

@st.cache_data(ttl=180, show_spinner=False)
def github_futures_history():
    import io
    import requests
    root = ("https://raw.githubusercontent.com/"
            "djfokus176-stack/live-signal-claude/main/data/auto_futures/")
    status_response = requests.get(root + "status.json", timeout=10)
    if status_response.status_code == 404:
        return None, pd.DataFrame()
    status_response.raise_for_status()
    status = status_response.json()
    now_time = pd.Timestamp.now(tz="UTC")
    names = []
    for t in (now_time - pd.Timedelta(days=7), now_time):
        year, week, _ = t.isocalendar()
        name = f"ES_F_{year}-W{week:02d}.csv"
        if name not in names:
            names.append(name)
    frames = []
    for name in names:
        resp = requests.get(root + name, timeout=10)
        if resp.status_code == 404:
            continue
        resp.raise_for_status()
        frame = pd.read_csv(io.BytesIO(resp.content))
        if not frame.empty:
            frames.append(frame)
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return status, data


@st.fragment(run_every=refresh)
def github_futures_panel():
    try:
        status, history = github_futures_history()
        if status is None:
            st.info("Brak uruchomionych skanów GitHub. "
                    "Kod i testy skanera są przygotowane, "
                    "ale harmonogram nie przeszedł walidacji GitHub.")
            return
        checked = pd.Timestamp(status["checked_utc"]).tz_convert(
            "Europe/Warsaw").strftime("%d.%m.%Y %H:%M")
        source_state = status.get("status", "NIEZNANY")
        st.caption(f'Ostatnia próba GitHub: {checked} (Polska) · '
                   f'wynik: {source_state} · wyłącznie ES=F Yahoo.')
        if source_state == "ERROR":
            st.warning("Automat zgłosił problem z Yahoo lub zapisem. "
                       "To nie jest sygnał tradingowy.")
        if history.empty:
            st.info("Brak zapisanych prognoz futures. Może to oznaczać "
                    "zamknięty rynek, brak danych lub same decyzje WAIT.")
            return
        history = history.loc[
            (history["symbol"] == "ES=F") &
            (history["source"] == "Yahoo") &
            (history["model"] == "ema-rsi-v5-auto-futures-github")
        ]
        recorded = history["created_utc"].nunique()
        final_statuses = ("TRAFIONY", "BLEDNY", "REMIS")
        settled = history[history["status"].isin(final_statuses)]
        wins = int((settled["status"] == "TRAFIONY").sum())
        a, b, c = st.columns(3)
        a.metric("Osobne prognozy ES=F", int(recorded))
        b.metric("Rozliczone horyzonty", len(settled))
        c.metric("Trafione horyzonty", f"{wins}/{len(settled)}")
        st.caption("Jedna prognoza ma 6 horyzontów, które **nie są** "
                   "niezależnymi transakcjami ani portfelem. "
                   "Nie łączymy tej serii z CAPITAL_US500.")
        shown = history.sort_values(
            ["created_utc", "horizon"], ascending=[False, True])
        st.dataframe(shown[["created_utc", "side", "horizon", "status",
                            "gross_pct", "net_pct"]].head(60),
                     use_container_width=True, hide_index=True)
        st.download_button("⬇️ Pobierz historię ES=F GitHub (ostatnie 2 tygodnie)",
                           data=history.to_csv(index=False).encode("utf-8-sig"),
                           mime="text/csv", file_name="claude_esf_auto_github.csv")
    except Exception:
        st.warning("Nie udało się chwilowo odczytać publicznego archiwum "
                   "GitHub. Nie wpływa to na analizy Capital.com.")


github_futures_panel()
with st.expander("📰 Wiadomości z rana i południa — kontekst, nie sygnał", expanded=False):
    st.caption("Wiadomości nie przełączają automatycznie LONG/SHORT. "
               "Nagłówki nie zastępują zweryfikowanych danych cenowych.")
    if st.button("Pobierz aktualne nagłówki", key="claude_load_news"):
        try:
            titles = get_news(symbol)
            score, reason = news_score(titles, symbol, use_gpt, model)
            st.caption(f"Sentyment nagłówków: {score:+.2f} — {reason}. "
                       "Wartość informacyjna, nie szansa wygranej.")
            for title in titles[:10]:
                st.write("• " + title)
        except Exception:
            st.info("Nagłówki chwilowo niedostępne.")

if symbol == "CAPITAL_US500":
    with st.expander("🛠 Diagnostyka Capital.com DEMO (bez sekretów)", expanded=False):
        st.caption("Odczyt tylko z DEMO. Login i dane API wpisuje się wyłącznie "
                   "w Settings → Secrets tej aplikacji.")
        if st.button("Sprawdź logowanie US500", key="claude_diagnostic"):
            try:
                d = capital_client().market_details()
                st.success(f'DEMO OK · {d["epic"]} · stan rynku: {d["market_status"]}')
            except Exception as exc:
                st.error(safe_capital_error(exc))

st.warning("PAPER — bez rzeczywistych zleceń. Świece TradingView aktualizują się "
           "w ramach warunków dostawcy; analiza bota odświeża się przy otwartej stronie. "
           "Lokalny SQLite w darmowym Streamlit może zniknąć po restarcie. "
           "Do testów 24/7 potrzebujemy trwałej bazy i procesu w tle.")
