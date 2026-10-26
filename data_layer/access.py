"""Normalized access to every structured source already used by the terminal.

Specialized presentations consume these same records. Source fields are retained separately
when a provider has fields without an established canonical name or unit.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, fields, replace
from datetime import date, datetime
import re

import numpy as np
import pandas as pd

from . import field_values as s
from .query_models import (TableQuery, TableBatch, Result, ResourceQuery, FactsQuery, FilingsQuery,
    Metadata, HistoryQuery, BarsQuery, StoredBarsQuery, SeriesQuery, QuotesQuery, NewsQuery, SearchQuery, ComparisonQuery, TextQuery)
from .tabular import Dataset, Field, normalize_record, select
from .serialization import key
from .yahoo_records import COMMON, T, N, D, TS, CURRENCY, snake

COMMON = dict(COMMON, available_date=D('Available Date'), currency=CURRENCY)
FACTS = dict(COMMON, cik=N('CIK','identifier'), namespace=T('Taxonomy'), concept=T('Concept'), metric=T('Metric'),
    provider_field=T('Provider Field'),metric_basis=T('Metric Naming Basis'),
    name=T('Label'), description=T('Description'), value=N('Value'), unit=T('Unit'), provider_unit=T('Provider Unit'),
    statement=T('Statement'), period=T('Period'), period_start=D('Period Start'), period_end=D('Period End'),
    is_forward_period=Field('boolean','Period Ends After Filing'),
    filing_date=D('Filed Date'), accession=T('Accession'), form=T('Form'), fiscal_year=N('Actual Fiscal Year','year'),
    fiscal_quarter=N('Actual Fiscal Quarter','quarter'),fiscal_period=T('Actual Fiscal Period'),
    filing_fiscal_year=N('Filing Context Fiscal Year','year'),filing_fiscal_period=T('Filing Context Fiscal Period'),frame=T('SEC Frame'))
SOURCE = dict(COMMON, path=T('Source Field Path'), row_index=N('Row Index','count'), field=T('Provider Field'),
    operation=T('Source Operation'),
    value=N('Numeric Value'), text=T('Text Value'), flag=Field('boolean','Boolean Value'),
    date_value=D('Calendar Value'), timestamp_value=TS('UTC Value'), value_type=T('Value Type'), unit=T('Unit'))
SCHEMAS = {
 'facts':FACTS, 'reporting':FACTS,
 'statement_values':dict(FACTS,period_label=T('Fiscal Period'),value_basis=T('Value Basis')),
 'prices':dict(COMMON, interval=T('Interval'), observation_date=D('Session Date'), open=N('Open','native_price'),
     high=N('High','native_price'), low=N('Low','native_price'), close=N('Close','native_price'),
     volume=N('Volume','provider_volume'), bad_volume=Field('boolean','Excluded Bad Volume'), provider_time=T('Provider Index')),
 'series':dict(COMMON, series=T('Series'), observation_date=D('Observation Date'), value=N('Value'), unit=T('Unit')),
 'quotes':dict(COMMON, price=N('Price','native_price'), previous_close=N('Previous Close','native_price'),
     change=N('Change','native_price'), pct_change=N('Change %','percent'), volume=N('Volume','provider_volume'),
     day_low=N('Day Low','native_price'), day_high=N('Day High','native_price')),
 'filings':dict(COMMON, cik=N('CIK','identifier'), accession=T('Accession'), filing_date=D('Filed Date'),
     period_end=D('Report Date'), form=T('Form'), document=T('Document'), description=T('Description'), items=T('Items'), url=T('URL')),
 'news':dict(COMMON, title=T('Title'), summary=T('Summary'), url=T('URL'), publisher=T('Publisher'), published_at=TS('Published UTC')),
 'options':dict(COMMON, underlying=T('Underlying'), expiry=D('Expiry'), side=T('Side'), contract_symbol=T('Contract'),
     strike=N('Strike','currency/share'), last_price=N('Last','currency/share'), bid=N('Bid','currency/share'), ask=N('Ask','currency/share'),
     change=N('Change','currency/share'), pct_change=N('Change %','percent'), volume=N('Volume','contracts'), open_interest=N('Open Interest','contracts'),
     implied_volatility=N('IV','ratio'), in_the_money=Field('boolean','In the Money'), last_trade_at=TS('Last Trade UTC'),
     contract_size=T('Contract Size'), field=T('Other Provider Field'), value=N('Other Numeric Value'), text=T('Other Text Value')),
 'expiries':dict(COMMON, expiry=D('Expiry')),
 'book':dict(COMMON, side=T('Side'), level=N('Level','count'), price=N('Price','currency/base'), size=N('Size','provider_size'), unit=T('Size Unit')),
 'auctions':dict(COMMON, auction_date=D('Auction Date'), security_type=T('Type'), security_term=T('Term'), cusip=T('CUSIP'),
     high_yield=N('High Yield %','percent'), high_discnt_rate=N('Discount Rate %','percent'), high_investment_rate=N('Investment Rate %','percent'),
     bid_to_cover_ratio=N('Bid/Cover','ratio'), offering_amt=N('Offering','currency')),
 'source_fields':SOURCE, 'profile_fields':SOURCE, 'documents':dict(COMMON, url=T('URL'), text=T('Text')),
 'search':dict(COMMON, name=T('Name'), type=Field('text','Type',normalizer=s.security_type), description=T('Description'),
    frequency=T('Frequency'),unit=T('Unit'),adjustment=T('Adjustment'),observation_start_date=D('Available Start'),observation_end_date=D('Available End')),
 'comparison':dict(COMMON, reference=T('Reference'), observation_date=D('Observation Date'), value=N('Value'), unit=T('Unit')),
 'statistics':dict(COMMON, range=T('Range'),interval=T('Interval'),metric=T('Metric'),value=N('Value'),unit=T('Unit')),
 'indicators':dict(COMMON,range=T('Range'),interval=T('Interval'),metric=T('Metric'),value=N('Value'),unit=T('Unit'),observation_date=D('Session Date')),
 'calendars':dict(COMMON,earnings_start_date=D('Earnings Window Start'),earnings_end_date=D('Earnings Window End'),
    dividend_date=D('Dividend Date'),ex_dividend_date=D('Ex-Dividend Date'),calculated_date=D('Calculation Date'),days=N('Days to Earnings','days'),
    eps_mean=N('EPS Mean','currency/share'),eps_low=N('EPS Low','currency/share'),eps_high=N('EPS High','currency/share'),
    revenue_mean=N('Revenue Mean','currency'),revenue_low=N('Revenue Low','currency'),revenue_high=N('Revenue High','currency')),
 'series_metadata':dict(COMMON,series=T('Series'),field=T('Field'),text=T('Text'),value=N('Value'),date_value=D('Calendar Value'),unit=T('Unit')),
}
SCHEMAS['options'].update(currency=CURRENCY,spot=N('Spot','currency/share'),risk_free_rate=N('Risk-Free Rate','ratio'),
    time_to_expiry=N('Model Time to Expiry (years)','years'),delta=N('Delta','ratio'),gamma=N('Gamma','1/price'),
    theta=N('Theta / Calendar Day','currency/share/day'),vega=N('Vega / Vol Point','currency/share/vol_point'),
    model=T('Model Assumptions'),model_at=TS('Model Calculation UTC'))
DEFAULTS = {
 'facts':('concept','value','unit','period_start','period_end','filing_date','form'),
 'reporting':('statement','metric','value','unit','period','period_end','filing_date'),
 'statement_values':('statement','metric','period_label','period_end','value','unit','currency'),
 'prices':('observed_at','observation_date','open','high','low','close','volume'),
 'series':('series','observation_date','value','unit'), 'quotes':('symbol','price','pct_change','volume','observed_at'),
 'filings':('filing_date','form','period_end','description','accession'), 'news':('published_at','symbol','title','publisher'),
 'options':('contract_symbol','expiry','side','strike','bid','ask','last_price','volume','open_interest','implied_volatility'),
 'expiries':('symbol','expiry'), 'book':('symbol','side','level','price','size','unit'),
 'auctions':('auction_date','security_type','security_term','high_yield','bid_to_cover_ratio','offering_amt'),
 'source_fields':('path','value_type','value','text','flag','date_value','timestamp_value'),
 'profile_fields':('path','value_type','value','text','flag','date_value','timestamp_value'),
 'documents':('url','text'), 'search':('symbol','name','type','source'),
 'comparison':('reference','observation_date','value','unit'),
 'statistics':('metric','value','unit'), 'indicators':('observation_date','observed_at','metric','value','unit'),
 'calendars':('symbol','earnings_start_date','earnings_end_date','eps_mean','revenue_mean','days'),
 'series_metadata':('series','field','text','value','date_value','unit'),
}
PARAMETERS = {
 'facts':{'symbol':None,'cik':None,'as_of':None,'concept':None},
 'reporting':{'symbol':None,'cik':None,'as_of':None,'statement':'all','period':'all','source':'sec'},
 'statement_values':{'symbol':None,'cik':None,'as_of':None,'statement':'balance','period':'annual'},
 'prices':{'symbol':None,'range':'1Y','interval':'1d','stored':False,'start':None,'end':None},
 'series':{'series':None}, 'quotes':{'symbols':()}, 'filings':{'symbol':None,'cik':None,'form':None,'year':None},
 'news':{'symbol':None,'limit':200,'feeds':()}, 'options':{'symbol':None,'expiry':None}, 'expiries':{'symbol':None},
 'book':{'symbol':None}, 'auctions':{}, 'profile_fields':{'symbol':None},
 'source_fields':{'symbol':None,'operation':'profile','expiry':None,'statement':None,'period':None,'dataset':None,
    'start':None,'end':None,'scope':None,'key':None,'region':None},
 'documents':{'source':'sec','url':None}, 'search':{'domain':'security','text':'','limit':100},
 'comparison':{'references':(),'range':'1Y','interval':'1d','mode':'REAL'},
 'statistics':{'symbol':None,'range':'1Y','interval':'1d'},
 'indicators':{'symbol':None,'range':'1Y','interval':'1d','sma':'20,50','ema':'21','bb':'20,2','rsi':'14','macd':'12,26,9'},
 'calendars':{'symbols':()},
 'series_metadata':{'series':None},
}
# Existing named transports only. This is field access, never an arbitrary URL/provider gateway.
OPERATIONS = {'profile','book','chain','earnings','calendar','financials','expiries',
              'fundamental-data','estimate-data','holdings-data',
              'plug-ownership','plug-actions','plug-shares','plug-statement','plug-company','plug-research'}
AUCTIONS_URL = ('https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query'
 '?sort=-auction_date&page[size]=60&fields=auction_date,security_type,security_term,cusip,high_yield,'
 'high_discnt_rate,high_investment_rate,bid_to_cover_ratio,offering_amt')

# Conservative aliases only. Original concepts/fields, signs and period contexts remain available.
REPORT_ALIASES={
 'TotalAssets':'assets','Assets':'assets','AssetsCurrent':'current_assets','CurrentAssets':'current_assets',
 'Liabilities':'liabilities','TotalLiabilitiesNetMinorityInterest':'liabilities',
 'LiabilitiesCurrent':'current_liabilities','CurrentLiabilities':'current_liabilities',
 'StockholdersEquity':'stockholders_equity',
 'CashAndCashEquivalentsAtCarryingValue':'cash_and_cash_equivalents','CashAndCashEquivalents':'cash_and_cash_equivalents',
 'InventoryNet':'inventory','Inventory':'inventory','AccountsReceivableNetCurrent':'accounts_receivable',
 'AccountsReceivable':'accounts_receivable','AccountsPayableCurrent':'accounts_payable','AccountsPayable':'accounts_payable',
 'PropertyPlantAndEquipmentNet':'net_ppe','NetPPE':'net_ppe','GrossProfit':'gross_profit',
 'OperatingIncomeLoss':'operating_income','OperatingIncome':'operating_income',
 'EarningsPerShareBasic':'basic_eps','BasicEPS':'basic_eps','EarningsPerShareDiluted':'diluted_eps','DilutedEPS':'diluted_eps',
 'NetCashProvidedByUsedInOperatingActivities':'operating_cash_flow','OperatingCashFlow':'operating_cash_flow',
 'Revenues':'revenue','TotalRevenue':'revenue',
}
FRED_PERCENT={'DFF','SOFR','DFII10','T10Y2Y','T10Y3M','BAMLC0A0CM','BAMLH0A0HYM2','MORTGAGE30US'}


def report_metric(field):
    compact=re.sub(r'[^A-Za-z0-9]','',field or '')
    return REPORT_ALIASES.get(compact,snake(field or '')), 'canonical_alias' if compact in REPORT_ALIASES else 'provider_field'


def common(meta, symbol=None):
    return dict(symbol=symbol,source=s.source(meta.source),fetched_at=s.timestamp(meta.fetched_at or None),
        available_at=s.timestamp(meta.fetched_at or None),availability_basis='first_observed',freshness=meta.freshness.upper(),
        normalization_version='access-v1-'+s.VERSION)


def provider_unit(unit):
    if re.fullmatch(r'[A-Z]{3}',unit):return 'currency',s.currency(unit)
    if re.fullmatch(r'[A-Z]{3}/shares',unit):return 'currency/share',s.currency(unit[:3])
    return {'pure':'ratio','shares':'shares'}.get(unit,'native:'+unit),None


def flatten(data, base, path='', index=0):
    """Retain every source scalar, its structural path and original type. Never stringify nested objects."""
    if hasattr(data,'calls') and hasattr(data,'puts'):
        for name in ('calls','puts','underlying'):yield from flatten(getattr(data,name,{}),base,f'{path}.{name}',index)
    elif isinstance(data,pd.DataFrame):
        for row_index,(idx,row) in enumerate(data.iterrows()):
            for col,value in row.items():
                yield from flatten(value,base,f'{path}[{idx!s}].{col!s}',row_index)
    elif isinstance(data,pd.Series):
        for name,value in data.items():yield from flatten(value,base,f'{path}.{name}',index)
    elif isinstance(data,dict):
        for name,value in data.items():yield from flatten(value,base,f'{path}.{name}' if path else str(name),index)
    elif isinstance(data,(list,tuple,np.ndarray)):
        for i,value in enumerate(data):yield from flatten(value,base,f'{path}[{i}]',i)
    else:
        row=dict(base,path=path,row_index=index,field=path.rsplit('.',1)[-1],unit='unknown')
        name=path.rsplit('.',1)[-1]
        if name=='raw' and '.' in path:name=path.rsplit('.',2)[-2]
        if base.get('operation')=='calendar' and any(path.startswith(label) for label in ('Earnings Date','Dividend Date','Ex-Dividend Date')):
            row.update(value_type='date',date_value=s.calendar_date(data),unit='date',text=str(data) if data is not None else None)
        elif name in ('currency','financialCurrency','revenue_currency'):
            row.update(value_type='currency',currency=s.currency(data),text=str(data) if data is not None else None,unit='currency_code')
        elif name in ('regularMarketTime','preMarketTime','postMarketTime','firstTradeDateEpochUtc','lastTradeDate','earningsTimestamp','earningsTimestampStart','earningsTimestampEnd'):
            row.update(value_type='timestamp',timestamp_value=s.timestamp(data),unit='timestamp',text=str(data) if data is not None else None)
        elif name in ('dividendDate','exDividendDate','lastSplitDate','fundInceptionDate','mostRecentQuarter','lastFiscalYearEnd','nextFiscalYearEnd'):
            row.update(value_type='date',date_value=s.calendar_date(data),unit='date',text=str(data) if data is not None else None)
        elif isinstance(data,(datetime,pd.Timestamp,np.datetime64)):
            stamp=s.timestamp(data)
            row.update(value_type='timestamp' if stamp else 'naive_datetime',timestamp_value=stamp,text=None if stamp else str(data))
        elif isinstance(data,date):row.update(value_type='date',date_value=s.calendar_date(data))
        elif isinstance(data,(bool,np.bool_)):row.update(value_type='boolean',flag=bool(data))
        elif isinstance(data,(int,float,np.number)):row.update(value_type='number',value=s.number(data))
        elif data is None:row.update(value_type='null')
        else:row.update(value_type='text',text=s.text(str(data)))
        yield row


def fact_records(data,meta,symbol,cik,as_of=None,latest=False):
    cutoff=as_of or date.today().isoformat()
    from .reporting import fiscal_calendar,fiscal_period
    calendar_input={'facts':{namespace:{concept:dict(definition,units={unit:[r for r in records if r.get('filed','9999')<=cutoff]
        for unit,records in definition.get('units',{}).items()}) for concept,definition in concepts.items()}
        for namespace,concepts in data.get('facts',{}).items()}}
    calendar=fiscal_calendar(calendar_input);fiscal_cache={}
    rows=[]
    for namespace,concepts in data.get('facts',{}).items():
        for concept,definition in concepts.items():
            for native,values in definition.get('units',{}).items():
                unit,currency=provider_unit(native)
                for item in values:
                    filed=s.calendar_date(item.get('filed'));end=s.calendar_date(item.get('end'));start=s.calendar_date(item.get('start'))
                    if filed and filed>cutoff or latest and end and end>cutoff or as_of and not filed:continue
                    duration=(date.fromisoformat(end)-date.fromisoformat(start)).days if start and end else None
                    period='instant' if start is None else 'annual' if duration is not None and 330<=duration<=400 else 'quarterly' if duration is not None and 65<=duration<=110 else 'ytd' if duration is not None and 111<=duration<330 else 'duration'
                    # An instant is observable balance data. SEC companyfacts does not classify every duration concept by statement.
                    statement='balance' if start is None else 'unclassified_flow'
                    metric,basis=report_metric(concept)
                    if end not in fiscal_cache:
                        fiscal_cache[end]=fiscal_period(date.fromisoformat(end),calendar) if end and end<=cutoff else None
                    fiscal=fiscal_cache[end]
                    rows.append(dict(common(meta,symbol),cik=cik,namespace=namespace,concept=concept,provider_field=concept,metric=metric,metric_basis=basis,
                        name=definition.get('label'),description=definition.get('description'),value=s.number(item.get('val')),
                        unit=unit,currency=currency,provider_unit=native,statement=statement,period=period,period_start=start,
                        period_end=end,filing_date=filed,available_date=filed,available_at=None,availability_basis='filing_date',
                        is_forward_period=(end>filed) if end and filed else None,
                        accession=item.get('accn'),form=item.get('form'),fiscal_year=fiscal[0] if fiscal else None,
                        fiscal_quarter=fiscal[1] if fiscal else None,fiscal_period=('FY' if period=='annual' else 'Q'+str(fiscal[1])) if fiscal else None,
                        filing_fiscal_year=item.get('fy'),filing_fiscal_period=item.get('fp'),frame=item.get('frame')))
    if latest:
        retained={}
        for row in rows:
            identity=tuple(row.get(k) for k in ('namespace','concept','provider_unit','period_start','period_end'))
            if identity not in retained or (row['filing_date'] or '',row.get('accession') or '')>(retained[identity]['filing_date'] or '',retained[identity].get('accession') or ''):
                retained[identity]=row
        rows=list(retained.values())
    return sorted(rows,key=lambda r:(r['period_end'] or '',r['metric'],r['filing_date'] or ''),reverse=True)


def matrix_records(data,meta,p,cik):
    from copy import deepcopy
    from collections import Counter
    from .reporting import statement_matrix, STATEMENTS, fiscal_calendar, fiscal_period
    data=deepcopy(data);cutoff=p.get('as_of') or date.today().isoformat()
    for concepts in data.get('facts',{}).values():
        for definition in concepts.values():
            for unit,items in definition.get('units',{}).items():
                definition['units'][unit]=[r for r in items if r.get('filed') and r['filed']<=cutoff and r.get('end','9999')<=cutoff]
    statement='cash' if p['statement']=='cashflow' else p['statement']
    name,periods,rows=statement_matrix(data,statement,p['period']=='annual')
    calendar=fiscal_calendar(data)
    all_facts=fact_records(data,meta,p.get('symbol'),cik,p.get('as_of'))
    # Conservative availability of this calculated matrix: never earlier than its retained input filings.
    filed=max((r['filing_date'] for r in all_facts if r['filing_date']),default=None)
    candidates={}
    for record in all_facts:
        if not record['period_end']:continue
        fiscal=fiscal_period(date.fromisoformat(record['period_end']),calendar)
        if fiscal:
            year,quarter=fiscal;label=str(year) if p['period']=='annual' else f'{year}Q{quarter}'
            if (p['period']!='annual' or quarter==4) and record.get('namespace') in ('us-gaap','ifrs-full'):
                candidates.setdefault(label,Counter())[record['period_end']]+=1
    ends={label:counts.most_common(1)[0][0] for label,counts in candidates.items()}
    for end,year in calendar:
        label=str(year) if p['period']=='annual' else f'{year}Q4'
        ends[label]=end.isoformat()
    lines={label:concepts for label,concepts,_ in STATEMENTS[statement][2]}
    result=[]
    for label,values,display_unit in rows:
        concepts=lines.get(label,[])
        metric=report_metric(concepts[0])[0] if concepts else 'free_cash_flow'
        unit='shares' if any('Shares' in c and not c.startswith('EarningsPerShare') for c in concepts) else 'currency/share' if display_unit=='eps' else 'currency'
        for period,value in values.items():
            result.append(dict(common(meta,p.get('symbol')),cik=cik,source='sec',namespace='calculated_statement',
                statement=p['statement'],period=p['period'],period_label=period,fiscal_year=int(period[:4]),
                fiscal_quarter=int(period[-1]) if 'Q' in period else 4,period_end=ends.get(period),
                metric=metric,name=label,provider_field=','.join(concepts) or 'operating_cash_flow - capex',
                metric_basis='statement_display_metric',value=value,unit=unit,currency='USD' if unit.startswith('currency') else None,
                filing_date=filed,available_date=filed,available_at=None,availability_basis='conservative_input_filing_date',
                value_basis='shared_fiscal_statement_calculation; standalone quarters/FCF may be derived'))
    return result


def validate(query,dataset):
    p=query.parameters;allowed=PARAMETERS[query.dataset]
    unknown=set(p)-set(allowed)
    if unknown:raise ValueError('Unknown parameters: '+', '.join(sorted(unknown)))
    p=dict(allowed,**p)
    if query.dataset in ('facts','reporting','statement_values','filings'):
        if not p.get('symbol') and not p.get('cik'):raise ValueError('Supply symbol or cik')
        if p.get('cik') is not None and (type(p['cik']) is not int or not 1<=p['cik']<=9999999999):raise ValueError('CIK must be a positive integer')
        if p.get('as_of') and (not re.fullmatch(r'\d{4}-\d{2}-\d{2}',p['as_of']) or s.calendar_date(p['as_of'])!=p['as_of']):raise ValueError('as_of needs YYYY-MM-DD')
    elif 'symbol' in allowed and query.dataset not in ('news','source_fields') and not p.get('symbol'):
        raise ValueError('Supply a symbol')
    if query.dataset=='reporting':
        if p['source'] not in ('sec','yf','yahoo'):raise ValueError('Reporting source must be sec or yahoo')
        if p['statement'] not in ('all','balance','income','cashflow'):raise ValueError('Statement must be all/balance/income/cashflow')
        if p['period'] not in ('all','annual','quarterly','ttm','instant','ytd','duration'):raise ValueError('Unknown reporting period')
        if p['source'] in ('yf','yahoo') and (p['as_of'] or not p['symbol']):raise ValueError('Yahoo reporting needs a symbol and has no historical filing-date cutoff; use SEC for as_of')
        if p['source'] in ('yf','yahoo') and p['period'] in ('instant','ytd','duration'):raise ValueError('Yahoo reporting periods are annual/quarterly/ttm/all')
        if p['source']=='sec' and p['statement'] in ('income','cashflow'):
            raise ValueError('SEC fact durations do not identify every statement: use statement=all and WHERE concept/metric, or Yahoo statement=income/cashflow')
    if query.dataset=='statement_values' and (p['statement'] not in ('income','balance','cashflow') or p['period'] not in ('annual','quarterly')):
        raise ValueError('Statement values need income/balance/cashflow and annual/quarterly')
    if query.dataset=='source_fields' and p['operation'] not in OPERATIONS:raise ValueError('Unsupported existing source operation')
    if query.dataset=='documents' and p['source'] not in ('sec','articles'):raise ValueError('Document source must be sec/articles')
    if query.dataset in ('quotes','comparison','calendars') and not p.get('symbols',p.get('references')):raise ValueError('Supply symbols/references separated by commas')
    if query.dataset in ('quotes','calendars') and (not isinstance(p['symbols'],(list,tuple)) or len(p['symbols'])>100):raise ValueError('Supply up to 100 symbols')
    if query.dataset in ('series','series_metadata') and (not isinstance(p['series'],str) or not re.fullmatch(r'[A-Za-z0-9_.,-]{1,300}',p['series'])):raise ValueError('Supply a FRED series identifier (series accepts comma-separated identifiers)')
    if query.dataset in ('prices','statistics','indicators'):
        from .windows import base_for
        if base_for(p['range'],p['interval']) is None:raise ValueError('Unavailable history window/interval')
    if query.dataset=='options' and p['expiry'] and s.calendar_date(p['expiry'])!=p['expiry']:raise ValueError('expiry needs YYYY-MM-DD')
    if query.dataset=='documents' and not p['url']:raise ValueError('Supply a retained document URL')
    if query.dataset=='source_fields':
        if not p['symbol']:raise ValueError('Supply a symbol')
        if p['operation']=='chain' and not p['expiry']:raise ValueError('chain requires expiry')
        if p['operation']=='financials' and not p['statement']:raise ValueError('financials requires a statement attribute')
    if query.dataset in ('news','search') and (type(p['limit']) is not int or not 1<=p['limit']<=10000):raise ValueError('Source limit must be 1–10000')
    if query.dataset=='indicators':
        for field in ('sma','ema','bb','rsi','macd'):
            try:values=[float(v) for v in str(p[field]).split(',')]
            except ValueError:raise ValueError('Indicator parameters must be numeric') from None
            if not 1<=len(values)<=8 or any(not np.isfinite(v) or not 0<v<=10000 for v in values):raise ValueError('Indicator parameters must be positive and bounded')
            if field=='rsi' and len(values)!=1 or field=='bb' and len(values)!=2 or field=='macd' and (len(values)!=3 or values[0]>=values[1]):raise ValueError('Use rsi=14, bb=20,2 and macd=12,26,9')
    return p


async def cik_for(service,p,force,stored_only):
    if p.get('cik'):return p['cik']
    result=await service.query(ResourceQuery('sec','sec',{'url':'https://www.sec.gov/files/company_tickers.json'}),force=force,stored_only=stored_only)
    return next((int(row['cik_str']) for row in (result.data or {}).values() if row.get('ticker','').upper().replace('.','-')==p['symbol'].upper().replace('.','-')),None)


async def load(service,query,force,stored_only):
    name=query.dataset;p=validate(query,service.table_dataset(name));results=[];complete=True;coverage=''
    async def get(request):
        result=await service.query(request,force=force,stored_only=stored_only)
        results.append(result);return result
    if name in ('facts','reporting','statement_values','filings') and not (name=='reporting' and p['source'] in ('yf','yahoo')):
        cik=await cik_for(service,p,force,stored_only)
        if not cik:return Result(None, Metadata('sec',reason='No SEC CIK available'))
        result=await get(FilingsQuery(cik,p.get('form'),p.get('year')) if name=='filings' else FactsQuery(cik,p.get('concept')))
        if result.data is None:return result
        if name=='filings':
            rows=[dict(common(result.meta,p.get('symbol')),cik=cik,accession=r.get('acc'),filing_date=r.get('date'),
                available_date=r.get('date'),available_at=None,availability_basis='filing_date',period_end=r.get('period'),
                form=r.get('form'),document=r.get('doc'),description=r.get('desc'),items=r.get('items'),
                url=f"https://www.sec.gov/Archives/edgar/data/{cik}/{r.get('acc','').replace('-','')}/{r.get('doc','')}") for r in result.data]
        elif name=='statement_values':
            rows=await asyncio.to_thread(matrix_records,result.data,result.meta,p,cik)
            coverage='Exact FA fiscal statement calculations, unscaled numeric values; USD display convention. Original native-currency/unlisted concepts are in facts/reporting; filing availability is conservative.'
        else:
            rows=await asyncio.to_thread(fact_records,result.data,result.meta,p.get('symbol'),cik,p.get('as_of'),name=='reporting')
            if p.get('concept'):rows=[r for r in rows if r['concept']==p['concept']]
            if name=='reporting':
                if p['statement']=='balance':rows=[r for r in rows if r['period']=='instant']
                if p['period']!='all':rows=[r for r in rows if r['period']==p['period']]
                coverage='All reported SEC concepts/units; latest eligible filing per concept and actual period. Duration statement membership is unclassified; use facts for all amendments. No inferred standalone quarters or publication times.'
    elif name=='reporting':
        from .query_models import StatementsQuery
        rows=[]
        for statement in (('income','balance','cashflow') if p['statement']=='all' else (p['statement'],)):
            periods=('annual','quarterly') if statement=='balance' else ('annual','quarterly','ttm')
            for period in (periods if p['period']=='all' else (p['period'],)):
                result=await get(StatementsQuery(p['symbol'],statement,period))
                if result.data is not None:
                    rows.extend(dict(r,concept=r.get('provider_field'),metric=report_metric(r.get('provider_field'))[0],
                        metric_basis=report_metric(r.get('provider_field'))[1],name=r.get('provider_field'),provider_unit=r.get('unit')) for r in result.data)
        complete=False;coverage='Yahoo reported statement periods; all available provider line items, original publication times unavailable'
    elif name in ('statistics','indicators'):
        from .analytics import statistics_records,indicators
        result=await get(BarsQuery(p['symbol'],p['range'],p['interval']))
        if result.data is None:return result
        base=dict(common(result.meta,p['symbol']),range=p['range'],interval=p['interval'],
            available_at=s.timestamp(datetime.now().astimezone()),availability_basis='calculated_at')
        if name=='statistics':rows=[dict(base,**r) for r in await asyncio.to_thread(statistics_records,result.data,p['interval'])]
        else:
            features=await asyncio.to_thread(indicators,result.data,{'s','e','b','w','r','m'},p)
            curves=list(features['overlays'])+[('RSI',features['rsi']),('MACD',features['macd'][0]),('Signal',features['macd'][1]),('MACD Histogram',features['macd'][0]-features['macd'][1])]
            rows=[]
            for metric,values in curves:
                for stamp,value in values.items():rows.append(dict(base,metric=snake(metric),value=value,unit='index_0_100' if metric=='RSI' else 'native_price',
                    observation_date=s.calendar_date(stamp),observed_at=s.timestamp(stamp) if result.data.index.tz is not None else None))
    elif name=='prices':
        request=StoredBarsQuery(p['symbol'],p['interval'],p['start'],p['end']) if p['stored'] else BarsQuery(p['symbol'],p['range'],p['interval'])
        result=await get(request)
        if result.data is None:return result
        rows=[]
        from .windows import candle_interval
        daily=candle_interval(p['interval']).minutes>=1440
        for stamp,row in result.data.iterrows():
            provenance=common(result.meta,p['symbol'])
            if 'FetchedAt' in row:provenance['fetched_at']=s.timestamp(row['FetchedAt'])
            if 'Source' in row:provenance['source']=s.source(row['Source'])
            rows.append(dict(provenance,interval=p['interval'],provider_time=str(stamp),
                source_timezone=str(stamp.tzinfo) if stamp.tzinfo else None,observation_date=s.calendar_date(stamp),
                observed_at=None if daily else s.timestamp(stamp),available_at=None,availability_basis='collection_not_historical_publication',
                open=row.get('Open'),high=row.get('High'),low=row.get('Low'),close=row.get('Close'),volume=row.get('Volume'),bad_volume=row.get('BadVol')))
        coverage='Native bars; daily sessions remain dates. Volume is in provider units; no historical publication time is inferred.'
    elif name=='series_metadata':
        from .normalization import parse_description
        sid=p['series'].upper()
        result=await get(ResourceQuery('fred','fred-meta',{'url':f'https://fred.stlouisfed.org/series/{sid}','per_sec':2,'_ttl':86400,'as_json':False,'retries':2}))
        fields,notes=await asyncio.to_thread(parse_description,result.data) if result.data else ({},'')
        rows=[dict(common(result.meta),series=sid,field=snake(field),text=text) for field,text in fields.items()]
        rows.append(dict(common(result.meta),series=sid,field='notes',text=notes))
        values=await get(SeriesQuery(sid))
        if values.data is not None and len(values.data):
            rows.extend([dict(common(values.meta),series=sid,field='latest_value',value=values.data.iloc[-1],unit=fields.get('Units') or 'unknown'),
                dict(common(values.meta),series=sid,field='observations',value=len(values.data),unit='count'),
                dict(common(values.meta),series=sid,field='observation_start',date_value=s.calendar_date(values.data.index[0])),
                dict(common(values.meta),series=sid,field='observation_end',date_value=s.calendar_date(values.data.index[-1]))])
    elif name=='series':
        rows=[]
        for sid in p['series'].split(','):
            result=await get(SeriesQuery(sid))
            if result.data is not None:
                known_unit='percent' if sid.upper() in FRED_PERCENT or re.fullmatch(r'DGS(?:\d+|\d+MO)',sid.upper()) else 'unknown'
                rows.extend(dict(common(result.meta),series=sid,observation_date=s.calendar_date(stamp),value=value,unit=known_unit,
                    available_at=None,availability_basis='collection_not_historical_release') for stamp,value in result.data.items())
    elif name=='quotes':
        result=await get(QuotesQuery(tuple(p['symbols'])))
        rows=[dict(common(result.meta,symbol),source=r.get('source'),observed_at=s.timestamp(r.get('ts')),fetched_at=s.timestamp(r.get('fetched')),
            available_at=s.timestamp(r.get('fetched')),
            price=r.get('price'),previous_close=r.get('prev_close'),volume=r.get('volume'),day_low=r.get('day_low'),day_high=r.get('day_high'),
            change=(r['price']-r['prev_close']) if r.get('price') is not None and r.get('prev_close') is not None else None,
            pct_change=(100*(r['price']/r['prev_close']-1)) if r.get('price') is not None and r.get('prev_close') else None) for symbol,r in (result.data or {}).items()]
        complete=len(rows)==len(p['symbols'])
    elif name=='calendars':
        rows=[]
        for symbol in p['symbols']:
            result=await get(ResourceQuery('yf','calendar',{'symbol':symbol}));raw=result.data if isinstance(result.data,dict) else {}
            dates=raw.get('Earnings Date') or []
            if not isinstance(dates,(list,tuple)):dates=[dates]
            dates=[s.calendar_date(v) for v in dates if s.calendar_date(v)]
            start=min(dates) if dates else None;end=max(dates) if dates else None
            rows.append(dict(common(result.meta,symbol),earnings_start_date=start,earnings_end_date=end,
                dividend_date=s.calendar_date(raw.get('Dividend Date')),ex_dividend_date=s.calendar_date(raw.get('Ex-Dividend Date')),
                eps_mean=s.number(raw.get('Earnings Average')),eps_low=s.number(raw.get('Earnings Low')),eps_high=s.number(raw.get('Earnings High')),
                revenue_mean=s.number(raw.get('Revenue Average')),revenue_low=s.number(raw.get('Revenue Low')),revenue_high=s.number(raw.get('Revenue High')),
                calculated_date=date.today().isoformat(),days=(date.fromisoformat(start)-date.today()).days if start else None))
        complete=False;coverage='Reported ticker calendars; date windows preserve schedule uncertainty. No exact event/publication instant is inferred.'
    elif name=='news':
        feeds=tuple(p['feeds']) or ((f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={p['symbol']}&region=US&lang=en-US",) if p['symbol'] else tuple(service.ingestion.cfg.get('news','feeds',[])))
        result=await get(NewsQuery(p['symbol'],feeds=feeds,limit=p['limit']));complete=False
        rows=[dict(common(result.meta,p['symbol']),title=r.get('title'),summary=r.get('summary'),url=r.get('url'),publisher=r.get('src'),published_at=s.timestamp(r.get('ts')),
            available_at=s.timestamp(datetime.now().astimezone()),availability_basis='extracted_at_not_original_publication') for r in result.data or []]
    elif name in ('options','expiries'):
        expiries=await get(ResourceQuery('yf','expiries',{'symbol':p['symbol']}))
        expiry=p.get('expiry') or next(iter(expiries.data or []),None)
        if name=='expiries':rows=[dict(common(expiries.meta,p['symbol']),expiry=day) for day in expiries.data or []]
        elif not expiry:rows=[]
        else:
            result=await get(ResourceQuery('yf','chain',{'symbol':p['symbol'],'expiry':expiry}))
            rows=[]
            columns={'contractSymbol':'contract_symbol','lastTradeDate':'last_trade_at','lastPrice':'last_price','openInterest':'open_interest',
                'impliedVolatility':'implied_volatility','inTheMoney':'in_the_money','contractSize':'contract_size','percentChange':'pct_change'}
            chain=result.data
            blocks=chain.items() if isinstance(chain,dict) else ((side,getattr(chain,side,None)) for side in ('calls','puts'))
            quotes=await get(QuotesQuery((p['symbol'],'^IRX')))
            spot=s.number((quotes.data or {}).get(p['symbol'],{}).get('price'))
            irx=s.number((quotes.data or {}).get('^IRX',{}).get('price'))
            rate=irx/100 if irx else float(service.ingestion.cfg.get('options','risk_free_fallback',.045))
            now=pd.Timestamp.now(tz='UTC');years=max(((pd.Timestamp(expiry).tz_localize('America/New_York')+pd.Timedelta(hours=16))-now).total_seconds(),60)/(365*86400)
            from .analytics import bs_greeks
            for side,frame in blocks:
                if not isinstance(frame,pd.DataFrame):continue
                for original in frame.to_dict('records'):
                    row=dict(common(result.meta,original.get('contractSymbol')),underlying=p['symbol'],expiry=expiry,side=side)
                    row.update({columns.get(k,snake(k)):v for k,v in original.items()})
                    if 'last_trade_at' in row:row['last_trade_at']=s.timestamp(row['last_trade_at'])
                    row.update(spot=spot,risk_free_rate=rate,time_to_expiry=years,model_at=s.timestamp(now),available_at=s.timestamp(now),availability_basis='model_calculated_at',model='Black-Scholes; expiry assumed 16:00 New York; theta/day; vega/vol point; IRX or configured fallback')
                    g=bs_greeks(spot,s.number(row.get('strike')),years,rate,s.number(row.get('implied_volatility')),side=='calls')
                    row.update(dict(zip(('delta','gamma','theta','vega'),g or (None,)*4)))
                    rows.append(row)
            complete=False;coverage='One provider expiry; native chain fields retained. Greeks are model outputs, not provider observations.'
    elif name=='book':
        rows=[]
        if p['symbol'].endswith('-USD'):
            result=await get(ResourceQuery('coinbase','coinbase',{'url':f"https://api.exchange.coinbase.com/products/{p['symbol']}/book?level=2",'_ttl':5,'retries':0}))
            for side in ('bids','asks'):
                entries=(result.data or {}).get(side,[])
                for level,entry in enumerate(entries[:20],1):rows.append(dict(common(result.meta,p['symbol']),side=side[:-1],level=level,price=s.number(entry[0]),size=s.number(entry[1]),unit='base_asset'))
            coverage='Coinbase top 20 aggregated levels per side; snapshot, not trade history'
        else:
            result=await get(ResourceQuery('yf','book',{'symbol':p['symbol']}));raw=result.data or {}
            for side in ('bid','ask'):
                if s.number(raw.get(side)) is not None:rows.append(dict(common(result.meta,p['symbol']),side=side,level=1,price=raw.get(side),size=raw.get(side+'Size'),unit='provider_size'))
            coverage='Yahoo best bid/ask only; no full book depth implied'
        complete=False
    elif name=='auctions':
        result=await get(ResourceQuery('fiscal','fiscal',{'url':AUCTIONS_URL}));rows=[dict(common(result.meta),currency='USD',**r) for r in (result.data or {}).get('data',[])]
        complete=False;coverage='Latest 60 requested Treasury auction results; source may disclose fewer'
    elif name in ('source_fields','profile_fields'):
        operation='profile' if name=='profile_fields' else p['operation']
        params={k:v for k,v in p.items() if k!='operation' and v is not None}
        result=await get(ResourceQuery('yf',operation,params));rows=list(flatten(result.data,dict(common(result.meta,p.get('symbol')),operation=operation))) if result.data is not None else []
        complete=False;coverage='All retained source scalar fields; unclassified source fields have unknown units and preserve original representations'
    elif name=='documents':
        result=await get(TextQuery(p['source'],p['url']));rows=[dict(common(result.meta),url=p['url'],text=result.data)] if result.data else []
    elif name=='search':
        result=await get(SearchQuery(p['domain'],p['text'],limit=p['limit']));rows=[]
        for row in result.data or []:
            if isinstance(row,dict):rows.append(dict(common(result.meta),**row))
            elif p['domain']=='fred':
                sid,title,frequency,unit,adjustment,start,end=row[:7]
                rows.append(dict(common(result.meta),symbol=sid,name=title,type='FRED',frequency=frequency,unit=unit,adjustment=adjustment,
                    observation_start_date=s.calendar_date(start),observation_end_date=s.calendar_date(end)))
            else:
                symbol,title,kind,source=row[:4]
                rows.append(dict(common(result.meta,symbol),name=title,type=kind,source=source))
        complete=False
    elif name=='comparison':
        result=await get(ComparisonQuery(tuple(p['references']),p['range'],p['interval'],p['mode']))
        frame=result.data
        if isinstance(frame,dict):frame=frame.get('values')
        rows=[]
        if isinstance(frame,pd.DataFrame):
            for stamp,row in frame.iterrows():
                for reference,value in row.items():rows.append(dict(common(result.meta),reference=reference,observation_date=s.calendar_date(stamp),value=value,unit='percent' if p['mode']=='NORMALIZED' else 'native',
                    available_at=s.timestamp(datetime.now().astimezone()),availability_basis='calculated_at'))
        complete=False
    else:raise ValueError('Unsupported dataset '+name)
    if not results:return Result(TableBatch((),name,0,False),Metadata('',reason='No source result'))
    meta=service.combine(tuple(r.meta for r in results),'|'.join(dict.fromkeys(r.meta.source for r in results)))
    if all(r.data is None for r in results):return Result(None,meta,tuple(r.meta for r in results))
    def normalize_rows():
        normalized=[]
        for index,row in enumerate(rows):
            row=normalize_record(row,SCHEMAS[name])
            if meta.freshness!='fresh':row['freshness']=meta.freshness.upper()
            identity={k:v for k,v in row.items() if k not in ('record_id','fetched_at','available_at','freshness','normalization_version')}
            row.setdefault('record_id',key(('reported' if name in ('facts','reporting') else name,identity)))
            normalized.append(row)
        return normalized
    normalized=await asyncio.to_thread(normalize_rows)
    reason='; '.join(filter(None,(meta.reason,coverage)))
    meta=replace(meta,reason=reason,complete=complete and all(r.meta.freshness=='fresh' for r in results),coverage=coverage)
    return Result(TableBatch(tuple(normalized),coverage or name,len(normalized),complete),meta,tuple(r.meta for r in results))


async def execute(service,query,dataset,required,force,stored_only):
    result=await load(service,query,force,stored_only)
    if result.data is None:return result
    return Result(await asyncio.to_thread(select,query,dataset,required,result.data),result.meta,result.inputs)


def register(service):
    for name,schema in SCHEMAS.items():
        search=tuple(f for f in ('symbol','name','concept','metric','title','path','text','description') if f in schema)
        service.register_table(name,Dataset(schema,DEFAULTS[name],search,None,execute,validate))


def parameters(service,name):
    from .yahoo_datasets import TABLE_REQUESTS
    if name in PARAMETERS:return dict(PARAMETERS[name])
    if name in TABLE_REQUESTS:
        from dataclasses import MISSING
        return {f.name:(None if f.default is MISSING else f.default) for f in fields(TABLE_REQUESTS[name])}
    if name in ('fundamentals','estimates','analysts','holdings'):
        return {'symbol':None,**({'source':'yahoo','as_of':None} if name=='fundamentals' else {})}
    return {}
