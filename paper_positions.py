"""Present a single PAPER position per forecast for a chosen horizon."""
import math
import pandas as pd

COLUMNS = ["Sygnał PL","Instrument","Kierunek","Tryb","Wejście PL",
           "Cena wejścia","Wyjście PL","Cena wyjścia","Punkty","Netto %",
           "Status"]

def local_time(value):
    if value is None or pd.isna(value):
        return "—"
    return pd.Timestamp(value).tz_convert("Europe/Warsaw").strftime("%d.%m %H:%M:%S")

def positions(frame, horizon=15):
    if frame is None or frame.empty:
        return pd.DataFrame(columns=COLUMNS), {"all":0,"done":0,"plus":0,"minus":0,"avg":None}
    subset=frame.loc[frame["horizon"]==horizon].sort_values("created_utc",ascending=False)
    result=[]
    for _,r in subset.iterrows():
        entry,exit_=r.get("entry_price"),r.get("exit_price")
        points=None
        if pd.notna(entry) and pd.notna(exit_):
            points=(float(exit_)-float(entry))*(1 if r["side"]=="LONG" else -1)
        net=r.get("net_pct")
        net=(float(net) if pd.notna(net) and math.isfinite(float(net)) else None)
        status=str(r["status"])
        label={"TRAFIONY":"PLUS","BLEDNY":"MINUS","REMIS":"ZERO",
               "BRAK_DANYCH":"BRAK DANYCH"}.get(status,
                "OTWARTA PAPER" if pd.notna(entry) else "OCZEKUJE NA WEJŚCIE")
        result.append({
            "Sygnał PL":local_time(r["created_utc"]),
            "Instrument":str(r["symbol"]), "Kierunek":r["side"],
            "Tryb":r.get("origin","MANUAL"),
            "Wejście PL":local_time(r.get("entry_utc")),
            "Cena wejścia":float(entry) if pd.notna(entry) else None,
            "Wyjście PL":local_time(r.get("exit_utc")),
            "Cena wyjścia":float(exit_) if pd.notna(exit_) else None,
            "Punkty":round(points,3) if points is not None else None,
            "Netto %":round(net,4) if net is not None else None,
            "Status":label})
    finished=subset.loc[subset["status"].isin(["TRAFIONY","BLEDNY","REMIS"])]
    return pd.DataFrame(result,columns=COLUMNS),{
        "all":len(subset),"done":len(finished),
        "plus":int((finished["status"]=="TRAFIONY").sum()),
        "minus":int((finished["status"]=="BLEDNY").sum()),
        "avg":float(finished["net_pct"].mean()) if len(finished) else None}

def counts_today(frame,now):
    now=pd.Timestamp(now)
    if now.tzinfo is None:raise ValueError("Clock requires timezone")
    if frame is None or frame.empty:
        return {"auto":0,"manual":0,"wait":0,"saved":0,"all":0}
    created=pd.to_datetime(frame["created_utc"],utc=True,errors="coerce")
    part=frame.loc[created.dt.tz_convert("Europe/Warsaw").dt.date==now.tz_convert("Europe/Warsaw").date()]
    return {"auto":int((part["origin"]=="AUTO").sum()),
            "manual":int((part["origin"]=="MANUAL").sum()),
            "wait":int((part["direction"]=="WAIT").sum()),
            "saved":int((part["result"]=="ZAPISANO").sum()),"all":len(part)}
