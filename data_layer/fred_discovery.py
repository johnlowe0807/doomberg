"""FRED discovery catalog and query validation, independent of rendering."""
from .symbols import parse_filters, matches_filters

COLUMNS = {"s": 0, "n": 1, "f": 2, "u": 3, "a": 4, "b": 5, "e": 6}
FREQUENCIES = {"D": "Daily", "W": "Weekly", "BW": "Biweekly", "M": "Monthly",
               "Q": "Quarterly", "SA": "Semiannual", "A": "Annual"}
ADJUSTMENTS = {"SA": "Seasonally Adjusted", "NSA": "Not Seasonally Adjusted",
               "SAAR": "Seasonally Adjusted Annual Rate"}
HELP = """FRED search · /FRED? text [filters] · Enter opens the selected series graph
_s= series ID contains · _n= title contains · _f= frequency · _u= units contains
_a= seasonal adjustment · _b= observation start contains · _e= observation end contains
Frequency values: D/Daily, W/Weekly, BW/Biweekly, M/Monthly, Q/Quarterly,
SA/Semiannual, A/Annual. Weekly/biweekly also match their ending-day variants.
Adjustment values: SA, NSA, SAAR (or their full names).
Other column filters match text, ignoring case. Dates match the displayed source date,
e.g. _b=1947 or _e=2026. Missing metadata displays N/A and never matches a filter.
Filters combine with AND; comma-separated alternatives within a filter use OR.
Quote values containing spaces: _u="Percent Change".
/FRED? inflation _f=M _a=SA · /FRED? treasury _f=D _u=Percent
/FRED? _s=DGS · /FRED? unemployment _n=rate · /FRED?? opens this page.
No API key needed. Text searches inspect up to four public result pages; at most
100 matches are displayed. Filters apply to those candidates, not the entire FRED catalog.
Blank/filter-only searches browse local and previously indexed series; use text for broader discovery.
If FRED is unavailable, cached pages/local matches remain available and are labelled.
"""


def catalog():
    from .catalog import TENORS
    rows = [(sid, f"Market Yield on U.S. Treasury Securities at {tenor} Constant Maturity",
             "Daily", "Percent", "Not Seasonally Adjusted", "N/A", "N/A") for tenor, sid in TENORS]
    rows += [(sid, title, freq, units, adjustment, "N/A", "N/A") for sid, title, freq, units, adjustment in [
        ("DFF", "Federal Funds Effective Rate", "Daily", "Percent", "Not Seasonally Adjusted"),
        ("SOFR", "Secured Overnight Financing Rate", "Daily", "Percent", "Not Seasonally Adjusted"),
        ("DFII10", "10-Year Treasury Inflation-Indexed Security, Constant Maturity", "Daily", "Percent", "Not Seasonally Adjusted"),
        ("T10Y2Y", "10-Year Treasury Constant Maturity Minus 2-Year Treasury Constant Maturity", "Daily", "Percent", "Not Seasonally Adjusted"),
        ("T10Y3M", "10-Year Treasury Constant Maturity Minus 3-Month Treasury Constant Maturity", "Daily", "Percent", "Not Seasonally Adjusted"),
        ("T10YIE", "10-Year Breakeven Inflation Rate", "Daily", "Percent", "Not Seasonally Adjusted"),
        ("BAMLC0A0CM", "ICE BofA US Corporate Index Option-Adjusted Spread", "Daily", "Percent", "Not Seasonally Adjusted"),
        ("BAMLH0A0HYM2", "ICE BofA US High Yield Index Option-Adjusted Spread", "Daily", "Percent", "Not Seasonally Adjusted"),
        ("MORTGAGE30US", "30-Year Fixed Rate Mortgage Average in the United States", "Weekly", "Percent", "Not Seasonally Adjusted"),
        ("CPIAUCSL", "Consumer Price Index for All Urban Consumers: All Items in U.S. City Average", "Monthly", "Index 1982-1984=100", "Seasonally Adjusted"),
        ("UNRATE", "Unemployment Rate", "Monthly", "Percent", "Seasonally Adjusted"),
        ("PAYEMS", "All Employees, Total Nonfarm", "Monthly", "Thousands of Persons", "Seasonally Adjusted"),
        ("GDP", "Gross Domestic Product", "Quarterly", "Billions of Dollars", "Seasonally Adjusted Annual Rate"),
        ("GDPC1", "Real Gross Domestic Product", "Quarterly", "Billions of Chained 2017 Dollars", "Seasonally Adjusted Annual Rate"),
        ("PCEPI", "Personal Consumption Expenditures: Chain-type Price Index", "Monthly", "Index 2017=100", "Seasonally Adjusted"),
    ]]
    return rows


def parse_query(query):
    text, filters = parse_filters(query, COLUMNS)
    for key, aliases in (("f", FREQUENCIES), ("a", ADJUSTMENTS)):
        if key in filters:
            values = [aliases.get(value.strip().upper(), value.strip()) for value in filters[key].split(',')]
            if any(value.casefold() not in {v.casefold() for v in aliases.values()} for value in values):
                raise ValueError(f"Unknown _{key}= value; /FRED?? lists filters and values")
            filters[key] = ','.join(values)
    return text, filters


def matches(row, filters):
    if any(row[COLUMNS[key]] == "N/A" for key in filters):
        return False
    regular = {key: value for key, value in filters.items() if key != 'f'}
    if not matches_filters(row, regular, COLUMNS, ('a',)):
        return False
    return 'f' not in filters or any(row[2].casefold().split(',')[0].strip() == v.strip().casefold()
                                   for v in filters['f'].split(','))
