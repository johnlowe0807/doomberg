"""Typed, durable fundamentals, consensus estimates and reported fund exposures."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime, timezone

from .financial_fields import METRICS, raw, values
from .query_models import (FundamentalsQuery, EstimatesQuery, HoldingsQuery, FactsQuery,
                           ResourceQuery, Result, Metadata, TableBatch)
from .tabular import Dataset, Field, clean


def iso(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return None
    return str(value)


def provenance(meta):
    return dict(source=meta.source, fetched_at=iso(meta.fetched_at), observed_at=iso(meta.observed_at), freshness=meta.freshness.upper())


def number(value, scale=1):
    value = clean(raw(value), 'number')
    return value * scale if value is not None else None


def normalize_fundamentals(symbol, data, meta):
    flat = {k: raw(v) for module in data.values() if isinstance(module, dict) for k, v in module.items()}
    normalized = values(flat)
    records = []
    for name, spec in METRICS.items():
        period_end = flat.get('mostRecentQuarter') if spec.period in ('ttm', 'latest_balance', 'latest_quarter') else None
        # Statement period end is not its publication date. Yahoo usually does not
        # supply publication timestamps; retain the fetch time and leave that unknown.
        observed = flat.get('dateShortInterest') if spec.period == 'short_interest' else flat.get('regularMarketTime') if spec.period == 'snapshot' else None
        records.append(dict(symbol=symbol, metric=name, name=spec.title, value=normalized[name], unit=spec.unit,
                            period=spec.period, period_end=iso(period_end), fiscal_year=None, filing_date=None,
                            currency=(flat.get('currency') if spec.unit == 'currency/share' or spec.period == 'snapshot' else flat.get('financialCurrency'))
                                     if spec.unit.startswith('currency') else None,
                            is_estimate=spec.period == 'forward_estimate', **provenance(meta)))
        records[-1]['observed_at'] = iso(observed)
    return records


def normalize_estimates(symbol, data, meta):
    records = []
    financial = data.get('financialData') or {}
    common = {key: number(financial.get(spec.key)) for key, spec in METRICS.items() if key.startswith('target_') or key in ('analyst_count', 'recommendation_mean')}
    common['recommendation'] = financial.get('recommendationKey')
    for item in (data.get('earningsTrend') or {}).get('trend', []):
        row = dict(symbol=symbol, kind='estimate', period=item.get('period'), period_end=item.get('endDate'),
                   is_estimate=True, **common, **provenance(meta))
        for block, prefix, fields in (
            ('earningsEstimate', 'eps_', {'avg':'mean', 'low':'low', 'high':'high', 'yearAgoEps':'year_ago', 'numberOfAnalysts':'analysts', 'growth':'growth'}),
            ('revenueEstimate', 'revenue_', {'avg':'mean', 'low':'low', 'high':'high', 'yearAgoRevenue':'year_ago', 'numberOfAnalysts':'analysts', 'growth':'growth'}),
            ('epsTrend', 'eps_', {'current':'current', '7daysAgo':'7d_ago', '30daysAgo':'30d_ago', '60daysAgo':'60d_ago', '90daysAgo':'90d_ago'}),
            ('epsRevisions', 'eps_', {'upLast7days':'up_7d', 'upLast30days':'up_30d', 'downLast7Days':'down_7d', 'downLast30days':'down_30d'}),
        ):
            source = item.get(block) or {}
            row.update({prefix + key: number(source.get(remote), 100 if key == 'growth' else 1) for remote, key in fields.items()})
        row.update(currency=(item.get('earningsEstimate') or {}).get('earningsCurrency'),
                   revenue_currency=(item.get('revenueEstimate') or {}).get('revenueCurrency'),
                   stock_growth=number(item.get('growth'), 100))
        for module, field in (('industryTrend','industry_growth'), ('sectorTrend','sector_growth'), ('indexTrend','index_growth')):
            match = next((v for v in (data.get(module) or {}).get('estimates', []) if v.get('period') == item.get('period')), {})
            row[field] = number(match.get('growth'), 100)
        records.append(row)
    if any(v is not None for v in common.values()):
        records.append(dict(symbol=symbol, kind='targets', period=None, period_end=None, is_estimate=True,
                            currency=(data.get('price') or {}).get('currency'), **common, **provenance(meta)))
    for item in (data.get('recommendationTrend') or {}).get('trend', []):
        records.append(dict(symbol=symbol, kind='recommendation', period=item.get('period'), period_end=None,
                            is_estimate=True, **{k: number(item.get(remote)) for k, remote in
                            [('strong_buy','strongBuy'),('buy','buy'),('hold','hold'),('sell','sell'),('strong_sell','strongSell')]}, **provenance(meta)))
    for item in (data.get('upgradeDowngradeHistory') or {}).get('history', []):
        records.append(dict(symbol=symbol, kind='rating_change', period=None, period_end=None, date=iso(item.get('epochGradeDate')),
                            firm=item.get('firm'), from_grade=item.get('fromGrade'), to_grade=item.get('toGrade'),
                            action=item.get('action'), is_estimate=True, **provenance(meta)))
    return records


def normalize_holdings(symbol, data, meta):
    block = data.get('topHoldings') or {}
    profile = data.get('fundProfile') or {}
    currency = (data.get('price') or {}).get('currency')
    report_date = iso(block.get('reportDate') or block.get('holdingsDate'))
    records = []

    def append(kind, name, *, holding=None, weight=None, value=None, unit=None, reported=None):
        records.append(dict(fund=symbol, symbol=holding or symbol, kind=kind, name=name, weight=weight,
                            value=value, unit=unit or ('percent' if weight is not None else 'provider_value'),
                            currency=currency, report_date=reported or report_date, **provenance(meta)))

    for item in block.get('holdings', []):
        if item.get('symbol'):
            append('holding', item.get('holdingName') or item['symbol'], holding=item['symbol'],
                   weight=number(item.get('holdingPercent'), 100), reported=iso(item.get('reportDate')))
    for key, name in [('cashPosition','Cash'),('stockPosition','Equities'),('bondPosition','Bonds'),
                      ('preferredPosition','Preferred'),('convertiblePosition','Convertible'),('otherPosition','Other')]:
        value = number(block.get(key), 100)
        if value is not None:
            append('asset', name, weight=value)
    for key, kind in [('sectorWeightings','sector'), ('bondRatings','bond_rating')]:
        for item in block.get(key, []):
            for name, value in item.items():
                append(kind, name, weight=number(value, 100))
    for key, kind in [('bondHoldings','bond_metric'), ('equityHoldings','equity_metric')]:
        for name, value in (block.get(key) or {}).items():
            value = number(value)
            if value is not None:
                # Preserve provider ratios without guessing their inversion/scale.
                append(kind, name, value=value, unit='years' if name in ('duration','maturity') else 'provider_value')
    for name, value in (profile.get('feesExpensesInvestment') or {}).items():
        value = number(value)
        if value is not None:
            percent = name in ('annualReportExpenseRatio','annualHoldingsTurnover')
            append('fund_metric', name, value=value * 100 if percent else value, unit='percent' if percent else 'provider_value')
    return records


async def snapshot(service, query, *, force=False, stored_only=False):
    if not query.symbol or len(query.symbol) > 100:
        raise ValueError('Supply one security identifier')
    if isinstance(query, FundamentalsQuery):
        if query.source not in ('yahoo', 'sec'):
            raise ValueError('Fundamentals source must be yahoo or sec')
        if query.as_of:
            date.fromisoformat(query.as_of)
            if query.source != 'sec':
                raise ValueError('Historical filing-date queries require source=sec; Yahoo snapshots are current only')
        operation, kind, ttl, normalize = 'fundamental-data', 'fundamental_metrics', 21600, normalize_fundamentals
    elif isinstance(query, EstimatesQuery):
        operation, kind, ttl, normalize = 'estimate-data', 'analyst_estimates', 3600, normalize_estimates
    else:
        operation, kind, ttl, normalize = 'holdings-data', 'fund_holdings', 86400, normalize_holdings
    identity = 'financial-v3:' + service.identity(query)

    async def load(refresh):
        if isinstance(query, FundamentalsQuery) and query.source == 'sec':
            tickers = await service.query(ResourceQuery('sec','sec',{'url':'https://www.sec.gov/files/company_tickers.json'}), force=refresh,stored_only=stored_only)
            match = next((v for v in (tickers.data or {}).values() if v['ticker'].upper().replace('.','-') == query.symbol.upper().replace('.','-')), None)
            if match is None:
                raise ValueError('SEC ticker mapping unavailable for ' + query.symbol)
            result = await service.query(FactsQuery(int(match['cik_str'])), force=refresh,stored_only=stored_only)
            if result.data is None:
                return result
            from .sec_metrics import normalize as normalize_sec
            records = await asyncio.to_thread(normalize_sec, query.symbol, result.data, result.meta, query.as_of)
            inputs = (tickers.meta, result.meta)
            meta = service.combine(list(inputs), 'sec')
        else:
            result = await service.query(ResourceQuery('yf', operation, {'symbol':query.symbol}), force=refresh,stored_only=stored_only)
            if result.data is None:
                return result
            records = await asyncio.to_thread(normalize, query.symbol, result.data, result.meta)
            meta, inputs = result.meta, (result.meta,)
        return Result(records, meta, inputs)

    result = await service._dataset(identity, kind, 'sec' if isinstance(query, FundamentalsQuery) and query.source == 'sec' else 'yf',
                                    ttl, load, force=force, stored_only=stored_only)
    if stored_only and result.data is None:
        # A normalization upgrade can rebuild its domain records from retained source
        # documents without treating a missing derived snapshot as a reason to fetch.
        try:
            rebuilt = await load(False)
            if rebuilt.data is not None:
                try:
                    saved = await asyncio.to_thread(service.repository.put,identity,kind,rebuilt.meta.source,rebuilt.data,
                                                    rebuilt.meta.fetched_at,rebuilt.meta.observed_at)
                    result = Result(saved[0],replace(rebuilt.meta,version=saved[1].version,cache='store'),rebuilt.inputs)
                except Exception:
                    result = rebuilt
            else:
                result = rebuilt
        except ValueError as error:
            result = Result(None,replace(result.meta,reason=str(error)))
    if result.data is not None:
        return Result([dict(row, freshness=result.meta.freshness.upper()) for row in result.data], result.meta, result.inputs)
    return result


COMMON = {'symbol':Field('text','Ticker'), 'source':Field('text','Source'), 'freshness':Field('text','Freshness'),
          'observed_at':Field('text','Observed UTC'), 'fetched_at':Field('text','Fetched UTC')}


def validate_table(query, dataset):
    if set(query.parameters) - {'symbol','source','as_of'} or not query.parameters.get('symbol'):
        raise ValueError('Open this table for one security, e.g. /EST AAPL or /HOLD SPY')
    if query.dataset != 'fundamentals' and (query.parameters.get('source') or query.parameters.get('as_of')):
        raise ValueError('Source/date selection is available on METRICS only')
    if query.parameters.get('source', 'yahoo') not in ('yahoo','sec'):
        raise ValueError('Use METRICS ticker [SEC [YYYY-MM-DD]]')
    if query.parameters.get('as_of'):
        date.fromisoformat(query.parameters['as_of'])
        if query.parameters.get('source') != 'sec':
            raise ValueError('Historical metrics require SEC')


async def execute_table(service, query, dataset, required, force, stored_only):
    from .tabular import select
    symbol = query.parameters['symbol']
    if query.dataset == 'fundamentals':
        request = FundamentalsQuery(symbol, query.parameters.get('source','yahoo'), query.parameters.get('as_of'))
    elif query.dataset in ('estimates','analysts'):
        request = EstimatesQuery(symbol)
    else:
        request = HoldingsQuery(symbol)
    result = await service.query(request, force=force, stored_only=stored_only)
    if result.data is None:
        return result
    rows = result.data
    if query.dataset in ('estimates','analysts'):
        rows = [r for r in rows if (r['kind'] == 'estimate') == (query.dataset == 'estimates')]
    limited = query.dataset == 'holdings'
    label = f'{symbol} ' + ('reported holdings/allocations (positions may be top holdings only; report date may be unavailable)' if limited else query.dataset)
    batch = TableBatch(tuple(rows), label, len(rows), not limited)
    data = await asyncio.to_thread(select, query, dataset, required, batch)
    meta = result.meta
    if not rows:
        meta = replace(meta, reason=f'No {query.dataset} disclosed by this provider for {symbol}')
    return Result(data, meta, (result.meta,))


def register(service):
    fundamental = dict(COMMON, metric=Field('text','Metric'), name=Field('text','Name'), value=Field('number','Value'),
        unit=Field('text','Unit'), period=Field('text','Period'), period_end=Field('text','Period End'),
        fiscal_year=Field('number','Fiscal Year'), filing_date=Field('text','Filed'), currency=Field('text','Currency'),
        is_estimate=Field('boolean','Estimate'))
    estimate = dict(COMMON, kind=Field('text','Kind'), period=Field('text','Period'), period_end=Field('text','Period End'),
        currency=Field('text','EPS Currency'), revenue_currency=Field('text','Revenue Currency'),
        recommendation=Field('text','Recommendation'), is_estimate=Field('boolean','Estimate'),
        date=Field('text','Date'), firm=Field('text','Firm'), from_grade=Field('text','From'), to_grade=Field('text','To'), action=Field('text','Action'))
    numeric = ['eps_mean','eps_low','eps_high','eps_year_ago','eps_growth','eps_analysts','revenue_mean','revenue_low','revenue_high',
               'revenue_year_ago','revenue_growth','revenue_analysts','eps_current','eps_7d_ago','eps_30d_ago','eps_60d_ago','eps_90d_ago',
               'eps_up_7d','eps_up_30d','eps_down_7d','eps_down_30d','stock_growth','industry_growth','sector_growth','index_growth',
               'target_mean','target_median','target_low','target_high','analyst_count','recommendation_mean',
               'strong_buy','buy','hold','sell','strong_sell']
    estimate.update({name:Field('number',name.replace('_',' ').title(),unit='percent' if 'growth' in name else '') for name in numeric})
    holdings = dict(COMMON, fund=Field('text','Fund'), kind=Field('text','Kind'), name=Field('text','Name'),
        weight=Field('number','Weight %',unit='percent'), value=Field('number','Value'), unit=Field('text','Unit'),
        currency=Field('text','Currency'), report_date=Field('text','Reported'))
    definitions = {
        'fundamentals':(fundamental,('metric','value','unit','period','period_end','currency','source'),('symbol','metric','name')),
        'estimates':(estimate,('period','period_end','eps_mean','eps_growth','eps_analysts','revenue_mean','revenue_growth','currency'),('symbol','period')),
        'analysts':(estimate,('kind','date','firm','from_grade','to_grade','target_mean','recommendation'),('symbol','kind','firm','to_grade')),
        'holdings':(holdings,('kind','symbol','name','weight','value','unit','report_date'),('symbol','fund','name','kind')),
    }
    for name,(fields,defaults,search) in definitions.items():
        service.register_table(name, Dataset(fields,defaults,search,None,execute_table,validate_table))


async def enrich(service, rows, fields, budget, attempted, *, force=False, stored_only=False):
    """Bound per-security enrichment independently of page size; reuse complete cached snapshots."""
    wanted = set(fields) & set(METRICS)
    if any(str(f).startswith('financial_') for f in fields):
        wanted.add('revenue_ttm')
    metadata = []
    pending = []
    for row in rows:
        if row.get('_financial_loaded') or not any(row.get(f) is None for f in wanted):
            continue
        if row['symbol'] in attempted or len(attempted) >= budget:
            continue
        attempted.add(row['symbol'])
        pending.append(row)
    # Two paced requests at a time; each security uses one grouped quote-summary response.
    async def load(row):
        result = await service.query(FundamentalsQuery(row['symbol']),force=force,stored_only=stored_only)
        inputs = [result.meta]
        records = result.data
        if records is None:
            return inputs
        row['_financial_loaded'] = True
        missing_growth = {f for f in ('eps_growth_ttm','revenue_growth_ttm') if f in wanted and not any(r['metric'] == f and r.get('value') is not None for r in records)}
        if missing_growth:
            sec = await service.query(FundamentalsQuery(row['symbol'],'sec'),force=force,stored_only=stored_only)
            inputs.append(sec.meta)
            if sec.data is None:
                row['_financial_failed_fields'] = missing_growth
            records = [*records, *[r for r in sec.data or [] if r['metric'] in missing_growth and r.get('value') is not None]]
        for record in records:
            if row.get(record['metric']) is None:
                row[record['metric']] = record['value']
        available = [r for r in records if r.get('value') is not None]
        row.update(financial_source='|'.join(dict.fromkeys(r['source'] for r in available)),
                   financial_period_end=max((r.get('period_end') or '' for r in available),default='') or None,
                   financial_filing_date=max((r.get('filing_date') or '' for r in available),default='') or None,
                   financial_fetched_at=iso(result.meta.fetched_at),
                   financial_currency=next((r.get('currency') for r in available if r['metric'] == 'revenue_ttm'),None))
        return inputs
    for start in range(0,len(pending),2):
        results = await asyncio.gather(*(load(row) for row in pending[start:start+2]))
        metadata.extend(m for inputs in results for m in inputs)
    unresolved = [i for i,row in enumerate(rows) if (not row.get('_financial_loaded') and any(row.get(f) is None for f in wanted)) or
                  wanted.intersection(row.get('_financial_failed_fields',()))]
    return metadata, min(unresolved) if unresolved else len(rows)
