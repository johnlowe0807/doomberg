"""Bounded provider discovery populates an index; searches select only indexed rows."""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import replace
from urllib.parse import urlencode

from .query_models import Metadata, ResourceQuery, Result
from .serialization import key
from .symbols import catalog, canonical_type, rank, matches_filters
from .normalization import parse_page


async def search(service, query, *, force=False, stored_only=False):
    await service.initialize()
    repo = service.repository
    domain, text, filters = query.domain, query.text, query.filters
    cutoff = service.cutoff
    now = time.time()
    inputs = []
    stale = False
    if domain == 'security':
        seeds = catalog()
        names = {s: (n, k) for s, n, k, _ in seeds}
        aliases = {}
        for alias, symbol in getattr(service, 'aliases', {}).items():
            aliases.setdefault(symbol, []).append(alias)
        seeds += [(symbol, names.get(symbol, (symbol, 'SECURITY'))[0] + ' (' + ', '.join(values) + ')',
                   names.get(symbol, (symbol, 'SECURITY'))[1], 'alias') for symbol, values in aliases.items()]
    else:
        from .fred_discovery import catalog as fred_catalog
        seeds = fred_catalog()
    await asyncio.to_thread(repo.index_rows, domain, seeds)
    prior = await asyncio.to_thread(repo.discovery_time, domain, text + repr(sorted(filters.items())))
    refresh = force or prior < cutoff or now - prior > (600 if domain == 'security' else 86400)
    if not stored_only and refresh and (text or filters.get('t') == 'OPTION'):
        flight = ('discovery', domain, text, repr(sorted(filters.items())), service.generation)
        if flight in service.inflight:
            await asyncio.shield(service.inflight[flight])
            return await search(service, query, stored_only=True)

        async def discover():
            nonlocal stale
            if domain == 'fred':
                discovered = []
                for page in range(1, 5):
                    url = 'https://fred.stlouisfed.org/searchresults/?' + urlencode({'st': text, 'pageID': page})
                    result = await service.query(ResourceQuery('fred-search', 'fred-search',
                        {'url': url, 'as_json': False, 'retries': 0}), force=force)
                    inputs.append(result.meta)
                    if not result.data:
                        stale = True; break
                    rows, last = await asyncio.to_thread(parse_page, result.data)
                    if not rows and 'searchContentContainer' not in result.data:
                        stale = True; break
                    discovered.extend(rows)
                    if result.meta.freshness != 'fresh':
                        stale = True; break
                    if page >= last:
                        break
                fetched = min((m.fetched_at for m in inputs if m.fetched_at), default=now)
                await asyncio.to_thread(repo.index_rows, domain, discovered, text, fetched, not stale)
            elif filters.get('t') == 'OPTION':
                rows, metadata = await option_rows(service, text, force=force)
                inputs.extend(metadata)
                stale = any(m.freshness != 'fresh' for m in metadata)
                await asyncio.to_thread(repo.index_rows, domain, rows, text)
            else:
                result = await service.query(ResourceQuery('yf', 'search', {'text': text}), force=force)
                inputs.append(result.meta)
                rows = [(item['symbol'], item.get('longname') or item.get('shortname') or item['symbol'],
                         canonical_type(item.get('quoteType') or item.get('typeDisp') or 'SECURITY'), 'Yahoo')
                        for item in result.data or [] if item.get('symbol')]
                await asyncio.to_thread(repo.index_rows, domain, rows, text, result.meta.fetched_at)
                if not filters.get('t') or 'EQUITY' in filters['t'].split(','):
                    sec = await service.query(ResourceQuery('sec', 'sec', {'url': 'https://www.sec.gov/files/company_tickers.json'}), force=force)
                    inputs.append(sec.meta)
                    sec_rows = [(r['ticker'].upper(), r['title'], 'EQUITY', 'EDGAR') for r in (sec.data or {}).values()]
                    await asyncio.to_thread(repo.index_rows, domain, sec_rows, fetched=sec.meta.fetched_at or now)
                candidates = await asyncio.to_thread(repo.search, domain, text)
                symbols = sorted({r[0] for r in candidates if rank(text, *r[:3]) < 99 or r[3] == 'Yahoo'})[:100]
                if symbols:
                    detail = await service.query(ResourceQuery('yf', 'names', {'symbols': tuple(symbols)}), force=force)
                    inputs.append(detail.meta)
                    names = [(r['symbol'].upper(), r.get('longName') or r.get('shortName') or r['symbol'],
                              canonical_type(r.get('quoteType', 'SECURITY')), 'Yahoo') for r in detail.data or [] if r.get('symbol')]
                    # Enriched candidates retain their discovery association for fuzzy matches.
                    await asyncio.to_thread(repo.index_rows, domain, names, fetched=detail.meta.fetched_at or now)
                stale = any(m.freshness != 'fresh' for m in inputs)
            if not stale:
                await asyncio.to_thread(repo.mark_discovery, domain, text + repr(sorted(filters.items())), now)

        task = asyncio.create_task(discover())
        service.inflight[flight] = task
        task.add_done_callback(lambda _: service.inflight.pop(flight, None))
        await asyncio.shield(task)
    candidates = await asyncio.to_thread(repo.search, domain, text)

    def select():
        if domain == 'fred':
            from .fred_discovery import matches
            grouped = {}
            for row in candidates:
                existing = grouped.get(row[0])
                if existing is None or row[5] != 'N/A':
                    grouped[row[0]] = row
            return sorted((r for r in grouped.values() if matches(r, filters)), key=lambda r: (rank(text, r[0], r[1]), r[0]))[:query.limit]
        from .options import contract
        groups = {}
        for row in candidates:
            if row[3] == 'alias' and row[0] not in aliases:
                continue
            groups.setdefault(row[0].upper(), []).append(row)
        ranked = []
        for symbol, rows in groups.items():
            option = bool(contract(symbol)) or any(r[2] == 'OPTION' for r in rows)
            if option != (filters.get('t') == 'OPTION'):
                continue
            score = min(rank(text, *r[:3]) for r in rows)
            if score == 99 and not any(r[3] == 'Yahoo' for r in rows):
                continue
            # Preserve provider/alias matching but display the most descriptive name.
            best = min(rows, key=lambda r: (r[3] != 'Yahoo', r[3] == 'EDGAR', -len(r[1])))
            row = (symbol, *best[1:])
            if matches_filters(row, filters, {'s': 0, 'n': 1, 't': 2, 'p': 3}, ('t', 'p')):
                ranked.append((score, symbol, row))
        return [r for _, _, r in sorted(ranked)[:query.limit]]

    data = await asyncio.to_thread(select)
    meta = service.combine(inputs, 'search-index') if inputs else Metadata('search-index', fetched_at=prior or now,
                                                                          freshness='fresh', cache='index')
    if stale or (stored_only and text and refresh):
        meta = replace(meta, freshness='stale', reason=meta.reason or ('Discovery provider unavailable or unreadable; using stored/local index' if stale else 'Discovery freshness expired; using stored/local index'))
    return Result(data, replace(meta, version=await asyncio.to_thread(key, data), cache='index'), tuple(inputs))


async def option_rows(service, text, *, force=False):
    """One underlying/expiry, up to 100 contracts; no unbounded chain indexing."""
    from .options import contract, name, number
    text = text.upper().strip()
    text = getattr(service, 'aliases', {}).get(text, text)
    exact = contract(text)
    inputs = []
    words = text.split()
    expiry = exact['expiry'] if exact else next((w for w in words if re.fullmatch(r'\d{4}-\d{2}-\d{2}', w)), None)
    underlying = exact['underlying'] if exact else ' '.join(w for w in words if w != expiry)
    if not underlying:
        raise ValueError('Options search needs an underlying: /? AAPL _t=OPTION')
    underlying = getattr(service, 'aliases', {}).get(underlying, underlying)
    result = await service.query(ResourceQuery('yf', 'expiries', {'symbol': underlying}), force=force)
    inputs.append(result.meta)
    if not result.data and not exact:
        found = await service.query(ResourceQuery('yf', 'search', {'text': underlying, 'limit': 8}), force=force)
        inputs.append(found.meta)
        underlying = next((r['symbol'] for r in found.data or [] if r.get('quoteType') in ('EQUITY', 'ETF') and r.get('symbol')), None)
        if underlying:
            result = await service.query(ResourceQuery('yf', 'expiries', {'symbol': underlying}), force=force)
            inputs.append(result.meta)
    if not result.data:
        return [], inputs
    expiry = expiry or result.data[0]
    if expiry not in result.data:
        raise ValueError(f'No listed expiry {expiry} for {underlying}; nearest is {result.data[0]}')
    result = await service.query(ResourceQuery('yf', 'chain', {'symbol': underlying, 'expiry': expiry}), force=force)
    inputs.append(result.meta)
    if result.data is None:
        return [], inputs
    chain = result.data
    spot = number((getattr(chain, 'underlying', None) or {}).get('regularMarketPrice'))
    def select():
        rows = [r for frame in (chain.calls, chain.puts) if frame is not None for _, r in frame.iterrows() if contract(r.get('contractSymbol'))]
        rows.sort(key=lambda r: (abs((number(r.get('strike')) or 0) - spot) if spot else 0, r['contractSymbol']))
        return [(r['contractSymbol'], name(r['contractSymbol']), 'OPTION', 'Yahoo') for r in rows[:100]
                if not exact or r['contractSymbol'] == text]
    return await asyncio.to_thread(select), inputs
