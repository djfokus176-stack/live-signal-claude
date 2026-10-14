"""Pure timing check for an interactive Streamlit PAPER collector.

This does not schedule background work. Only an active Streamlit session
can call it; SQLite there is not durable.
"""
import pandas as pd


def auto_scan_due(last_scan, now, minutes=15):
    """Return True if this active session should evaluate another PAPER slot."""
    current = pd.Timestamp(now)
    if current.tzinfo is None:
        raise ValueError("Now must be timezone-aware")
    current = current.tz_convert("UTC")
    if last_scan is None:
        return True
    previous = pd.Timestamp(last_scan)
    if previous.tzinfo is None:
        return True
    age = (current - previous.tz_convert("UTC")).total_seconds()
    # Clock correction / inconsistent state: require a new clean session.
    if age < 0:
        return False
    return age >= minutes * 60
