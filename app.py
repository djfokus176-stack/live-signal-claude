"""Independent PAPER research app: no trading or brokerage order access."""
import json
import os

import feedparser
import pandas as pd
import streamlit.components.v1 as components
import streamlit as st

import data_sources as ds
import store
from signal_logic import evaluate

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
    st.caption("PROGRAM 2 · niezależny eksperyment · wersja V4 AUDYT")
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
    cost = st.number_input("Koszt dodatkowy ROUND TRIP (%)", min_value=0.0,
                           max_value=2.0, value=0.08, step=0.01)
    st.caption("Dla CFD spread bid/ask jest już uwzględniany w teście. "
               "Ten koszt to dodatkowe opłaty i poślizg.")
    st.divider()
    use_gpt = st.checkbox("Dodatkowa analiza newsów przez OpenAI (płatne API)",
                          value=False, disabled=not bool(OPENAI_KEY))
    model = st.text_input("Model OpenAI", value="gpt-4o-mini", disabled=not use_gpt)
    st.caption("Brak realnych transakcji. Oddzielny dziennik i wersja modelu.")

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
    opinion = evaluate(candles, now)
    st.caption("Model: EMA9/21 + RSI + momentum · tylko zamknięte świece M1.")
    st.caption(f"Bieżąca wersja: {store.MODEL} · wymagane horyzonty PAPER: "
               f'{", ".join(str(h) for h in store.HORIZONS)} min · '
               f'{opinion["bars"]} zamkniętych świec M1.')
    age = opinion.get("age_min")
    data_recent = age is not None and 0 <= age <= 2.5
    history_ok = data_recent and opinion["bars"] >= 60 and not (
        "Przerwa" in opinion["reason"])
    st.metric("Stan danych", "Gotowe do analizy" if history_ok else
              "Świece świeże, historia niepełna" if data_recent else
              "Brak świeżych danych")
    st.metric("Wiek świecy", f"{age:.1f} min" if age is not None else "—")
    if opinion["side"] == "WAIT" and not history_ok:
        st.warning(opinion["reason"])
    st.session_state["claude_last_source_" + symbol] = actual_source
    if not candles.empty:
        # Passive settlement is safe; no forecast is silently logged.
        try:
            store.settle(symbol, actual_source, candles, now)
        except Exception:
            st.warning("Rozliczanie chwilowo niedostępne (baza).")
    if symbol == "CAPITAL_US500" and candles.empty:
        st.caption("Nie ma potwierdzonego odczytu US500 przez API. Sprawdź "
                   "diagnostykę Capital.com poniżej. Nie analizujemy cen TV jako cen brokera.")

    clicked = st.button("🤖 ANALIZUJ TERAZ — LONG czy SHORT?",
                        use_container_width=True, type="primary")
    current_key = f"{symbol}|{actual_source}"
    if clicked:
        result = dict(opinion)
        saved = 0
        issue = ""
        if result["side"] in ("LONG", "SHORT") and result["reference"] is not None:
            try:
                saved = store.log_signal(symbol, actual_source, result["side"],
                                         result["reference"], now, cost)
                if saved == 0:
                    issue = "Podobny sygnał już zapisano w ostatnich 15 minutach."
            except Exception:
                issue = "Błąd zapisu; nie zakładamy, że sygnał zapisano."
        else:
            issue = "WAIT — nie zapisujemy hipotetycznego wejścia."
        st.session_state["last_bot_v3"] = {
            "key": current_key, "analysis": result, "time": now.isoformat(),
            "saved": saved, "message": issue,
        }

    previous = st.session_state.get("last_bot_v3")
    if previous and previous.get("key") == current_key:
        assessed = previous["analysis"]
        direction = assessed["side"]
        time_pl = pd.Timestamp(previous["time"]).tz_convert("Europe/Warsaw").strftime("%H:%M:%S")
        if previous["saved"] and direction == "LONG":
            st.success(f"🟢 LONG — NOWY test PAPER zapisany · {time_pl}")
        elif previous["saved"] and direction == "SHORT":
            st.error(f"🔴 SHORT — NOWY test PAPER zapisany · {time_pl}")
        elif direction in ("LONG", "SHORT"):
            st.info(f"{direction} — analiza {time_pl}; NIE zapisano nowego testu")
        else:
            st.warning(f"🟡 WAIT — bez testowego wejścia · {time_pl}")
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
        st.caption("Wyświetlana decyzja pochodzi z ostatniego kliknięcia, "
                   "a nie z każdego automatycznego odświeżenia.")
    else:
        st.info("Wybierz moment, w którym chcesz sprawdzić rynek. "
                "Bot zapisze test tylko po kliknięciu i tylko dla świeżych danych.")
    st.caption("Ocena nie jest prawdopodobieństwem wygranej. "
               "System nie ma jeszcze potwierdzonej przewagi po kosztach.")


st.title("CLAUDE LIVE SIGNAL — PROGRAM 2")
st.caption("V4 AUDYT · źródła cen kontrolowane · rozliczenia PAPER bez przyszłych danych")
st.caption("Duże świece + przycisk analizy + niezależne wyniki PAPER. "
           "Nie zmienia PROGRAMU 1: SP500 AI PRO.")

chart_col, assistant_col = st.columns([3.0, 1.3], gap="medium")
with chart_col:
    tv_widget(TV_SYMBOLS[symbol], interval)
    st.caption("Wykres dostarcza TradingView; bot korzysta z innego, wskazanego w menu źródła "
               "danych. Ceny i godziny aktualizacji mogą się różnić.")
with assistant_col:
    st.subheader("Twój bot")
    bot_and_log()

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
        st.dataframe(df[["created_utc", "side", "horizon", "status", "net_pct"]],
                     use_container_width=True, hide_index=True)
    st.download_button("⬇️ Pobierz aktualny dziennik CSV",
                       data=df.to_csv(index=False).encode("utf-8-sig"),
                       file_name="claude_paper_v4.csv", mime="text/csv",
                       use_container_width=False)
    # Never mix V3 and V4 outcomes in the same success percentage.
    all_history = store.load(symbol, source, model=None)
    old = all_history.loc[all_history["model"] != store.MODEL]
    if not old.empty:
        with st.expander("📚 Historia starszych wersji algorytmu (osobno)"):
            st.caption("Wyników V3 nie łączymy ze skutecznością V4. "
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
