"""Friendly read-only overview of historical virtual 15m trades.

Costs: Capital.com fills were simulated with bid/ask, so the spread is
already in prices. Additional commission/slippage is deducted once.
Six horizons of a single signal are NOT six trades.
"""
import math
import pandas as pd
import streamlit as st
import store

def _pl(value):
    if value is None or pd.isna(value):
        return "—"
    return pd.Timestamp(value).tz_convert("Europe/Warsaw").strftime("%d.%m.%Y %H:%M")

def trade_table(df):
    headers = ["Data/godzina PL","Sygnał","Cena wejścia","Cena wyjścia",
               "Wynik netto (pkt)","Status","Dlaczego taki typ?","Model"]
    if df is None or df.empty:
        return pd.DataFrame(columns=headers), {"wins":0, "losses":0, "trades":0,
                                                "points":None, "winrate":None}
    df=df[df["horizon"]==15].sort_values("created_utc",ascending=False)
    records=[]
    for _,r in df.iterrows():
        status=str(r["status"])
        entry,exit_=r.get("entry_price"),r.get("exit_price")
        net_points=None
        if pd.notna(entry) and pd.notna(exit_) and status in ("TRAFIONY","BLEDNY","REMIS"):
            # Spread already paid through executable ASK/BID entry/exit.
            # Extra round-trip cost is converted from % to index points.
            signed=(float(exit_)-float(entry))*(1 if r["side"]=="LONG" else -1)
            net_points=round(signed-float(entry)*float(r["cost_pct"])/100,3)
        why=str(r.get("rationale") or "").strip()
        if not why or why.lower()=="nan":
            why="Archiwalny test: starsza wersja nie zapisywała uzasadnienia."
        records.append({
            "Data/godzina PL":_pl(r["created_utc"]),
            "Sygnał":str(r["side"]),
            "Cena wejścia":round(float(entry),2) if pd.notna(entry) else None,
            "Cena wyjścia":round(float(exit_),2) if pd.notna(exit_) else None,
            "Wynik netto (pkt)":net_points,
            "Status":{"TRAFIONY":"TRAFIONY","BLEDNY":"BŁĘDNY",
                      "REMIS":"REMIS","BRAK_DANYCH":"BRAK DANYCH",
                      "OCZEKUJE":"OTWARTA / OCZEKUJE"}.get(status,status),
            "Dlaczego taki typ?":why,
            "Model":r["model"],
        })
    table=pd.DataFrame(records,columns=headers)
    completed=table[table["Status"].isin(("TRAFIONY","BŁĘDNY","REMIS")) &
                     table["Wynik netto (pkt)"].notna()]
    wins=int((completed["Status"]=="TRAFIONY").sum())
    losses=int((completed["Status"]=="BŁĘDNY").sum())
    count=len(completed)
    return table,{"wins":wins,"losses":losses,"trades":count,
                  "points":round(float(completed["Wynik netto (pkt)"].sum()),3)
                           if count else None,
                  "winrate":round(100*wins/count,1) if count else None}

def render(symbol):
    st.subheader("Wyniki bota — wirtualne pozycje 15 minut")
    try:
        source="Capital.com DEMO" if symbol=="CAPITAL_US500" else None
        rows=store.load(symbol,source,model=None)
        # Other instruments: avoid mixing quoted market sources in a single
        # ledger. Prefer dedicated Capital.com US500 view by default.
        if source is None and not rows.empty:
            source=rows.iloc[0]["source"]
            rows=rows[rows["source"]==source]
        table,total=trade_table(rows)
        a,b,c,d=st.columns(4)
        a.metric("Winrate (rozliczone)",f'{total["winrate"]}%' if total["winrate"] is not None else "—")
        b.metric("Trafione",total["wins"])
        c.metric("Błędne",total["losses"])
        d.metric("Suma netto (pkt)",f'{total["points"]:+.2f}' if total["points"] is not None else "—")
        st.caption("Tylko jeden horyzont 15 minut na prognozę. "
                   "Cena po spreadzie ASK/BID Capital.com i po dodatkowym "
                   "symulowanym koszcie. Stare modele są pokazane, "
                   "ale nie dowodzą skuteczności nowej strategii.")
        if table.empty:
            st.info("Brak zachowanych prób PAPER w lokalnej bazie. "
                    "Nie podajemy wymyślonego bilansu.")
        else:
            st.dataframe(table,use_container_width=True,hide_index=True,
                         height=min(600,100+len(table)*35))
            st.download_button("Pobierz tabelę CSV",
                    data=table.to_csv(index=False).encode("utf-8-sig"),
                    file_name="claude-us500-trades.csv",mime="text/csv")
    except Exception:
        st.error("Nie udało się odczytać dziennika — nie pokazujemy niezweryfikowanych wyników.")
