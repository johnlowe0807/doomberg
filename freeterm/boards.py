"""Phase 3/4 boards: WEI CMDTY FXC CRYPTO MOST HOME, bonds GC BTMM AUCT, IEX toggle."""
from __future__ import annotations

import asyncio
import time

import pandas as pd
import numpy as np
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Static

from .app import DIM, WARN, VIEWS, View, MonView, PromptScreen, fbig, fnum, colored, tag_text
from .charts import QuoteChartView, HomePreview, fred, PALETTE
from data_layer.query_models import Quote
from .pixelcharts import ChartSurface, Panel, Scene, Trace

from data_layer.catalog import BOARDS, TAPE


class QuoteBoard(View):
    """Thin table client; all selection and ordering run through TableQuery."""
    FUNC = "INDEX"
    board = "index"
    view_commands_enabled = table_commands_enabled = True
    hints = ". commands  ? fields/help  Enter graph  Ctrl+R refresh"
    refresh_secs = 15
    DEFAULT_CSS = 'QuoteBoard > #info { height: auto; max-height: 3; } QuoteBoard > #query-settings { height: auto; max-height: 3; color: #FF5FD2; }'

    def __init__(self, hub, sec, args):
        super().__init__(hub, sec, args)
        from data_layer.query_models import TableQuery
        self.table_query = TableQuery(self.board)
        self.command_history = []
        self.result = None

    @property
    def dataset(self):
        return self.hub.queries.table_dataset(self.table_query.dataset)

    def compose(self) -> ComposeResult:
        yield Static("Querying market universe…", id="info", markup=False)
        yield Static('', id='query-settings', markup=False)
        yield DataTable(cursor_type="row")

    def execute_graph_command(self, line):
        from .screen_commands import execute
        return execute(self, line)

    def open_graph_commands(self):
        from .graph_commands import GraphCommandScreen
        self.app.push_screen(GraphCommandScreen(self))

    def render_result(self, result):
        from data_layer.query_language import format_query
        self.result = result
        self.query_one('#query-settings', Static).update(format_query(self.table_query))
        table = self.query_one(DataTable)
        selected = None
        if table.row_count:
            selected = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        table.clear(columns=True)
        self.navigation = []
        if result.data is None:
            self.query_one("#info", Static).update("N/A · " + result.meta.reason)
            return
        data = result.data
        for field in data.fields:
            table.add_column(self.dataset.fields[field].title or field, key=field)
        for i, row in enumerate(data.rows):
            cells = []
            for field in data.fields:
                value = row.get(field)
                if self.dataset.fields[field].kind == 'number':
                    spec = self.dataset.fields[field]
                    unit = row.get('unit') if field == 'value' else spec.unit
                    text = fbig(value) if field in ('volume', 'market_cap') or unit in ('currency','shares') else fnum(value)
                    if unit == 'percent' or field == 'pct_change':
                        text += '%'
                    cells.append(colored(value, text) if field in ('change', 'pct_change') else Text(text, justify='right'))
                else:
                    cells.append(Text('N/A' if value is None else str(value), style=WARN if value == 'STALE' else ''))
            table.add_row(*cells, key=str(i))
        # Navigation identifiers are supplied separately from the user's projected fields.
        self.navigation = self._navigation
        if selected is not None and table.row_count:
            table.move_cursor(row=min(int(selected), table.row_count - 1))
        coverage = ('market-ranked' if data.ranking_complete else 'PARTIAL ranking') if data.execution == 'yahoo' else ('complete listed universe' if data.complete else 'PARTIAL coverage')
        missing = ', '.join(f'{field}:{count}' for field, count in data.missing.items()) or 'none'
        counts = (f"{len(data.rows)} shown / {'' if data.matched_exact else 'at least '}{data.matched} matched / {data.available} fetched of {data.total} candidates"
                  if data.execution == 'yahoo' else f"{len(data.rows)} shown / {data.matched} matched / {data.total} listed")
        self.query_one("#info", Static).update(
            f"{data.universe} · {coverage} · {counts}"
            f" · {result.meta.source} {result.meta.freshness.upper()} · missing {missing}"
            + (f"\n{result.meta.reason}" if result.meta.reason else ''))
        if getattr(self.parent, 'id', None) == 'home-movers':
            self.parent.query_one('#home-movers-label', Static).update(self.title)

    async def load(self):
        # Always retain the identifier for Enter, even if FIELDS omits its display.
        from dataclasses import replace
        request = self.table_query
        fields = request.fields or self.dataset.defaults
        result = await self.hub.query(replace(request, fields=tuple(dict.fromkeys((*fields, 'symbol')))))
        if request != self.table_query:
            return
        if result.data is not None:
            self._navigation = [row['symbol'] for row in result.data.rows]
            projected = tuple({field: row.get(field) for field in fields} for row in result.data.rows)
            result = replace(result, data=replace(result.data, rows=projected, fields=fields))
        else:
            self._navigation = []
        self.render_result(result)

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        from shlex import quote
        index = int(event.row_key.value)
        if index < len(getattr(self, 'navigation', ())):
            self.app.run_command('/GP _symbol=' + quote(self.navigation[index]))


class FuturesBoard(QuoteBoard):
    FUNC, board = "FUTURES", "futures"
    refresh_secs = 300

    def __init__(self, hub, sec, args):
        super().__init__(hub, sec, args)
        from dataclasses import replace
        from data_layer.query_models import SortOrder
        self.table_query = replace(self.table_query, sort=(SortOrder('volume', True),))


class CryptoBoard(QuoteBoard):
    FUNC, board = "CRYPTO", "crypto"


class EquityBoard(QuoteBoard):
    FUNC, board = "EQUITY", "equity"


class EtfBoard(QuoteBoard):
    FUNC, board = "ETF", "etf"


class FxBoard(QuoteBoard):
    FUNC, board = "FX", "fx"


class FundBoard(QuoteBoard):
    FUNC, board = "FUND", "fund"
    refresh_secs = 300


class BondBoard(QuoteBoard):
    FUNC, board = "ETF", "etf"

    def __init__(self, hub, sec, args):
        super().__init__(hub, sec, args)
        from dataclasses import replace
        from data_layer.query_models import Condition
        from data_layer.yahoo_screening import bond_categories
        self.table_query = replace(self.table_query, where=Condition('category', 'IN', bond_categories()))


class FinancialTableView(QuoteBoard):
    refresh_secs = None

    @property
    def title(self):
        title = f'{self.sec or ""} {self.FUNC}'.strip()
        request = getattr(self,'table_query',None)
        parameters = request.parameters if request else {}
        if parameters.get('source') == 'sec':
            title += ' SEC' + (' filed ≤ ' + parameters['as_of'] if parameters.get('as_of') else '')
        return title

    def __init__(self, hub, sec, args):
        super().__init__(hub, sec, args)
        from dataclasses import replace
        from data_layer.financial_data import validate_table
        parameters = {'symbol':sec}
        if self.board == 'fundamentals':
            if len(args) > 2 or (args and args[0].upper() not in ('YF','YAHOO','SEC')):
                raise ValueError('Use /METRICS ticker [SEC [YYYY-MM-DD]]; dot commands filter/sort the result')
            parameters['source'] = 'sec' if args and args[0].upper() == 'SEC' else 'yahoo'
            if len(args) == 2:
                parameters['as_of'] = args[1]
        elif args:
            raise ValueError(f'Use /{self.FUNC} ticker, followed by dot query commands')
        self.table_query = replace(self.table_query,parameters=parameters)
        validate_table(self.table_query,self.dataset)


class MetricsView(FinancialTableView):
    FUNC, board = 'METRICS', 'fundamentals'


class EstimatesView(FinancialTableView):
    FUNC, board = 'EST', 'estimates'


class AnalystView(FinancialTableView):
    FUNC, board = 'ANALYST', 'analysts'


class HoldingsView(FinancialTableView):
    FUNC, board = 'HOLD', 'holdings'


class MostBoard(QuoteBoard):
    FUNC, board = "EQUITY", "equity"
    hints = "s gainers/losers/active  . commands  ? fields/help  Enter graph"
    modes = ('gainers', 'losers', 'active')

    def __init__(self, hub, sec, args):
        super().__init__(hub, sec, args)
        self.m = 0
        self.set_mode()

    @property
    def title(self):
        return 'EQUITY ' + self.modes[self.m]

    def set_mode(self):
        from dataclasses import replace
        from data_layer.query_models import SortOrder, Condition
        field, descending = ('volume', True) if self.m == 2 else ('pct_change', self.m == 0)
        self.table_query = replace(self.table_query, where=None if self.m == 2 else Condition('pct_change', '>' if self.m == 0 else '<', 0), sort=(SortOrder(field, descending),))

    def key_s(self):
        self.m = (self.m + 1) % len(self.modes)
        self.set_mode()
        self.retitle()
        self.reload()


class FxcView(View):
    FUNC = "FXC"
    hints = "Cross-rate matrix: row currency priced in column currency"
    refresh_secs = 20
    CCY = {"USD": None, "EUR": ("EURUSD=X", False), "GBP": ("GBPUSD=X", False), "JPY": ("JPY=X", True),
           "CHF": ("CHF=X", True), "AUD": ("AUDUSD=X", False), "CAD": ("CAD=X", True), "CNY": ("CNY=X", True)}

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="cell")

    async def load(self) -> None:
        qs = await self.hub.quotes([v[0] for v in self.CCY.values() if v])
        usd = {"USD": 1.0}  # value of 1 unit in USD
        for c, v in self.CCY.items():
            if v and v[0] in qs:
                p = qs[v[0]].price
                usd[c] = 1 / p if v[1] else p
        t = self.query_one(DataTable)
        t.clear(columns=True)
        t.add_columns("", *self.CCY)
        for r in self.CCY:
            cells = [Text(r, style="bold #FFFFFF")]
            for c in self.CCY:
                v = usd.get(r) / usd[c] if r in usd and c in usd else None
                cells.append(Text("—" if r == c else fnum(v, 4 if v and v < 10 else 2), justify="right",
                                  style=DIM if r == c else ""))
            t.add_row(*cells)


class HomeView(View):
    FUNC = "HOME"
    hints = "[ ] watchlist/movers/headlines  Enter open  arrows preview watchlist chart  s sort/movers"
    BINDINGS = [Binding("left_square_bracket", "section(-1)", show=False),
                Binding("right_square_bracket", "section(1)", show=False)]
    refresh_secs = 20

    def compose(self) -> ComposeResult:
        from .depth import NewsView
        yield Static("", id="tape", classes="msg")
        with Horizontal(id="home-upper"):
            with Vertical():
                yield Static("WATCHLIST", classes="msg")
                yield MonView(self.hub, None, [])
            yield HomePreview(self.hub, "^GSPC", [])
        with Horizontal(id="home-lower"):
            with Vertical(id="home-movers"):
                yield Static("MOVERS", id="home-movers-label", classes="msg")
                yield MostBoard(self.hub, None, [])
            with Vertical():
                yield Static("HEADLINES", classes="msg")
                yield NewsView(self.hub, None, [])
        yield Static("", id="rates", classes="msg")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "mon":
            return
        symbol = event.row_key.value
        chart = self.query_one(QuoteChartView)
        timer = getattr(self, "preview_timer", None)
        if timer:
            timer.stop()
        if symbol and symbol != chart.sec:
            self.preview_timer = self.set_timer(0.3, lambda: self.preview(symbol))

    def preview(self, symbol: str) -> None:
        chart = self.query_one(QuoteChartView)
        chart.sec = symbol
        chart.df = None
        chart.zoom, chart.cx, chart.fit_pending = (0.0, 1.0), None, True
        chart.prefetched = False
        chart.reload()

    async def load(self) -> None:
        qs = await self.hub.quotes([s for s, _ in TAPE])
        t = Text()
        for s, n in TAPE:
            q = qs.get(s)
            t.append(f"{n} ", style="bold #FFFFFF")
            t.append(fnum(q.price) + " " if q else "N/A ")
            if q:
                t.append_text(colored(q.pct, fnum(q.pct) + "%"))
            t.append("  ")
        self.query_one("#tape", Static).update(t)
        vals = await asyncio.gather(*(fred(self.hub, s) for s in ("DGS2", "DGS10", "T10Y2Y", "DFF")))
        r = Text("RATES ", style="bold #FFFFFF")
        for n, s in zip(("2Y", "10Y", "2s10s", "FF"), vals):
            r.append(f"{n} {fnum(s.iloc[-1]) if s is not None and len(s) else 'N/A'}  ")
        r.append("(FRED daily)", style=DIM)
        self.query_one("#rates", Static).update(r)


# ---------------------------------------------------------------- bonds
from data_layer.catalog import TENORS


def asof(s: pd.Series | None, when: pd.Timestamp):
    if s is None:
        return None
    s = s[s.index <= when]
    return float(s.iloc[-1]) if len(s) else None


class GCView(View):
    FUNC = "GC"
    hints = "t table/chart  d custom date  (FRED daily par yields)"

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row")
        yield ChartSurface(self.app.cfg.get("chart", "renderer", "auto"))
        yield Static("Dealer quotes, swaps and corporate bond trades are not available from free sources.", classes="msg")

    def on_mount(self) -> None:
        self.chart, self.custom = False, None
        self.query_one(ChartSurface).display = False
        super().on_mount()

    async def load(self) -> None:
        ss = await asyncio.gather(*(fred(self.hub, sid) for _, sid in TENORS))
        last = max((s.index[-1] for s in ss if s is not None), default=pd.Timestamp.today())
        whens = [("Current", last), ("1M ago", last - pd.DateOffset(months=1)), ("1Y ago", last - pd.DateOffset(years=1))]
        if self.custom is not None:
            whens.append((f"{self.custom:%Y-%m-%d}", self.custom))
        self.curves = [(n, w, [asof(s, w) for s in ss]) for n, w in whens]
        t = self.query_one(DataTable)
        t.clear(columns=True)
        t.add_columns("Curve", "As of", *[n for n, _ in TENORS], "2s10s", "3m10y")
        for n, w, ys in self.curves:
            sp = lambda a, b: fnum((ys[b] - ys[a]) * 100, 0) + "bp" if ys[a] is not None and ys[b] is not None else "N/A"
            t.add_row(Text(n, style="bold"), f"{w:%Y-%m-%d}", *[Text(fnum(y), justify="right") for y in ys], sp(4, 8), sp(1, 8))
        self.draw()

    def draw(self) -> None:
        traces = [Trace(name, np.asarray(ys, dtype=float), PALETTE[j % len(PALETTE)])
                  for j, (name, _, ys) in enumerate(getattr(self, "curves", []))]
        self.query_one(ChartSurface).submit(Scene([Panel("Treasury yield %", traces)],
                                                [name for name, _ in TENORS],
                                                font_size=int(self.app.cfg.get("chart", "font_px", 12))))

    def key_t(self) -> None:
        self.chart = not self.chart
        self.query_one(ChartSurface).display = self.chart
        self.query_one(DataTable).display = not self.chart
        if self.chart:
            self.call_after_refresh(self.draw)

    def key_d(self) -> None:
        def setd(v: str) -> None:
            try:
                self.custom = pd.Timestamp(v)
                self.reload()
            except Exception:
                self.hub.warn("bad date; use YYYY-MM-DD")
        self.app.push_screen(PromptScreen("Overlay curve date (YYYY-MM-DD):"), setd)


class BTMMView(View):
    FUNC = "BTMM"
    BINDINGS = [Binding("[", "section(-1)", show=False), Binding("]", "section(1)", show=False)]
    hints = "[ ] switch rates / ETFs  FRED = daily close; ^IRX/^FVX/^TNX/^TYX = intraday proxies (DLY)"
    refresh_secs = 30
    FRED_ROWS = [(n, s) for n, s in TENORS] + [("Fed Funds", "DFF"), ("SOFR", "SOFR"), ("10Y TIPS real", "DFII10"),
                                               ("Mortgage 30Y", "MORTGAGE30US")]
    ETFS = "SHY IEF TLT TIP LQD HYG AGG MUB EMB BND".split()
    PROXIES = [("^IRX", "13W bill"), ("^FVX", "5Y note"), ("^TNX", "10Y note"), ("^TYX", "30Y bond")]

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield DataTable(id="rates", cursor_type="row")
            yield DataTable(id="etf", cursor_type="row")
        yield Static("Dealer quotes, swaps and corporate bond trades are not available from free sources.", classes="msg")

    async def load(self) -> None:
        ss = await asyncio.gather(*(fred(self.hub, s) for _, s in self.FRED_ROWS))
        t = self.query_one("#rates", DataTable)
        t.clear(columns=True)
        t.add_columns("Rate", "Yield", "Chg bp", "As of")
        d = dict(zip((s for _, s in self.FRED_ROWS), ss))
        for (n, sid), s in zip(self.FRED_ROWS, ss):
            if s is None or len(s) < 2:
                t.add_row(n, "N/A", "", "")
                continue
            ch = (s.iloc[-1] - s.iloc[-2]) * 100
            t.add_row(n, Text(fnum(s.iloc[-1], 3), justify="right"), colored(ch, fnum(ch, 1)), f"{s.index[-1]:%m-%d}")
        if d.get("DGS10") is not None and d.get("DFII10") is not None:
            t.add_row(Text("10Y breakeven", style="bold"), Text(fnum(d["DGS10"].iloc[-1] - d["DFII10"].iloc[-1], 3), justify="right"), "", "calc")
        qs = await self.hub.quotes([s for s, _ in self.PROXIES] + self.ETFS)
        e = self.query_one("#etf", DataTable)
        e.clear(columns=True)
        e.add_columns("Sym", "Last", "%Chg", "Src")
        for s, n in self.PROXIES + [(x, x) for x in self.ETFS]:
            q = qs.get(s)
            e.add_row(n, Text(fnum(q.price, 3) if q else "N/A", justify="right"),
                      colored(q.pct, fnum(q.pct) + "%") if q else "", tag_text(q) if q else "")


class AuctView(View):
    FUNC = "AUCT"
    hints = "Recent Treasury auction results (FiscalData)"
    URL = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query"
           "?sort=-auction_date&page[size]=60&fields=auction_date,security_type,security_term,cusip,high_yield,"
           "high_discnt_rate,high_investment_rate,bid_to_cover_ratio,offering_amt")

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row")

    async def load(self) -> None:
        data = await self.hub.resource("fiscal", "fiscal", url=self.URL)
        t = self.query_one(DataTable)
        t.clear(columns=True)
        t.add_columns("Date", "Type", "Term", "CUSIP", "High Yld/Rate", "Bid/Cover", "Offering")
        ok = lambda v: v not in (None, "", "null")
        for d in (data or {}).get("data", []):
            if not ok(d.get("bid_to_cover_ratio")):
                continue
            y = next((d[k] for k in ("high_yield", "high_investment_rate", "high_discnt_rate") if ok(d.get(k))), None)
            t.add_row(d["auction_date"], d["security_type"], d["security_term"], d["cusip"],
                      Text(f"{float(y):.3f}%" if y else "N/A", justify="right"),
                      Text(f"{float(d['bid_to_cover_ratio']):.2f}", justify="right"),
                      Text(fbig(float(d["offering_amt"])) if ok(d.get("offering_amt")) else "", justify="right"))


class IexView(View):
    """IEX command: toggles the global overlay, refusing when Alpaca keys are missing or invalid."""
    FUNC = "IEX"

    def compose(self) -> ComposeResult:
        yield Static("", classes="msg")

    def on_mount(self) -> None:
        from .live import set_iex
        try:
            if len(self.args) > 1:
                raise ValueError("Use /IEX ON, /IEX OFF or /IEX TOGGLE")
            msg = set_iex(self.hub, self.args[0] if self.args else "TOGGLE")
        except ValueError as error:
            msg = str(error)
        self.hub.warn(msg)
        self.query_one(Static).update(Text(msg, style=WARN))


VIEWS.update({"WEI": QuoteBoard, "INDEX": QuoteBoard, "EQUITY": EquityBoard, "ETF": EtfBoard, "FX": FxBoard, "FUND": FundBoard, "BONDS": BondBoard, "FUTURES": FuturesBoard, "CMDTY": FuturesBoard, "CRYPTO": CryptoBoard, "MOST": MostBoard, "FXC": FxcView,
              "HOME": HomeView, "GC": GCView, "BTMM": BTMMView, "AUCT": AuctView, "IEX": IexView,
              'METRICS':MetricsView,'EST':EstimatesView,'ANALYST':AnalystView,'HOLD':HoldingsView})
