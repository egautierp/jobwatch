"""Turn the many ways job systems report a posting date into one ISO date."""
import datetime as dt
import re
from email.utils import parsedate_to_datetime


def parse_posted(value, today):
    """Return (iso_date, approximate) or ("", False) when unknown.

    Handles ISO dates, epoch milliseconds, RSS dates and Workday's
    "Posted 3 Days Ago" style text. "30+ Days Ago" is marked approximate.
    """
    if value in (None, ""):
        return "", False
    t = dt.date.fromisoformat(today)
    if isinstance(value, (int, float)):
        return dt.datetime.utcfromtimestamp(value / 1000).date().isoformat(), False
    v = str(value).strip()
    low = v.lower()
    if "today" in low or "just posted" in low:
        return t.isoformat(), False
    if "yesterday" in low:
        return (t - dt.timedelta(days=1)).isoformat(), False
    m = re.search(r"(\d+)\+?\s*(day|week|month)s?\s+ago", low)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n * {"day": 1, "week": 7, "month": 30}[unit]
        return (t - dt.timedelta(days=days)).isoformat(), "+" in low
    m = re.match(r"(\d{4}-\d{2}-\d{2})", v)
    if m:
        return m.group(1), False
    try:
        return parsedate_to_datetime(v).date().isoformat(), False
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in ("%d/%m/%Y", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return dt.datetime.strptime(v, fmt).date().isoformat(), False
        except ValueError:
            continue
    return "", False
