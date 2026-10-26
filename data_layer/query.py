"""Typed query services over ingestion, durable repositories and versioned analysis.

No Textual imports. Call query(..., stored_only=True) for offline analysis; all
SQLite, decoding, normalization and computation run in worker threads.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable, TypeVar, overload

import pandas as pd

from .ingestion import Ingestion
from .normalization import fred_series, norm_yf_news, rss_items
from .query_models import (ComparisonQuery, BarsQuery, StoredBarsQuery, TextQuery, AnalysisQuery, FactsQuery, FilingsQuery, HistoryQuery, Metadata,
                           NewsQuery, Query, QuotesQuery, ResourceQuery, Result, SearchQuery, SeriesQuery, TableQuery, TableData,
                           FundamentalsQuery, EstimatesQuery, HoldingsQuery, OwnershipQuery, CorporateActionsQuery,
                           SharesQuery, ResearchQuery, StatementsQuery, EventsQuery, NewsRecordsQuery, NewsArticleQuery,
                           CompanyQuery, FinancialArchiveQuery, YahooPricesQuery, ExtractQuery, ExtractedData)
from .serialization import key
from .storage import Repository, ResultCache

T = TypeVar("T")

# Observation freshness and response reuse are intentionally different policies.
# Long-lived normalized documents stay in the repository after response eviction.
POLICIES = {
    'quote': (5, 5), 'history': (60, 60), 'fred-series': (21600, 300),
    'profile': (3600, 60), 'book': (15, 5), 'chain': (30, 15), 'expiries': (300, 30),
    'earnings': (3600, 60), 'calendar': (3600, 60), 'financials': (3600, 60),
    'news': (300, 30), 'screen': (300, 30), 'search': (600, 60), 'names': (86400, 60),
    'market-screen': (300, 300),
    'fundamental-data': (21600, 21600), 'estimate-data': (3600, 3600), 'holdings-data': (86400, 86400),
    'plug-ownership': (21600, 3600), 'plug-actions': (21600, 3600), 'plug-shares': (21600, 3600),
    'plug-research': (21600, 3600), 'plug-statement': (21600, 3600), 'plug-company': (21600, 3600),
    'plug-calendar': (3600, 300), 'plug-news': (300, 60), 'plug-search-news': (300, 60),
    'commodity': (300, 60), 'secfacts': (86400, 60), 'secsub': (86400, 60),
    'secdocs': (365 * 86400, 60), 'secidx': (365 * 86400, 60), 'sec': (7 * 86400, 60),
    'rss': (300, 30), 'fred-search': (86400, 60), 'fred-meta': (86400, 60),
    'articles': (3600, 30), 'fiscal': (3600, 60), 'coinbase': (5, 5), 'alpaca': (5, 1),
}


class QueryService:
    def __init__(self, home: Path, ingestion: Ingestion, warn: Callable[[str], None]):
        self.home = home
        self.repository = Repository(home / 'data' / 'observations.sqlite3')
        self.cache = ResultCache(home / 'cache' / 'pipeline.sqlite3')
        self.ingestion, self.warn = ingestion, warn
        self.ready = False
        self.init_lock = asyncio.Lock()
        self.inflight = {}
        self.cutoff = 0.0
        self.generation = 0
        self.invalidated = {}
        self.last_cleanup = 0
        self.closing = False
        self.tables = {}
        from .screening import register
        register(self)
        from .financial_data import register as register_financial
        register_financial(self)
        from .yahoo_datasets import register as register_yahoo
        register_yahoo(self)
        from .access import register as register_access
        register_access(self)
        self.yahoo_streams = []

    def register_table(self, name, dataset):
        """Register a dataset adapter once; all clients share the same query semantics."""
        if not name or name.casefold() in self.tables:
            raise ValueError('Dataset name is empty or already registered: ' + name)
        self.tables[name.casefold()] = dataset

    def table_dataset(self, name):
        try:
            return self.tables[name.casefold()]
        except KeyError:
            raise ValueError('Unknown dataset ' + name + '; available: ' + ', '.join(self.tables)) from None

    async def initialize(self):
        async with self.init_lock:
            if self.ready:
                return
            if hasattr(self.ingestion, 'initialize_yahoo'):
                await asyncio.to_thread(self.ingestion.initialize_yahoo, self.home / 'cache' / 'yahoo')
            try:
                await asyncio.to_thread(self.repository.initialize)
            except Exception as error:
                self.warn(f'Data store unavailable ({type(error).__name__}); using session-only storage')
                await asyncio.to_thread(self.repository.close)
                self.repository = Repository(':memory:')
                await asyncio.to_thread(self.repository.initialize)
            try:
                await asyncio.to_thread(self.cache.initialize)
            except Exception as error:
                self.warn(f'Persistent caches unavailable ({type(error).__name__}); using session-only caches')
                self.cache = ResultCache(':memory:')
                await asyncio.to_thread(self.cache.initialize)
            self.ready = True
            try:
                await self.cleanup()
            except Exception as error:
                self.warn(f'Data retention cleanup failed: {type(error).__name__}')

    async def cleanup(self):
        await asyncio.to_thread(self.repository.cleanup)
        await asyncio.to_thread(self.cache.cleanup)
        self.last_cleanup = time.time()

    @staticmethod
    def identity(query):
        if isinstance(query, ResourceQuery):
            # Transport settings and credentials do not become durable keys.
            params = {k: v for k, v in query.params.items() if k not in ('headers', 'per_sec', 'retries', '_ttl')}
            return 'resource:' + key((query.source, query.operation, params))
        return type(query).__name__ + ':' + key(asdict(query))

    def refresh(self):
        """Called before reloading views. Older in-flight work cannot satisfy this generation."""
        self.cutoff = time.time()
        self.generation += 1

    def invalidate(self, query):
        self.invalidated[self.identity(query)] = time.time()
        self.generation += 1

    async def _response(self, identity, fetch, ttl, force):
        try:
            cached = None if force else await asyncio.to_thread(self.cache.get, 'provider', identity)
        except Exception as error:
            self.warn(f'Provider cache unreadable: {type(error).__name__}; bypassing')
            cached = None
        if cached is not None:
            return cached
        generation = self.generation
        data = await fetch()
        if data is None:
            raise ValueError('Provider returned no data')
        fetched = time.time()
        try:
            if generation == self.generation:
                await asyncio.to_thread(self.cache.put, 'provider', identity, data, ttl, fetched)
        except Exception as error:
            self.warn(f'Provider cache write failed: {type(error).__name__}; using fetched data')
        return data, fetched

    async def _dataset(self, identity, kind, source, ttl, loader, *, force=False, stored_only=False, filters=None):
        await self.initialize()
        if time.time() - self.last_cleanup > 600:
            self.last_cleanup = time.time()
            try:
                await self.cleanup()
            except Exception as error:
                self.warn(f'Data retention cleanup failed: {type(error).__name__}')
        try:
            old = await asyncio.to_thread(self.repository.get, identity, **(filters or {}))
        except Exception as error:
            self.warn(f'Stored data unreadable: {type(error).__name__}; fetching fresh data')
            old = None
        cutoff = max(self.cutoff, self.invalidated.get(identity, 0))
        if old and (stored_only or (not force and old[1].fetched_at >= cutoff and time.time() - old[1].fetched_at < ttl)):
            data, meta = old
            if time.time() - meta.fetched_at >= ttl or meta.fetched_at < cutoff:
                meta = replace(meta, freshness='stale', reason='Stored observations exceed freshness policy')
            return Result(data, meta)
        if stored_only:
            return Result(None, Metadata(source, reason='No stored observations'))
        flight_key = (identity, self.generation)
        if flight_key in self.inflight:
            # Filter requests share ingestion, then select their own stored projection below.
            result = await asyncio.shield(self.inflight[flight_key])
            if filters and result.data is not None:
                selected = await asyncio.to_thread(self.repository.get, identity, **filters)
                return Result(selected[0], result.meta, result.inputs)
            return result
        generation = self.generation

        async def load():
            try:
                result = await loader(force or bool(old and old[1].fetched_at < cutoff))
                if result.data is None:
                    raise ValueError(result.meta.reason or 'Provider returned no data')
                if generation != self.generation:
                    # A force-refresh arrived during ingestion: let the next generation win.
                    return await self._dataset(identity, kind, source, ttl, loader, filters=filters)
                try:
                    stored = await asyncio.to_thread(self.repository.put, identity, kind, source, result.data,
                                                     result.meta.fetched_at, result.meta.observed_at,
                                                     complete=result.meta.complete,coverage=result.meta.coverage,reason=result.meta.reason)
                except Exception as error:
                    self.warn(f'Data store write failed: {type(error).__name__}; result is session-only')
                    version = await asyncio.to_thread(key, result.data)
                    return Result(result.data, replace(result.meta, cache='session-only', version=version), result.inputs)
                data, meta = stored
                meta = replace(meta, freshness=result.meta.freshness, reason=result.meta.reason)
                if filters:
                    data = (await asyncio.to_thread(self.repository.get, identity, **filters))[0]
                return Result(data, meta, result.inputs)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                reason = f'{source}: {type(error).__name__}: {str(error)[:90]}'
                if old:
                    return Result(old[0], replace(old[1], freshness='stale', reason=reason))
                return Result(None, Metadata(source, reason=reason))
        task = asyncio.create_task(load())
        self.inflight[flight_key] = task
        task.add_done_callback(lambda _: self.inflight.pop(flight_key, None))
        return await asyncio.shield(task)

    @overload
    async def query(self, query: ExtractQuery, *, force: bool = False,
                    stored_only: bool = False) -> Result[ExtractedData | None]: ...

    @overload
    async def query(self, query: HistoryQuery | BarsQuery | StoredBarsQuery, *, force: bool = False,
                    stored_only: bool = False) -> Result[pd.DataFrame | None]: ...

    @overload
    async def query(self, query: SeriesQuery, *, force: bool = False,
                    stored_only: bool = False) -> Result[pd.Series | None]: ...

    @overload
    async def query(self, query: FactsQuery | QuotesQuery | ComparisonQuery, *, force: bool = False,
                    stored_only: bool = False) -> Result[dict | None]: ...

    @overload
    async def query(self, query: FilingsQuery | NewsQuery | SearchQuery, *, force: bool = False,
                    stored_only: bool = False) -> Result[list | None]: ...

    @overload
    async def query(self, query: TextQuery, *, force: bool = False,
                    stored_only: bool = False) -> Result[str | None]: ...

    @overload
    async def query(self, query: FundamentalsQuery | EstimatesQuery | HoldingsQuery, *, force: bool = False,
                    stored_only: bool = False) -> Result[list | None]: ...

    @overload
    async def query(self, query: TableQuery, *, force: bool = False,
                    stored_only: bool = False) -> Result[TableData | None]: ...

    @overload
    async def query(self, query: OwnershipQuery | CorporateActionsQuery | SharesQuery | ResearchQuery | StatementsQuery |
                    EventsQuery | NewsRecordsQuery | CompanyQuery | FinancialArchiveQuery | YahooPricesQuery,
                    *, force: bool = False, stored_only: bool = False) -> Result[list[dict] | None]: ...

    @overload
    async def query(self, query: NewsArticleQuery, *, force: bool = False,
                    stored_only: bool = False) -> Result[dict | None]: ...

    @overload
    async def query(self, query: ResourceQuery, *, force: bool = False,
                    stored_only: bool = False) -> Result[Any]: ...

    async def query(self, query: Query, *, force: bool = False, stored_only: bool = False) -> Result[Any]:
        if isinstance(query, ExtractQuery):
            from .extraction import execute
            return await execute(self, query, force=force, stored_only=stored_only)
        from .yahoo_datasets import REQUESTS, snapshot as yahoo_snapshot
        if isinstance(query,REQUESTS):
            return await yahoo_snapshot(self,query,force=force,stored_only=stored_only)
        if isinstance(query, (FundamentalsQuery, EstimatesQuery, HoldingsQuery)):
            from .financial_data import snapshot
            return await snapshot(self, query, force=force, stored_only=stored_only)
        if isinstance(query, TableQuery):
            from .tabular import execute
            return await execute(self, query, force=force, stored_only=stored_only)
        if isinstance(query, ComparisonQuery):
            return await self.comparison(query, force=force, stored_only=stored_only)
        if isinstance(query, StoredBarsQuery):
            await self.initialize()
            result = await asyncio.to_thread(self.repository.stored_bars, query.symbol, query.interval, query.start, query.end)
            return Result(*result) if result else Result(None, Metadata('yf', reason='No stored observations'))
        if isinstance(query, TextQuery):
            resource = ResourceQuery(query.source, 'secdocs' if query.source == 'sec' else 'articles',
                                     {'url': query.url, 'as_json': False, 'retries': 0})
            result = await self.query(resource, force=force, stored_only=stored_only)
            if not result.data:
                return result
            from .normalization import html_to_text, extract_article
            normalize = html_to_text if query.source == 'sec' else extract_article
            return await self.analysis(AnalysisQuery('readable-document', asdict(query), (result.meta.version,), 'extraction-v1'),
                                       lambda: normalize(result.data), inputs=(result.meta,), force=force)
        if isinstance(query, BarsQuery):
            from .windows import base_for, slice_range, resample_ohlcv, candle_interval
            base = base_for(query.range, query.interval)
            if base is None:
                return Result(None, Metadata('yf', reason='Interval unavailable for selected window'))
            result = await self.query(HistoryQuery(query.symbol, base[1], base[0]), force=force, stored_only=stored_only)
            if result.data is None:
                return result
            def compute():
                frame = slice_range(result.data, query.range)
                if candle_interval(query.interval).value != base[0]:
                    frame = resample_ohlcv(frame, query.interval)
                return frame.copy()
            # Relative windows depend on the last stored observation, not the query clock.
            request = AnalysisQuery('bars', asdict(query), (result.meta.version,), 'ohlcv-window-v1')
            return await self.analysis(request, compute, inputs=(result.meta,), force=force)
        if isinstance(query, HistoryQuery):
            identity = self.identity(query)
            async def load(refresh):
                async def fetch():
                    frame = await self.ingestion.yf.history(query.symbol, query.period, query.interval)
                    if frame is None or frame.empty:
                        raise ValueError(f'No history for {query.symbol}')
                    return frame
                raw, fetched = await self._response(identity, fetch, min(60, self.history_ttl(query)), refresh)
                from .windows import flag_bad_volume
                def normalize():
                    data = raw[['Open', 'High', 'Low', 'Close', 'Volume']].dropna(subset=['Close']).copy()
                    data = data[~data.index.duplicated(keep='last')].sort_index()
                    return flag_bad_volume(data) if query.interval == '1m' else data.assign(BadVol=False)
                frame = await asyncio.to_thread(normalize)
                return Result(frame, Metadata('yf', observed_at=frame.index[-1].timestamp() if len(frame) else None, fetched_at=fetched, freshness='fresh'))
            result = await self._dataset(identity, 'bars', 'yf', self.history_ttl(query), load, force=force, stored_only=stored_only)
            if result.data is not None:
                if result.meta.cache != 'session-only':
                    await asyncio.to_thread(self.repository.register_series, identity, query.symbol, query.interval)
                if query.period.lower() != 'max':
                    from .windows import slice_range
                    data = await asyncio.to_thread(slice_range, result.data, query.period.upper().replace('MO', 'M'))
                    result = Result(data, result.meta, result.inputs)
            return result
        if isinstance(query, SeriesQuery):
            sid = query.series.upper()
            async def load(refresh):
                raw = await self.query(ResourceQuery('fred', 'fred-series', dict(
                    url=f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}', as_json=False)), force=refresh)
                values = await asyncio.to_thread(fred_series, raw.data, sid) if raw.data else None
                return Result(values, raw.meta, (raw.meta,))
            return await self._dataset(self.identity(SeriesQuery(sid)), 'series', 'fred', 21600, load, force=force, stored_only=stored_only)
        if isinstance(query, ResourceQuery):
            ttl, response_ttl = POLICIES.get(query.operation, POLICIES.get(query.source, (60, 30)))
            ttl = query.params.get('_ttl', ttl)
            async def load(refresh):
                value, fetched = await self._response(self.identity(query), lambda: self.ingestion.resource(query),
                                                      min(response_ttl, ttl), refresh)
                return Result(value, Metadata(query.source, fetched_at=fetched, freshness='fresh'))
            return await self._dataset(self.identity(query), 'document', query.source, ttl, load, force=force, stored_only=stored_only)
        if isinstance(query, QuotesQuery):
            return await self.quotes(query, force=force, stored_only=stored_only)
        if isinstance(query, FactsQuery):
            async def load(refresh):
                resource = ResourceQuery('sec', 'secfacts', {'url': f'https://data.sec.gov/api/xbrl/companyfacts/CIK{query.cik:010d}.json'})
                result = await self.query(resource, force=refresh)
                if result.data and not result.data.get('facts'):
                    raise ValueError('SEC returned no company facts')
                observed = None
                if result.data:
                    def latest():
                        dates = [r.get('filed', '') for concepts in result.data['facts'].values() for c in concepts.values()
                                 for rows in c.get('units', {}).values() for r in rows]
                        return pd.Timestamp(max(dates)).timestamp() if dates and max(dates) else None
                    observed = await asyncio.to_thread(latest)
                return Result(result.data, replace(result.meta, observed_at=observed), (result.meta,))
            return await self._dataset('facts:' + str(query.cik), 'facts', 'sec', 86400, load, force=force,
                                       stored_only=stored_only, filters={'concept': query.concept, 'start': query.start, 'end': query.end})
        if isinstance(query, FilingsQuery):
            async def load(refresh):
                base = 'https://data.sec.gov/submissions/'
                result = await self.query(ResourceQuery('sec', 'secsub', {'url': f'{base}CIK{query.cik:010d}.json'}), force=refresh)
                if not result.data:
                    return result
                inputs, pages = [result.meta], [result.data['filings']['recent']]
                for page in result.data['filings'].get('files', []):
                    extra = await self.query(ResourceQuery('sec', 'secsub', {'url': base + page['name'], '_ttl': 365 * 86400}), force=refresh)
                    if extra.data is None:
                        raise ValueError('Older SEC submissions page unavailable; retaining complete stored index')
                    pages.append(extra.data); inputs.append(extra.meta)
                def normalize():
                    out = {}
                    for page in pages:
                        for i, accession in enumerate(page.get('accessionNumber', [])):
                            col = lambda k: (page.get(k) or [''] * len(page['accessionNumber']))[i]
                            out[accession] = dict(acc=accession, date=col('filingDate'), form=col('form'),
                                                 doc=col('primaryDocument'), desc=col('primaryDocDescription'),
                                                 items=col('items'), period=col('reportDate'))
                    return sorted(out.values(), key=lambda r: (r['date'], r['acc']), reverse=True)
                data = await asyncio.to_thread(normalize)
                meta = self.combine(inputs, 'sec')
                observed = pd.Timestamp(data[0]['date']).timestamp() if data and data[0]['date'] else None
                return Result(data, replace(meta, observed_at=observed), tuple(inputs))
            return await self._dataset('filings:' + str(query.cik), 'filings', 'sec', 86400, load, force=force,
                                       stored_only=stored_only, filters={'form': query.form, 'year': query.year})
        if isinstance(query, NewsQuery):
            return await self.news(query, force=force, stored_only=stored_only)
        if isinstance(query, SearchQuery):
            return await self.search(query, force=force, stored_only=stored_only)
        raise TypeError(f'Unsupported query {type(query).__name__}')

    @staticmethod
    def history_ttl(query):
        return {('7d', '1m'): 60, ('60d', '5m'): 120, ('730d', '1h'): 300, ('max', '1d'): 900}.get((query.period, query.interval), 60)

    @staticmethod
    def combine(inputs, source):
        if not inputs:
            return Metadata(source, fetched_at=time.time(), freshness='fresh')
        state = 'unavailable' if all(i.freshness == 'unavailable' for i in inputs) else (
            'stale' if any(i.freshness != 'fresh' for i in inputs) else 'fresh')
        return Metadata(source, max((i.observed_at or 0 for i in inputs), default=0) or None,
                        min(i.fetched_at for i in inputs), key(tuple(i.version for i in inputs)), state,
                        'store' if all(i.cache == 'store' for i in inputs) else 'provider',
                        '; '.join(dict.fromkeys(i.reason for i in inputs if i.reason)))

    async def quotes(self, query, *, force=False, stored_only=False):
        await self.initialize()
        symbols = tuple(dict.fromkeys(query.symbols))
        old, need = {}, []
        for symbol in symbols:
            hit = await asyncio.to_thread(self.repository.get, 'quote:' + symbol)
            if hit:
                old[symbol] = hit
            ttl = 30 if hit and hit[0].get('live') else 5
            if not stored_only and (force or not hit or hit[1].fetched_at < self.cutoff or time.time() - hit[1].fetched_at >= ttl):
                need.append(symbol)
        # Identical concurrent batches share one rate-limited request.
        if need:
            identity = 'quotes:' + key(tuple(sorted(need)))
            async def load(refresh):
                async def fetch():
                    values = await self.ingestion.yf.quote(need)
                    if not values:
                        raise ValueError('No quote observations')
                    return {s: asdict(q) for s, q in values.items()}
                values, fetched = await self._response(identity, fetch, 5, refresh)
                return Result(values, Metadata('yf', fetched_at=fetched, freshness='fresh'))
            batch = await self._dataset(identity, 'document', 'yf', 5, load, force=force)
            if batch.data:
                for symbol, quote in batch.data.items():
                    if batch.meta.freshness == 'fresh':
                        try:
                            old[symbol] = await asyncio.to_thread(self.repository.put, 'quote:' + symbol, 'quote', quote['source'],
                                                                 quote, quote['fetched'], quote.get('ts'))
                        except Exception as error:
                            self.warn(f'Quote store write failed: {type(error).__name__}; using fetched snapshot')
                            old[symbol] = (quote, Metadata(quote['source'], quote.get('ts'), quote['fetched'],
                                                          await asyncio.to_thread(key, quote), 'fresh', 'session-only'))
            if batch.meta.freshness != 'fresh':
                self.warn(batch.meta.reason)
        values, metadata = {}, []
        for symbol in symbols:
            if symbol in old:
                value, meta = old[symbol]
                ttl = 30 if value.get('live') else 5
                if time.time() - meta.fetched_at >= ttl or meta.fetched_at < self.cutoff:
                    meta = replace(meta, freshness='stale', reason='Quote refresh unavailable; stored snapshot')
                values[symbol] = dict(value, stale=meta.freshness != 'fresh')
                metadata.append(meta)
        sources = '|'.join(dict.fromkeys(m.source for m in metadata))
        meta = self.combine(metadata, sources) if metadata else Metadata('yf', reason='No quote data')
        return Result(values, meta, tuple(metadata))

    async def news(self, query, *, force=False, stored_only=False):
        identity = 'news:' + key((query.symbol, query.feeds))
        async def load(refresh):
            requests = [self.query(ResourceQuery('rss', 'rss', {'url': url, 'as_json': False, 'retries': 0,
                        'headers': {'User-Agent': 'Mozilla/5.0 freeterm'}}), force=refresh) for url in query.feeds]
            results = await asyncio.gather(*requests)
            items = []
            for url, result in zip(query.feeds, results):
                if result.data:
                    items.extend(await asyncio.to_thread(rss_items, result.data, url))
            if query.symbol:
                result = await self.query(ResourceQuery('yf', 'news', {'symbol': query.symbol}), force=refresh)
                results.append(result)
                items.extend(await asyncio.to_thread(norm_yf_news, result.data or []))
            if not items and any(r.meta.freshness != 'fresh' for r in results):
                raise ValueError('News sources unavailable')
            unique = {i['title'].strip().casefold(): i for i in items if i['title'].strip()}
            data = sorted(unique.values(), key=lambda i: i['ts'].timestamp() if i.get('ts') and not pd.isna(i['ts']) else 0, reverse=True)
            meta = self.combine([r.meta for r in results], 'news')
            observed = max((i['ts'].timestamp() for i in data if i.get('ts') and not pd.isna(i['ts'])), default=None)
            return Result(data, replace(meta, observed_at=observed), tuple(r.meta for r in results))
        return await self._dataset(identity, 'news', 'news', 300, load, force=force, stored_only=stored_only, filters={'limit': query.limit})

    async def analysis(self, query: AnalysisQuery, compute: Callable[[], T], *, inputs: tuple[Metadata, ...] = (),
                       force: bool = False) -> Result[T]:
        """A calculation must name its logic version and every input dataset version.

        `compute` is a synchronous pure function run in a worker. Cached output never
        promotes stale input metadata to fresh, including after a process restart.
        """
        if not query.calculation_version or not query.input_versions:
            raise ValueError('Analysis requires calculation and input-data versions')
        await self.initialize()
        identity = key(asdict(query))
        try:
            hit = None if force else await asyncio.to_thread(self.cache.get, 'analysis', identity)
        except Exception as error:
            self.warn(f'Analysis cache unreadable: {type(error).__name__}; recalculating')
            hit = None
        provenance = '|'.join(dict.fromkeys(i.source for i in inputs)) or 'analysis'
        meta = self.combine(list(inputs), provenance)
        if hit and hit[1] >= self.cutoff:
            return Result(hit[0], replace(meta, version=identity, cache='analysis'), tuple(inputs))
        flight = ('analysis', identity, self.generation)
        if flight in self.inflight:
            return await asyncio.shield(self.inflight[flight])
        async def calculate():
            data = await asyncio.to_thread(compute)
            try:
                await asyncio.to_thread(self.cache.put, 'analysis', identity, data, 30 * 86400)
            except Exception as error:
                self.warn(f'Analysis cache write failed: {type(error).__name__}; using calculated result')
            return Result(data, replace(meta, version=identity, cache='calculation'), tuple(inputs))
        task = asyncio.create_task(calculate())
        self.inflight[flight] = task
        task.add_done_callback(lambda _: self.inflight.pop(flight, None))
        return await asyncio.shield(task)

    async def comparison(self, query, *, force=False, stored_only=False):
        from . import comparison
        comparison.validate_mode(comparison.mode(query.mode), query.references)
        columns, inputs, missing = {}, [], []
        for ref in query.references:
            if ref.startswith('FRED:'):
                result = await self.query(SeriesQuery(ref[5:]), force=force, stored_only=stored_only)
                values = result.data
            elif ref.startswith('ERN:'):
                result = await self.query(ResourceQuery('yf', 'earnings', {'symbol': ref[4:]}), force=force, stored_only=stored_only)
                def earnings():
                    if result.data is None or 'Surprise(%)' not in result.data:
                        return None
                    values = pd.to_numeric(result.data['Surprise(%)'], errors='coerce').dropna().sort_index()
                    return values[values.index <= pd.Timestamp.now(tz=values.index.tz)]
                values = await asyncio.to_thread(earnings)
            else:
                result = await self.query(BarsQuery(ref, query.range, query.interval), force=force, stored_only=stored_only)
                values = result.data.Close if result.data is not None and len(result.data) else None
            inputs.append(result.meta)
            if values is not None and len(values):
                columns[ref] = values
            else:
                missing.append(ref)
        if not columns:
            return Result(None, replace(self.combine(inputs, 'comparison'), reason='No comparison data'), tuple(inputs))
        def compute():
            frame = comparison.align(columns, query.range, query.interval)
            empty = frame.columns[frame.isna().all()].tolist()
            frame = frame.drop(columns=empty)
            unavailable = list(dict.fromkeys(missing + empty))
            if frame.empty:
                raise ValueError('No observations in the selected comparison window')
            if query.mode == 'STACKED' and len(frame.columns) != len(query.references):
                raise ValueError('STACKED needs data for every selected series')
            return dict(raw=frame, values=comparison.normalized(frame) if query.mode == 'NORMALIZED' else frame.copy(), missing=unavailable)
        return await self.analysis(AnalysisQuery('comparison', asdict(query), tuple(m.version for m in inputs), 'comparison-v1'),
                                   compute, inputs=inputs, force=force)

    async def search(self, query, *, force=False, stored_only=False):
        # Discovery writes normalized rows first. All result selection reads this index.
        from .discovery import search
        return await search(self, query, force=force, stored_only=stored_only)

    async def close(self):
        self.closing = True
        for stream in self.yahoo_streams:
            await stream.stop()
        tasks = list(self.inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.ingestion.close()
        if self.ready:
            await asyncio.to_thread(self.repository.close)
            await asyncio.to_thread(self.cache.repo.close)
        self.ready = False

    async def start_yahoo_stream(self, symbols, on_record=None):
        """Opt-in data-layer stream; existing terminal feeds are unaffected."""
        from .yahoo_stream import YahooPriceStream
        await self.initialize()
        stream=YahooPriceStream(self,symbols,on_record=on_record)
        self.yahoo_streams.append(stream)
        await stream.start()
        return stream
