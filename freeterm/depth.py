"""Phase 5 depth screens: DES, HP, CN, TOP, OMON (local Black-Scholes greeks), /search."""
from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timezone

import pandas as pd
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from .app import DIM, ET, WARN, VIEWS, View, colored, fbig, fnum
from .charts import DEF_IV, PERIOD, RANGES
from data_layer.options import remember_quote, name as option_name, contract, number


# ---------------------------------------------------------------- DES
DES_FIELDS = [
    ("Name", "longName"), ("Exchange", "fullExchangeName"), ("Type", "quoteType"), ("Sector", "sector"),
    ("Industry", "industry"), ("Country", "country"), ("Employees", "fullTimeEmployees"), ("Website", "website"),
    ("Market cap", "marketCap"), ("Enterprise val", "enterpriseValue"), ("P/E (ttm)", "trailingPE"),
    ("P/E (fwd)", "forwardPE"), ("PEG", "pegRatio"), ("P/B", "priceToBook"), ("EV/EBITDA", "enterpriseToEbitda"),
    ("EPS (ttm)", "trailingEps"), ("EPS (fwd)", "forwardEps"), ("Div yield", "dividendYield"), ("Beta", "beta"),
    ("52w high", "fiftyTwoWeekHigh"), ("52w low", "fiftyTwoWeekLow"), ("50d avg", "fiftyDayAverage"),
    ("200d avg", "twoHundredDayAverage"), ("Avg volume", "averageVolume"), ("Shares out", "sharesOutstanding"),
    ("Float", "floatShares"), ("Short % float", "shortPercentOfFloat"), ("Revenue (ttm)", "totalRevenue"),
    ("Gross margin", "grossMargins"), ("Op margin", "operatingMargins"), ("Profit margin", "profitMargins"),
    ("ROE", "returnOnEquity"), ("Debt/Equity", "debtToEquity"), ("Free cash flow", "freeCashflow"),
    ("Target (mean)", "targetMeanPrice"), ("Analyst rec", "recommendationKey"), ("# analysts", "numberOfAnalystOpinions"),
]
PCT_KEYS = {"grossMargins", "operatingMargins", "profitMargins", "returnOnEquity", "shortPercentOfFloat"}
BIG_KEYS = {"marketCap", "enterpriseValue", "averageVolume", "sharesOutstanding", "floatShares", "totalRevenue",
            "freeCashflow", "fullTimeEmployees"}


def fmt_info(k, v) -> str:
    if v is None or v == "":
        return "N/A"
    if isinstance(v, (int, float)):
        if k in PCT_KEYS:
            return f"{v * 100:.2f}%"
        if k == "dividendYield":   # yfinance now reports this already in percent
            return f"{v:.2f}%"
        if k in BIG_KEYS:
            return fbig(v)
        return fnum(v)
    return str(v)


class DesView(View):
    FUNC = "DES"
    hints = "Company profile (Yahoo quoteSummary, unofficial)  /GP chart  /FA financials  /ERN earnings"

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row", show_header=False)
        yield Static("", classes="msg", id="summary")

    async def load(self) -> None:
        info = await self.hub.resource("yf", "profile", symbol=self.sec)
        if not info:
            self.query_one("#summary", Static).update(Text(f"N/A — no profile for {self.sec}", style=WARN))
            return
        t = self.query_one(DataTable)
        t.clear(columns=True)
        t.add_columns("k1", "v1", "k2", "v2", "k3", "v3")
        cells = [(lab, fmt_info(k, info.get(k))) for lab, k in DES_FIELDS]
        per = math.ceil(len(cells) / 3)
        for i in range(per):
            row = []
            for c in range(3):
                j = i + c * per
                lab, val = cells[j] if j < len(cells) else ("", "")
                row += [Text(lab, style=DIM), Text(val, style="bold", justify="right")]
            t.add_row(*row)
        self.query_one("#summary", Static).update(Text(info.get("longBusinessSummary") or "", style="#5FD7FF"))


# ---------------------------------------------------------------- HP
class HpView(View):
    FUNC = "HP"
    hints = "1-9 range (1D..MAX)  daily bars, newest first"

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row")

    def on_mount(self) -> None:
        self.rng = next((a for a in self.args if a in RANGES), "3M")
        super().on_mount()

    @property
    def title(self) -> str:
        return f"{self.sec} HP {getattr(self, 'rng', '3M')}"

    async def load(self) -> None:
        iv = "1d" if self.rng not in ("1D", "5D") else DEF_IV[self.rng]
        df = await self.hub.bars(self.sec, self.rng, iv)
        t = self.query_one(DataTable)
        t.clear(columns=True)
        t.add_columns("Date", "Open", "High", "Low", "Close", "Chg", "%Chg", "Volume")
        if df is None or not len(df):
            return
        ch = df.Close.diff()
        pc = df.Close.pct_change() * 100
        fmt = "%Y-%m-%d" if iv == "1d" else "%m-%d %H:%M"
        for idx in df.index[::-1]:
            r = df.loc[idx]
            c, p = ch.loc[idx], pc.loc[idx]
            c = None if pd.isna(c) else float(c)
            p = None if pd.isna(p) else float(p)
            t.add_row(f"{idx:{fmt}}", *[Text(fnum(r[k]), justify="right") for k in ("Open", "High", "Low", "Close")],
                      colored(c, fnum(c)), colored(p, fnum(p) + "%"), Text(fbig(r.Volume), justify="right"))

    def on_key(self, e) -> None:
        if e.character and e.character in "123456789":
            self.rng = RANGES[int(e.character) - 1]
            self.parent.query_one(".title", Static).update(self.title)
            self.reload()
            e.stop()


# ---------------------------------------------------------------- news
from data_layer.normalization import norm_yf_news


async def rss(hub, url: str) -> list[dict]:
    from data_layer.query_models import NewsQuery
    return hub.value(await hub.query(NewsQuery(feeds=(url,)))) or []


class NewsView(View):
    FUNC = "TOP"
    hints = "Enter read article  o in reader: browser (confirm)  Ctrl+R refresh"
    refresh_secs = 300

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row")

    async def fetch(self) -> list[dict]:
        from data_layer.query_models import NewsQuery
        result = await self.hub.query(NewsQuery(feeds=tuple(self.app.cfg.get("news", "feeds", []))))
        return result.data or []

    async def load(self) -> None:
        items = await self.fetch()
        seen, uniq = set(), []
        for it in items:
            k = it["title"].strip().lower()
            if k not in seen:
                seen.add(k)
                uniq.append(it)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        uniq.sort(key=lambda i: i["ts"].astimezone(timezone.utc) if i["ts"] and i["ts"].tzinfo else epoch, reverse=True)
        self.items = uniq
        t = self.query_one(DataTable)
        t.clear(columns=True)
        t.add_columns("Time", "Source", "Headline")
        for i, it in enumerate(uniq[:200]):
            when = it["ts"].astimezone(ET).strftime("%m-%d %H:%M") if it["ts"] and it["ts"].tzinfo else ""
            t.add_row(Text(when, style=DIM), Text(it["src"][:18], style="#FFD700"), it["title"], key=str(i))
        if not uniq:
            self.hub.warn(f"{self.FUNC}: no headlines (feeds unreachable?)")

    def on_data_table_row_selected(self, e: DataTable.RowSelected) -> None:
        from .news import ArticleReader
        self.app.push_screen(ArticleReader(dict(self.items[int(e.row_key.value)]), self.hub))


class CnView(NewsView):
    FUNC = "CN"
    refresh_secs = 300

    async def fetch(self) -> list[dict]:
        from data_layer.query_models import NewsQuery
        feed = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={self.sec}&region=US&lang=en-US"
        result = await self.hub.query(NewsQuery(symbol=self.sec, feeds=(feed,)))
        return result.data or []


# ---------------------------------------------------------------- OMON
from data_layer.analytics import bs_greeks


class OmonView(View):
    FUNC = "OMON"
    hints = ". options commands  [ ] expiry  +/- width  c/p call/put  Enter graph contract  greeks: local"
    view_commands_enabled = True
    refresh_secs = 30
    SIDE = ("Bid", "Ask", "Last", "Vol", "OI", "IV", "Δ", "Γ", "Θ", "ν")

    def compose(self) -> ComposeResult:
        yield Static("", classes="msg", id="hdr")
        yield DataTable(cursor_type="row", id="chain")

    def on_mount(self) -> None:
        self.exp_i = 0
        self.side = "C"
        self.contracts = []
        self.strikes, self.expiries, self.command_history = [], [], []
        self.loaded_expiry = None
        self.current_expiry = None
        self.center, self.selected_strike = None, None
        self.requested_expiry = self.args[0] if self.args else None
        option = contract(self.sec)
        if option:
            self.sec, self.requested_expiry = option['underlying'], option['expiry']
            self.side = option['side'][0]
            self.center = self.selected_strike = option['strike']
        self.width = max(1, min(100, int(self.app.cfg.get("options", "strikes_each_side", 12))))
        self.chain_header = ''
        self.retitle()
        super().on_mount()

    async def load(self) -> None:
        sym = self.sec
        self.contracts = []
        self.strikes = []
        self.loaded_expiry = None
        self.query_one("#chain", DataTable).clear(columns=True)
        exps = await self.hub.resource("yf", "expiries", symbol=sym)
        if not exps:
            self.expiries = []
            self.query_one("#hdr", Static).update(Text(f"N/A — no listed options for {sym}", style=WARN))
            return
        self.expiries = list(exps)
        if self.requested_expiry:
            requested = self.requested_expiry.upper()
            if requested in ('NEXT', 'PREV', 'FIRST', 'LAST') or requested.isdigit():
                choices = {'NEXT': self.exp_i + 1, 'PREV': self.exp_i - 1, 'FIRST': 0, 'LAST': len(exps) - 1}
                index = int(requested) - 1 if requested.isdigit() else max(0, min(len(exps) - 1, choices[requested]))
                if not 0 <= index < len(exps):
                    self.query_one('#hdr', Static).update(f'N/A — expiry index must be 1–{len(exps)}; EXP LIST shows dates')
                    return
                self.requested_expiry = exps[index]
            if self.requested_expiry not in exps:
                self.query_one('#hdr', Static).update(f'N/A — expiry {self.requested_expiry} is not listed; EXP LIST shows available dates')
                return
            self.exp_i = exps.index(self.requested_expiry)
            self.requested_expiry = None
        self.exp_i = max(0, min(self.exp_i, len(exps) - 1))
        exp = exps[self.exp_i]
        self.current_expiry = exp
        ch = await self.hub.resource("yf", "chain", symbol=sym, expiry=exp)
        if ch is None or all(frame is None or frame.empty for frame in (ch.calls, ch.puts)):
            self.chain_header = f'{sym} expiry {exp} — N/A: Yahoo returned no quoted contracts; EXP NEXT / REFRESH'
            self.render_header()
            return
        qs = await self.hub.quotes([sym, "^IRX"])
        S = qs[sym].price if sym in qs else None
        irx = qs.get("^IRX")
        r = irx.price / 100 if irx and irx.price else float(self.app.cfg.get("options", "risk_free_fallback", 0.045))
        exp_dt = pd.Timestamp(exp).tz_localize(ET) + pd.Timedelta(hours=16)
        T = max((exp_dt - pd.Timestamp.now(tz=ET)).total_seconds(), 60) / (365 * 86400)
        self.chain_header = f"{sym} spot {fnum(S)}  expiry {exp} ({self.exp_i + 1}/{len(exps)})  DTE {T * 365:.1f}  r {r * 100:.2f}%"
        self.render_header()
        calls = (ch.calls if ch.calls is not None else pd.DataFrame(columns=['strike'])).set_index("strike")
        puts = (ch.puts if ch.puts is not None else pd.DataFrame(columns=['strike'])).set_index("strike")
        strikes = sorted(set(calls.index) | set(puts.index))
        center = self.center if self.center is not None else S
        if center is not None and strikes:
            atm = min(range(len(strikes)), key=lambda i: abs(strikes[i] - center))
            strikes = strikes[max(0, atm - self.width): atm + self.width + 1]
        else:
            strikes = strikes[:2 * self.width + 1]
        self.strikes = strikes
        t = self.query_one("#chain", DataTable)
        t.clear(columns=True)
        t.add_columns(*[f"C {c}" for c in reversed(self.SIDE)], "STRIKE", *[f"P {c}" for c in self.SIDE])
        for k in strikes:
            row = []
            contracts = {}
            for side, df, is_call in (("C", calls, True), ("P", puts, False)):
                if k in df.index:
                    o = df.loc[k]
                    o = o.iloc[0] if isinstance(o, pd.DataFrame) else o
                    contracts[side] = o.get("contractSymbol")
                    remember_quote(self.hub, o, getattr(ch, "freeterm", None))
                    iv = number(o.get("impliedVolatility")) or 0
                    g = bs_greeks(S, k, T, r, iv, is_call) if S and iv > 0.001 else None
                    itm = bool(o.get("inTheMoney"))
                    sty = "on #0a2a3a" if itm else ""
                    num = lambda v, dp=2: Text("" if v is None or pd.isna(v) else fnum(float(v), dp), justify="right", style=sty)
                    cells = [num(o.get("bid")), num(o.get("ask")), num(o.get("lastPrice")),
                             Text(fbig(o.get("volume") if not pd.isna(o.get("volume")) else None), justify="right", style=sty),
                             Text(fbig(o.get("openInterest") if not pd.isna(o.get("openInterest")) else None), justify="right", style=sty),
                             Text(f"{iv * 100:.1f}%", justify="right", style=sty)]
                    cells += [num(g[0], 3), num(g[1], 4), num(g[2], 3), num(g[3], 3)] if g else [num(None)] * 4
                else:
                    cells = [Text("")] * len(self.SIDE)
                row += list(reversed(cells)) if is_call else cells
                if is_call:
                    row.append(Text(fnum(k), style="bold #FFFFFF on #0B3D91", justify="center"))
            t.add_row(*row)
            self.contracts.append(contracts)
        self.loaded_expiry = exp
        selected = self.selected_strike if self.selected_strike is not None else center
        if selected is not None and t.row_count:
            t.move_cursor(row=min(range(len(strikes)), key=lambda index: abs(strikes[index] - selected)))

    def render_header(self):
        header = Text(self.chain_header or f'{self.sec} options', style='bold #FFFFFF')
        header.append(f"  Enter opens {'call' if self.side == 'C' else 'put'} chart · Yahoo DLY", style=DIM)
        self.query_one('#hdr', Static).update(header)

    def invalidate_chain(self):
        self.contracts, self.strikes, self.loaded_expiry = [], [], None
        self.chain_header = ''
        self.query_one('#chain', DataTable).clear()
        self.query_one('#hdr', Static).update(f'{self.sec} — loading selected options chain…')

    def selected_contract(self, strike=None, side=None):
        table = self.query_one('#chain', DataTable)
        if self.loaded_expiry is None or not self.contracts:
            raise ValueError('Chain is still loading / unavailable; retry after it loads')
        row = table.cursor_row
        if strike is not None:
            row = next((i for i, value in enumerate(self.strikes) if abs(value - strike) < 1e-7), None)
            if row is None:
                raise ValueError('Strike is outside the displayed window; use STRIKE price first')
        symbol = self.contracts[row].get(side or self.side) if 0 <= row < len(self.contracts) else None
        if not symbol:
            raise ValueError('No contract on the selected side of this strike')
        return symbol

    def on_data_table_row_highlighted(self, event):
        row = event.data_table.cursor_row
        if 0 <= row < len(self.strikes):
            self.selected_strike = self.strikes[row]

    def execute_graph_command(self, line):
        from .option_commands import execute
        return execute(self, line)

    def open_graph_commands(self):
        from .graph_commands import GraphCommandScreen
        self.app.push_screen(GraphCommandScreen(self))

    def on_key(self, e) -> None:
        k = e.character
        if k in ("[", "]"):
            self.exp_i = max(0, self.exp_i + (1 if k == "]" else -1))
            self.requested_expiry = None
            self.invalidate_chain()
        elif k in ("+", "=", "-"):
            self.width = max(1, min(100, self.width + (4 if k != "-" else -4)))
        elif k in ("c", "p"):
            self.side = k.upper()
            self.render_header()
            e.stop()
            return
        else:
            return
        e.stop()
        self.reload()

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        event.stop()
        try:
            self.app.run_command(self.selected_contract())
        except ValueError as error:
            self.hub.warn(str(error))


# ---------------------------------------------------------------- search
class SearchView(View):
    FUNC = "SEARCH"
    hints = "Enter open  /?? filters/help  _s=symbol _n=name _t=type _p=source"

    @property
    def title(self) -> str:
        return f"SEARCH {' '.join(self.args)}"

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row")

    async def load(self) -> None:
        from data_layer.symbols import search_symbols
        q = " ".join(self.args).strip()
        if not q:
            self.render_matches([])
            self.hub.warn("Search: enter a ticker, asset name, or type after /?")
            return
        try:
            rows = await search_symbols(self.hub, q, self.app.aliases, self.render_matches)
        except ValueError as exc:
            self.render_matches([])
            self.hub.warn(str(exc))
            return
        self.render_matches(rows)
        if not rows:
            self.hub.warn(f"No matches for {q!r}")

    def render_matches(self, rows) -> None:
        t = self.query_one(DataTable)
        selected = str(t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value) if t.row_count else None
        t.clear(columns=True)
        t.add_columns("Symbol", "Name", "Type", "Source")
        for i, (symbol, name, kind, source) in enumerate(rows):
            t.add_row(Text(symbol, style="bold"), Text(name), Text(kind, style=DIM), source, key=symbol)
            if symbol == selected:
                t.move_cursor(row=i)

    def on_data_table_row_selected(self, e: DataTable.RowSelected) -> None:
        self.app.run_command(e.row_key.value)


class SearchHelpView(View):
    FUNC = "SEARCH HELP"
    hints = "Up/Down scroll  Esc back  /? text search"

    def compose(self):
        from data_layer.symbols import SEARCH_HELP
        with VerticalScroll():
            yield Static(SEARCH_HELP, markup=False)


VIEWS.update({"DES": DesView, "HP": HpView, "CN": CnView, "TOP": NewsView, "OMON": OmonView, "SEARCH": SearchView,
              "SEARCHHELP": SearchHelpView})
