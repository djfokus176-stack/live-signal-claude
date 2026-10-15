"""Live BLS ICS often returns HTTP 403; no permanent macro WAIT on PAPER."""
import pandas as pd
import requests
import macro_calendar as macro


def test_bls_html_yearly_rows_capture_high_impact_releases_and_timezone():
    html = """<html><table><tbody>
    <tr><th>Date</th><th>Time</th><th>Release</th></tr>
    <tr><td>Wednesday, October 14, 2026</td><td>08:30 AM</td>
        <td><a>Consumer Price Index for September 2026</a></td></tr>
    <tr><td>Thursday, October 15, 2026</td><td>08:30 AM</td>
        <td>Producer Price Index for September 2026</td></tr>
    <tr><td>Friday, November 6, 2026</td><td>08:30 AM</td>
        <td>Employment Situation for October 2026</td></tr>
    <tr><td>Monday, October 12, 2026</td><td></td><td>Columbus Day</td></tr>
    </tbody></table></html>"""
    result = macro.parse_bls_html(html)
    assert len(result) == 3
    assert result[0][0] == pd.Timestamp("2026-10-14T12:30:00Z")
    assert result[1][0] == pd.Timestamp("2026-10-15T12:30:00Z")
    assert result[2][0] == pd.Timestamp("2026-11-06T13:30:00Z")


def test_bls_403_html_403_uses_verified_dated_2026_backup(monkeypatch):
    def forbidden(*args, **kwargs):
        raise requests.HTTPError("official BLS pages blocked")
    monkeypatch.setattr(macro.requests, "get", forbidden)
    clock = pd.Timestamp("2026-10-15T14:30:00Z")
    events, ok = macro.load_official_calendar(now=clock)
    assert ok
    assert len(events) >= 11
    within, label = macro.macro_guard(
        pd.Timestamp("2026-10-15T12:30:00Z"), events, ok)
    assert within and "Producer Price Index" in label
    no_news, _ = macro.macro_guard(clock, events, ok)
    assert not no_news


def test_calendar_fails_closed_outside_verified_year(monkeypatch):
    def forbidden(*args, **kwargs):
        raise requests.HTTPError("blocked")
    monkeypatch.setattr(macro.requests, "get", forbidden)
    _, ok = macro.load_official_calendar(
        now=pd.Timestamp("2027-01-20T15:00:00Z"))
    assert not ok


def test_fetches_official_html_when_ics_is_blocked(monkeypatch):
    html = "<table>" + "".join(
        f"<tr><td>Thursday, October {i}, 2026</td><td>08:30 AM</td>"
        f"<td>Producer Price Index</td></tr>" for i in range(1, 9)
    ) + "</table>"
    class Fake:
        def __init__(self, body):
            self.text = body
        def raise_for_status(self):
            pass
    seen = []
    def fetch(url, **kwargs):
        seen.append(url)
        if url.endswith(".ics"):
            raise requests.HTTPError("no calendar")
        return Fake(html)
    monkeypatch.setattr(macro.requests, "get", fetch)
    events, ok = macro.load_official_calendar(
        now=pd.Timestamp("2026-10-15T10:00:00Z"))
    assert ok
    assert len(events) >= 8
    assert any("/schedule/2026/" in item for item in seen)


def test_snapshot_does_not_become_realtime_factual_report():
    dates = macro.verified_2026_snapshot()
    assert len(dates) == 9
    assert all(when.year == 2026 for when, _ in dates)
