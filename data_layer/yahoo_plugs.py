"""Additional Yahoo transports, invoked only by Ingestion's paced/session worker.

SDK internals are isolated here to retain raw calendar zeros, column identifiers,
and HTTP errors that public convenience methods can discard or swallow.
"""
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote


def fetch(operation, params):
    import yfinance as yf
    from yfinance.data import YfData
    p = params
    ticker = yf.Ticker(p['symbol']) if p.get('symbol') else None
    data = YfData()
    if operation in ('plug-ownership', 'plug-company'):
        modules = ('majorHoldersBreakdown,institutionOwnership,fundOwnership,insiderTransactions,insiderHolders,netSharePurchaseActivity,price'
                   if operation == 'plug-ownership' else
                   {'sustainability':'esgScores', 'filings':'secFilings', 'earnings_history':'earningsHistory,financialData,price'}[p['dataset']])
        payload = data.get_raw_json('https://query2.finance.yahoo.com/v10/finance/quoteSummary/' + p['symbol'],
                                   params={'modules':modules, 'formatted':'false', 'corsDomain':'finance.yahoo.com'})
        root = payload.get('quoteSummary', {})
        if root.get('error') or not root.get('result'):
            raise ValueError('Yahoo did not supply the requested company dataset')
        return root['result'][0]
    if operation == 'plug-actions':
        frame = ticker.history(period='max', interval='1d', auto_adjust=False, actions=True, raise_errors=True)
        if frame is None:
            raise ValueError('Yahoo action history unavailable')
        columns = [c for c in ('Dividends','Stock Splits','Capital Gains') if c in frame]
        if not columns and len(frame):
            raise ValueError('Yahoo omitted corporate-action columns')
        actions = frame[columns]
        actions = actions.loc[(actions.fillna(0) != 0).any(axis=1)]
        metadata=ticker.get_history_metadata()
        # The SDK returns a lazy Mapping; expanding it can fetch trading periods.
        return {'actions':actions, 'metadata':{k:metadata.get(k) for k in ('currency','exchangeTimezoneName','instrumentType')}}
    if operation == 'plug-shares':
        epoch = lambda d: int(datetime.combine(date.fromisoformat(d), datetime.min.time(), timezone.utc).timestamp())
        result = data.get_raw_json('https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/' + p['symbol'],
                                  params={'symbol':p['symbol'], 'period1':epoch(p['start']), 'period2':epoch(p['end'])})
        root = result.get('timeseries', {})
        if root.get('error') or not isinstance(root.get('result'), list):
            raise ValueError('Yahoo share-count response unavailable or invalid')
        return root['result']
    if operation == 'plug-research':
        payload = data.get_raw_json('https://query1.finance.yahoo.com/v1/finance/' +
                                   ('sectors' if p['scope'] == 'sector' else 'industries') + '/' + quote(p['key'],safe=''),
                                   params={'formatted':'false', 'withReturns':'true', 'lang':'en-US', 'region':p['region']})
        if not isinstance(payload.get('data'), dict):
            raise ValueError('Yahoo research response unavailable or invalid')
        return payload['data']
    if operation == 'plug-calendar':
        from yfinance.calendars import PREDEFINED_CALENDARS
        kind = {'earnings':'sp_earnings','ipo':'ipo_info','economic':'economic_event','splits':'splits'}[p['kind']]
        within = lambda field: {'operator':'GTELT','operands':[field, p['start'] + 'T00:00:00.000Z', p['end'] + 'T00:00:00.000Z']}
        conditions = ([{'operator':'OR','operands':[within(f) for f in ('startdatetime','filingdate','amendeddate')]}]
                      if p['kind'] == 'ipo' else [within('startdatetime')])
        if p['kind'] == 'earnings':
            conditions.append({'operator':'OR','operands':[{'operator':'EQ','operands':['eventtype',t]} for t in ('EAD','ERA')]})
        if p.get('region'):
            conditions.append({'operator':'EQ','operands':['country_code' if p['kind'] == 'economic' else 'region',
                                                         p['region'] if p['kind'] == 'economic' else p['region'].lower()]})
        if p.get('symbol'):
            conditions.append({'operator':'EQ','operands':['ticker',p['symbol']]})
        body = {'entityIdType':kind, 'sortType':'ASC', 'sortField':'startdatetime',
                'includeFields':PREDEFINED_CALENDARS[kind]['includeFields'], 'size':100, 'offset':p['offset'],
                'query':{'operator':'AND','operands':conditions}}
        response = data.post('https://query1.finance.yahoo.com/v1/finance/visualization',
                             params={'region':'US','lang':'en-US'}, body=body)
        response.raise_for_status()
        root = response.json().get('finance', {})
        if root.get('error') or not root.get('result'):
            raise ValueError('Yahoo calendar response unavailable or invalid')
        result = root['result'][0]
        documents = result.get('documents')
        if not isinstance(documents, list) or not documents or not isinstance(result.get('total'), int):
            raise ValueError('Yahoo calendar omitted rows or coverage counts')
        records = []
        for document in documents:
            columns = [c['id'] for c in document['columns']]
            for row in document['rows']:
                if len(row) != len(columns):
                    raise ValueError('Malformed Yahoo calendar row')
                records.append(dict(zip(columns, row)))
        return {'rows':records, 'total':result['total']}
    if operation == 'plug-statement':
        method = {'income':ticker.get_income_stmt, 'balance':ticker.get_balance_sheet, 'cashflow':ticker.get_cashflow}[p['statement']]
        result = method(freq={'annual':'yearly','quarterly':'quarterly','ttm':'trailing'}[p['period']], pretty=False)
        if result is None:
            raise ValueError('Yahoo financial statement unavailable')
        return result
    if operation == 'plug-news':
        result = ticker.get_news(count=p['limit'], tab='all')
        if not result:
            # Same provider: the SDK's ticker feed and search have different coverage.
            result = yf.Search(p['symbol'], max_results=0, news_count=p['limit'], lists_count=0).news
        return result
    if operation == 'plug-search-news':
        return yf.Search(p['text'], max_results=0, news_count=p['limit'], lists_count=0).news
    raise ValueError('Unknown additional Yahoo operation ' + operation)
