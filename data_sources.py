"""Real market-data adapters. No live brokerage execution, only readings."""
import time
import pandas as pd
import requests


def closed_bars(df, now=None):
    """Market data can include an unfinished 1m candle. Exclude it."""
    if df.empty:
        return df
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
    df = df.copy()
    df.index = pd.to_datetime(df.index, utc=True)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df.loc[df.index + pd.Timedelta(minutes=1) <= now]


def yahoo_bars(symbol):
    import yfinance as yf
    for period in ("1d", "5d"):
        df = yf.download(symbol, period=period, interval="1m", progress=False,
                         auto_adjust=False, threads=False)
        if df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return closed_bars(df.dropna(subset=["Open", "Close"]))
    return pd.DataFrame()


def alpaca_bars(symbol, key, secret):
    if symbol not in ("SPY", "QQQ"):
        raise ValueError("Alpaca IEX w tej aplikacji obsługuje SPY i QQQ")
    start = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    response = requests.get(
        f"https://data.alpaca.markets/v2/stocks/{symbol}/bars",
        params={"timeframe": "1Min", "start": start, "limit": 1000,
                "sort": "desc", "feed": "iex", "adjustment": "raw"},
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        timeout=15,
    )
    response.raise_for_status()
    df = pd.DataFrame(response.json().get("bars") or [])
    if df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"t": "Date", "o": "Open", "h": "High",
                            "l": "Low", "c": "Close", "v": "Volume"})
    df.index = pd.to_datetime(df.pop("Date"), utc=True)
    return closed_bars(df)


def capital_to_bars(payload, now=None):
    """Parse Capital.com DEMO bid/ask candles, preserving executable prices."""
    rows = []
    for bar in payload.get("prices", []):
        try:
            def mid(part):
                return (float(part["bid"]) + float(part["ask"])) / 2
            rows.append({
                "Date": pd.to_datetime(bar["snapshotTimeUTC"], utc=True),
                "Open": mid(bar["openPrice"]),
                "High": mid(bar["highPrice"]),
                "Low": mid(bar["lowPrice"]),
                "Close": mid(bar["closePrice"]),
                "OpenBid": float(bar["openPrice"]["bid"]),
                "OpenAsk": float(bar["openPrice"]["ask"]),
                "CloseBid": float(bar["closePrice"]["bid"]),
                "CloseAsk": float(bar["closePrice"]["ask"]),
            })
        except (KeyError, ValueError, TypeError):
            continue
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("Date")
    return closed_bars(df, now=now)


class CapitalDemo:
    """Per-session reusable DEMO-only Capital.com API client.
    A DEMO key (not a live-account key) must be provided.
    """
    BASE = "https://demo-api-capital.backend-capital.com/api/v1"

    def __init__(self, api_key, identifier, password, epic):
        self.api_key = api_key
        self.identifier = identifier
        self.password = password
        self.epic = epic
        self.tokens = None
        self.auth_ts = 0.0

    def _auth(self):
        if self.tokens and time.monotonic() - self.auth_ts < 8 * 60:
            return self.tokens
        response = requests.post(
            f"{self.BASE}/session",
            headers={"X-CAP-API-KEY": self.api_key},
            json={"identifier": self.identifier, "password": self.password,
                  "encryptedPassword": False}, timeout=15)
        response.raise_for_status()
        cst, security = response.headers.get("CST"), response.headers.get("X-SECURITY-TOKEN")
        if not cst or not security:
            raise ValueError("Brak tokenów autoryzacyjnych Capital.com")
        self.tokens = {"CST": cst, "X-SECURITY-TOKEN": security}
        self.auth_ts = time.monotonic()
        return self.tokens

    def bars(self):
        headers = self._auth()
        response = requests.get(
            f"{self.BASE}/prices/{self.epic}",
            headers=headers,
            params={"resolution": "MINUTE", "max": 450}, timeout=15)
        if response.status_code == 401:
            self.tokens = None
            headers = self._auth()
            response = requests.get(f"{self.BASE}/prices/{self.epic}",
                headers=headers, params={"resolution": "MINUTE", "max": 450}, timeout=15)
        response.raise_for_status()
        self.auth_ts = time.monotonic()  # REST session extends on use
        return capital_to_bars(response.json())
