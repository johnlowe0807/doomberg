"""Scheduled NYSE sessions, including 13:00 ET closes (NYSE hours-calendars)."""
from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


@lru_cache(maxsize=32)
def holidays(year: int) -> frozenset[date]:
    def nth(month, weekday, n):
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))

    def observed(month, day):
        d = date(year, month, day)
        return d + timedelta(days=-1 if d.weekday() == 5 else 1 if d.weekday() == 6 else 0)

    # Gregorian Easter; Good Friday is two days earlier.
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    easter = date(year, (h + l - 7 * m + 114) // 31, (h + l - 7 * m + 114) % 31 + 1)
    memorial = date(year, 5, 31)
    memorial -= timedelta(days=memorial.weekday())
    days = {observed(1, 1), nth(2, 0, 3), easter - timedelta(days=2), memorial,
            observed(7, 4), nth(9, 0, 1), nth(11, 3, 4), observed(12, 25)}
    if year >= 1998:
        days.add(nth(1, 0, 3))
    if year >= 2022:
        days.add(observed(6, 19))
    if year == 2025:
        days.add(date(2025, 1, 9))  # Carter national day of mourning
    return frozenset(days)


def close_minutes(day: date) -> int:
    """Regular-session end; weekend/holiday callers must check holidays separately."""
    thanksgiving = date(day.year, 11, 1)
    thanksgiving += timedelta(days=(3 - thanksgiving.weekday()) % 7 + 21)
    early = day == thanksgiving + timedelta(days=1) or (day.month, day.day) in ((7, 3), (12, 24))
    return 780 if early and day.weekday() < 5 and day not in holidays(day.year) else 960


def market_status(now: datetime | None = None) -> str:
    now = (now or datetime.now(ET)).astimezone(ET)
    day = now.date()
    if day.weekday() >= 5:
        return "US CLOSED (wknd)"
    if day in holidays(day.year):
        return "US CLOSED (hol)"
    minute, close = now.hour * 60 + now.minute, close_minutes(day)
    if 570 <= minute < close:
        return "US OPEN"
    if 240 <= minute < 570:
        return "US PRE"
    if close <= minute < (1020 if close == 780 else 1200):
        return "US POST"
    return "US CLOSED"
