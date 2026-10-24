"""Compatibility facade and chart window math; UI data access uses typed queries.

Ingestion, normalization, repositories and query/analysis services are separate,
UI-independent modules. DataHub keeps the established commands/views compatible.
"""
from __future__ import annotations
import asyncio
import math
import os
import re
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from functools import lru_cache

from .config import HOME, Config, load_aliases
from .query_models import Quote, Result, Metadata, QuotesQuery, HistoryQuery, ResourceQuery, AnalysisQuery, BarsQuery
from .ingestion import RateLimiter, Provider, YFProvider, Ingestion, _sub
from .query import QueryService
from .serialization import key

class TTLCache:
    def __init__(self) -> None:
        self._d: dict[str, tuple[float, object]] = {}

    def get(self, key: str):
        hit = self._d.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        return None

    def put(self, key: str, value, ttl: float) -> None:
        if len(self._d) >= 512:
            now = time.time()
            self._d = {k: v for k, v in self._d.items() if v[0] > now}
            if len(self._d) >= 512:
                self._d.pop(next(iter(self._d)))
        self._d[key] = (time.time() + ttl, value)


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


class DataHub:
    """View adapter. Typed query results remain available through `query`."""
    QUOTE_TTL, HIST_TTL = 5.0, 60.0
    base_for = staticmethod(base_for)

    def __init__(self, config: Config):
        self.cfg = config
        self.mem = TTLCache()  # small UI/live snapshot cache; never the observation store
        self.limiters = {'yf': RateLimiter('yf', 2.0, 4)}
        self.yf = YFProvider(self.limiters['yf'])
        self.quote_chain = [self.yf]
        self.last = {}
        self.warnings = []
        self.streams = {}
        self.iex = {}
        self.watch = {}
        self.queries = QueryService(HOME, Ingestion(config, self.limiters, self.yf), self.warn)
        self.queries.aliases = load_aliases()
        self.provenance = {}
        self._live_saved = {}
        self.alpaca_ok = False
        self.alpaca_reason = 'Alpaca keys awaiting validation' if (
            os.environ.get('ALPACA_API_KEY') and os.environ.get('ALPACA_API_SECRET')) else 'no ALPACA_API_KEY/ALPACA_API_SECRET'

    def warn(self, msg):
        if msg and (not self.warnings or self.warnings[-1] != msg):
            self.warnings = (self.warnings + [msg])[-5:]

    async def initialize(self):
        await self.queries.initialize()
        try:
            snapshots = await asyncio.to_thread(self.queries.repository.quote_snapshot)
        except Exception as error:
            self.warn(f'Stored quotes unreadable: {type(error).__name__}; fetching fresh data')
            snapshots = []
        for item in snapshots:
            if item:
                data, meta = item
                quote = Quote(**data)
                quote.live = False
                quote.stale = time.time() - meta.fetched_at >= 5
                self.last[quote.symbol] = quote

    async def query(self, request, *, force=False, stored_only=False):
        result = await self.queries.query(request, force=force, stored_only=stored_only)
        identity = self.queries.identity(request)
        self.provenance[identity] = result.meta
        if len(self.provenance) > 256:
            self.provenance.pop(next(iter(self.provenance)))
        if result.meta.freshness != 'fresh':
            self.warn(('STALE stored fallback · ' if result.meta.freshness == 'stale' else 'N/A · ') + result.meta.reason)
        return result

    @staticmethod
    def value(result):
        data = result.data
        if hasattr(data, 'calls') and hasattr(data, 'puts'):
            from types import SimpleNamespace
            data = SimpleNamespace(calls=data.calls, puts=data.puts, underlying=getattr(data, 'underlying', {}),
                                   freeterm=asdict(result.meta))
        if hasattr(data, 'attrs'):
            data = data.copy()
            data.attrs['freeterm'] = asdict(result.meta)
        return data

    async def resource(self, source, operation, **params):
        return self.value(await self.query(ResourceQuery(source, operation, params)))

    async def validate_alpaca(self):
        if not (os.environ.get('ALPACA_API_KEY') and os.environ.get('ALPACA_API_SECRET')):
            self.alpaca_ok, self.alpaca_reason = False, 'no ALPACA_API_KEY/ALPACA_API_SECRET'
        else:
            request = ResourceQuery('alpaca', 'validate', {
                'url': 'https://data.alpaca.markets/v2/stocks/SPY/trades/latest?feed=iex', '_ttl': 0, 'retries': 0})
            result = await self.query(request, force=True)
            self.alpaca_ok = result.data is not None and result.meta.freshness == 'fresh'
            self.alpaca_reason = '' if self.alpaca_ok else 'Alpaca keys rejected or unreachable'
        if not self.alpaca_ok and self.cfg.get('iex', 'enabled'):
            self.cfg.set('iex', 'enabled', False)

    def cached_quotes(self, symbols):
        return {s: self.last[s] for s in symbols if s in self.last}

    async def quotes(self, symbols):
        from .options import contract, snapshot
        for symbol in symbols:
            if contract(symbol) and self.mem.get('q:' + symbol) is None:
                await snapshot(self, symbol)
        need = [s for s in symbols if not self.live_fresh(s) and self.mem.get('q:' + s) is None]
        if need:
            result = await self.query(QuotesQuery(tuple(need)))
            for symbol, data in (result.data or {}).items():
                if not self.live_fresh(symbol):
                    quote = Quote(**data)
                    quote.stale = quote.stale or time.time() - quote.fetched >= 5
                    self.last[symbol] = quote
                    if not quote.stale:
                        self.mem.put('q:' + symbol, quote, 5)
        return self.cached_quotes(symbols)

    async def publish_quote(self, quote):
        """Persist stream observations at most once per five seconds per source/symbol."""
        self.last[quote.symbol] = quote
        identity = 'quote:' + quote.symbol
        if time.time() - self._live_saved.get(identity, 0) < 5:
            return
        self._live_saved[identity] = time.time()
        await self.queries.initialize()
        try:
            await asyncio.to_thread(self.queries.repository.put, identity, 'quote', quote.source,
                                    asdict(quote), quote.fetched, quote.ts)
        except Exception as error:
            self.warn(f'Live quote storage failed: {type(error).__name__}; stream continues')

    async def publish_iex(self, symbol, record):
        self.iex[symbol] = record
        identity = 'iex:' + symbol
        if time.time() - self._live_saved.get(identity, 0) < 5:
            return
        self._live_saved[identity] = time.time()
        await self.queries.initialize()
        try:
            await asyncio.to_thread(self.queries.repository.put, identity, 'document', 'alpaca', record,
                                    time.time(), record.get('ts'))
        except Exception as error:
            self.warn(f'IEX storage failed: {type(error).__name__}; stream continues')

    async def history(self, symbol, period='1y', interval='1d'):
        return self.value(await self.query(HistoryQuery(symbol, period, interval)))

    async def _base(self, sym, biv, period, ttl):
        return await self.history(sym, period, biv)

    async def bars(self, sym, rng, iv):
        return self.value(await self.query(BarsQuery(sym, rng, iv)))

    async def prefetch_bars(self, sym):
        for biv, period, _, ttl in reversed(BASES):
            await self._base(sym, biv, period, ttl)

    async def derive(self, name, params, frames, compute, version):
        metadata = [Metadata(**f.attrs['freeterm']) for f in frames if f.attrs.get('freeterm')]
        versions = []
        for frame in frames:
            versions.append(await asyncio.to_thread(key, frame))
            if frame.attrs.get('freeterm', {}).get('version'):
                versions.append(frame.attrs['freeterm']['version'])
        versions = tuple(versions)
        result = await self.queries.analysis(AnalysisQuery(name, params, versions, version), compute, inputs=tuple(metadata))
        return self.value(result)

    async def http(self, name, url, per_sec=2.0, headers=None, ttl=60, disk_ns=None, disk_age=None,
                   as_json=True, retries=2):
        # Compatibility for public HTTP resources; normalized feature services sit above it.
        return await self.resource(name, disk_ns or name, url=url, per_sec=per_sec, headers=headers,
                                   _ttl=disk_age if disk_age is not None else ttl, as_json=as_json, retries=retries)

    def invalidate_resource(self, source, operation, **params):
        self.queries.invalidate(ResourceQuery(source, operation, params))

    def force_refresh(self):
        self.mem = TTLCache()
        self.queries.refresh()

    def live_fresh(self, symbol):
        quote = self.last.get(symbol)
        return bool(quote and quote.live and time.time() - quote.fetched < 30)

    def status(self):
        parts = [lim.state() for lim in self.limiters.values()] + [f'{k}:{v}' for k, v in self.streams.items()]
        stale = sum(m.freshness == 'stale' for m in self.provenance.values())
        if stale:
            parts.append(f'data:STALE {stale}')
        return ' '.join(parts)

    async def close(self):
        await self.queries.close()
