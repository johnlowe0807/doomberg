"""Phase 3/4 boards: WEI CMDTY FXC CRYPTO MOST HOME, bonds GC BTMM AUCT, IEX toggle."""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict

import pandas as pd
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Static
from textual_plotext import PlotextPlot

from .app import DIM, WARN, VIEWS, View, MonView, PromptScreen, fbig, fnum, colored, tag_text
from .charts import QuoteChartView, fred, PALETTE
from .data import Quote, RateLimiter, _sub

BOARDS = {
    "WEI": [("^GSPC", "S&P 500"), ("^DJI", "Dow Jones"), ("^IXIC", "Nasdaq Comp"), ("^RUT", "Russell 2000"),
            ("^VIX", "VIX"), ("^GSPTSE", "TSX"), ("^BVSP", "Bovespa"), ("^FTSE", "FTSE 100"), ("^GDAXI", "DAX"),
            ("^FCHI", "CAC 40"), ("^STOXX50E", "Euro Stoxx 50"), ("^N225", "Nikkei 225"), ("^HSI", "Hang Seng"),
            ("000001.SS", "Shanghai"), ("^KS11", "KOSPI"), ("^AXJO", "ASX 200"), ("^BSESN", "Sensex")],
    "CMDTY": [("CL=F", "WTI Crude"), ("BZ=F", "Brent"), ("NG=F", "Nat Gas"), ("RB=F", "RBOB Gas"), ("HO=F", "Heating Oil"),
              ("GC=F", "Gold"), ("SI=F", "Silver"), ("HG=F", "Copper"), ("PL=F", "Platinum"), ("PA=F", "Palladium"),
              ("ZC=F", "Corn"), ("ZW=F", "Wheat"), ("ZS=F", "Soybeans"), ("KC=F", "Coffee"), ("SB=F", "Sugar"),
              ("CC=F", "Cocoa"), ("CT=F", "Cotton"), ("LE=F", "Live Cattle"),
              ("ALI=F", "Aluminum"), ("LBR=F", "Lumber"), ("KE=F", "KC HRW Wheat"), ("ZO=F", "Oats"),
              ("ZR=F", "Rough Rice"), ("ZM=F", "Soybean Meal"), ("ZL=F", "Soybean Oil"),
              ("GF=F", "Feeder Cattle"), ("HE=F", "Lean Hogs"), ("OJ=F", "Orange Juice")],
    "CRYPTO": [(f"{c}-USD", c) for c in ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "LTC", "DOT", "BCH")],
    "FXL": [("EURUSD=X", "EUR/USD"), ("GBPUSD=X", "GBP/USD"), ("JPY=X", "USD/JPY"), ("CHF=X", "USD/CHF"),
            ("AUDUSD=X", "AUD/USD"), ("CAD=X", "USD/CAD"), ("NZDUSD=X", "NZD/USD"), ("CNY=X", "USD/CNY"),
            ("MXN=X", "USD/MXN"), ("DX-Y.NYB", "Dollar Index")],
    "MOSTU": [(s, s) for s in "AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO JPM V LLY UNH XOM MA COST HD PG JNJ ABBV NFLX "
              "CRM AMD ORCL BAC KO PEP MRK WMT ADBE CSCO INTC QCOM TXN MU PLTR UBER COIN SMCI BA DIS".split()],
}
TAPE = [("^GSPC", "SPX"), ("^DJI", "INDU"), ("^IXIC", "CCMP"), ("^RUT", "RTY"), ("^VIX", "VIX"), ("CL=F", "CL1"),
        ("GC=F", "GOLD"), ("EURUSD=X", "EUR"), ("BTC-USD", "BTC")]


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
        self.snapshot_key = "cmdty-daily:" + ",".join(s for s, _ in BOARDS[self.board])
        snapshot = self.hub.disk.get(self.snapshot_key) or {}
        cache_key = f"{self.snapshot_key}:{pd.Timestamp.now(tz='America/New_York').date()}"
        fetched = min((q["fetched"] for q in snapshot.get("quotes", {}).values()), default=0)
        remaining = self.refresh_secs - (time.time() - fetched)
        if remaining > 0 and self.hub.mem.get(cache_key) is None:
            self.hub.mem.put(cache_key, snapshot, remaining)
        self.volumes = snapshot.get("volumes", {})
        super().on_mount()
        if snapshot:
            self.render_rows({s: Quote(**q) for s, q in snapshot.get("quotes", {}).items()})

    def ordered(self, qs):
        def rank(row):
            volume = self.volumes.get(row[0], {}).get("volume")
            return (volume is None, -(volume or 0), row[0])
        return sorted(self.rows, key=rank)[:self.LIMIT]

    def volume_text(self, symbol, quote):
        entry = self.volumes.get(symbol, {})
        date = entry.get("date", "")
        return f"{fbig(entry.get('volume'))} {date}".strip()

    async def load(self):
        symbols = [s for s, _ in self.rows][:self.LIMIT]
        today = pd.Timestamp.now(tz="America/New_York").date()

        def download():
            import yfinance as yf
            # Daily bars supply both quotes and ranking: no extra intraday requests.
            frame = yf.download(symbols, period="5d", interval="1d", group_by="ticker",
                                auto_adjust=False, progress=False, threads=False)
            snapshot = {"quotes": {}, "volumes": {}}
            for symbol in symbols:
                bars = _sub(frame, symbol)
                if bars is None or "Close" not in bars:
                    continue
                bars = bars.dropna(subset=["Close"])
                if bars.empty:
                    continue
                last = bars.iloc[-1]
                number = lambda v: float(v) if pd.notna(v) else None
                quote = Quote(symbol, price=number(last["Close"]),
                              prev_close=number(bars.iloc[-2]["Close"]) if len(bars) > 1 else None,
                              volume=number(last.get("Volume")), day_low=number(last.get("Low")),
                              day_high=number(last.get("High")), ts=bars.index[-1].timestamp(), fetched=time.time())
                snapshot["quotes"][symbol] = asdict(quote)
                prior = bars[bars.index.date < today]
                if not prior.empty and "Volume" in prior:
                    snapshot["volumes"][symbol] = {
                        "volume": number(prior.iloc[-1]["Volume"]), "date": str(prior.index[-1].date())}
            return snapshot if snapshot["quotes"] else None

        # Share the five-minute cache; Ctrl+R clears memory to request a fresh batch.
        snapshot = await self.hub.yf_call(f"{self.snapshot_key}:{today}", self.refresh_secs, download)
        if snapshot:
            self.hub.disk.put(self.snapshot_key, snapshot)
        if snapshot:
            self.volumes = snapshot["volumes"]
            self.render_rows({s: Quote(**q) for s, q in snapshot["quotes"].items()})
        else:
            self.hub.warn("CMDTY: daily data unavailable; retaining cached rows")


class CryptoBoard(QuoteBoard):
    FUNC = board = "CRYPTO"
    hints = "Enter open  (Coinbase live stream: phase 7)"


class MostBoard(QuoteBoard):
    """Movers from a liquid large-cap universe (yf.screen is flaky; fallback universe is deterministic)."""
    FUNC, board = "MOST", "MOSTU"
    hints = "s cycle gainers/losers/active  Enter open"
    modes = ["gainers", "losers", "active"]

    def on_mount(self) -> None:
        self.m = 0
        super().on_mount()

    def ordered(self, qs):
        have = [r for r in self.rows if r[0] in qs and qs[r[0]].pct is not None]
        key = {"gainers": lambda r: -qs[r[0]].pct, "losers": lambda r: qs[r[0]].pct,
               "active": lambda r: -(qs[r[0]].volume or 0) * (qs[r[0]].price or 0)}[self.modes[self.m]]
        return sorted(have, key=key)

    @property
    def title(self) -> str:
        return f"MOST {self.modes[getattr(self, 'm', 0)]} (large-cap universe)"

    def key_s(self) -> None:
        self.m = (self.m + 1) % 3
        self.parent.query_one(".title", Static).update(self.title) if self.parent else None
        self.render_rows(self.hub.cached_quotes([s for s, _ in self.rows]))


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
    hints = "Tab panes  /MON /WEI /GP …  Enter on a row opens it"
    refresh_secs = 20

    def compose(self) -> ComposeResult:
        yield Static("", id="tape", classes="msg")
        with Horizontal():
            yield MonView(self.hub, None, [])
            yield QuoteChartView(self.hub, "^GSPC", ["3M"])
        yield Static("", id="rates", classes="msg")

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
TENORS = [("1M", "DGS1MO"), ("3M", "DGS3MO"), ("6M", "DGS6MO"), ("1Y", "DGS1"), ("2Y", "DGS2"), ("3Y", "DGS3"),
          ("5Y", "DGS5"), ("7Y", "DGS7"), ("10Y", "DGS10"), ("20Y", "DGS20"), ("30Y", "DGS30")]


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
        yield PlotextPlot()
        yield Static("Dealer quotes, swaps and corporate bond trades are not available from free sources.", classes="msg")

    def on_mount(self) -> None:
        self.chart, self.custom = False, None
        self.query_one(PlotextPlot).display = False
        self.query_one(PlotextPlot).theme = "clear"
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
        p = self.query_one(PlotextPlot)
        plt = p.plt
        plt.clear_figure()
        x = list(range(len(TENORS)))
        for j, (n, _, ys) in enumerate(getattr(self, "curves", [])):
            pts = [(i, y) for i, y in zip(x, ys) if y is not None]
            if pts:
                plt.plot([a for a, _ in pts], [b for _, b in pts], label=n, color=PALETTE[j], marker="braille")
        plt.xticks(x, [n for n, _ in TENORS])
        p.refresh()

    def key_t(self) -> None:
        self.chart = not self.chart
        self.query_one(PlotextPlot).display = self.chart
        self.query_one(DataTable).display = not self.chart

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
        import httpx
        lim = self.hub.limiters.setdefault("fiscal", RateLimiter("fiscal", 2, 2))
        async with lim:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(self.URL)
                r.raise_for_status()
        t = self.query_one(DataTable)
        t.clear(columns=True)
        t.add_columns("Date", "Type", "Term", "CUSIP", "High Yld/Rate", "Bid/Cover", "Offering")
        ok = lambda v: v not in (None, "", "null")
        for d in r.json().get("data", []):
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
        cfg, hub = self.app.cfg, self.hub
        if not hub.alpaca_ok:
            msg = f"IEX overlay unavailable: {hub.alpaca_reason}. Set valid ALPACA_API_KEY/ALPACA_API_SECRET and restart."
            cfg.set("iex", "enabled", False)
        else:
            cfg.set("iex", "enabled", not cfg.get("iex", "enabled"))
            msg = f"IEX overlay {'ON' if cfg.get('iex', 'enabled') else 'OFF'} (IEX-only trades; small slice of volume)."
        hub.warn(msg)
        self.query_one(Static).update(Text(msg, style=WARN))


VIEWS.update({"WEI": QuoteBoard, "CMDTY": CmdtyBoard, "CRYPTO": CryptoBoard, "MOST": MostBoard, "FXC": FxcView,
              "HOME": HomeView, "GC": GCView, "BTMM": BTMMView, "AUCT": AuctView, "IEX": IexView})
