"""Read the complete selected financial record without fetching another dataset."""
from data_layer.query_language import format_query
from textual.binding import Binding
from .fundamentals import Pager


def detail_text(view, record):
    kind = record.get('kind')
    if view.board == 'fundamentals':
        names = ('metric', 'name', 'value', 'unit', 'period', 'period_end', 'fiscal_year',
                 'filing_date', 'currency', 'is_estimate', 'observed_date')
    elif kind == 'targets':
        names = ('target_low', 'target_mean', 'target_median', 'target_high', 'currency',
                 'analyst_count', 'recommendation_mean', 'recommendation')
    elif kind == 'recommendation':
        names = ('period', 'strong_buy', 'buy', 'hold', 'sell', 'strong_sell')
    elif kind == 'rating_change':
        names = ('event_at', 'firm', 'from_grade', 'to_grade', 'action')
    elif kind == 'estimate':
        names = tuple(name for name in view.dataset.fields if name not in
                      ('symbol', 'source', 'freshness', 'observed_at', 'fetched_at', 'date',
                       'event_at', 'firm', 'from_grade', 'to_grade', 'action', 'strong_buy', 'buy', 'hold', 'sell', 'strong_sell'))
    else:
        names = ('fund', 'symbol', 'kind', 'name', 'weight', 'value', 'unit', 'currency', 'report_date')
    fields = view.dataset.fields
    lines = [view.title, str(record.get('name') or record.get('metric') or kind or 'Record'), '']

    def append(name):
        spec, value = fields[name], record.get(name)
        if isinstance(value, bool):
            value = 'yes' if value else 'no'
        elif isinstance(value, (float, int)):
            value = format(value, '.15g')
        unit = record.get('unit') if name == 'value' else spec.unit
        suffix = ' %' if unit == 'percent' else ''
        lines.append(f'{spec.title or name}: {"N/A" if value is None else str(value) + suffix}')

    for name in names:
        if name in fields:
            append(name)
    lines += ['', 'Source and freshness']
    for name in ('source', 'freshness', 'observed_at', 'fetched_at'):
        append(name)
    if view.result.meta.reason:
        lines += [view.result.meta.reason]
    if view.board == 'fundamentals':
        lines += ['', 'Period and filing dates describe different events. TTM means trailing twelve months.',
                  'This record is a snapshot; a metric history and exact calculation-input drill-down are not available here.',
                  'Existing statement history is available through /FA; SEC filing-date snapshots through /METRICS ticker SEC YYYY-MM-DD.']
    elif kind == 'estimate':
        lines += ['', 'The 7/30/60/90-day EPS values and revision counts are supplied in the current provider snapshot.',
                  'They are not a complete historical estimate archive.']
    elif view.board == 'holdings':
        lines += ['', 'Reported positions may be top holdings only. Unknown reporting dates remain N/A.']
    elif kind == 'targets':
        lines += ['', 'Consensus price targets are a current snapshot; a historical target series is not available here.']
    lines += ['', 'Query context', format_query(view.table_query), '',
              'q / Esc returns to the same table and selection. Shift+Enter on the table opens the security price graph.']
    return '\n'.join(lines)


class FinancialDetail(Pager):
    BINDINGS = [Binding('escape', 'close_details', show=False, priority=True)]

    def __init__(self, view, record):
        self.record = dict(record)
        label = record.get('metric') or record.get('kind') or 'record'
        super().__init__(f'{view.title} · {label}', detail_text(view, record))

    def on_mount(self):
        super().on_mount()
        self.query_one('#phint').update('j/k scroll  space/b page  g/G top/bottom  / search  n/N next/prev  q / Esc return')

    def action_close_details(self):
        self.close()
