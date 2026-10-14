"""Read-only Streamlit PAPER positions and scan reporting."""
import pandas as pd
import streamlit as st

import store
from paper_positions import positions, counts_today


def render(symbol, source):
    st.divider()
    st.subheader("📈 Wirtualne wejścia LONG / SHORT — wynik PLUS lub MINUS")
    st.caption("Automatyczne testy PAPER na 15 minut. Cena wejścia pochodzi "
               "z pierwszej potwierdzonej świecy M1, nie z kliknięcia. "
               "Dla CFD liczymy spread bid/ask i dodatkowy koszt. "
               "To nie są zlecenia brokera ani prawdziwe pieniądze.")
    try:
        rows = store.load(symbol, source, model=store.MODEL)
        table, totals = positions(rows, horizon=15)
        a,b,c,d = st.columns(4)
        a.metric("Wirtualne pozycje", totals["all"])
        b.metric("Zamknięte", totals["done"])
        c.metric("Na PLUS", totals["plus"])
        d.metric("Na MINUS", totals["minus"])
        if totals["avg"] is not None:
            st.caption(f'Średni wynik netto pozycji 15m: '
                       f'{totals["avg"]:+.4f}% (nie ROI portfela).')
        if table.empty:
            st.info("Brak zapisanych wejść PAPER w wybranym źródle.")
        else:
            st.dataframe(table.head(100), use_container_width=True,
                         hide_index=True)
            st.download_button(
                "⬇️ Pobierz wirtualne pozycje",
                data=table.to_csv(index=False).encode("utf-8-sig"),
                mime="text/csv", file_name="claude_paper_pozycje_15m.csv")
    except Exception:
        st.warning("Nie udało się odczytać wirtualnych pozycji.")

    st.divider()
    st.subheader("🧠 Historia skanów bota — również WAIT")
    st.caption("Co około 15 minut przy otwartej stronie bot sprawdza "
               "zamknięte świece i bezpłatne nagłówki RSS. "
               "Sentyment wiadomości to tylko kontekst — nie jest "
               "zweryfikowanym predyktorem. OpenAI API pozostaje wyłączone.")
    try:
        scans = store.load_scans(symbol)
        counts = counts_today(scans, pd.Timestamp.now(tz="Europe/Warsaw"))
        a,b,c,d = st.columns(4)
        a.metric("Skanów dzisiaj", counts["all"])
        b.metric("AUTO", counts["auto"])
        c.metric("WAIT", counts["wait"])
        d.metric("Zapisane LONG/SHORT", counts["saved"])
        if scans.empty:
            st.info("Brak zapisanych skanów. Nowe decyzje WAIT będą tu widoczne.")
        else:
            show = scans.head(40).copy()
            show["Godzina PL"] = pd.to_datetime(
                show["created_utc"], utc=True).dt.tz_convert(
                    "Europe/Warsaw").dt.strftime("%d.%m %H:%M")
            st.dataframe(show[["Godzina PL", "origin", "direction",
                               "result", "note", "news_score",
                               "news_count", "news_sample"]],
                         use_container_width=True, hide_index=True)
    except Exception:
        st.warning("Nie udało się odczytać historii skanów.")
