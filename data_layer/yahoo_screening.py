"""Translate universal selections to market-wide Yahoo queries and bounded residual scans.

The UI never sees provider field names. AND conjuncts may be pushed independently;
OR/NOT remain whole unless every branch can be translated without changing null semantics.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from functools import lru_cache

from .query_models import BooleanExpression, Condition, Metadata, ResourceQuery, Result, TableBatch
from .tabular import clean, select
from .financial_fields import METRICS, values as metric_values

NUMERIC = {'price': 'intradayprice', 'change': 'intradaypricechange', 'pct_change': 'percentchange',
           'volume': 'dayvolume', 'market_cap': 'intradaymarketcap', 'pe': 'peratio.lasttwelvemonths'}
NUMERIC.update({name:spec.remote for name,spec in METRICS.items() if spec.remote})
ENUMS = {'region': 'region', 'exchange': 'exchange', 'sector': 'sector', 'industry': 'industry',
         'category': 'categoryname', 'currency': 'currency'}
KINDS = {'equity': 'EQUITY', 'etf': 'ETF', 'fund': 'MUTUALFUND', 'bonds': 'ETF',
         'crypto': 'CRYPTOCURRENCY', 'futures': 'FUTURE', 'cmdty': 'FUTURE', 'fx': 'CURRENCY', 'index': 'INDEX'}
LABELS = {'equity': 'US equities', 'etf': 'US ETFs', 'fund': 'US mutual funds', 'bonds': 'US bond ETFs (proxies)',
          'crypto': 'USD crypto pairs', 'futures': 'futures (commodity and financial contracts)', 'cmdty': 'futures (commodity and financial contracts)',
          'fx': 'FX pairs', 'index': 'world indices'}


def combine(operator, children):
    children = [c for c in children if c is not None]
    return None if not children else children[0] if len(children) == 1 else {'operator': operator, 'operands': children}


def leaf(operator, field, value):
    return {'operator': operator, 'operands': [field, value]}


@lru_cache(maxsize=1)
def bond_categories():
    import yfinance as yf
    node = yf.PREDEFINED_SCREENER_QUERIES['bond_etfs']['query'].to_dict()
    group = next(c for c in node['operands'] if c['operator'] == 'OR')
    return tuple(c['operands'][1] for c in group['operands'])


@lru_cache(maxsize=8)
def capabilities(kind):
    import yfinance as yf
    cls = {'EQUITY': yf.EquityQuery, 'ETF': yf.ETFQuery, 'MUTUALFUND': yf.FundQuery}.get(kind)
    if cls is None:
        return {**NUMERIC, **ENUMS}, {}, {}
    query = cls('GTE', ['intradayprice', 0])
    valid = set().union(*query.valid_fields.values())
    filters = {name: field for name, field in {**NUMERIC, **ENUMS}.items() if field in valid}
    return filters, query.valid_values, query.valid_values.get('exchange', {})


def canonical_value(field, value, allowed):
    values = allowed.get(field)
    if values is None:
        return value
    if isinstance(values, dict):
        values = set().union(*values.values())
    for candidate in values:
        if str(candidate).casefold() == str(value).casefold():
            return candidate
    raise ValueError(f'Unknown {field} value {value!r}; use a provider-supported value')


def translate(node, mapping, allowed):
    if node is None:
        return None
    if isinstance(node, BooleanExpression):
        if node.operator == 'NOT':
            child = node.children[0]
            if isinstance(child, Condition):
                inverse = {'=': '!=', '!=': '=', '<>': '=', '>': '<=', '>=': '<', '<': '>=', '<=': '>'}
                if child.operator not in inverse:
                    return None
                return translate(replace(child, operator=inverse[child.operator]), mapping, allowed)
            # De Morgan is valid for SQL unknown values too. Do not push fragments of a negated group.
            if child.operator in ('AND', 'OR'):
                return translate(BooleanExpression('OR' if child.operator == 'AND' else 'AND',
                                 tuple(BooleanExpression('NOT', (c,)) for c in child.children)), mapping, allowed)
            if child.operator == 'NOT':
                return translate(child.children[0], mapping, allowed)
            return None
        children = [translate(c, mapping, allowed) for c in node.children]
        return None if any(c is None for c in children) else combine(node.operator, children)
    field = mapping.get(node.field)
    if field is None:
        return None
    op, value = node.operator, node.value
    if node.field in ENUMS:
        if op not in ('=', 'IN'):
            return None
        values = value if op == 'IN' else (value,)
        return combine('OR', [leaf('EQ', field, canonical_value(field, v, allowed)) for v in values])
    if op == 'IS NOT NULL':
        return combine('OR', [leaf('GTE', field, 0), leaf('LT', field, 0)])
    if op == 'IN':
        return combine('OR', [leaf('EQ', field, v) for v in value])
    if op in ('!=', '<>'):
        return combine('OR', [leaf('LT', field, value), leaf('GT', field, value)])
    operators = {'=': 'EQ', '>': 'GT', '>=': 'GTE', '<': 'LT', '<=': 'LTE'}
    return leaf(operators[op], field, value) if op in operators else None


def split_predicate(node, mapping, allowed):
    translated = translate(node, mapping, allowed)
    if translated is not None or node is None:
        return translated, None
    if isinstance(node, BooleanExpression) and node.operator == 'AND':
        split = [split_predicate(c, mapping, allowed) for c in node.children]
        residual = tuple(r for _, r in split if r is not None)
        return combine('AND', [p for p, _ in split]), (None if not residual else residual[0] if len(residual) == 1 else BooleanExpression('AND', residual))
    return None, node


@dataclass(frozen=True)
class Plan:
    kind: str
    query: dict
    residual: object
    sort_field: str
    descending: bool
    ordered: bool
    label: str


def plan(query, dataset):
    name = query.dataset
    base_name = 'equity' if name.startswith('most_') else name
    kind = KINDS[base_name]
    mapping, allowed, _ = capabilities(kind)
    # Derived fields must never be pushed using a similarly named provider field.
    mapping = {f: remote for f, remote in mapping.items() if f in dataset.fields and not dataset.fields[f].compute}
    pushed, residual = split_predicate(query.where, mapping, allowed)
    def references(node, field):
        return node is not None and (any(references(c, field) for c in node.children) if isinstance(node, BooleanExpression) else node.field == field)
    if base_name in ('equity', 'etf', 'bonds'):
        scope = leaf('EQ', 'region', 'us')
        # An explicit region selection replaces the US default, preserving the SQL-style interface.
        if references(query.where, 'region') or references(query.where, 'exchange'):
            scope = None
    elif base_name == 'fund':
        scope = None if references(query.where, 'exchange') else leaf('EQ', 'exchange', 'NAS')
    elif base_name == 'crypto':
        scope = None if references(query.where, 'currency') else leaf('EQ', 'currency', 'USD')
    else:
        scope = combine('OR', [leaf('GTE', 'intradayprice', 0), leaf('LT', 'intradayprice', 0)])
    if base_name == 'bonds':
        import yfinance as yf
        categories = yf.PREDEFINED_SCREENER_QUERIES['bond_etfs']['query'].to_dict()
        # Use the published bond categories; retain the explicit region scope above.
        category = next(c for c in categories['operands'] if c['operator'] == 'OR')
        scope = combine('AND', [scope, category])
    if name in ('most_gainers', 'most_losers'):
        scope = combine('AND', [scope, leaf('GT' if name == 'most_gainers' else 'LT', 'percentchange', 0)])
    primary = query.sort[0] if query.sort else None
    sort_mapping = {**NUMERIC, 'symbol': 'ticker'}
    remote_sort = sort_mapping.get(primary.field) if primary and not dataset.fields[primary.field].compute else None
    ordered = primary is None or remote_sort is not None
    label = LABELS[base_name]
    if base_name in ('equity', 'etf', 'bonds') and (references(query.where, 'region') or references(query.where, 'exchange')):
        label = label.replace('US ', 'region/exchange-filtered ')
    elif base_name == 'fund' and references(query.where, 'exchange'):
        label = 'exchange-filtered mutual funds'
    elif base_name == 'crypto' and references(query.where, 'currency'):
        label = 'currency-filtered crypto pairs'
    wire_query = combine('AND', [scope, pushed]) or combine('OR', [leaf('GTE', 'intradayprice', 0), leaf('LT', 'intradayprice', 0)])
    return Plan(kind, wire_query, residual, remote_sort or 'ticker',
                primary.descending if remote_sort else False, ordered, 'Yahoo ' + label)


def fetch_page(params):
    """Blocking transport, called only by Ingestion's paced worker."""
    import yfinance as yf
    kind = params['quote_type']
    cls = {'EQUITY': yf.EquityQuery, 'ETF': yf.ETFQuery, 'MUTUALFUND': yf.FundQuery}.get(kind)
    if cls is not None:
        def convert(node):
            values = node['operands']
            return cls(node['operator'], [convert(v) for v in values] if node['operator'] in ('AND', 'OR') else values)
        data = yf.screen(convert(params['query']), offset=params['offset'], size=params['size'],
                         sortField=params['sort_field'], sortAsc=not params['descending'])
    else:
        # Yahoo exposes these asset screeners beyond yfinance's three public query classes.
        # Keep that wire protocol in the adapter; it never enters the universal request model.
        from yfinance.data import YfData
        body = {'quoteType': kind, 'query': params['query'], 'offset': params['offset'], 'size': params['size'],
                'sortField': params['sort_field'], 'sortType': 'DESC' if params['descending'] else 'ASC',
                'userId': '', 'userIdType': 'guid'}
        response = YfData().post('https://query1.finance.yahoo.com/v1/finance/screener', data=json.dumps(body),
                                 params={'corsDomain': 'finance.yahoo.com', 'formatted': 'false', 'lang': 'en-US', 'region': 'US'})
        response.raise_for_status()
        payload = response.json().get('finance', {})
        if payload.get('error') or not payload.get('result'):
            raise ValueError('Yahoo screening returned an error or an invalid response')
        data = payload['result'][0]
    if not isinstance(data, dict) or not isinstance(data.get('quotes'), list) or not isinstance(data.get('total'), int):
        raise ValueError('Yahoo screening returned an invalid page')
    return data


def rows_from_page(data, kind, meta):
    from .screening import quote_row
    _, _, exchanges = capabilities(kind)
    regions = {exchange: region for region, names in exchanges.items() for exchange in names}
    rows = []
    for item in data.get('quotes', []):
        symbol = item.get('symbol')
        if not symbol or item.get('quoteType', kind) != kind:
            continue
        previous = item.get('regularMarketPreviousClose')
        if previous is None and item.get('regularMarketPrice') is not None and item.get('regularMarketChange') is not None:
            previous = item['regularMarketPrice'] - item['regularMarketChange']
        quote = dict(symbol=symbol, price=item.get('regularMarketPrice'), prev_close=previous,
                     volume=item.get('regularMarketVolume'), day_low=item.get('regularMarketDayLow'),
                     day_high=item.get('regularMarketDayHigh'), ts=item.get('regularMarketTime'),
                     fetched=meta.fetched_at, source='yf', stale=meta.freshness != 'fresh')
        row = quote_row(symbol, item.get('longName') or item.get('shortName') or symbol, kind, quote)
        row.update(change=item.get('regularMarketChange', row['change']), pct_change=item.get('regularMarketChangePercent', row['pct_change']),
                   market_cap=item.get('marketCap'), pe=item.get('trailingPE'), exchange=item.get('exchange'),
                   currency=item.get('currency'), sector=item.get('sector'), industry=item.get('industry'),
                   category=item.get('categoryName') or item.get('category'),
                   region=regions.get(item.get('exchange')))
        row.update(metric_values(item,quote=True))
        rows.append(row)
    return rows


async def execute(service, query, dataset, required, force, stored_only):
    await service.initialize()
    query_plan = await asyncio.to_thread(plan, query, dataset)
    max_pages = max(1, min(40, int(service.ingestion.cfg.get('screener', 'max_pages', 8))))
    budget = max(1,min(100,int(service.ingestion.cfg.get('screener','max_enrich_symbols',25))))
    attempted = set()
    def expression_fields(node):
        if node is None:
            return set()
        return set().union(*(expression_fields(c) for c in node.children)) if isinstance(node,BooleanExpression) else {node.field}
    local_fields = expression_fields(query_plan.residual)
    if not query_plan.ordered or len(query.sort) > 1:
        local_fields.update(s.field for s in query.sort)
    # Derived fields inherit their metric dependencies through normalize()'s required order.
    def dependencies(names):
        result = set(names)
        for name in list(names):
            result.update(dependencies(dataset.fields[name].dependencies))
        return result
    local_fields = dependencies(local_fields)
    residual = replace(query, where=query_plan.residual)
    if query_plan.ordered and len(query.sort) <= 1:
        # The provider ranked the whole universe. A missing value in its quote payload
        # must not move that security behind a lower-ranked, more fully populated row.
        residual = replace(residual,sort=())
    simple = query_plan.residual is None and not query.search and len(query.sort) <= 1 and query_plan.ordered
    page_size = min(250, max(50, ((query.limit + 49) // 50) * 50)) if simple else 250
    rows, inputs, seen = [], [], set()
    total, exhausted, sufficient, ordered, failed = 0, False, False, query_plan.ordered, ''
    result_data = None
    offset = 0
    evaluable = []
    for page in range(max_pages):
        request = ResourceQuery('yf', 'market-screen', {'quote_type': query_plan.kind, 'query': query_plan.query,
                    'sort_field': query_plan.sort_field, 'descending': query_plan.descending,
                    'offset': offset, 'size': page_size})
        result = await service.query(request, force=force, stored_only=stored_only)
        inputs.append(result.meta)
        if result.data is None:
            failed = result.meta.reason or 'Screening page unavailable'
            break
        data = result.data
        if not isinstance(data.get('total'), int) or data['total'] < 0 or not isinstance(data.get('quotes'), list) or len(data['quotes']) > data['total']:
            failed = 'Invalid screening page'
            break
        if page and data['total'] != total:
            failed = 'Market result count changed during pagination; rerun to refresh'
            break
        total = data['total']
        offset += len(data['quotes'])
        new_rows = await asyncio.to_thread(rows_from_page, data, query_plan.kind, result.meta)
        page_symbols = set(seen)
        added = []
        for row in new_rows:
            if row['symbol'] not in page_symbols:
                added.append(row)
                page_symbols.add(row['symbol'])
        # Repeated/overlapping pages must never be counted as complete coverage.
        if len(added) != len(new_rows) or len(new_rows) != len(data['quotes']):
            failed = 'Yahoo returned overlapping or unrecognized records'
            rows.extend(added)
            break
        seen.update(r['symbol'] for r in added)
        rows.extend(added)
        if not added and len(rows) < total:
            failed = 'Yahoo stopped pagination before the reported total'
            break
        from .financial_data import enrich
        extra_inputs, covered = await enrich(service,rows,local_fields,budget,attempted,force=force,stored_only=stored_only)
        inputs.extend(extra_inputs)
        evaluable = rows[:covered]
        enrichment_limited = covered < len(rows)
        exhausted = len(rows) >= total and not enrichment_limited
        batch = TableBatch(tuple(evaluable), query_plan.label, total, exhausted)
        result_data = await asyncio.to_thread(select, residual, dataset, required, batch)
        enough = result_data.matched >= query.limit
        if enough and ordered:
            if len(query.sort) <= 1:
                sufficient = True
            else:
                # Secondary ordering needs the whole boundary tie group, possibly across pages.
                proof_query = replace(residual, fields=tuple(required))
                proof = await asyncio.to_thread(select, proof_query, dataset, required, batch)
                cutoff = proof.rows[-1].get(query.sort[0].field)
                tail = clean(evaluable[-1].get(query.sort[0].field), dataset.fields[query.sort[0].field].kind) if evaluable else None
                if isinstance(cutoff, str):
                    cutoff = cutoff.casefold()
                if isinstance(tail, str):
                    tail = tail.casefold()
                sufficient = cutoff is not None and (tail is None or (tail < cutoff if query.sort[0].descending else tail > cutoff))
        if exhausted or sufficient:
            break
        if enrichment_limited:
            failed = f'Financial lookup budget reached ({budget} securities), or financial data unavailable; narrow provider-supported WHERE filters'
            break
        # A stale page ends network paging; do not build a mixed-age ranking by fetching more pages.
        if result.meta.freshness != 'fresh' and not stored_only:
            failed = 'Stored screening page is stale; further network paging skipped'
            break
    meta = service.combine(inputs, 'yf') if inputs else Metadata('yf', reason='No screening pages')
    if not rows and failed:
        return Result(None, replace(meta, freshness='unavailable', reason=failed), tuple(inputs))
    if result_data is None or failed:
        if not evaluable and not local_fields:
            evaluable = rows
        result_data = await asyncio.to_thread(select, residual, dataset, required, TableBatch(tuple(evaluable), query_plan.label, total, exhausted))
    ranking_complete = (exhausted or sufficient) and not failed
    exact_count = exhausted or query_plan.residual is None and not query.search
    # Enrich only the displayed selection when filters/order already ran at the provider.
    selected_symbols = {r['symbol'] for r in result_data.rows if 'symbol' in r}
    if not selected_symbols:
        proof = await asyncio.to_thread(select,replace(residual,fields=tuple(dict.fromkeys((*query.fields,'symbol')))),dataset,required,
                                         TableBatch(tuple(evaluable),query_plan.label,total,exhausted))
        selected_symbols = {r['symbol'] for r in proof.rows}
    visible = [r for r in rows if r['symbol'] in selected_symbols]
    by_symbol = {r['symbol']:r for r in visible}
    # Keep selection order, even if the server sorted a field absent from its quote payload.
    selection = await asyncio.to_thread(select,replace(residual,fields=tuple(dict.fromkeys((*query.fields,'symbol')))),dataset,required,
                                         TableBatch(tuple(evaluable),query_plan.label,total,exhausted))
    visible = [by_symbol[r['symbol']] for r in selection.rows if r['symbol'] in by_symbol]
    extra_inputs, covered = await enrich(service,visible,required,budget,attempted,force=force,stored_only=stored_only)
    inputs.extend(extra_inputs)
    display = await asyncio.to_thread(select,replace(query,where=None,search=(),sort=()),dataset,required,
                                     TableBatch(tuple(visible),query_plan.label,len(visible)))
    result_data = replace(result_data,rows=display.rows,missing={f:sum(r.get(f) is None for r in display.rows) for f in query.fields if any(r.get(f) is None for r in display.rows)})
    meta = service.combine(inputs,'|'.join(dict.fromkeys(m.source for m in inputs)))
    if covered < len(visible):
        meta = replace(meta,reason='; '.join(filter(None,(meta.reason,f'Some displayed financial fields exceed the {budget}-security lookup budget or are unavailable'))))
    result_data = replace(result_data, total=total, available=len(rows), matched=total if exact_count and not exhausted else result_data.matched,
                          complete=exhausted and not failed, execution='yahoo', ranking_complete=ranking_complete, matched_exact=exact_count)
    if not ranking_complete:
        reason = failed or f'Scan budget reached ({max_pages} pages); narrow WHERE filters for complete rankings'
        meta = replace(meta, reason='; '.join(filter(None, (meta.reason, reason))))
        service.warn(reason)
    return Result(result_data, meta, tuple(inputs))
