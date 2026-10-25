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
    FUNC = "WEI"
    hints = "Enter open  Ctrl+R refresh"
    refresh_secs = 15
    board = "WEI"
    volume_label = "Volume"

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row")

    def on_mount(self) -> None:
        self.rows = BOARDS[self.board]
        t = self.query_one(DataTable)
        for c in ("Name", "Ticker", "Last", "Chg", "%Chg", self.volume_label, "Day Range", "Src"):
            t.add_column(c)
        self.render_rows(self.hub.cached_quotes([s for s, _ in self.rows]))
        super().on_mount()

    def ordered(self, qs):
        return self.rows

    def volume_text(self, symbol, quote):
        return fbig(quote.volume)

    def render_rows(self, qs) -> None:
        t = self.query_one(DataTable)
        cur = t.cursor_row
        t.clear()
        for s, name in self.ordered(qs):
            q = qs.get(s)
            if not q:
                t.add_row(name, s, *["N/A"] * 6, key=s)
                continue
            t.add_row(Text(name, style="bold"), Text(s, style=DIM), Text(fnum(q.price), justify="right"),
                      colored(q.change, fnum(q.change)), colored(q.pct, fnum(q.pct) + "%"),
                      Text(self.volume_text(s, q), justify="right"), f"{fnum(q.day_low)}-{fnum(q.day_high)}", tag_text(q), key=s)
        if cur and t.row_count:
            t.move_cursor(row=min(cur, t.row_count - 1))

    async def load(self) -> None:
        self.render_rows(await self.hub.quotes([s for s, _ in self.rows]))

    def on_data_table_row_selected(self, e: DataTable.RowSelected) -> None:
        self.app.run_command(e.row_key.value)


class CmdtyBoard(QuoteBoard):
    FUNC = board = "CMDTY"
    refresh_secs = 300
    volume_label = "Prior volume (date)"
    hints = "Prior-day contract volume ↓ (before today's NY date)  5m refresh  Enter open  Ctrl+R refresh"
    LIMIT = 50

    @property
    def title(self):
        return "CMDTY prior-day volume (50 max, curated futures)"

    def on_mount(self):
        self.volumes = {}
        super().on_mount()

    def ordered(self, qs):
        def rank(row):
            volume = self.volumes.get(row[0], {}).get("volume")
            return (volume is None, -(volume or 0), row[0])
        return sorted(self.rows[:self.LIMIT], key=rank)

    def volume_text(self, symbol, quote):
        entry = self.volumes.get(symbol, {})
        date = entry.get("date", "")
        return f"{fbig(entry.get('volume'))} {date}".strip()

    async def load(self):
        symbols = [s for s, _ in self.rows][:self.LIMIT]
        today = pd.Timestamp.now(tz="America/New_York").date()

        snapshot = await self.hub.resource("yf", "commodity", symbols=tuple(symbols), date=str(today))
        if snapshot:
            self.volumes = snapshot["volumes"]
            self.render_rows({s: Quote(**q) for s, q in snapshot["quotes"].items()})
        else:
            self.hub.warn("CMDTY: daily data unavailable; retaining cached rows")


class CryptoBoard(QuoteBoard):
    FUNC = board = "CRYPTO"
    hints = "Enter open  Coinbase live where available; Yahoo fallback"


class MostBoard(QuoteBoard):
    """Yahoo screeners with a liquid large-cap fallback when screening is unavailable."""
    FUNC, board = "MOST", "MOSTU"
    hints = "s cycle gainers/losers/active  Enter open"
    modes = ["gainers", "losers", "active"]

    def on_mount(self) -> None:
        self.m = 0
        self.screened = False
        super().on_mount()

    async def load(self) -> None:
        mode = self.modes[self.m]
        query = {"gainers": "day_gainers", "losers": "day_losers", "active": "most_actives"}[mode]
        data = await self.hub.resource("yf", "screen", screen=query)
        quotes, rows = {}, []
        for item in (data or {}).get("quotes", []):
            symbol, price = item.get("symbol"), item.get("regularMarketPrice")
            if not symbol or price is None or symbol in quotes:
                continue
            previous = item.get("regularMarketPreviousClose")
            if previous is None and item.get("regularMarketChange") is not None:
                previous = price - item["regularMarketChange"]
            quotes[symbol] = Quote(symbol, price=price, prev_close=previous,
                                   volume=item.get("regularMarketVolume"), day_low=item.get("regularMarketDayLow"),
                                   day_high=item.get("regularMarketDayHigh"), ts=item.get("regularMarketTime"),
                                   fetched=time.time())
            rows.append((symbol, item.get("longName") or item.get("shortName") or symbol))
        if mode != self.modes[self.m]:
            return
        self.screened = bool(rows)
        self.rows = rows or BOARDS[self.board]
        if not rows:
            quotes = await self.hub.quotes([s for s, _ in self.rows])
        self.render_rows(quotes)
        self.retitle()
        if isinstance(self.parent, Vertical) and self.parent.id == "home-movers":
            self.parent.query_one("#home-movers-label", Static).update(self.title)

    def ordered(self, qs):
        have = [r for r in self.rows if r[0] in qs and qs[r[0]].pct is not None]
        key = {"gainers": lambda r: -qs[r[0]].pct, "losers": lambda r: qs[r[0]].pct,
               "active": lambda r: -(qs[r[0]].volume or 0)}[self.modes[self.m]]
        return sorted(have, key=key)

    @property
    def title(self) -> str:
        source = "Yahoo screener" if getattr(self, "screened", False) else "large-cap fallback"
        return f"MOST {self.modes[getattr(self, 'm', 0)]} ({source})"

    def key_s(self) -> None:
        self.m = (self.m + 1) % 3
        self.screened = False
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


VIEWS.update({"WEI": QuoteBoard, "CMDTY": CmdtyBoard, "CRYPTO": CryptoBoard, "MOST": MostBoard, "FXC": FxcView,
              "HOME": HomeView, "GC": GCView, "BTMM": BTMMView, "AUCT": AuctView, "IEX": IexView})
