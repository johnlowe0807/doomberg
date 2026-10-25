"""Compatibility facade and chart window math; UI data access uses typed queries.

Ingestion, normalization, repositories and query/analysis services are separate,
UI-independent modules. DataHub keeps the established commands/views compatible.
"""
from __future__ import annotations
import asyncio
import os
import time
from dataclasses import asdict

from .config import HOME, Config, load_aliases
from data_layer.query_models import Quote, Result, Metadata, QuotesQuery, HistoryQuery, ResourceQuery, AnalysisQuery, BarsQuery
from data_layer.ingestion import RateLimiter, Provider, YFProvider, Ingestion, _sub
from data_layer.query import QueryService
from data_layer.serialization import key

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


from data_layer.windows import (RANGES, IVS, IV_MIN, BASES, CandleInterval, candle_interval, is_interval, step_interval, range_days, timeframe_bounds, parse_timeframe, range_span_days, resample_ohlcv, flag_bad_volume, slice_range, base_for)


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
        from data_layer.options import contract, snapshot
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
