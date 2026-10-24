"""Keyless FRED discovery using public search pages, with a small offline catalog."""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from .app import VIEWS, View
from .symbols import parse_filters, matches_filters, rank

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
Blank/filter-only searches browse a small local catalog; use text for broader discovery.
If FRED is unavailable, cached pages/local matches remain available and are labelled.
"""


def catalog():
    from .boards import TENORS
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


def parse_page(html):
    soup = BeautifulSoup(html, 'lxml')
    rows = []
    for block in soup.select('.search-series'):
        title = block.select_one('a.series-title[href*="/series/"]')
        if title is None:
            continue
        sid = title['href'].split('/series/')[-1].split('?')[0]
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', sid):
            continue
        meta = block.select_one('.search-result-meta')
        metadata = meta.get_text(' ', strip=True) if meta else ''
        found = re.fullmatch(r'(.*?),\s*((?:Daily|Weekly|Biweekly|Monthly|Quarterly|Annual|Semiannual)\b.*),\s*([^,]+)', metadata, re.I)
        units, frequency, adjustment = found.groups() if found else ('N/A', 'N/A', 'N/A')
        dates = block.select_one('.search-result-meta-dates')
        date_text = dates.get_text(' ', strip=True) if dates else ''
        period = re.match(r'(.+?)\s+to\s+(.+?)(?:\s*\(.*\))?$', date_text)
        start, end = period.groups() if period else ('N/A', 'N/A')
        rows.append((sid.upper(), title.get_text(' ', strip=True), frequency.strip(), units.strip(),
                     adjustment.strip(), start.strip(), end.strip()))
    pages = [int(m.group(1)) for a in soup.select('a[href]')
             if (m := re.search(r'[?&]pageID=(\d+)', a['href']))]
    return rows, max(pages, default=1)


async def search(hub, query, publish=None):
    text, filters = parse_query(query)
    rows = {row[0]: row for row in catalog() if not text or rank(text, row[0], row[1]) < 99}

    def results():
        return sorted((row for row in rows.values() if matches(row, filters)),
                      key=lambda row: (rank(text, row[0], row[1]), row[0]))[:100]

    if publish:
        publish(results(), 'Local catalog · searching FRED…' if text else 'Local catalog')
    if not text:
        return results(), 'Local catalog · add search text for broader FRED discovery'
    source = 'FRED public search · up to 4 pages / 100 displayed matches'
    for page in range(1, 5):
        url = 'https://fred.stlouisfed.org/searchresults/?' + urlencode({'st': text, 'pageID': page})
        warnings_before = len(hub.warnings)
        raw = await hub.http('fred-search', url, 2, ttl=3600, disk_ns='fred-search', disk_age=86400,
                             as_json=False, retries=0)
        if not raw:
            source = 'FRED unavailable · cached/local results only'
            break
        found, last_page = await asyncio.to_thread(parse_page, raw)
        stale = len(hub.warnings) > warnings_before
        if stale:
            source = 'FRED unavailable · cached/local results only'
        if not found and 'searchContentContainer' not in raw:
            source = 'FRED search page unavailable · cached/local results only'
            hub.warn('FRED search did not return a readable results page')
            break
        rows.update((row[0], row) for row in found)
        if publish:
            publish(results(), source)
        if stale or page >= last_page:
            break
    return results(), source


class FredSearchView(View):
    FUNC = 'FRED SEARCH'
    hints = 'Enter graph series  /FRED?? filters/help  /FRED? text [filters]'

    @property
    def title(self):
        return 'FRED SEARCH ' + ' '.join(self.args)

    def compose(self):
        yield Static('', classes='msg', id='info', markup=False)
        yield DataTable(cursor_type='row')

    def render_matches(self, rows, source):
        table = self.query_one(DataTable)
        selected = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value if table.row_count else None
        table.clear(columns=True)
        table.add_columns('Series', 'Name', 'Frequency', 'Units', 'Adjustment', 'Start', 'End')
        for i, row in enumerate(rows):
            table.add_row(*row, key=row[0])
            if row[0] == selected:
                table.move_cursor(row=i)
        self.query_one('#info', Static).update(f'{source} · {len(rows)} matches')

    async def load(self):
        try:
            rows, source = await search(self.hub, ' '.join(self.args), self.render_matches)
        except ValueError as error:
            self.render_matches([], str(error))
            self.hub.warn(str(error))
            return
        self.render_matches(rows, source)

    def on_data_table_row_selected(self, event):
        event.stop()
        self.app.run_command('/FRED ' + str(event.row_key.value))


class FredSearchHelpView(View):
    FUNC = 'FRED SEARCH HELP'
    hints = 'Up/Down scroll  Esc back  /FRED? text search'

    def compose(self):
        with VerticalScroll():
            yield Static(HELP, markup=False)


VIEWS.update({'FREDSEARCH': FredSearchView, 'FREDSEARCHHELP': FredSearchHelpView})
