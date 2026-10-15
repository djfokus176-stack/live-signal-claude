"""Official US scheduled high-impact events. No user accounts or API tokens.

BLS official ICS is refreshed periodically, plus independently verified
FOMC decision times and BEA release schedule for late 2026. Only timestamps
known from sources are used; failure of the primary official feed returns
calendar_ok=False, which MUST cause WAIT rather than pretending no news.
"""
from __future__ import annotations
import re
from zoneinfo import ZoneInfo
import pandas as pd
import requests

BLS_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"
NY = ZoneInfo("America/New_York")
KEYWORDS = (
    "consumer price index", "employment situation",
    "producer price index", "cpi", "ppi", "nonfarm", "non-farm",
)
# Federal Reserve FOMC: policy decisions usually at 14:00 ET and a
# press conference at 14:30 ET. Decisions for future years must come from
# the official calendar before enabling a new year's signals.
FOMC_2026 = ("2026-10-28", "2026-12-09")
BEA_2026 = (
    ("2026-10-29", "PKB (GDP), PCE – BEA"),
    ("2026-11-25", "PKB (GDP), PCE – BEA"),
    ("2026-12-23", "PKB (GDP), PCE – BEA"),
)


def _when_local(date_string, hour, minute):
    return pd.Timestamp(f"{date_string} {hour:02}:{minute:02}",
                        tz=NY).tz_convert("UTC")


def official_fixed_events():
    events = []
    for day in FOMC_2026:
        events.append((_when_local(day, 14, 0), "Decyzja Fed/FOMC"))
        events.append((_when_local(day, 14, 30), "Konferencja Fed/FOMC"))
    for day, name in BEA_2026:
        events.append((_when_local(day, 8, 30), name))
    return events


def parse_bls_ics(source):
    # Unfold RFC5545 continuation lines, keep events only.
    source = re.sub(r"\r?\n[ \t]", "", source)
    events = []
    for raw in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", source, re.S):
        summary = re.search(r"(?m)^SUMMARY:(.*)$", raw)
        if not summary:
            continue
        title = summary.group(1).strip()
        if not any(x in title.lower() for x in KEYWORDS):
            continue
        line = re.search(r"(?m)^DTSTART([^:\n]*):([^\n]+)", raw)
        if not line:
            continue
        options, stamp = line.group(1), line.group(2).strip()
        if "VALUE=DATE" in options or "T" not in stamp:
            continue
        try:
            if stamp.endswith("Z"):
                when = pd.to_datetime(stamp, utc=True)
            else:
                naive = pd.to_datetime(stamp, format="%Y%m%dT%H%M%S" if len(stamp)==15 else "%Y%m%dT%H%M")
                when = pd.Timestamp(naive).tz_localize(NY).tz_convert("UTC")
            events.append((when, title))
        except (ValueError, TypeError):
            continue
    return events


def load_official_calendar():
    events = official_fixed_events()
    try:
        response = requests.get(BLS_ICS, timeout=10, headers={
            "User-Agent": "CLAUDE-LIVE-SIGNAL-PAPER/1.0 (calendar research)"
        })
        response.raise_for_status()
        if "BEGIN:VCALENDAR" not in response.text:
            raise ValueError("No iCalendar data")
        parsed = parse_bls_ics(response.text)
        if len(parsed) < 2:
            raise ValueError("No BLS high-impact releases")
        events.extend(parsed)
        return sorted(set(events), key=lambda x: x[0]), True
    except (requests.RequestException, ValueError):
        # A partial fixed list is shown as context ONLY. It is not safe to
        # enable PAPER entries when CPI/NFP dates may be missing.
        return sorted(events, key=lambda x: x[0]), False


def macro_guard(now, events, calendar_ok):
    now = pd.Timestamp(now).tz_convert("UTC")
    for when, name in events:
        delta = abs((now - pd.Timestamp(when).tz_convert("UTC")).total_seconds())
        if delta <= 15 * 60:
            local = pd.Timestamp(when).tz_convert("Europe/Warsaw")
            return True, (f"STOP MAKRO: {name}, "
                          f"{local.strftime('%d.%m %H:%M')} PL; "
                          "zakaz sygnałów ±15 minut")
    if not calendar_ok:
        return True, "WAIT: kalendarz BLS niedostępny; nie potwierdzono braku publikacji USA"
    return False, "Brak potwierdzonej publikacji wysokiego wpływu w oknie ±15 minut"
