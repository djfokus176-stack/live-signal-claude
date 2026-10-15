"""Official US scheduled high-impact events. No user accounts or API tokens.

BLS official ICS is refreshed periodically, plus independently verified
FOMC decision times and BEA release schedule for late 2026. Only timestamps
known from sources are used; failure of the primary official feed returns
calendar_ok=False, which MUST cause WAIT rather than pretending no news.
"""
from __future__ import annotations
import re
from html.parser import HTMLParser
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



# Documented snapshot from the BLS official 2026 release schedule, read on
# 2026-10-15: https://www.bls.gov/schedule/2026/
# This is a LAST RESORT for research when both official remote endpoints
# return HTTP 403 / cannot be downloaded. Never use it beyond 2026.
BLS_VERIFIED_2026 = (
    ("2026-10-02", "08:30", "Employment Situation"),
    ("2026-10-14", "08:30", "Consumer Price Index"),
    ("2026-10-15", "08:30", "Producer Price Index"),
    ("2026-11-06", "08:30", "Employment Situation"),
    ("2026-11-10", "08:30", "Consumer Price Index"),
    ("2026-11-13", "08:30", "Producer Price Index"),
    ("2026-12-04", "08:30", "Employment Situation"),
    ("2026-12-10", "08:30", "Consumer Price Index"),
    ("2026-12-15", "08:30", "Producer Price Index"),
)


class _OfficialHTMLRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.current = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.current = []
        elif tag in ("td", "th") and self.current is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.current is not None:
            self.current.append(" ".join("".join(self.cell).split()))
            self.cell = None
        if tag == "tr" and self.current is not None:
            self.rows.append(self.current)
            self.current = None


def parse_bls_html(html):
    """Parse official annual BLS schedule, not arbitrary finance news sites."""
    parser = _OfficialHTMLRows()
    parser.feed(html)
    events = []
    for cols in parser.rows:
        if len(cols) < 3 or not any(x in cols[2].lower() for x in KEYWORDS):
            continue
        try:
            date_field = re.search(
                r"(January|February|March|April|May|June|July|August|"
                r"September|October|November|December)\s+\d{1,2},\s+20\d{2}",
                cols[0])
            time_field = re.search(r"\b(\d{1,2}):(\d{2})\s*(AM|PM)\b",
                                   cols[1], re.I)
            if not date_field or not time_field:
                continue
            year_day = pd.to_datetime(date_field.group(0), format="%B %d, %Y")
            hour = int(time_field.group(1)) % 12
            if time_field.group(3).upper() == "PM":
                hour += 12
            when = _when_local(year_day.strftime("%Y-%m-%d"),
                               hour, int(time_field.group(2)))
            events.append((when, cols[2]))
        except (ValueError, TypeError):
            continue
    return events


def verified_2026_snapshot():
    events = []
    for date, hour_minute, name in BLS_VERIFIED_2026:
        h, m = map(int, hour_minute.split(":"))
        events.append((_when_local(date, h, m), name))
    return events


def load_official_calendar():
    """Prefer official BLS ICS, then official BLS HTML, then dated 2026 snapshot.

    Fallback is not a claim of real-time confirmation; it exists solely to
    prevent *all* simulated PAPER entries being blocked by a 403 on BLS ICS.
    Dates in a different year or a malformed response fail closed to WAIT.
    """
    fixed = official_fixed_events()
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ClaudePaperCalendar/1.0)"}
    try:
        response = requests.get(BLS_ICS, timeout=8, headers=headers)
        response.raise_for_status()
        if "BEGIN:VCALENDAR" not in response.text:
            raise ValueError("Bad BLS ICS response")
        events = parse_bls_ics(response.text)
        if len(events) < 2:
            raise ValueError("Empty BLS ICS")
        return sorted(set(fixed + events), key=lambda x: x[0]), True
    except (requests.RequestException, ValueError):
        pass

    try:
        response = requests.get("https://www.bls.gov/schedule/2026/",
                                timeout=8, headers=headers)
        response.raise_for_status()
        events = parse_bls_html(response.text)
        if len(events) < 6:
            raise ValueError("Not a valid BLS yearly schedule")
        return sorted(set(fixed + events), key=lambda x: x[0]), True
    except (requests.RequestException, ValueError):
        # Last resort: carefully copied from BLS 2026 official schedule
        # and limited to the 2026 year. At year change this is invalid.
        now = pd.Timestamp.now(tz="UTC")
        if now.year == 2026 and now.month in (10, 11, 12):
            events = verified_2026_snapshot()
            return sorted(set(fixed + events), key=lambda x: x[0]), True
        return sorted(fixed, key=lambda x: x[0]), False


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
