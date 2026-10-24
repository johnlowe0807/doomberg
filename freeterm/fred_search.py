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

from .fred_discovery import COLUMNS, FREQUENCIES, ADJUSTMENTS, HELP, catalog, parse_query, matches

from .normalization import parse_page


async def search(hub, query, publish=None):
    from .query_models import SearchQuery
    text, filters = parse_query(query)
    request = SearchQuery('fred', text, filters)
    if publish:
        initial = await hub.queries.query(request, stored_only=True)
        publish(initial.data or [], 'Stored search index · searching FRED…' if text else 'Local/stored search index')
    result = await hub.query(request)
    label = 'FRED search index · up to 4 discovery pages / 100 displayed matches' if text else 'Local/stored search index'
    if result.meta.freshness != 'fresh':
        label += ' · STALE / sources unavailable'
    return result.data or [], label


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
