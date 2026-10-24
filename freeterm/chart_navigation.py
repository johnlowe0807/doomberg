"""Selection requests for loaded chart observations; no data fetches or UI work."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from .data import candle_interval


@dataclass(frozen=True)
class GotoTarget:
    offset: int | None = None
    timestamp: pd.Timestamp | None = None


def parse_goto(args: list[str]) -> GotoTarget:
    text = ' '.join(args).strip()
    relative = re.fullmatch(r'(LEFT|RIGHT|L|R)\s+(\d{1,9})', text, re.I)
    if relative:
        direction, count = relative.groups()
        return GotoTarget(offset=int(count) * (-1 if direction.upper() in ('LEFT', 'L') else 1))
    if re.fullmatch(r'[+-]\d{1,9}', text):
        return GotoTarget(offset=int(text))
    pattern = r'(\d{8}|\d{4}-\d{2}-\d{2})(?:[Tt ](\d{2}:?\d{2}(?::?\d{2})?)(Z|[+-]\d{2}:?\d{2})?)?'
    match = re.fullmatch(pattern, text, re.I)
    if not match:
        raise ValueError('Use GOTO YYYYMMDD [HHMM], GOTO YYYY-MM-DDTHH:MM, GOTO LEFT/RIGHT n or GOTO -n/+n')
    day, clock, zone = match.groups()
    try:
        day = datetime.strptime(day, '%Y%m%d' if '-' not in day else '%Y-%m-%d').strftime('%Y-%m-%d')
        if clock:
            digits = clock.replace(':', '')
            clock = ':'.join(digits[i:i+2] for i in range(0, len(digits), 2))
        stamp = pd.Timestamp(day + ('T' + clock if clock else '') + (zone.upper() if zone else ''))
        # Validate NY wall-clock dates even when the selected source has a naive date index.
        if stamp.tzinfo is None:
            stamp.tz_localize('America/New_York', ambiguous='raise', nonexistent='raise')
    except Exception as error:
        raise ValueError('Invalid GOTO datetime; use an existing, unambiguous New York time or an explicit UTC offset') from error
    return GotoTarget(timestamp=stamp)


def locate(index: pd.DatetimeIndex, target: pd.Timestamp, interval: str, price: bool) -> int:
    """Containing native candle; gaps fall back to the previous observed datapoint.

    Naive input is New York wall time. Explicit offsets are converted before
    lookup. Source series use their own observations, without invented frequency.
    """
    if not len(index):
        raise ValueError('GOTO needs loaded datapoints')
    stamp = target
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize('America/New_York', ambiguous='raise', nonexistent='raise')
    stamp = stamp.tz_convert(index.tz or 'America/New_York')
    if index.tz is None:
        stamp = stamp.tz_localize(None)
    if price:
        duration = candle_interval(interval)
        if interval == '1wk':
            # Native weekly candles are right-labelled Friday, including that date.
            position = int(index.searchsorted(stamp.normalize(), side='left'))
            start = index[0].normalize() - pd.Timedelta(days=6)
            end = index[-1].normalize() + pd.DateOffset(days=1)
        else:
            position = int(index.searchsorted(stamp, side='right')) - 1
            start = index[0]
            end = index[-1] + (pd.DateOffset(months=duration.months) if duration.months else
                               pd.Timedelta(minutes=duration.minutes))
    else:
        position = int(index.searchsorted(stamp, side='right')) - 1
        start, end = index[0], index[-1].normalize() + pd.DateOffset(days=1)
    if stamp < start or stamp >= end or not 0 <= position < len(index):
        raise ValueError(f'GOTO datetime is outside the loaded graph ({index[0]:%Y-%m-%d %H:%M} to {index[-1]:%Y-%m-%d %H:%M})')
    return position
