"""Real market-data adapters. No live brokerage execution, only readings."""
import time
import math
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
            parts = [bar[name] for name in
                     ("openPrice", "highPrice", "lowPrice", "closePrice")]
            for part in parts:
                bid, ask = float(part["bid"]), float(part["ask"])
                if (not math.isfinite(bid) or not math.isfinite(ask)
                        or bid <= 0 or ask <= 0 or bid > ask):
                    raise ValueError("Invalid quote")
            def mid(part):
                return (float(part["bid"]) + float(part["ask"])) / 2
            mid_open, mid_high, mid_low, mid_close = [mid(p) for p in parts]
            if (mid_high < mid_low or
                    not mid_low <= mid_open <= mid_high or
                    not mid_low <= mid_close <= mid_high):
                raise ValueError("Invalid candle OHLC")
            rows.append({
                "Date": pd.to_datetime(bar["snapshotTimeUTC"], utc=True),
                "Open": mid_open,
                "High": mid_high,
                "Low": mid_low,
                "Close": mid_close,
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


class CapitalAPIError(Exception):
    """Public, sanitized API error. Never include credential or response bodies."""

    def __init__(self, stage, status=None, code="", retry_seconds=0):
        self.stage = stage
        self.status = status
        self.code = code
        self.retry_seconds = retry_seconds
        msg = f"Capital DEMO — {stage}"
        if status is not None:
            msg += f" HTTP {status}"
        if code:
            msg += f" ({code})"
        if retry_seconds:
            msg += f"; odczekaj ok. {retry_seconds} s"
        super().__init__(msg)


class CapitalDemo:
    """DEMO-only, read-only market data. Never sends trading orders.

    Session belongs to one Streamlit browser session and persists across reruns.
    """

    BASE = "https://demo-api-capital.backend-capital.com/api/v1"

    def __init__(self, api_key, identifier, password, epic):
        self.api_key = api_key
        self.identifier = identifier
        self.password = password
        self.epic = epic.strip().upper()
        if not self.epic or not self.epic.replace('_', '').isalnum():
            raise ValueError("EPIC powinien składać się z liter/cyfr; dla S&P 500 zwykle US500")
        self.tokens = None
        self.auth_ts = 0.0
        self.blocked_until = 0.0

    def _check_cooldown(self, stage):
        remaining = max(0, round(self.blocked_until - time.monotonic()))
        if remaining > 0:
            raise CapitalAPIError(stage, status=429, retry_seconds=remaining)

    def _check_response(self, response, stage):
        if response.ok:
            return
        # Do not show the response body: it can contain account details or secrets.
        code = ""
        try:
            raw_code = str(response.json().get("errorCode", ""))
            import re
            if re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", raw_code):
                code = raw_code
        except (TypeError, ValueError, AttributeError):
            pass
        delay = 0
        if response.status_code == 429:
            try:
                delay = min(600, max(60, int(response.headers.get("Retry-After", 60))))
            except (TypeError, ValueError):
                delay = 60
            self.blocked_until = time.monotonic() + delay
        raise CapitalAPIError(stage, response.status_code, code, delay)

    def _auth(self):
        self._check_cooldown("logowanie")
        if self.tokens and time.monotonic() - self.auth_ts < 8 * 60:
            return self.tokens
        try:
            response = requests.post(
                f"{self.BASE}/session",
                headers={"X-CAP-API-KEY": self.api_key},
                json={"identifier": self.identifier, "password": self.password,
                      "encryptedPassword": False}, timeout=15)
        except requests.RequestException as exc:
            raise CapitalAPIError("sieć przy logowaniu") from exc
        self._check_response(response, "logowanie")
        cst = response.headers.get("CST")
        security = response.headers.get("X-SECURITY-TOKEN")
        if not cst or not security:
            raise CapitalAPIError("logowanie: brak tokenów CST / X-SECURITY-TOKEN")
        self.tokens = {"CST": cst, "X-SECURITY-TOKEN": security}
        self.auth_ts = time.monotonic()
        return self.tokens

    def _get(self, path, stage, params=None):
        self._check_cooldown(stage)
        headers = self._auth()
        try:
            response = requests.get(f"{self.BASE}{path}", headers=headers,
                                    params=params, timeout=15)
        except requests.RequestException as exc:
            raise CapitalAPIError(f"sieć: {stage}") from exc
        if response.status_code == 401:
            # Expired session: renew only once. Never loop indefinitely.
            self.tokens = None
            headers = self._auth()
            try:
                response = requests.get(f"{self.BASE}{path}", headers=headers,
                                        params=params, timeout=15)
            except requests.RequestException as exc:
                raise CapitalAPIError(f"sieć: {stage}") from exc
        self._check_response(response, stage)
        self.auth_ts = time.monotonic()  # REST tokens are renewed on successful activity.
        try:
            return response.json()
        except (TypeError, ValueError) as exc:
            raise CapitalAPIError(f"{stage}: niepoprawny JSON") from exc

    def bars(self):
        payload = self._get(f"/prices/{self.epic}", "świece 1 min",
                            params={"resolution": "MINUTE", "max": 450})
        if not isinstance(payload, dict):
            raise CapitalAPIError("świece 1 min: niepoprawna odpowiedź")
        return capital_to_bars(payload)

    def market_details(self):
        """On-demand instrument validation; no orders or account changes."""
        payload = self._get(f"/markets/{self.epic}", "sprawdzanie EPIC")
        if not isinstance(payload, dict):
            raise CapitalAPIError("sprawdzanie EPIC: niepoprawna odpowiedź")
        info, snapshot = payload.get("instrument") or {}, payload.get("snapshot") or {}
        return {
            "epic": info.get("epic") or self.epic,
            "name": info.get("name") or info.get("instrumentName") or "Nieznana nazwa",
            "market_status": snapshot.get("marketStatus", "nieznany"),
            "bid": snapshot.get("bid"), "ask": snapshot.get("offer"),
            "update_time": snapshot.get("updateTimeUTC"),
        }
