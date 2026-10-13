"""No external network. Capital.com MOCK only (DEMO endpoint)."""
import requests
import pytest
import data_sources as ds


class Reply:
    def __init__(self, code=200, data=None, headers=None):
        self.status_code = code
        self._payload = data if data is not None else {}
        self.headers = headers or {}
        self.ok = 200 <= code < 400

    def json(self):
        return self._payload


def client():
    return ds.CapitalDemo("secret_key", "login@example.com", "api_password", "US500")


def success_login(monkeypatch):
    def post(url, headers, json, timeout):
        assert "demo-api" in url and url.endswith("/session")
        assert json["password"] == "api_password"
        return Reply(headers={"CST": "token1", "X-SECURITY-TOKEN": "token2"})
    monkeypatch.setattr(ds.requests, "post", post)


def test_demo_market_check(monkeypatch):
    success_login(monkeypatch)
    def get(url, headers, params, timeout):
        assert url.endswith("/markets/US500")
        assert headers["CST"] == "token1"
        return Reply(data={"instrument": {"name": "S&P 500", "epic": "US500"},
                           "snapshot": {"marketStatus": "TRADEABLE", "bid": 5800,
                                        "offer": 5801, "updateTimeUTC": "2026-10-12T10:00:00"}})
    monkeypatch.setattr(ds.requests, "get", get)
    details = client().market_details()
    assert details["epic"] == "US500"
    assert details["ask"] == 5801


def test_diagnostic_error_separates_auth_401(monkeypatch):
    monkeypatch.setattr(ds.requests, "post", lambda *args, **kw: Reply(401, data={"errorCode": "invalid.credentials"}))
    with pytest.raises(ds.CapitalAPIError) as ex:
        client().bars()
    assert "logowanie HTTP 401" in str(ex.value)
    assert "secret_key" not in str(ex.value)
    assert "api_password" not in str(ex.value)


def test_epic_404_is_not_authentication(monkeypatch):
    success_login(monkeypatch)
    monkeypatch.setattr(ds.requests, "get", lambda *args, **kw: Reply(404, data={"errorCode": "error.market.not-found"}))
    with pytest.raises(ds.CapitalAPIError) as ex:
        client().bars()
    assert "świece 1 min HTTP 404" in str(ex.value)


def test_limits_have_cooldown(monkeypatch):
    success_login(monkeypatch)
    calls = []
    def get(*args, **kw):
        calls.append(kw)
        return Reply(429, headers={"Retry-After": "120"})
    monkeypatch.setattr(ds.requests, "get", get)
    c = client()
    with pytest.raises(ds.CapitalAPIError, match="HTTP 429"):
        c.bars()
    with pytest.raises(ds.CapitalAPIError, match="HTTP 429"):
        c.bars()
    assert len(calls) == 1


def test_401_on_prices_renews_session_only_once(monkeypatch):
    n = [0]
    def post(*args, **kwargs):
        n[0] += 1
        return Reply(headers={"CST": f"T{n[0]}", "X-SECURITY-TOKEN": "S"})
    monkeypatch.setattr(ds.requests, "post", post)
    def get(url, headers, params, timeout):
        if headers["CST"] == "T1":
            return Reply(401)
        return Reply(data={"prices": []})
    monkeypatch.setattr(ds.requests, "get", get)
    c = client()
    assert c.bars().empty
    assert n[0] == 2


def test_never_send_trades(monkeypatch):
    success_login(monkeypatch)
    def get(url, headers, params, timeout):
        assert "/prices/" in url
        assert params == {"resolution": "MINUTE", "max": 450}
        return Reply(data={"prices": []})
    monkeypatch.setattr(ds.requests, "get", get)
    assert client().bars().empty


def test_bad_epic_disallowed():
    with pytest.raises(ValueError):
        ds.CapitalDemo("key", "user", "pass", "US500/positions")


def test_network_failure_safe(monkeypatch):
    monkeypatch.setattr(ds.requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(requests.Timeout("secret_key")))
    with pytest.raises(ds.CapitalAPIError) as ex:
        client().bars()
    assert "secret_key" not in str(ex.value)


def test_corrupted_capital_quotes_are_dropped():
    import pandas as pd
    def price(bid, ask):
        return {"bid": bid, "ask": ask}
    def record(bid, ask):
        return {
            "snapshotTimeUTC": "2026-10-12T14:01:00",
            "openPrice": price(bid, ask),
            "highPrice": price(bid+1, ask+1),
            "lowPrice": price(bid-1, ask-1),
            "closePrice": price(bid, ask),
        }
    clock = pd.Timestamp("2026-10-12T14:10:00Z")
    for bad in (record(101, 100), record(-4, -3),
                record(float("inf"), float("inf")),
                record(float("nan"), 100)):
        assert ds.capital_to_bars({"prices": [bad]}, now=clock).empty
    good = record(99, 101)
    df = ds.capital_to_bars({"prices": [good]}, now=clock)
    assert len(df) == 1
    assert df.iloc[0]["Close"] == 100
