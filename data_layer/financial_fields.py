"""Canonical financial fields. Percent values use percentage points (15 means 15%)."""
from dataclasses import dataclass
from .field_values import raw, number


@dataclass(frozen=True)
class Metric:
    title: str
    key: str | None
    unit: str = 'ratio'
    period: str = 'ttm'
    remote: str | None = None
    scale: float = 1
    quote_scale: float | None = None


METRICS = {
    'pe': Metric('P/E TTM', 'trailingPE', remote='peratio.lasttwelvemonths'),
    'forward_pe': Metric('Forward P/E', 'forwardPE', period='forward_estimate'),
    'price_book': Metric('Price/Book', 'priceToBook', period='latest_balance', remote='pricebookratio.quarterly'),
    'peg': Metric('PEG (5Y)', 'pegRatio', period='forward_estimate', remote='pegratio_5y'),
    'ev_ebitda': Metric('EV/EBITDA', 'enterpriseToEbitda', remote='lastclosetevebitda.lasttwelvemonths'),
    'enterprise_value': Metric('Enterprise Value', 'enterpriseValue', 'currency', 'snapshot'),
    'eps_ttm': Metric('EPS TTM', 'trailingEps', 'currency/share'),
    'eps_forward': Metric('EPS Forward', 'forwardEps', 'currency/share', 'forward_estimate'),
    'revenue_ttm': Metric('Revenue TTM', 'totalRevenue', 'currency', remote='totalrevenues.lasttwelvemonths'),
    'net_income_ttm': Metric('Net Income TTM', 'netIncomeToCommon', 'currency', remote='netincomeis.lasttwelvemonths'),
    'ebitda_ttm': Metric('EBITDA TTM', 'ebitda', 'currency', remote='ebitda.lasttwelvemonths'),
    'revenue_growth': Metric('Revenue Growth (quarter YoY)', 'revenueGrowth', 'percent', 'latest_quarter', 'quarterlyrevenuegrowth.quarterly', 100),
    'earnings_growth': Metric('Earnings Growth (quarter YoY)', 'earningsGrowth', 'percent', 'latest_quarter', scale=100),
    'eps_growth_ttm': Metric('EPS Growth TTM YoY', None, 'percent', remote='epsgrowth.lasttwelvemonths'),
    'revenue_growth_ttm': Metric('Revenue Growth TTM YoY', None, 'percent', remote='totalrevenues1yrgrowth.lasttwelvemonths'),
    'gross_margin': Metric('Gross Margin', 'grossMargins', 'percent', remote='grossprofitmargin.lasttwelvemonths', scale=100),
    'operating_margin': Metric('Operating Margin', 'operatingMargins', 'percent', scale=100),
    'profit_margin': Metric('Profit Margin', 'profitMargins', 'percent', remote='netincomemargin.lasttwelvemonths', scale=100),
    'ebitda_margin': Metric('EBITDA Margin', 'ebitdaMargins', 'percent', remote='ebitdamargin.lasttwelvemonths', scale=100),
    'roe': Metric('ROE', 'returnOnEquity', 'percent', remote='returnonequity.lasttwelvemonths', scale=100),
    'roa': Metric('ROA', 'returnOnAssets', 'percent', remote='returnonassets.lasttwelvemonths', scale=100),
    'debt_equity': Metric('Debt/Equity', 'debtToEquity', period='latest_balance', scale=.01),
    'current_ratio': Metric('Current Ratio', 'currentRatio', period='latest_balance', remote='currentratio.lasttwelvemonths'),
    'quick_ratio': Metric('Quick Ratio', 'quickRatio', period='latest_balance', remote='quickratio.lasttwelvemonths'),
    'free_cash_flow': Metric('Free Cash Flow TTM', 'freeCashflow', 'currency', remote='leveredfreecashflow.lasttwelvemonths'),
    'operating_cash_flow': Metric('Operating Cash Flow TTM', 'operatingCashflow', 'currency', remote='cashfromoperations.lasttwelvemonths'),
    'dividend_yield': Metric('Dividend Yield TTM', 'trailingAnnualDividendYield', 'percent', remote='dividendyield', scale=100),
    'forward_dividend_yield': Metric('Dividend Yield Forward', 'dividendYield', 'percent', 'forward_estimate', 'forward_dividend_yield', 100, 1),
    'dividend_per_share': Metric('Dividend/Share TTM', 'trailingAnnualDividendRate', 'currency/share', remote='dividendpershare.lasttwelvemonths'),
    'payout_ratio': Metric('Payout Ratio', 'payoutRatio', 'percent', scale=100),
    'beta': Metric('Beta', 'beta', period='snapshot', remote='beta'),
    'shares_outstanding': Metric('Shares Outstanding', 'sharesOutstanding', 'shares', 'latest_balance', 'totalsharesoutstanding'),
    'float_shares': Metric('Float Shares', 'floatShares', 'shares', 'snapshot'),
    'short_float': Metric('Short Float', 'shortPercentOfFloat', 'percent', 'short_interest', 'short_percentage_of_float.value', 100),
    'short_ratio': Metric('Days to Cover', 'shortRatio', 'days', 'short_interest', 'days_to_cover_short.value'),
    'target_mean': Metric('Analyst Target Mean', 'targetMeanPrice', 'currency/share', 'forward_estimate'),
    'target_median': Metric('Analyst Target Median', 'targetMedianPrice', 'currency/share', 'forward_estimate'),
    'target_low': Metric('Analyst Target Low', 'targetLowPrice', 'currency/share', 'forward_estimate'),
    'target_high': Metric('Analyst Target High', 'targetHighPrice', 'currency/share', 'forward_estimate'),
    'analyst_count': Metric('Analyst Count', 'numberOfAnalystOpinions', 'count', 'forward_estimate'),
    'recommendation_mean': Metric('Recommendation (1=buy, 5=sell)', 'recommendationMean', 'rating', 'forward_estimate'),
}


def values(data, *, quote=False):
    result = {}
    for name, spec in METRICS.items():
        value = raw(data.get(spec.key)) if spec.key else None
        scale = spec.quote_scale if quote and spec.quote_scale is not None else spec.scale
        result[name] = number(value, scale)
    return result
