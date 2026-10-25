"""Public FRED series descriptions, source metadata and explanatory notes."""
from __future__ import annotations

import asyncio
import re
from bs4 import BeautifulSoup
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static
from textual.binding import Binding

from .app import VIEWS, View


from data_layer.normalization import parse_description


async def description(hub, sid):
    if not re.fullmatch(r'[A-Z0-9_.-]{1,80}', sid):
        raise ValueError('Invalid FRED series ID')
    raw = await hub.http('fred', f'https://fred.stlouisfed.org/series/{sid}', 2,
                         ttl=21600, disk_ns='fred-meta', disk_age=86400, as_json=False)
    return await asyncio.to_thread(parse_description, raw) if raw else ({}, '')


class FredDesView(View):
    FUNC = 'FRED DES'
    hints = '[ ] metadata/notes  g graph series  PageDown notes  Esc back  /FRED? search'
    BINDINGS = [Binding('pagedown', 'notes', show=False, priority=True),
                Binding('right_square_bracket', 'notes', show=False, priority=True),
                Binding('left_square_bracket', 'metadata', show=False, priority=True)]

    def compose(self):
        yield Static('', id='info', classes='msg', markup=False)
        table = DataTable(show_header=False, cursor_type='row')
        table.styles.height = '40%'
        table.styles.min_height = 3
        table.styles.max_height = 12
        yield table
        with VerticalScroll(id='fred-notes'):
            yield Static('', id='notes', markup=False)

    async def load(self):
        from .charts import fred
        from .fred_search import catalog
        (fields, notes), series = await asyncio.gather(description(self.hub, self.sec), fred(self.hub, self.sec))
        fallback = next((r for r in catalog() if r[0] == self.sec), None)
        title = fields.get('Title') or (fallback[1] if fallback else self.sec)
        self.query_one('#info', Static).update(title)
        values = [('Series', self.sec), ('Source', fields.get('Source') or 'N/A'),
                  ('Release', fields.get('Release') or 'N/A'), ('Units', fields.get('Units') or (fallback[3] if fallback else 'N/A')),
                  ('Adjustment', fields.get('Adjustment') or (fallback[4] if fallback else 'N/A')),
                  ('Frequency', fields.get('Frequency') or (fallback[2] if fallback else 'N/A')),
                  ('Updated', fields.get('Updated') or 'N/A')]
        if series is not None and len(series):
            values += [('Latest observation', f'{series.index[-1]:%Y-%m-%d}: {series.iloc[-1]:g}'),
                       ('Available history', f'{series.index[0]:%Y-%m-%d} – {series.index[-1]:%Y-%m-%d}'),
                       ('Observations', f'{len(series):,}')]
        table = self.query_one(DataTable)
        table.clear(columns=True); table.add_columns('Field', 'Value')
        for label, value in values:
            table.add_row(Text(label, style='#3A6B80'), value)
        self.query_one('#notes', Static).update('NOTES\n' + (notes or 'N/A — series notes unavailable') +
                                              f'\n\nFRED · https://fred.stlouisfed.org/series/{self.sec}\nData may be revised.')

    def on_key(self, event):
        if event.character == 'g':
            self.app.run_command('/FRED ' + self.sec); event.stop()

    def action_notes(self):
        notes = self.query_one('#fred-notes')
        if notes.has_focus:
            notes.scroll_page_down()
        notes.focus()

    def action_metadata(self):
        self.query_one(DataTable).focus()


VIEWS['FREDDES'] = FredDesView
