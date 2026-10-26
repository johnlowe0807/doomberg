"""Typed additional Yahoo datasets and shared table-query adapters; no terminal code."""
from __future__ import annotations

import asyncio
from dataclasses import MISSING, asdict, fields, replace
from datetime import date, timedelta
import re
import time

from . import field_values as s, yahoo_records as n
from .financial_data import schemas as financial_schemas
from .query_models import (OwnershipQuery, CorporateActionsQuery, SharesQuery, ResearchQuery, StatementsQuery,
    EventsQuery, NewsRecordsQuery, NewsArticleQuery, CompanyQuery, FinancialArchiveQuery, YahooPricesQuery,
    ResourceQuery, Result, Metadata, TableBatch)
from .serialization import key
from .tabular import Dataset, Field, select

REQUESTS = (OwnershipQuery, CorporateActionsQuery, SharesQuery, ResearchQuery, StatementsQuery, EventsQuery,
            NewsRecordsQuery, NewsArticleQuery, CompanyQuery, FinancialArchiveQuery, YahooPricesQuery)
TABLE_REQUESTS = {'ownership':OwnershipQuery, 'actions':CorporateActionsQuery, 'shares':SharesQuery,
    'research':ResearchQuery, 'statements':StatementsQuery, 'events':EventsQuery, 'articles':NewsRecordsQuery,
    'company':CompanyQuery, 'financial_archive':FinancialArchiveQuery, 'yahoo_prices':YahooPricesQuery}
TTL = {'ownership':21600,'actions':21600,'shares':21600,'research':21600,'statements':21600,
       'events':3600,'articles':300,'company':21600}
KINDS = {'ownership':'ownership_records','actions':'action_records','shares':'share_records',
         'research':'research_records','statements':'statement_records','events':'event_records',
         'articles':'article_records','company':'company_records'}


def identifier(value, name='symbol'):
    if not isinstance(value,str) or not value.strip() or len(value)>100:
        raise ValueError(f'Supply one nonempty {name} of at most 100 characters')
    return value.strip()


def day(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value) or s.calendar_date(value)!=value:
        raise ValueError('Date bounds require valid YYYY-MM-DD calendar dates')
    return value


def instant(value):
    normalized=s.timestamp(value)
    if normalized is None:
        raise ValueError('Timestamp bounds require ISO instants with Z or an explicit timezone offset')
    return normalized


def bounds(start,end,*,default=False):
    start=day(start) if start else date.today().isoformat() if default else None
    end=day(end) if end else (date.fromisoformat(start)+timedelta(days=7)).isoformat() if default else None
    if start and end and start>=end:raise ValueError('Start must precede the exclusive end')
    return start,end


def validate(query):
    if hasattr(query,'symbol') and query.symbol is not None:
        query=replace(query,symbol=identifier(query.symbol))
    if isinstance(query,(CorporateActionsQuery,SharesQuery)):
        start,end=bounds(query.start,query.end or ((date.today()+timedelta(days=1)).isoformat() if isinstance(query,SharesQuery) else None))
        query=replace(query,start=start,end=end)
    if isinstance(query,ResearchQuery):
        if query.scope not in ('sector','industry'):raise ValueError('Research scope must be sector or industry')
        if not isinstance(query.key,str) or not re.fullmatch(r'[\w&\-—]{1,100}',query.key):raise ValueError('Use the Yahoo sector/industry key')
        query=replace(query,region=identifier(query.region,'region').upper())
        if not re.fullmatch(r'[A-Z]{2}',query.region):raise ValueError('Region requires a two-letter ISO code')
    if isinstance(query,StatementsQuery):
        if query.statement not in ('income','balance','cashflow') or query.period not in ('annual','quarterly','ttm'):
            raise ValueError('Statements require income/balance/cashflow and annual/quarterly/ttm')
        if query.statement=='balance' and query.period=='ttm':raise ValueError('A balance sheet is an instant, not a TTM flow')
    if isinstance(query,EventsQuery):
        if query.kind not in ('earnings','ipo','economic','splits','dividends'):raise ValueError('Unknown event kind')
        if type(query.max_pages) is not int or not 1<=query.max_pages<=20:raise ValueError('Event max_pages must be 1–20')
        if query.kind=='dividends' and not query.symbol:raise ValueError('Dividend calendar needs a symbol')
        if query.kind=='economic' and query.symbol:raise ValueError('Economic releases do not have security symbols')
        if query.region and query.kind not in ('earnings','economic'):raise ValueError('Region filtering is supported for earnings/economic calendars only')
        if query.region is not None and (not isinstance(query.region,str) or not re.fullmatch(r'[A-Za-z]{2}',query.region)):
            raise ValueError('Region requires a two-letter ISO code')
        start,end=bounds(query.start,query.end,default=True)
        if (date.fromisoformat(end)-date.fromisoformat(start)).days>366:raise ValueError('Event windows are bounded to 366 days')
        query=replace(query,start=start,end=end,region=(query.region or ('US' if query.kind=='earnings' else '')).upper() or None)
    if isinstance(query,(NewsRecordsQuery,YahooPricesQuery)):
        if not isinstance(query.symbols,(tuple,list)) or len(query.symbols)>(25 if isinstance(query,NewsRecordsQuery) else 100):
            raise ValueError('Supply a bounded list of symbols (news 25; prices 100)')
        symbols=tuple(dict.fromkeys(identifier(v) for v in query.symbols))
        query=replace(query,symbols=symbols)
        if type(query.limit) is not int or not 1<=query.limit<=10000:raise ValueError('Limit must be 1–10000')
        for value in (query.start,query.end):
            if value:day(value) if isinstance(query,NewsRecordsQuery) and len(value)==10 else instant(value)
        if query.start and query.end:
            # Explicit UTC boundaries, without assigning unknown event times to date-only rows.
            convert=lambda v: v+'T00:00:00.000000+00:00' if len(v)==10 else instant(v)
            if convert(query.start)>=convert(query.end):raise ValueError('Start must precede the exclusive end')
        if isinstance(query,YahooPricesQuery) and not symbols:raise ValueError('Price queries need at least one symbol')
        if isinstance(query,NewsRecordsQuery) and (not isinstance(query.text,str) or len(query.text)>1000):raise ValueError('News text must be at most 1000 characters')
    if isinstance(query,CompanyQuery) and query.dataset not in ('profile','sustainability','filings','earnings_history'):
        raise ValueError('Unknown company dataset')
    if isinstance(query,FinancialArchiveQuery):
        if query.dataset not in ('fundamentals','estimates','holdings'):raise ValueError('Unknown financial archive dataset')
        if type(query.limit) is not int or not 1<=query.limit<=10000:raise ValueError('Archive limit must be 1–10000')
        query=replace(query,source=s.source(query.source),as_of=instant(query.as_of) if query.as_of else None)
        if query.source!='yf':raise ValueError('This archive collects Yahoo/yfinance snapshots only')
    if isinstance(query,NewsArticleQuery):identifier(query.article_id,'article ID')
    return query


async def normalized(service, dataset, request, load, *, force=False, stored_only=False):
    identity=n.VERSION+':'+dataset+':'+service.identity(request)
    result=await service._dataset(identity,KINDS[dataset],'yf',TTL[dataset],load,force=force,stored_only=stored_only)
    if stored_only and result.data is None:
        # Rebuild from retained provider documents after a normalization-version change.
        rebuilt=await load(False)
        if rebuilt.data is not None:
            try:
                saved=await asyncio.to_thread(service.repository.put,identity,KINDS[dataset],'yf',rebuilt.data,
                    rebuilt.meta.fetched_at,rebuilt.meta.observed_at,complete=rebuilt.meta.complete,coverage=rebuilt.meta.coverage,reason=rebuilt.meta.reason)
                result=Result(saved[0],replace(rebuilt.meta,version=saved[1].version,cache='store'),rebuilt.inputs)
            except Exception:
                result=Result(rebuilt.data,replace(rebuilt.meta,version=key(rebuilt.data),cache='session-only',
                    reason='; '.join(filter(None,(rebuilt.meta.reason,'Normalization persistence failed; using session-only records')))),rebuilt.inputs)
        else:result=rebuilt
    if result.data is not None:
        now=time.time()
        rows=[]
        for row in result.data:
            fetched=s.timestamp(row.get('fetched_at'))
            age=now-pd_epoch(fetched) if fetched else float('inf')
            rows.append(dict(row,freshness='STALE' if result.meta.freshness!='fresh' or age>=TTL[dataset] else row.get('freshness','FRESH')))
        meta=result.meta
        if rows and any(row.get('freshness')=='STALE' for row in rows) and meta.freshness=='fresh':
            meta=replace(meta,freshness='stale',reason=meta.reason or 'Includes retained observations beyond their refresh policy')
        result=Result(rows,meta,result.inputs)
    return result


def pd_epoch(stamp):
    from datetime import datetime
    return datetime.fromisoformat(stamp).timestamp()


async def snapshot(service, query, *, force=False, stored_only=False):
    query=validate(query)
    await service.initialize()
    if isinstance(query,FinancialArchiveQuery):
        rows,untruncated=await asyncio.to_thread(service.repository.financial_captures,query.symbol,query.dataset,query.source,query.as_of,query.limit)
        meta=Metadata(query.source,fetched_at=max((pd_epoch(r['captured_at']) for r in rows),default=0),version=key((n.VERSION,rows)),
            freshness='fresh' if rows else 'unavailable',cache='observations',complete=False,
            coverage='Locally collected snapshots only; no earlier historical archive. Bounded retained history; availability begins at capture.',
            reason='Archive row limit reached' if not untruncated else '' if rows else 'No captures available at the requested cutoff')
        return Result(rows,meta)
    if isinstance(query,NewsArticleQuery):
        row=await asyncio.to_thread(service.repository.article,query.article_id)
        return Result(row,Metadata('yf',fetched_at=pd_epoch(row['fetched_at']) if row else 0,version=key(row),
            freshness='stale' if row else 'unavailable',cache='observations',complete=False,
            coverage='Retained Yahoo body or summary only; no publisher fetch or sentiment analysis',
            reason='' if row else 'Article not present in the retained Yahoo news store'))
    if isinstance(query,YahooPricesQuery):
        from .yahoo_stream import stored_prices
        return await stored_prices(service,query)
    if isinstance(query,EventsQuery):return await event_snapshot(service,query,force=force,stored_only=stored_only)
    if isinstance(query,NewsRecordsQuery):return await news_snapshot(service,query,force=force,stored_only=stored_only)
    if isinstance(query,OwnershipQuery):
        dataset,operation,params,normalizer,coverage='ownership','plug-ownership',{'symbol':query.symbol},lambda raw,meta:n.ownership(query.symbol,raw,meta),'Reported ownership/insider lists and aggregates; not a full ownership register'
    elif isinstance(query,CorporateActionsQuery):
        dataset,operation,params,normalizer,coverage='actions','plug-actions',{'symbol':query.symbol},lambda raw,meta:n.actions(query.symbol,raw,meta),'Yahoo action history, retaining provider dividend adjustments and supported coverage'
    elif isinstance(query,SharesQuery):
        end=query.end or (date.today()+timedelta(days=1)).isoformat()
        dataset,operation,params,normalizer,coverage='shares','plug-shares',{'symbol':query.symbol,'start':query.start or '2000-01-01','end':end},lambda raw,meta:n.shares(query.symbol,raw,meta),'Provider-dated share observations; collection dates are not original publication dates'
    elif isinstance(query,ResearchQuery):
        dataset,operation,params,normalizer,coverage='research','plug-research',asdict(query),lambda raw,meta:n.research(query,raw,meta),'Provider sector/industry overview and curated top lists, not complete constituent universes'
    elif isinstance(query,StatementsQuery):
        dataset,operation,params,normalizer,coverage='statements','plug-statement',asdict(query),None,'Reported Yahoo statement periods; original filing/publication dates are unknown'
    elif isinstance(query,CompanyQuery):
        dataset,operation,params,normalizer,coverage='company','fundamental-data' if query.dataset=='profile' else 'plug-company',{'symbol':query.symbol},lambda raw,meta:n.company(query,raw,meta),'Provider company data only; unavailable fields and dates remain null'
        if query.dataset!='profile':params['dataset']=query.dataset
    else:raise ValueError('Unsupported additional Yahoo dataset')
    request=ResourceQuery('yf',operation,params)
    async def load(refresh):
        raw=await service.query(request,force=refresh,stored_only=stored_only)
        if raw.data is None:return raw
        inputs=[raw.meta]
        if dataset=='statements':
            currency=await service.query(ResourceQuery('yf','fundamental-data',{'symbol':query.symbol}),force=refresh,stored_only=stored_only)
            inputs.append(currency.meta)
            flat={k:s.raw(v) for module in (currency.data or {}).values() if isinstance(module,dict) for k,v in module.items()}
            rows=await asyncio.to_thread(n.statements,query,raw.data,raw.meta,flat.get('financialCurrency'))
        else:rows=await asyncio.to_thread(normalizer,raw.data,raw.meta)
        reason=raw.meta.reason
        if dataset=='ownership':
            absent=set(('majorHoldersBreakdown','institutionOwnership','fundOwnership','insiderTransactions','insiderHolders','netSharePurchaseActivity'))-raw.data.keys()
            if absent:reason='; '.join(filter(None,(reason,'Provider omitted: '+', '.join(sorted(absent)))))
        if not rows:reason='; '.join(filter(None,(reason,'No normalized records disclosed by Yahoo for this dataset')))
        meta=replace(service.combine(inputs,'yf') if dataset=='statements' else raw.meta,
                     fetched_at=raw.meta.fetched_at,
                     complete=False,coverage=coverage,reason='; '.join(dict.fromkeys(filter(None,(
                         reason,*(m.reason for m in inputs))))))
        return Result(rows,meta,tuple(inputs))
    result=await normalized(service,dataset,request,load,force=force,stored_only=stored_only)
    if isinstance(query,SharesQuery):
        retained=await asyncio.to_thread(service.repository.record_observations,'share_records',symbol=query.symbol,
                                         start=query.start,end=query.end,date_field='observation_date',prefix=n.VERSION+':shares:')
        if retained and result.data is None:
            result=retained_result(retained,result.meta,'Captured share observations from other stored windows; no refresh performed')
        elif retained and result.data is not None:
            all_rows={r['record_id']:r for r in retained}
            for row in result.data:
                old=all_rows.get(row['record_id'])
                if old is None or row['fetched_at']>=old['fetched_at']:all_rows[row['record_id']]=row
            combined=list(all_rows.values())
            for row in combined:
                if time.time()-pd_epoch(row['fetched_at'])>=TTL['shares']:row['freshness']='STALE'
            meta=replace(result.meta,version=key((n.VERSION,combined,result.meta.version)),
                         freshness='stale' if any(r.get('freshness')=='STALE' for r in combined) else result.meta.freshness)
            result=Result(combined,meta,result.inputs+(Metadata('yf',fetched_at=min(pd_epoch(r['fetched_at']) for r in retained),
                version=key(retained),freshness=meta.freshness,cache='observations'),))
    if isinstance(query,(CorporateActionsQuery,SharesQuery)) and result.data is not None:
        field='event_date' if dataset=='actions' else 'observation_date'
        rows=[r for r in result.data if (not query.start or r.get(field) and r[field]>=query.start) and
                                      (not query.end or r.get(field) and r[field]<query.end)]
        return Result(rows,replace(result.meta,version=key((n.VERSION,query,rows,result.meta.version))),result.inputs)
    return result


async def event_snapshot(service,query,*,force,stored_only):
    identity=ResourceQuery('yf','plug-calendar',asdict(query))
    async def load(refresh):
        if query.kind=='dividends':
            raw=await service.query(ResourceQuery('yf','calendar',{'symbol':query.symbol}),force=refresh,stored_only=stored_only)
            if raw.data is None:return raw
            rows=await asyncio.to_thread(n.events,query,raw.data,raw.meta)
            rows=[r for r in rows if query.start<=r['event_date']<query.end]
            return Result(rows,replace(raw.meta,complete=False,coverage='Per-security provider dividend schedule only'),(raw.meta,))
        rows,inputs,seen=[],[],set()
        raw_seen=set()
        total=None
        complete=False
        reason=''
        offset=0
        for _ in range(query.max_pages):
            params=dict(asdict(query),offset=offset)
            params.pop('max_pages')
            raw=await service.query(ResourceQuery('yf','plug-calendar',params),force=refresh,stored_only=stored_only)
            inputs.append(raw.meta)
            if raw.data is None:
                reason=raw.meta.reason or 'Calendar page unavailable'
                break
            page=raw.data
            if type(page.get('total')) is not int or page['total']<0 or not isinstance(page.get('rows'),list) or len(page['rows'])>page['total']:
                raise ValueError('Yahoo returned an invalid calendar page/count')
            if total is not None and page['total']!=total:
                reason='Calendar count changed during pagination; coverage is incomplete'
                break
            total=page['total']
            fingerprints=[key(r) for r in page['rows']]
            duplicate=(len(set(fingerprints))!=len(fingerprints) or any(v in raw_seen for v in fingerprints)
                       )
            unique_rows={fingerprint:row for fingerprint,row in zip(fingerprints,page['rows']) if fingerprint not in raw_seen}
            normalized_rows=await asyncio.to_thread(n.events,query,dict(page,rows=list(unique_rows.values())),raw.meta)
            duplicate=duplicate or any(r['record_id'] in seen for r in normalized_rows)
            raw_seen.update(fingerprints)
            for row in normalized_rows:
                if row['record_id'] not in seen:rows.append(row);seen.add(row['record_id'])
            offset+=len(page['rows'])
            if duplicate:
                reason='Repeated/overlapping calendar pages; coverage is incomplete'
                break
            if offset>=total:
                complete=True
                break
            if len(page['rows'])<100:
                reason='Calendar returned a short page before its reported total'
                break
            if raw.meta.freshness!='fresh' and not stored_only:
                reason='Stored calendar page is stale; further network paging skipped'
                break
        if not complete and not reason:reason=f'Calendar page cap reached ({query.max_pages} pages); {len(rows)} of {total} provider rows'
        meta=service.combine(inputs,'yf') if inputs else Metadata('yf')
        coverage=f'Yahoo {query.kind} calendar, {query.region or "provider-wide"}, [{query.start},{query.end}); {len(rows)} of {total if total is not None else "unknown"} rows'
        if not rows and reason and total is None:return Result(None,replace(meta,reason=reason,complete=False,coverage=coverage))
        return Result(rows,replace(meta,reason='; '.join(filter(None,(meta.reason,reason))),complete=complete,coverage=coverage),tuple(inputs))
    result=await normalized(service,'events',identity,load,force=force,stored_only=stored_only)
    if result.data is None:
        retained=await asyncio.to_thread(service.repository.record_observations,'event_records',symbol=query.symbol,
            kind=query.kind if query.kind!='dividends' else None,start=query.start,end=query.end,date_field='event_date',prefix=n.VERSION+':events:')
        if query.kind=='dividends':retained=[r for r in retained if r['kind'] in ('ex_dividend','dividend_payment')]
        if query.region:retained=[r for r in retained if r.get('region')==query.region]
        if retained:result=retained_result(retained,result.meta,'Retained event observations from other stored windows; schedules may have changed')
    return result


def retained_result(rows,meta,reason):
    fetched=min(pd_epoch(r['fetched_at']) for r in rows)
    provenance=Metadata('yf',fetched_at=fetched,version=key((n.VERSION,rows)),freshness='stale',cache='observations',
        reason='; '.join(filter(None,(meta.reason,reason))),complete=False,coverage=reason)
    return Result([dict(r,freshness='STALE') for r in rows],provenance,(provenance,))


async def news_snapshot(service,query,*,force,stored_only):
    results=[]
    sources=[ResourceQuery('yf','plug-news',{'symbol':symbol,'limit':100}) for symbol in query.symbols]
    if not sources:sources=[ResourceQuery('yf','plug-search-news',{'text':query.text or 'markets','limit':100})]
    retained=await asyncio.to_thread(service.repository.articles,query.symbols,prefix=n.VERSION+':articles:')
    for request in sources:
        async def load(refresh,request=request):
            raw=await service.query(request,force=refresh,stored_only=stored_only)
            if raw.data is None:return raw
            rows=await asyncio.to_thread(n.articles,raw.data,raw.meta,request.params.get('symbol'))
            return Result(rows,replace(raw.meta,complete=False,coverage='Bounded Yahoo news discovery; not a full news archive',
                reason=raw.meta.reason or ('' if rows else 'Yahoo returned no news records')),(raw.meta,))
        results.append(await normalized(service,'articles',request,load,force=force,stored_only=stored_only))
    rows={}
    groups=[(retained,True),*((result.data or [],False) for result in results)]
    for records,from_archive in groups:
        for row in records:
            if query.text and (query.symbols or from_archive) and query.text.casefold() not in ((row.get('title') or '')+' '+(row.get('summary') or '')).casefold():continue
            include=True
            for bound,op in ((query.start,'start'),(query.end,'end')):
                if not bound:continue
                value=row.get('published_date') if len(bound)==10 else row.get('published_at')
                target=bound if len(bound)==10 else instant(bound)
                if not value or (value<target if op=='start' else value>=target):include=False
            if include:rows[(row['article_id'],row.get('symbol'))]=row
    ordered=sorted(rows.values(),key=lambda r:(r.get('published_at') or r.get('published_date') or '',r['article_id']),reverse=True)[:query.limit]
    meta=service.combine([r.meta for r in results],'yf')
    failed=any(r.meta.freshness!='fresh' for r in results)
    for row in ordered:
        if time.time()-pd_epoch(row['fetched_at'])>=TTL['articles'] or failed:
            row['freshness']='STALE'
    stale=failed or any(r.get('freshness')=='STALE' for r in ordered)
    if ordered:
        meta=replace(meta,freshness='stale' if stale else 'fresh',fetched_at=min(pd_epoch(r['fetched_at']) for r in ordered))
    inputs=tuple(r.meta for r in results)
    if ordered:inputs+=(replace(meta,version=key(ordered),cache='observations'),)
    return Result(ordered,replace(meta,version=key((n.VERSION,query,ordered)),complete=False,
        coverage='Bounded Yahoo feeds/search plus retained observations; rows can associate one article with several securities'),inputs)


def request_for_table(query):
    cls=TABLE_REQUESTS[query.dataset]
    allowed={f.name for f in fields(cls)}
    if set(query.parameters)-allowed:raise ValueError('Unknown dataset parameters: '+', '.join(sorted(set(query.parameters)-allowed)))
    parameters=dict(query.parameters)
    if cls is NewsRecordsQuery:parameters.setdefault('limit',10000)
    try:return validate(cls(**parameters))
    except TypeError as error:raise ValueError(f'{query.dataset} requires '+', '.join(f.name for f in fields(cls) if f.default is MISSING)) from error


def validate_table(query,dataset):request_for_table(query)


async def execute_table(service,query,dataset,required,force,stored_only):
    result=await service.query(request_for_table(query),force=force,stored_only=stored_only)
    if result.data is None:return result
    batch=TableBatch(tuple(result.data),result.meta.coverage or query.dataset,len(result.data),result.meta.complete is True)
    data=await asyncio.to_thread(select,query,dataset,required,batch)
    return Result(data,result.meta,result.inputs)


def register(service):
    defaults={
        'ownership':('symbol','kind','holder','metric','value','shares','weight','report_date'),
        'actions':('symbol','kind','event_date','value','unit','currency'),
        'shares':('symbol','observation_date','shares','available_at'),
        'research':('scope','scope_key','kind','symbol','name','metric','value','market_weight'),
        'events':('kind','symbol','name','event_date','event_at','time_precision','region'),
        'articles':('symbol','published_at','published_date','title','publisher','url'),
        'statements':('symbol','statement','period','period_end','metric','value','unit','currency'),
        'company':('symbol','kind','field','value','text','period_end','event_date'),
    }
    for name,schema in n.SCHEMAS.items():
        search=tuple(f for f in ('symbol','name','company_name','event_name','title','description','holder','metric','field','text','summary') if f in schema)
        service.register_table(name,Dataset(schema,defaults[name],search,None,execute_table,validate_table))
    archive={}
    for schema,_,_ in financial_schemas().values():archive.update(schema)
    archive.update(capture_id=n.T('Capture ID'),captured_at=n.TS('Captured UTC'),available_at=n.TS('Available UTC'),availability_basis=n.T('Availability Basis'))
    service.register_table('financial_archive',Dataset(archive,('symbol','captured_at','kind','metric','value'),('symbol','metric','name'),None,execute_table,validate_table))
    from .yahoo_stream import SCHEMA as prices
    service.register_table('yahoo_prices',Dataset(prices,('symbol','observed_at','received_at','price','currency','freshness'),('symbol','name'),None,execute_table,validate_table))
