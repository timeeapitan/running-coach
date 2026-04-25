"""
Date/time utilities.
"""

from datetime import datetime, timedelta, date
from typing import List, Tuple


def week_boundaries(reference: datetime, weeks_back: int) -> List[Tuple[datetime, datetime]]:
    """
    Return a list of (start, end) tuples for each of the last `weeks_back` weeks,
    ordered from oldest to newest.
    """
    boundaries = []
    for i in range(weeks_back, 0, -1):
        start = reference - timedelta(weeks=i)
        end = reference - timedelta(weeks=i - 1)
        boundaries.append((start, end))
    return boundaries


def days_since(dt: datetime) -> float:
    """Return fractional days elapsed since `dt`."""
    return (datetime.now() - dt).total_seconds() / 86400


def iso_date_key(dt: datetime) -> str:
    """Return ISO date string (YYYY-MM-DD) for a datetime."""
    return dt.date().isoformat()
