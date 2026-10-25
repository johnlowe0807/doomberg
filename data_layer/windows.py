from __future__ import annotations
import math
import re
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache

# ---------------------------------------------------------------- chart bars
RANGES = ["1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "5Y", "MAX"]
RANGE_DAYS = dict(zip(RANGES, [1, 5, 31, 92, 183, 366, 366, 1830, 99999]))
IVS = ["1m", "5m", "15m", "1h", "1d", "1wk"]
IV_MIN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 1440, "1wk": 10080}
# (interval, yfinance period, days covered, cache ttl seconds): yfinance's own per-interval history limits.
# Four requests per symbol cover every range/interval combination the chart offers.
BASES = [("1m", "7d", 7, 60), ("5m", "60d", 60, 120), ("1h", "730d", 730, 300), ("1d", "max", 99999, 900)]


@dataclass(frozen=True)
class CandleInterval:
    value: str
    minutes: int
    months: int = 0


@lru_cache(maxsize=256)
def candle_interval(value: str) -> CandleInterval:
    """Explicit bar duration: whole minutes, or calendar months (fractional years supported)."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?|\.\d+)\s*([a-zA-Z]+)", value.strip())
    if not match:
        raise ValueError("Use a candle interval such as 2m, 2d, 2 weeks or 0.5 years")
    amount, unit = match.groups()
    amount = Decimal(amount)
    units = {"m": "m", "min": "m", "mins": "m", "minute": "m", "minutes": "m",
             "h": "h", "hr": "h", "hour": "h", "hours": "h",
             "d": "d", "day": "d", "days": "d", "w": "wk", "wk": "wk", "week": "wk", "weeks": "wk",
             "mo": "mo", "month": "mo", "months": "mo", "y": "y", "yr": "y", "year": "y", "years": "y"}
    unit = "mo" if unit == "M" else units.get(unit.lower())
    if unit is None or amount <= 0:
        raise ValueError("Candle intervals must be positive minutes, hours, days, weeks, months or years")
    if unit in ("mo", "y"):
        months = amount * (12 if unit == "y" else 1)
        if months != months.to_integral_value() or not 1 <= months <= 12:
            raise ValueError("Calendar candles must span 1–12 whole months (e.g. 0.5y = 6mo)")
        months = int(months)
        return CandleInterval(f"{months}mo", months * 31 * 1440, months)
    minutes = amount * {"m": 1, "h": 60, "d": 1440, "wk": 10080}[unit]
    if minutes != minutes.to_integral_value() or not 1 <= minutes <= 366 * 1440:
        raise ValueError("Candle intervals must span 1 minute–366 days, in whole minutes")
    return CandleInterval(f"{format(amount.normalize(), 'f')}{unit}", int(minutes))


def is_interval(value: str) -> bool:
    try:
        candle_interval(value)
        return True
    except ValueError:
        return False


def step_interval(value: str, direction: int) -> str:
    """Step from a custom interval to the nearest preset in the requested direction."""
    minutes = candle_interval(value).minutes
    choices = [iv for iv in IVS if (IV_MIN[iv] - minutes) * direction > 0]
    return (choices[0] if direction > 0 else choices[-1]) if choices else value


def range_days(value: str) -> int:
    if value.upper().startswith("TF:"):
        import pandas as pd
        start, end = timeframe_bounds(value)
        # Intraday availability depends on the age of the first bar, not just window length.
        return max(1, math.ceil((max(pd.Timestamp.now(), end) - start).total_seconds() / 86400))
    value = value.upper()
    if value in RANGE_DAYS:
        return RANGE_DAYS[value]
    match = re.fullmatch(r"([1-9]\d{0,3})([DWMY])", value)
    if not match:
        raise ValueError("Use a positive timeframe such as 2Y, 18M, 12W, 90D, YTD or MAX")
    amount, unit = match.groups()
    return int(amount) * {"D": 1, "W": 7, "M": 31, "Y": 366}[unit]


def timeframe_bounds(value):
    import pandas as pd
    try:
        first, last = value[3:].split("/")
        start, end = pd.Timestamp(first), pd.Timestamp(last)
        if start.tzinfo or end.tzinfo or end <= start:
            raise ValueError()
        return start, end
    except Exception as error:
        raise ValueError("Invalid TF window; use TF YYYYMMDD YYYYMMDD or TF YYYYMMDD 3M") from error


def range_span_days(value):
    if value.upper().startswith("TF:"):
        start, end = timeframe_bounds(value)
        return max(1 / 1440, (end - start).total_seconds() / 86400)
    return range_days(value)


def parse_timeframe(args):
    """Date-only ends include that date; timestamp ends are exclusive. Times use New York time."""
    import pandas as pd
    from datetime import datetime
    tokens = [arg for arg in args if arg.upper() not in ("TO", "+")]

    def date_at(index):
        if index >= len(tokens):
            raise ValueError("TF needs a start date and an end date or length")
        token = tokens[index]
        if not re.fullmatch(r"(?:\d{8}|\d{4}-\d{2}-\d{2})(?:[Tt]\d{2}:?\d{2})?", token):
            raise ValueError("Use YYYYMMDD [HHMM] or YYYY-MM-DD[THH:MM] for TF dates")
        if re.fullmatch(r"\d{8}", token):
            stamp = pd.Timestamp(datetime.strptime(token, "%Y%m%d"))
        else:
            stamp = pd.Timestamp(token)
        index += 1
        date_only = "T" not in token.upper()
        if index < len(tokens) and re.fullmatch(r"\d{4}|\d{2}:\d{2}", tokens[index]):
            clock = tokens[index].replace(":", "")
            hour, minute = int(clock[:2]), int(clock[2:])
            if hour > 23 or minute > 59:
                raise ValueError("TF time must be HHMM or HH:MM (00:00–23:59 New York time)")
            stamp += pd.Timedelta(hours=hour, minutes=minute)
            index += 1
            date_only = False
        return stamp, index, date_only

    try:
        start, index, _ = date_at(0)
        length = " ".join(tokens[index:])
        duration = re.fullmatch(r"([1-9]\d{0,3})\s*(min|minutes?|hours?|h|d|days?|w|weeks?|mo|months?|y|years?|m)", length, re.I)
        if duration:
            amount, unit = duration.groups()
            unit = "months" if unit == "M" else unit.lower()
            field = "minutes" if unit in ("m", "min", "minute", "minutes") else "hours" if unit in ("h", "hour", "hours") else "days" if unit in ("d", "day", "days") else "weeks" if unit in ("w", "week", "weeks") else "months" if unit in ("mo", "month", "months") else "years"
            end = start + pd.DateOffset(**{field: int(amount)})
        else:
            end, index, date_only = date_at(index)
            if index != len(tokens):
                raise ValueError("Unexpected TF arguments")
            if date_only:
                end += pd.Timedelta(days=1)
        window = f"TF:{start.isoformat(timespec='minutes')}/{end.isoformat(timespec='minutes')}"
        timeframe_bounds(window)
        try:
            start.tz_localize("America/New_York")
            end.tz_localize("America/New_York")
        except Exception as error:
            raise ValueError("TF times must exist and be unambiguous in New York (daylight-saving transition)") from error
        if start > pd.Timestamp.now():
            raise ValueError("TF start date is in the future")
        return window
    except (ValueError, OverflowError) as error:
        raise ValueError(f"Invalid TF: {error}") from error


def is_range(value: str) -> bool:
    try:
        range_days(value)
        return True
    except ValueError:
        return False


def resample_ohlcv(df, iv: str):
    interval = candle_interval(iv)
    if interval.months:
        import pandas as pd
        # Calendar buckets have stable Jan-based boundaries, independent of the first source bar.
        idx = df.index
        month = (idx.year * 12 + idx.month - 1) // interval.months * interval.months
        labels = pd.DatetimeIndex([pd.Timestamp(year=int(m // 12), month=int(m % 12 + 1), day=1,
                                                tz=idx.tz) for m in month])
        r = df.groupby(labels)
    elif interval.value == "1wk":
        r = df.resample("W-FRI", label="right", closed="right")
    else:
        import pandas as pd
        r = df.resample(f"{interval.minutes}min", origin=pd.Timestamp("1970-01-05", tz=df.index.tz))
    out = r.agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum", "BadVol": "max"})
    out = out.dropna(subset=["Close"])
    out["BadVol"] = out["BadVol"].fillna(False).astype(bool)
    return out


def flag_bad_volume(df):
    """Yahoo's 1-minute equity data contains bogus volume prints (single minutes claiming 30-60% of the day;
    summed 1m volume reaching 2-5x the official daily volume). Bars above 100x their session's median volume
    are blanked (NaN) and flagged in BadVol so the chart can say how many it hid. Measured on NVDA/AAPL/SPY:
    afterwards 1m volume sums to 0.7-0.9x daily volume, matching Yahoo's clean 5m data. A real closing auction
    is far below 100x the median. Only applied to the 1m base: the 5m base is clean and crypto 5m false-flags."""
    vol = df["Volume"]
    med = vol.groupby(vol.index.normalize()).transform("median")
    bad = (med > 0) & (vol > 100 * med)
    out = df.copy()
    out.loc[bad, "Volume"] = float("nan")
    out["BadVol"] = bad
    return out


def slice_range(df, rng: str):
    rng = rng.upper()
    range_days(rng)
    if rng == "MAX" or not len(df):
        return df
    idx = df.index
    import pandas as pd
    if rng.startswith("TF:"):
        start, end = timeframe_bounds(rng)
        if idx.tz is not None:
            start = start.tz_localize("America/New_York").tz_convert(idx.tz)
            end = end.tz_localize("America/New_York").tz_convert(idx.tz)
        return df[(idx >= start) & (idx < end)]
    if rng in ("1D", "5D"):
        n = 1 if rng == "1D" else 5
        if (idx.dayofweek >= 5).any():            # trades on weekends (crypto): rolling 24h / 5d window
            return df[idx >= idx[-1] - pd.Timedelta(days=n)]
        days = idx.normalize().unique()           # exchange hours: last 1 / 5 sessions
        return df[idx.normalize() >= days[-min(n, len(days))]]
    if rng == "YTD":
        start = pd.Timestamp(year=idx[-1].year, month=1, day=1, tz=idx.tz)
    else:
        amount = int(rng[:-1])
        unit = {"D": "days", "W": "weeks", "M": "months", "Y": "years"}[rng[-1]]
        start = idx[-1] - pd.DateOffset(**{unit: amount})
    return df[idx >= start]



def base_for(rng: str, iv: str):
    interval = candle_interval(iv)
    need, days = interval.minutes, range_days(rng)
    if need >= range_span_days(rng) * 1440:
        return None
    best = None
    for biv, period, cover, ttl in BASES:
        if (biv == '1d' if interval.months else need % IV_MIN[biv] == 0) and cover >= days:
            best = (biv, period, ttl)
    return best
