"""Pure normalizers and discoverable schemas for the remaining Yahoo datasets.

All temporal semantics are explicit: report/period/event dates are calendar dates;
only known instants become UTC timestamps. First collection is the conservative
availability bound when Yahoo does not supply an original publication timestamp.
"""
from collections import Counter
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd

from . import field_values as s
from .serialization import key
from .tabular import Field, normalize_record

VERSION = 'yahoo-records-v5-' + s.VERSION
T = lambda title='': Field('text', title)
N = lambda title='', unit='': Field('number', title, unit=unit)
D = lambda title='': Field('date', title)
TS = lambda title='': Field('timestamp', title)
CURRENCY = Field('text', 'Currency', normalizer=s.currency, case_sensitive=True)
COMMON = dict(record_id=T('Record ID'), symbol=T('Ticker'), source=Field('text','Source',normalizer=s.source),
              normalization_version=T('Normalization Version'),
              freshness=T('Freshness'), fetched_at=TS('Fetched UTC'), available_at=TS('Available UTC'),
              availability_basis=T('Availability Basis'), observed_at=TS('Observed UTC'),
              observed_date=D('Observed Date'), source_timezone=T('Source Timezone'))

SCHEMAS = {
    'ownership': dict(COMMON, kind=T('Kind'), holder=T('Holder'), relation=T('Relation'), metric=T('Metric'),
        value=N('Value'), unit=T('Unit'), shares=N('Shares','shares'), weight=N('Weight %','percent'),
        weight_change=N('Weight Change %','percent'), currency=CURRENCY, report_date=D('Report Date'),
        transaction_date=D('Transaction Date'), direct_date=D('Direct Ownership Date'), indirect_date=D('Indirect Ownership Date'),
        direct_shares=N('Direct Shares','shares'), indirect_shares=N('Indirect Shares','shares'),
        ownership=T('Ownership'), description=T('Description'), url=T('URL'), period=T('Period')),
    'actions': dict(COMMON, kind=T('Kind'), event_date=D('Event Date'), value=N('Value'), unit=T('Unit'),
        currency=CURRENCY, split_ratio=N('New / Old Shares','ratio')),
    'shares': dict(COMMON, observation_date=D('Observation Date'), shares=N('Shares Outstanding','shares'),
        source_timestamp=TS('Provider Timestamp (not publication)'), unit=T('Unit')),
    'research': dict(COMMON, scope=T('Scope'), scope_key=T('Scope Key'), region=T('Region'), kind=T('Kind'),
        name=T('Name'), sector=T('Sector'), sector_key=T('Sector Key'), industry_key=T('Industry Key'), metric=T('Metric'),
        value=N('Value'), text=T('Text'), unit=T('Unit'), currency=CURRENCY, market_weight=N('Market Weight %','percent'),
        market_cap=N('Market Cap','currency'), price=N('Price','currency/share'), target_price=N('Target Price','currency/share'),
        ytd_return=N('YTD Return %','percent'), growth_estimate=N('Growth Estimate %','percent'), rating=T('Rating'),
        published_at=TS('Published UTC'), published_date=D('Published Date'), report_date=D('Report Date'), url=T('URL')),
    'events': dict(COMMON, kind=T('Kind'), name=T('Name'), company_name=T('Company'), event_name=T('Event Name'),
        event_date=D('Event Date'), event_at=TS('Event UTC'),
        scheduled_at=TS('Provider Scheduled UTC'), time_precision=T('Time Precision'), timing=T('Timing'),
        region=T('Region'), exchange=T('Exchange'), period=T('Reported Period Label'),
        period_end=D('Fiscal Period End'), filing_date=D('Filed Date'), amended_date=D('Amended Date'),
        currency=CURRENCY, estimate_eps=N('EPS Estimate','currency/share'), actual_eps=N('EPS Actual','currency/share'),
        surprise_pct=N('EPS Surprise %','percent'), market_cap=N('Market Cap','currency'),
        price_low=N('Offer Price Low','currency/share'), price_high=N('Offer Price High','currency/share'),
        price=N('Offer Price','currency/share'), shares=N('Shares','shares'), action=T('Action'),
        split_numerator=N('New Shares','shares'), split_denominator=N('Old Shares','shares'), split_ratio=N('New / Old Shares','ratio'),
        optionable=Field('boolean','Optionable'), actual=N('Actual'), expected=N('Expected'), prior=N('Prior'), revised=N('Revised'),
        actual_text=T('Actual (source)'), expected_text=T('Expected (source)'), prior_text=T('Prior (source)'),
        revised_text=T('Revised (source)'), actual_unit=T('Actual Unit'), expected_unit=T('Expected Unit'),
        prior_unit=T('Prior Unit'), revised_unit=T('Revised Unit'), unit=T('Economic Unit')),
    'articles': dict(COMMON, article_id=T('Article ID'), provider_id=T('Provider ID'), title=T('Title'),
        summary=T('Summary'), body=T('Body / Summary'), text_basis=T('Text Basis'), url=T('URL'), publisher=T('Publisher'),
        published_at=TS('Published UTC'), published_date=D('Published Date'), time_precision=T('Time Precision'),
        association_basis=T('Security Association'), query_symbol=T('Query Ticker'), language=T('Language')),
    'statements': dict(COMMON, statement=T('Statement'), period=T('Period'), period_end=D('Period End'),
        metric=T('Metric'), provider_field=T('Provider Field'), value=N('Value'), unit=T('Unit'), currency=CURRENCY,
        fiscal_year=N('Fiscal Year'), filing_date=D('Filed Date')),
    'company': dict(COMMON, kind=T('Kind'), metric=T('Metric'), field=T('Field'), value=N('Value'), text=T('Text'),
        flag=Field('boolean','Flag'),
        date_value=D('Date Value'), timestamp_value=TS('Timestamp Value'),
        unit=T('Unit'), currency=CURRENCY, name=T('Name'), event_date=D('Filing Date'), event_at=TS('Filing UTC'),
        source_timestamp=TS('Provider Timestamp (not publication)'), time_precision=T('Time Precision'),
        period_end=D('Fiscal Period End'), filing_type=T('Filing Type'), title=T('Title'), url=T('URL'),
        estimate_eps=N('EPS Estimate','currency/share'), actual_eps=N('EPS Actual','currency/share'),
        surprise_pct=N('EPS Surprise %','percent'), difference_eps=N('EPS Difference','currency/share')),
}


def snake(name):
    name = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
    return re.sub(r'[^a-z0-9]+', '_', re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', name).lower()).strip('_')


def timezone_name(value):
    value = s.raw(value)
    try:
        stamp = pd.Timestamp(value)
        return str(stamp.tzinfo) if stamp.tzinfo is not None and not pd.isna(stamp) else None
    except (ValueError, TypeError, OverflowError):
        return None


def base(meta):
    fetched = s.timestamp(meta.fetched_at)
    return dict(source='yf', normalization_version=VERSION, fetched_at=fetched, available_at=fetched,
                availability_basis='first_observed', freshness=meta.freshness.upper())


def finish(rows, dataset, identity):
    counts = Counter()
    out = []
    for row in rows:
        row = normalize_record(row, SCHEMAS[dataset])
        stable = key(tuple(row.get(name) for name in identity))
        occurrence = counts[stable]
        counts[stable] += 1
        row['record_id'] = stable if not occurrence else key((stable, occurrence))
        out.append(row)
    return out


def ownership(symbol, data, meta):
    common = dict(base(meta), symbol=symbol)
    currency = (data.get('price') or {}).get('currency')
    rows = []
    major = data.get('majorHoldersBreakdown') or {}
    for field, value in major.items():
        if field == 'maxAge':
            continue
        percent = field.endswith('PercentHeld')
        rows.append(dict(common, kind='major', metric=snake(field), value=s.number(value,100 if percent else 1),
                         unit='percent' if percent else 'count'))
    for module, kind in (('institutionOwnership','institutional'),('fundOwnership','fund')):
        for item in (data.get(module) or {}).get('ownershipList', []):
            rows.append(dict(common, kind=kind, holder=item.get('organization'), report_date=s.calendar_date(item.get('reportDate')),
                shares=s.number(item.get('position')), weight=s.number(item.get('pctHeld'),100),
                weight_change=s.number(item.get('pctChange'),100), value=s.number(item.get('value')),
                currency=currency, unit='currency'))
    for item in (data.get('insiderTransactions') or {}).get('transactions', []):
        rows.append(dict(common, kind='insider_transaction', holder=item.get('filerName'), relation=item.get('filerRelation'),
            transaction_date=s.calendar_date(item.get('startDate')), shares=s.number(item.get('shares')),
            value=s.number(item.get('value')), unit='currency', currency=currency, ownership=item.get('ownership'),
            description=item.get('transactionText') or item.get('moneyText'), url=item.get('filerUrl')))
    for item in (data.get('insiderHolders') or {}).get('holders', []):
        rows.append(dict(common, kind='insider_roster', holder=item.get('name'), relation=item.get('relation'),
            transaction_date=s.calendar_date(item.get('latestTransDate')), direct_date=s.calendar_date(item.get('positionDirectDate')),
            indirect_date=s.calendar_date(item.get('positionIndirectDate')), direct_shares=s.number(item.get('positionDirect')),
            indirect_shares=s.number(item.get('positionIndirect')), description=item.get('transactionDescription'), url=item.get('url')))
    purchases = data.get('netSharePurchaseActivity') or {}
    for field, value in purchases.items():
        if field in ('maxAge','period'):
            continue
        percent = 'Percent' in field
        rows.append(dict(common, kind='insider_purchase', metric=snake(field), period=purchases.get('period'),
            value=s.number(value,100 if percent else 1), unit='percent' if percent else 'count' if field.endswith('Count') else 'shares'))
    return finish(rows,'ownership',('symbol','kind','metric','holder','report_date','transaction_date','shares','value','description'))


def actions(symbol, data, meta):
    rows = []
    metadata = data.get('metadata') or {}
    for stamp, item in data['actions'].iterrows():
        for column, kind in (('Dividends','dividend'),('Stock Splits','split'),('Capital Gains','capital_gain')):
            value = s.number(item.get(column))
            if value is None or value == 0:
                continue
            rows.append(dict(base(meta), symbol=symbol, kind=kind, event_date=s.calendar_date(stamp),
                value=value, split_ratio=value if kind=='split' else None, unit='ratio' if kind=='split' else 'currency/share',
                currency=metadata.get('currency') if kind!='split' else None, source_timezone=metadata.get('exchangeTimezoneName') or timezone_name(stamp)))
    return finish(rows,'actions',('symbol','kind','event_date'))


def shares(symbol, data, meta):
    rows=[]
    for series in data:
        stamps, values = series.get('timestamp',[]), series.get('shares_out',[])
        if values and len(stamps)!=len(values):
            raise ValueError('Yahoo share timestamps and counts have different lengths')
        for stamp, value in zip(stamps,values):
            rows.append(dict(base(meta), symbol=symbol, observation_date=s.calendar_date(stamp),
                source_timestamp=s.timestamp(stamp), shares=s.number(value), unit='shares', source_timezone='UTC'))
    # Share-count timestamps identify dated observations, not publication instants.
    unique={(r['symbol'],r['source_timestamp']):r for r in rows}
    return finish(unique.values(),'shares',('symbol','source_timestamp'))


def research(query, data, meta):
    common=dict(base(meta), scope=query.scope, scope_key=query.key, region=query.region,
                sector=data.get('sectorName'),sector_key=data.get('sectorKey'))
    rows=[]
    overview=data.get('overview') or {}
    for field, value in overview.items():
        unit='percent' if field=='marketWeight' else 'currency' if field=='marketCap' else 'count' if field in ('companiesCount','industriesCount','employeeCount') else 'provider_value'
        number=s.number(value,100 if unit=='percent' else 1)
        rows.append(dict(common,kind='overview',name=data.get('name'),symbol=data.get('symbol'),metric=snake(field),
                         value=number,text=s.text(s.raw(value)) if number is None else None,unit=unit))
    for block,kind in (('performance','performance'),('performanceOverviewBenchmark','benchmark')):
        for field,value in (data.get(block) or {}).items():
            percent=field.endswith(('Percent','Return'))
            rows.append(dict(common,kind=kind,metric=snake(field),value=s.number(value,100 if percent else 1),
                             text=s.text(s.raw(value)),unit='percent' if percent else 'provider_value'))
    for block,kind in (('topCompanies','company'),('topPerformingCompanies','performing_company'),('topGrowthCompanies','growth_company'),
                       ('industries','industry'),('topETFs','etf'),('topMutualFunds','fund')):
        for item in data.get(block) or []:
            if not isinstance(item,dict) or item.get('name')=='All Industries':
                continue
            rows.append(dict(common,kind=kind,symbol=item.get('symbol'),name=item.get('name'),industry_key=item.get('key'),
                market_weight=s.number(item.get('marketWeight'),100),market_cap=s.number(item.get('marketCap')),
                price=s.number(item.get('lastPrice')),target_price=s.number(item.get('targetPrice')),
                ytd_return=s.number(item.get('ytdReturn'),100),growth_estimate=s.number(item.get('growthEstimate'),100),
                rating=item.get('rating'),currency=item.get('currency')))
    for item in data.get('researchReports') or []:
        rows.append(dict(common,kind='report',name=item.get('title'),url=item.get('link') or item.get('url'),
            published_at=s.timestamp(item.get('pubDate')),published_date=s.calendar_date(item.get('pubDate')),
            report_date=s.calendar_date(item.get('reportDate')) if isinstance(item.get('reportDate'),str) else None,
            source_timezone=timezone_name(item.get('pubDate')),text=item.get('summary')))
    return finish(rows,'research',('scope','scope_key','region','kind','symbol','metric','industry_key','name','url'))


def quantity(value):
    value=s.raw(value)
    if value is None:
        return None,'provider_value',None
    text=str(value).strip()
    match=re.fullmatch(r'([+-]?(?:\d[\d,]*\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([KMBT%]?)',text,re.I)
    if not match:
        return None,'provider_value',s.text(text)
    suffix=match[2].upper()
    return s.number(match[1].replace(',',''),{'K':1e3,'M':1e6,'B':1e9,'T':1e12}.get(suffix,1)), 'percent' if suffix=='%' else 'provider_value', text


def events(query, data, meta):
    rows=[]
    if query.kind=='dividends':
        for field,kind in (('Ex-Dividend Date','ex_dividend'),('Dividend Date','dividend_payment')):
            day=s.calendar_date(data.get(field))
            if day:
                rows.append(dict(base(meta),symbol=query.symbol,kind=kind,name=field,event_date=day,time_precision='date'))
        return finish(rows,'events',('symbol','kind','event_date'))
    for item in data['rows']:
        original=item.get('startdatetime')
        scheduled=s.timestamp(original)
        timing=s.text(item.get('startdatetimetype'))
        precision='session' if timing in ('BMO','AMC') else 'date' if query.kind in ('ipo','splits') else 'scheduled' if scheduled else 'unknown'
        row=dict(base(meta),kind=query.kind,symbol=item.get('ticker'),name=item.get('eventname') or item.get('econ_release') or item.get('companyshortname'),
            company_name=item.get('companyshortname'),event_name=item.get('eventname') or item.get('econ_release'),
            event_date=s.calendar_date(original),event_at=scheduled if precision=='scheduled' else None,scheduled_at=scheduled,
            time_precision=precision,timing=timing,source_timezone=timezone_name(original),region=item.get('country_code') or query.region,
            exchange=item.get('exchange_short_name'),period=item.get('period'),
            filing_date=s.calendar_date(item.get('filingdate')),amended_date=s.calendar_date(item.get('amendeddate')),
            currency=item.get('currencyname'),estimate_eps=s.number(item.get('epsestimate')),actual_eps=s.number(item.get('epsactual')),
            surprise_pct=s.number(item.get('epssurprisepct')) if item.get('epsactual') is not None else None,
            market_cap=s.number(item.get('intradaymarketcap')),price_low=s.number(item.get('pricefrom')),
            price_high=s.number(item.get('priceto')),price=s.number(item.get('offerprice')),shares=s.number(item.get('shares')),
            action=item.get('dealtype'),optionable=s.boolean(item.get('optionable')),
            split_numerator=s.number(item.get('share_worth')),split_denominator=s.number(item.get('old_share_worth')))
        if row['split_numerator'] is not None and row['split_denominator']:
            row['split_ratio']=row['split_numerator']/row['split_denominator']
        units=set()
        for source,field in (('after_release_actual','actual'),('consensus_estimate','expected'),('prior_release_actual','prior'),('originally_reported_actual','revised')):
            number,unit,text=quantity(item.get(source))
            row[field],row[field+'_text']=number,text
            row[field+'_unit']=unit if number is not None else None
            if number is not None:units.add(unit)
        row['unit']=next(iter(units)) if len(units)==1 else 'mixed' if units else None
        rows.append(row)
    return finish(rows,'events',('kind','symbol','name','event_date','scheduled_at','filing_date','period'))


def canonical_url(value):
    if not s.text(value):return None
    try:
        parts=urlsplit(value)
        if parts.scheme not in ('https','http') or not parts.netloc:return None
        query=urlencode([(k,v) for k,v in parse_qsl(parts.query) if not k.lower().startswith('utm_') and k.lower() not in ('guccounter','guce_referrer','guce_referrer_sig')])
        return urlunsplit((parts.scheme.lower(),parts.netloc.lower(),parts.path,query,''))
    except ValueError:return None


def articles(data, meta, requested_symbol=None):
    from .normalization import html_to_text
    rows={}
    for item in data or []:
        c=item.get('content') or item
        title=s.text(c.get('title'))
        if not title:continue
        url=canonical_url((c.get('canonicalUrl') or {}).get('url') or (c.get('clickThroughUrl') or {}).get('url') or c.get('link'))
        publication=c.get('pubDate') or c.get('providerPublishTime') or c.get('displayTime')
        published=s.timestamp(publication)
        published_date=s.calendar_date(publication)
        publisher=(c.get('provider') or {}).get('displayName') or c.get('publisher')
        provider_id=c.get('id') or item.get('uuid')
        article_id=key(('yf',url or provider_id or (publisher,title,published or published_date)))
        related=item.get('relatedTickers') or c.get('relatedTickers') or (c.get('finance') or {}).get('stockTickers') or []
        symbols=[v.get('symbol') if isinstance(v,dict) else v for v in related]
        symbols=[v for v in symbols if isinstance(v,str) and v.strip()]
        basis='provider' if symbols else 'security_feed' if requested_symbol else 'unassociated'
        symbols=symbols or [requested_symbol]
        summary=html_to_text(c.get('summary') or c.get('description') or '')
        body=c.get('body')
        body=html_to_text(body) if isinstance(body,str) else ''
        for symbol in symbols:
            row=dict(base(meta),symbol=symbol,article_id=article_id,provider_id=provider_id,title=title,url=url,
                summary=summary,body=body or summary,text_basis='provider_body' if body else 'provider_summary' if summary else 'title_only',
                publisher=publisher,published_at=published,published_date=published_date,
                time_precision='timestamp' if published else 'date' if published_date else 'unknown',
                source_timezone=timezone_name(publication),association_basis=basis,query_symbol=requested_symbol,
                language=c.get('lang') or c.get('language'))
            rows[(article_id,symbol)]=row
    return finish(rows.values(),'articles',('article_id','symbol'))


def statements(query, frame, meta, currency=None):
    rows=[]
    for column in frame.columns:
        for field,value in frame[column].items():
            value=s.number(value)
            if value is None:continue
            unit=('currency/share' if field in ('BasicEPS','DilutedEPS') else 'shares' if field in
                  ('BasicAverageShares','DilutedAverageShares','OrdinarySharesNumber','ShareIssued','TreasurySharesNumber') else
                  'ratio' if field=='TaxRateForCalcs' else 'currency')
            rows.append(dict(base(meta),symbol=query.symbol,statement=query.statement,period=query.period,
                period_end=s.calendar_date(column),metric=snake(field),provider_field=field,value=value,unit=unit,
                currency=currency if unit.startswith('currency') else None,fiscal_year=None,filing_date=None))
    return finish(rows,'statements',('symbol','statement','period','period_end','metric'))


def company(query,data,meta):
    rows=[]
    common=dict(base(meta),symbol=query.symbol,kind=query.dataset)
    if query.dataset=='earnings_history':
        currency=(data.get('financialData') or {}).get('financialCurrency')
        for item in (data.get('earningsHistory') or {}).get('history',[]):
            rows.append(dict(common,period_end=s.calendar_date(item.get('quarter')),actual_eps=s.number(item.get('epsActual')),
                estimate_eps=s.number(item.get('epsEstimate')),difference_eps=s.number(item.get('epsDifference')),
                surprise_pct=s.number(item.get('surprisePercent'),100),currency=currency))
    elif query.dataset=='filings':
        for item in (data.get('secFilings') or {}).get('filings',[]):
            rows.append(dict(common,event_date=s.calendar_date(item.get('date') or item.get('epochDate')),event_at=None,
                source_timestamp=s.timestamp(item.get('epochDate')),time_precision='date',
                filing_type=item.get('type'),title=item.get('title'),url=item.get('edgarUrl') or item.get('url')))
    else:
        modules=('assetProfile','summaryProfile','quoteType') if query.dataset=='profile' else ('esgScores',)
        blocks=[data.get(module) or {} for module in modules]
        if query.dataset=='profile':
            blocks.append({k:v for k,v in (data.get('price') or {}).items() if k in
                ('currency','exchange','exchangeName','quoteType','underlyingSymbol','longName','shortName','regularMarketTime')})
        for block in blocks:
            for field,value in block.items():
                if field=='maxAge' or isinstance(value,(list,dict)) and 'raw' not in value:continue
                number=s.number(value) if not isinstance(s.raw(value),str) else None
                text=s.text(s.raw(value)) if number is None else None
                unit='count' if field in ('fullTimeEmployees','peerCount') else 'percent' if field.endswith('Percentile') or field=='percentile' else 'milliseconds' if field=='gmtOffSetMilliseconds' else 'provider_value'
                day=stamp=None
                if field in ('firstTradeDateEpochUtc','regularMarketTime'):
                    stamp=s.timestamp(value);number=text=None;unit='timestamp'
                if field in ('dividendDate','exDividendDate'):
                    day=s.calendar_date(value);number=text=None;unit='date'
                if field=='currency':text=s.currency(value)
                if field=='quoteType':text=s.security_type(value)
                rows.append(dict(common,field=snake(field),metric=snake(field),value=number,flag=s.boolean(value),
                    text=text,unit=unit,date_value=day,timestamp_value=stamp,source_timezone='UTC' if stamp else None))
        if query.dataset=='profile':
            rows=list({row['field']:row for row in rows}.values())
    return finish(rows,'company',('symbol','kind','field','period_end','event_date','filing_type','url'))
