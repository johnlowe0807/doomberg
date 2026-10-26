"""Security dataset adapters. Selection semantics live exclusively in tabular.py."""
from __future__ import annotations

from .query_models import Quote
from .tabular import Dataset, Field, normalize_record
from . import field_values as scalar
from .financial_fields import METRICS

FIELDS = {
    'symbol': Field('text', 'Ticker'), 'name': Field('text', 'Name'), 'type': Field('text', 'Type', normalizer=scalar.security_type),
    'price': Field('number', 'Last', unit='native_price'), 'previous_close': Field('number', 'Prev Close', unit='native_price'),
    'change': Field('number', 'Chg', unit='native_price'), 'pct_change': Field('number', '%Chg', unit='percent'),
    'volume': Field('number', 'Volume', unit='provider_volume'), 'day_low': Field('number', 'Low', unit='native_price'), 'day_high': Field('number', 'High', unit='native_price'),
    'market_cap': Field('number', 'Market Cap', unit='currency'), 'pe': Field('number', 'P/E', unit='ratio'),
    'source': Field('text', 'Source', normalizer=scalar.source), 'freshness': Field('text', 'Freshness'),
    'observed_at': Field('timestamp', 'Observed UTC'), 'fetched_at': Field('timestamp', 'Fetched UTC'),
}
DEFAULTS = ('name', 'symbol', 'price', 'change', 'pct_change', 'volume', 'day_low', 'day_high', 'source', 'freshness')


def quote_row(symbol, name, kind, data):
    q = Quote(**data) if data else None
    return normalize_record({'symbol': symbol, 'name': name, 'type': kind,
            'price': q.price if q else None, 'previous_close': q.prev_close if q else None,
            'change': q.change if q else None, 'pct_change': q.pct if q else None,
            'volume': q.volume if q else None, 'day_low': q.day_low if q else None,
            'day_high': q.day_high if q else None, 'source': q.source if q else None,
            'freshness': q.tag if q else 'N/A', 'observed_at': q.ts if q else None,
            'fetched_at': q.fetched if q else None}, FIELDS)


def register(service):
    from .yahoo_screening import execute, plan
    names = ('equity', 'etf', 'fund', 'bonds', 'crypto', 'futures', 'cmdty', 'fx', 'index',
             'most_gainers', 'most_losers', 'most_active')
    for name in names:
        fields = dict(FIELDS)
        fields.update(region=Field('text', 'Region'), exchange=Field('text', 'Exchange'),
                      currency=Field('text', 'Currency', normalizer=scalar.currency, case_sensitive=True))
        if name == 'equity' or name.startswith('most_'):
            fields.update(sector=Field('text', 'Sector'), industry=Field('text', 'Industry'))
            fields.update({key:Field('number', spec.title, unit=spec.unit, period=spec.period) for key,spec in METRICS.items()})
            fields.update(financial_source=Field('text','Financial Source', normalizer=scalar.source), financial_period_end=Field('date','Financial Period End'),
                          financial_filing_date=Field('date','Financial Filed'), financial_fetched_at=Field('timestamp','Financial Fetched UTC'),
                          financial_currency=Field('text','Financial Currency', normalizer=scalar.currency, case_sensitive=True))
        if name in ('etf', 'fund', 'bonds'):
            fields['category'] = Field('text', 'Category')
        if name in ('futures', 'cmdty', 'fx', 'index', 'fund'):
            fields.pop('market_cap')
            fields.pop('pe')
        elif name == 'crypto':
            fields.pop('pe')
        defaults = DEFAULTS[:-2] + ('currency',) + DEFAULTS[-2:]
        service.register_table(name, Dataset(fields, defaults, ('symbol', 'name', 'type'), None, execute, plan))
