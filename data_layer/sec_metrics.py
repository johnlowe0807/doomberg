"""Conservative SEC metric calculations using filed dates and actual fiscal periods."""
from datetime import date

from .financial_fields import METRICS
from .financial_data import provenance, number, canonical_records

CONCEPTS = {
    'revenue': ('RevenueFromContractWithCustomerExcludingAssessedTax','Revenues','SalesRevenueNet','RevenueFromContractWithCustomerIncludingAssessedTax'),
    'net_income': ('NetIncomeLoss','ProfitLoss'), 'gross_profit': ('GrossProfit',),
    'operating_income': ('OperatingIncomeLoss',), 'eps': ('EarningsPerShareDiluted','EarningsPerShareBasic'),
    'operating_cash': ('NetCashProvidedByUsedInOperatingActivities',),
    'capex': ('PaymentsToAcquirePropertyPlantAndEquipment',),
    'assets': ('Assets',), 'equity': ('StockholdersEquity','StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'),
    'current_assets': ('AssetsCurrent',), 'current_liabilities': ('LiabilitiesCurrent',),
    'inventory': ('InventoryNet',), 'current_debt': ('LongTermDebtCurrent','LongTermDebtAndCapitalLeaseObligationsCurrent'),
    'long_debt': ('LongTermDebtNoncurrent','LongTermDebtAndCapitalLeaseObligationsNoncurrent'),
    'short_debt': ('ShortTermBorrowings','CommercialPaper'),
    'shares': ('CommonStockSharesOutstanding','EntityCommonStockSharesOutstanding'),
}


def normalize(symbol, data, meta, as_of=None):
    cutoff = as_of or date.today().isoformat()
    facts = data.get('facts', {})
    units = {u for concepts in facts.values() for c in concepts.values() for u in c.get('units', {}) if len(u) == 3 and u.isupper()}
    currency = 'USD' if 'USD' in units else next(iter(sorted(units)), None)

    def records(name, instant=False):
        wanted = 'shares' if name == 'shares' else currency + '/shares' if name == 'eps' and currency else currency
        for concept in CONCEPTS[name]:
            for taxonomy in ('us-gaap','dei'):
                values = facts.get(taxonomy, {}).get(concept, {}).get('units', {}).get(wanted, [])
                eligible = [r for r in values if r.get('filed') and r['filed'] <= cutoff and r.get('end','9999') <= cutoff
                            and number(r.get('val')) is not None and (not r.get('start')) == instant]
                if eligible:
                    latest = {}
                    for r in eligible:
                        key = (r.get('start'),r['end'])
                        if key not in latest or r['filed'] > latest[key]['filed']:
                            latest[key] = r
                    return list(latest.values())
        return []

    def quarters(name):
        values = records(name)
        result = {}
        for r in values:
            span = (date.fromisoformat(r['end']) - date.fromisoformat(r['start'])).days
            if 65 <= span <= 110:
                result[r['end']] = (float(r['val']),r)
        # SEC cash flow facts are often fiscal YTD. Difference only within the same start date.
        for r in values:
            span = (date.fromisoformat(r['end']) - date.fromisoformat(r['start'])).days
            if span <= 110 or span > 380:
                continue
            prior = [p for p in values if p['start'] == r['start'] and 65 <= (date.fromisoformat(r['end'])-date.fromisoformat(p['end'])).days <= 110]
            if prior and r['end'] not in result:
                p = max(prior,key=lambda x:x['end'])
                result[r['end']] = (float(r['val'])-float(p['val']),dict(r,filed=max(r['filed'],p['filed'])))
        return sorted(result.items())

    series = {name:quarters(name) for name in ('revenue','net_income','gross_profit','operating_income','eps','operating_cash','capex')}

    def ttm(name, lag=0):
        q = series[name]
        group = q[-4-lag:len(q)-lag if lag else None]
        if len(group) != 4 or any(not 65 <= (date.fromisoformat(b[0])-date.fromisoformat(a[0])).days <= 110 for a,b in zip(group,group[1:])):
            annual = [r for r in records(name) if 330 <= (date.fromisoformat(r['end'])-date.fromisoformat(r['start'])).days <= 400]
            if lag:
                current = ttm(name)
                annual = [r for r in annual if current and 330 <= (date.fromisoformat(current[1]['end'])-date.fromisoformat(r['end'])).days <= 400]
            if not annual:
                return None
            r = max(annual,key=lambda item:(item['end'],item['filed']))
            return float(r['val']),r
        annual = [r for r in records(name) if r['end'] == group[-1][0] and
                  330 <= (date.fromisoformat(r['end'])-date.fromisoformat(r['start'])).days <= 400]
        if annual:
            r = max(annual,key=lambda item:item['filed'])
            return float(r['val']),r
        return (sum(r[1][0] for r in group), dict(group[-1][1][1],filed=max(r[1][1]['filed'] for r in group)))

    def instant(name):
        r = max(records(name,True),key=lambda v:(v['end'],v['filed']),default=None)
        return (float(r['val']),r) if r else None

    calculated = {}

    def put(name, item, period):
        if item:
            calculated[name] = (item[0],item[1],period)

    def ratio(name, numerator, denominator, scale=1, period='ttm'):
        if numerator and denominator and denominator[0] and numerator[1]['end'] == denominator[1]['end']:
            put(name,(scale*numerator[0]/denominator[0], dict(numerator[1],filed=max(numerator[1]['filed'],denominator[1]['filed']))),period)

    for name, source in [('revenue_ttm','revenue'),('net_income_ttm','net_income'),('eps_ttm','eps'),('operating_cash_flow','operating_cash')]:
        put(name,ttm(source),'ttm')
    revenue, net = ttm('revenue'), ttm('net_income')
    for name, source in [('gross_margin','gross_profit'),('operating_margin','operating_income'),('profit_margin','net_income')]:
        ratio(name,ttm(source),revenue,100)
    # Annualized returns use average beginning/end balance values, never a mismatched fiscal date.
    for name, balance in [('roa','assets'),('roe','equity')]:
        current = instant(balance)
        previous = [r for r in records(balance,True) if current and 330 <= (date.fromisoformat(current[1]['end'])-date.fromisoformat(r['end'])).days <= 400]
        if current and previous:
            p = max(previous,key=lambda r:(r['end'],r['filed']))
            average = ((current[0]+float(p['val']))/2, dict(current[1],filed=max(current[1]['filed'],p['filed'])))
            ratio(name,net,average,100)
    ratio('current_ratio',instant('current_assets'),instant('current_liabilities'),period='latest_balance')
    assets, inventory = instant('current_assets'), instant('inventory')
    if assets and inventory and assets[1]['end'] == inventory[1]['end']:
        ratio('quick_ratio',(assets[0]-inventory[0],dict(assets[1],filed=max(assets[1]['filed'],inventory[1]['filed']))),instant('current_liabilities'),period='latest_balance')
    current, long, short = instant('current_debt'), instant('long_debt'), instant('short_debt')
    if current and long and current[1]['end'] == long[1]['end']:
        debt = current[0]+long[0]
        components = [current[1]['filed'],long[1]['filed']]
        if short and short[1]['end'] == current[1]['end']:
            debt += short[0]
            components.append(short[1]['filed'])
        ratio('debt_equity',(debt,dict(current[1],filed=max(components))),instant('equity'),period='latest_balance')
    cash, capex = ttm('operating_cash'), ttm('capex')
    if cash and capex and cash[1]['end'] == capex[1]['end']:
        put('free_cash_flow',(cash[0]-capex[0],dict(cash[1],filed=max(cash[1]['filed'],capex[1]['filed']))),'ttm')
    put('shares_outstanding',instant('shares'),'latest_balance')
    for name, source in [('eps_growth_ttm','eps'),('revenue_growth_ttm','revenue')]:
        now, prior = ttm(source), ttm(source,4)
        if now and prior and prior[0] > 0 and 330 <= (date.fromisoformat(now[1]['end'])-date.fromisoformat(prior[1]['end'])).days <= 400:
            put(name,(100*(now[0]/prior[0]-1),dict(now[1],filed=max(now[1]['filed'],prior[1]['filed']))),'ttm_yoy')
    result = []
    for name,spec in METRICS.items():
        item = calculated.get(name)
        fact = item[1] if item else {}
        result.append(dict(symbol=symbol,metric=name,name=spec.title,value=item[0] if item else None,unit=spec.unit,
            period=item[2] if item else spec.period,period_end=fact.get('end'),fiscal_year=fact.get('fy'),
            filing_date=fact.get('filed'),currency=currency if spec.unit.startswith('currency') else None,
            is_estimate=spec.period == 'forward_estimate',**provenance(meta)))
        result[-1]['observed_at'] = None
        result[-1]['observed_date'] = fact.get('filed')
    return canonical_records(result, 'fundamentals')
