"""Textual app: command bar, tiled panes, views. Phase 1 skeleton.

To add a function: subclass View, set FUNC/title/hints, implement `load()` (async, fetch via
self.hub) and optionally `key_*` handlers; register it in VIEWS. See HANDOFF.md.
"""
from __future__ import annotations

import asyncio
import difflib
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Static

from .config import Config, load_aliases, load_watchlist, save_watchlist
from .data import DataHub, Quote
from .dialogs import QuitConfirm

UP, DOWN, WARN, DIM = "#00FF87", "#FF4D6D", "#FFD700", "#3A6B80"
ET = ZoneInfo("America/New_York")

# Every function in the brief. Unbuilt ones open a StubView naming their phase.
FUNCS = {
    "Q": 1, "MON": 1, "HELP": 1, "HOME": 3, "DES": 5, "GP": 2, "GIP": 2, "HP": 5, "CN": 5,
    "COMP": 8, "WEI": 3, "MOST": 3, "CMDTY": 3, "FXC": 3, "CRYPTO": 3, "TOP": 5, "OMON": 5,
    "GC": 4, "BTMM": 4, "SPRD": 4, "AUCT": 4, "FRED": 4, "FA": 6, "ERN": 6, "FIL": 6,
    "EVTS": 6, "IEX": 7, "LAYOUT": 1, "?": 8,
}
SEC_FUNCS = {"Q", "DES", "GP", "GIP", "HP", "CN", "OMON", "FA", "ERN", "FIL"}
SUFFIXES = {"US", "EQUITY", "INDEX", "CURNCY", "COMDTY", "GOVT"}


# ---------------------------------------------------------------- helpers
def fnum(v, dp=2) -> str:
    return "N/A" if v is None else f"{v:,.{dp}f}"


def fbig(v) -> str:
    if v is None:
        return "N/A"
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{v / div:.2f}{suf}"
    return f"{v:.0f}"


def colored(v, s: str) -> Text:
    if v is None:
        return Text(s, style=DIM, justify="right")
    arrow = "▲" if v > 0 else "▼" if v < 0 else " "
    return Text(f"{arrow}{s}", style=UP if v > 0 else DOWN if v < 0 else "", justify="right")


def tag_text(q: Quote) -> Text:
    t = q.tag
    return Text(t, style={"LIVE": UP, "STALE": WARN}.get(t, DIM))


# NYSE full-day closures (extend yearly). Early closes (13:00 ET) are not modeled.
NYSE_HOLIDAYS = {
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26", "2025-06-19", "2025-07-04",
    "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
    "2027-11-25", "2027-12-24",
}


def market_status() -> str:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return "US CLOSED (wknd)"
    if now.strftime("%Y-%m-%d") in NYSE_HOLIDAYS:
        return "US CLOSED (hol)"
    m = now.hour * 60 + now.minute
    if 570 <= m < 960:
        return "US OPEN"
    if 240 <= m < 570:
        return "US PRE"
    if 960 <= m < 1200:
        return "US POST"
    return "US CLOSED"


def regular_hours() -> bool:
    return market_status() == "US OPEN"


def parse(line: str, aliases: dict[str, str]):
    """Slash-prefixed words are commands; bare words are securities."""
    toks = [t for t in line.strip().lstrip(":").upper().split() if t]
    if not toks:
        raise ValueError("")
    sec = None
    rest = toks
    if not toks[0].startswith("/"):
        sec = aliases.get(toks[0], toks[0])
        rest = toks[1:]
        while rest and rest[0] in SUFFIXES:
            rest = rest[1:]
        if not rest:
            return "Q", sec, []
    if rest[0].startswith("/"):
        if rest[0].startswith("/?"):
            query = ([rest[0][2:]] if rest[0][2:] else []) + rest[1:]
            return "SEARCH", sec, query
        func = rest[0][1:] or "HELP"
        if func in FUNCS:
            return func, sec, rest[1:]
        close = difflib.get_close_matches(func, FUNCS, n=1, cutoff=0.6)
        hint = f" — did you mean /{close[0]}?" if close else " — use /HELP for commands"
        raise ValueError(f"Unknown command /{func}{hint}")
    if all(re.fullmatch(r"[\^A-Z0-9.=\-]{1,12}", t) for t in rest):
        return "COMP", sec, [aliases.get(t, t) for t in rest]   # `AAPL AMZN` = compare
    raise ValueError("Use / before a command, e.g. /GP AAPL or AAPL /GP")


# ---------------------------------------------------------------- prompt
class PromptScreen(ModalScreen[str]):
    BINDINGS = [Binding("escape", "dismiss('')", show=False)]

    def __init__(self, label: str, value: str = "") -> None:
        super().__init__()
        self.label, self.value = label, value

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.label)
            yield Input(self.value)

    @on(Input.Submitted)
    def done(self, e: Input.Submitted) -> None:
        self.dismiss(e.value.strip())


# ---------------------------------------------------------------- views
class View(Vertical):
    can_focus = True
    FUNC = ""
    hints = ""
    refresh_secs: float | None = None

    def __init__(self, hub: DataHub, sec: str | None, args: list[str]) -> None:
        super().__init__(classes="body")
        self.hub, self.sec, self.args = hub, sec, args

    @property
    def title(self) -> str:
        return f"{self.sec or ''} {self.FUNC}".strip()

    def on_mount(self) -> None:
        self.reload()
        if self.refresh_secs:
            self.set_interval(self.refresh_secs, self.reload)

    def reload(self) -> None:
        self.run_worker(self._safe_load, exclusive=True, group="load")

    def retitle(self) -> None:
        """Refresh the pane title bar after the view's state (range, interval, mode) changes."""
        if isinstance(self.parent, Pane):
            self.parent.query_one(".title", Static).update(self.title)

    async def _safe_load(self) -> None:
        try:
            await self.load()
        except Exception as e:  # fail soft
            self.hub.warn(f"{self.title}: {type(e).__name__}: {e}"[:120])

    async def load(self) -> None:
        pass

    def action_section(self, direction: int) -> None:
        tables = list(self.query(DataTable))
        if tables:
            focused = self.app.focused
            index = tables.index(focused) if focused in tables else (0 if direction < 0 else -1)
            tables[(index + direction) % len(tables)].focus()


class StubView(View):
    def compose(self) -> ComposeResult:
        yield Static(f"{self.FUNC} is not built yet (phase {FUNCS.get(self.FUNC, '?')}). "
                     "See HANDOFF.md.", classes="msg")


class QuoteView(View):
    FUNC = "Q"
    hints = "Esc back  Ctrl+R refresh  (GP chart: phase 2)"
    refresh_secs = 10

    def compose(self) -> ComposeResult:
        yield Static("", id="qline", classes="msg")
        yield DataTable(id="qt", cursor_type="row", zebra_stripes=False)

    def on_mount(self) -> None:
        t = self.query_one("#qt", DataTable)
        t.add_columns("Date", "Open", "High", "Low", "Close", "Volume")
        q = self.hub.cached_quotes([self.sec]).get(self.sec)
        if q:
            self.show(q)
        super().on_mount()

    def show(self, q: Quote) -> None:
        line = Text(f"{q.symbol}  ", style="bold #FFFFFF")
        line.append(fnum(q.price) + "  ")
        line.append_text(colored(q.change, f"{fnum(q.change)} ({fnum(q.pct)}%)"))
        line.append(f"  vol {fbig(q.volume)}  range {fnum(q.day_low)}-{fnum(q.day_high)}  ")
        line.append_text(tag_text(q))
        if q.ts:
            line.append(f"  as of {datetime.fromtimestamp(q.ts, ET):%H:%M ET %b-%d}", style=DIM)
        self.query_one("#qline", Static).update(line)

    async def load(self) -> None:
        q = (await self.hub.quotes([self.sec])).get(self.sec)
        if not q:
            self.query_one("#qline", Static).update(Text(f"N/A — no data for {self.sec}", style=WARN))
            return
        self.show(q)
        df = await self.hub.history(self.sec, "1mo", "1d")
        t = self.query_one("#qt", DataTable)
        t.clear()
        if df is not None and len(df):
            for idx, r in df.iloc[::-1].iterrows():
                t.add_row(f"{idx:%Y-%m-%d}", *[Text(fnum(r[c]), justify="right") for c in ("Open", "High", "Low", "Close")],
                          Text(fbig(r["Volume"]), justify="right"))


class MonView(View):
    FUNC = "MON"
    hints = "a add  d delete  s sort  Enter open  Ctrl+R refresh"
    refresh_secs = 10
    SORTS = ["sym", "pct", "chg", "vol"]

    def compose(self) -> ComposeResult:
        yield DataTable(id="mon", cursor_type="row")

    def on_mount(self) -> None:
        self.wl = load_watchlist()
        self.sort = 0
        self.prev: dict[str, float] = {}
        t = self.query_one("#mon", DataTable)
        for c in ("Sym", "Last", "Chg", "%Chg", "Volume", "Day Range", "Src", "Age"):
            t.add_column(c, key=c)
        self.render_rows(self.hub.cached_quotes(self.wl))
        super().on_mount()

    def render_rows(self, qs: dict[str, Quote]) -> None:
        t = self.query_one("#mon", DataTable)
        keyf = {"sym": lambda s: s, "pct": lambda s: -(qs[s].pct or -1e9) if s in qs else 1e9,
                "chg": lambda s: -(qs[s].change or -1e9) if s in qs else 1e9,
                "vol": lambda s: -(qs[s].volume or 0) if s in qs else 0}[self.SORTS[self.sort]]
        cur = t.cursor_row
        t.clear()
        flashed = False
        for s in sorted(dict.fromkeys(self.wl), key=keyf):   # unique: row keys must not repeat
            q = qs.get(s)
            if not q:
                t.add_row(s, *["N/A"] * 7, key=s)
                continue
            flash = s in self.prev and self.prev[s] != q.price
            pstyle = ("bold reverse " + (UP if q.price > self.prev[s] else DOWN)) if flash else ""
            flashed = flashed or flash
            self.prev[s] = q.price
            age = "" if not q.ts else f"{max(0, time.time() - q.ts) / 60:.0f}m"
            t.add_row(Text(s, style="bold"), Text(fnum(q.price), style=pstyle, justify="right"),
                      colored(q.change, fnum(q.change)), colored(q.pct, fnum(q.pct) + "%"),
                      Text(fbig(q.volume), justify="right"), f"{fnum(q.day_low)}-{fnum(q.day_high)}",
                      tag_text(q), Text(age, style=DIM, justify="right"), key=s)
        if cur is not None and t.row_count:
            t.move_cursor(row=min(cur, t.row_count - 1))
        if flashed:   # brief flash: repaint without highlight after ~1s (prev already updated)
            self.set_timer(1.0, lambda: self.render_rows(self.hub.cached_quotes(self.wl)))

    async def load(self) -> None:
        self.render_rows(await self.hub.quotes(self.wl))

    def selected(self) -> str | None:
        t = self.query_one("#mon", DataTable)
        if not t.row_count:
            return None
        return t.coordinate_to_cell_key((t.cursor_row, 0)).row_key.value

    def key_a(self) -> None:
        def add(v: str) -> None:
            if not v:
                return
            s = load_aliases().get(v.upper(), v.upper())
            if s in self.wl:
                self.hub.warn(f"{s} is already in the watchlist")
                return
            self.wl.append(s)
            save_watchlist(self.wl)
            self.reload()
        self.app.push_screen(PromptScreen("Add symbol:"), add)

    def key_d(self) -> None:
        s = self.selected()
        if s in self.wl:
            self.wl.remove(s)
            save_watchlist(self.wl)
            self.render_rows(self.hub.cached_quotes(self.wl))

    def key_s(self) -> None:
        self.sort = (self.sort + 1) % len(self.SORTS)
        self.hub.warn(f"MON sorted by {self.SORTS[self.sort]}")
        self.render_rows(self.hub.cached_quotes(self.wl))

    @on(DataTable.RowSelected)
    def open_row(self, e: DataTable.RowSelected) -> None:
        self.app.run_command(e.row_key.value)


class HelpView(View):
    FUNC = "HELP"

    def compose(self) -> ComposeResult:
        yield Static(help_text(), markup=False)


FUNC_HELP = [
    ("<SEC>", "quote + chart (default screen)"), ("DES", "company profile"), ("GP", "price graph"),
    ("GIP", "intraday graph"), ("HP", "historical prices table"), ("CN", "company news"),
    ("COMP A B C", "normalized comparison (or just: AAPL AMZN)"),
    ("OMON", "option monitor with greeks"), ("FA", "financial statements (EDGAR XBRL)"), ("FIL", "SEC filings + reader"),
    ("ERN", "earnings history + 8-K releases"), ("MON", "watchlist"), ("EVTS", "watchlist earnings calendar"),
    ("HOME", "home dashboard"), ("WEI", "world indices"), ("MOST", "movers"), ("CMDTY", "commodities"), ("FXC", "FX cross matrix"),
    ("CRYPTO", "crypto (Coinbase live)"), ("TOP", "top news (RSS)"), ("GC", "Treasury curve"), ("BTMM", "rates / money markets"),
    ("SPRD", "IG/HY spreads + 2s10s"), ("AUCT", "Treasury auctions"), ("FRED <ID>", "chart any FRED series"),
    ("IEX", "toggle Alpaca IEX overlay"), ("? <text> [_t=TYPE]", "search securities; filter by type, e.g. _t=FUTURE"), ("LAYOUT 1|2|4", "pane layout"), ("HELP", "this help"),
]


def help_text() -> str:
    funcs = "\n".join(f"  {(a if a == '<SEC>' else '/' + a):<16}{b}" for a, b in FUNC_HELP)
    return (
        "Grammar: <SECURITY> [/FUNC] | /FUNC [args]     e.g.  AAPL · AAPL US /GP · /GP SPX · /COMP SPY QQQ\n"
        "Enter=GO  Esc=back  Up/Down=history  Tab=complete (command line)\n"
        "Uppercase letter starts a symbol; '/' starts a command. Both jump to the command line (':' also focuses it).\n"
        "Tab/Shift+Tab pane focus · Ctrl+1/2/4 or /LAYOUT n · Ctrl+W close pane · Ctrl+R refresh · ? help · Ctrl+Q quit (confirm)\n\n"
        f"{funcs}\n\n"
        "Charts: 1-9 range  [ ] interval  t type  v volume  o log  ←→ crosshair  x hide  +/- zoom  h/l pan  0 reset\n"
        "        s SMA  e EMA  b Bollinger  w VWAP  r RSI  m MACD  p params  c compare  i IEX\n"
        "Source tags: LIVE = stream · DLY = polled/unofficial (yfinance) · STALE = older than 5 min\n"
        "Aliases: ~/.freeterm/aliases.toml   Settings: ~/.freeterm/config.toml")


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape,question_mark,q", "dismiss", show=False)]
    DEFAULT_CSS = """
    HelpScreen { align: center middle; background: #000000 70%; }
    HelpScreen > Static { width: 110; height: auto; border: solid #FF5FD2; background: #000000; padding: 0 1; }
    """

    def __init__(self, hints: str) -> None:
        super().__init__()
        self.hints = hints

    def compose(self) -> ComposeResult:
        yield Static(help_text() + (f"\n\nThis screen: {self.hints}" if self.hints else ""), markup=False)


VIEWS: dict[str, type[View]] = {"Q": QuoteView, "MON": MonView, "HELP": HelpView}


# ---------------------------------------------------------------- pane
class Pane(Vertical):
    def __init__(self) -> None:
        super().__init__()
        self.history: list[str] = []
        self.view: View | None = None

    def compose(self) -> ComposeResult:
        yield Static("", classes="title")

    async def show(self, view: View, cmd: str, push: bool = True) -> None:
        if self.view is not None:
            await self.view.remove()
        if push:
            self.history.append(cmd)
        self.view = view
        self.query_one(".title", Static).update(view.title)
        await self.mount(view)
        self.focus_view()

    def focus_view(self) -> None:
        if self.view:
            tables = self.view.query(DataTable)
            (tables.first() if tables else self.view).focus()


# ---------------------------------------------------------------- app
class FreeTerm(App):
    CSS_PATH = "theme.tcss"
    TITLE = "freeterm"
    BINDINGS = [
        # priority: otherwise Screen's built-in Tab (focus_next) wins and cycles every widget, not panes
        Binding("tab", "pane(1)", show=False, priority=True),
        Binding("shift+tab", "pane(-1)", show=False, priority=True),
        Binding("ctrl+1", "layout(1)", show=False), Binding("ctrl+2", "layout(2)", show=False),
        Binding("ctrl+4", "layout(4)", show=False), Binding("ctrl+w", "close_pane", show=False),
        Binding("ctrl+r", "force_refresh", show=False), Binding("question_mark", "help", show=False),
        Binding("escape", "back", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.cfg = Config()
        self.hub = DataHub(self.cfg)
        self.aliases = load_aliases()
        self.cmd_hist: list[str] = []
        self.hist_i = 0
        self.tab_state: tuple[str, list[str], int] | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            # select_on_focus=False: we focus it with the first typed letter already inserted
            yield Input(placeholder="AAPL | /GP AAPL | /HELP", id="cmd", select_on_focus=False)
            yield Static("", id="mkt")
            yield Static("", id="clock")
        yield Horizontal(id="panes")
        yield Static("", id="hints")
        yield Static("", id="status")

    async def on_mount(self) -> None:
        await self.set_layout(int(self.cfg.get("general", "layout", 1)))
        self.set_interval(1, self.tick)
        self.run_worker(self.start_streams(), group="streams")
        start = self.cfg.get("general", "start_command", "/MON")
        if self.cfg.get("general", "command_syntax") != "slash":
            # Migrate saved startup input once; interactive input is always unambiguous.
            words = start.strip().lstrip(":").split()
            if (words and words[0].startswith("/") and words[0] != "/"
                    and not words[0].startswith("/?") and words[0][1:].upper() not in FUNCS):
                start = "/? " + start.strip()[1:]
            elif not any(word.startswith("/") for word in words):
                for i, word in enumerate(words):
                    if word.upper() in FUNCS:
                        words[i] = "/" + word
                        break
                start = " ".join(words)
            self.cfg.set("general", "start_command", start)
            self.cfg.set("general", "command_syntax", "slash")
        await self.run_command_async(start)
        self.tick()

    def stream_wants(self) -> list[str]:
        """Symbols worth streaming, most important first: recently viewed, then watchlist, then crypto board."""
        from .boards import BOARDS
        recent = [s for s, _ in sorted(self.hub.watch.items(), key=lambda kv: -kv[1])]
        return recent + load_watchlist() + [s for s, _ in BOARDS["CRYPTO"]]

    async def start_streams(self) -> None:
        from .live import alpaca_poll, alpaca_stream, coinbase_stream
        tasks = [coinbase_stream(self.hub, self.stream_wants)]
        await self.hub.validate_alpaca()          # IEX overlay can only ever turn on after keys validate
        if self.hub.alpaca_ok:
            tasks += [alpaca_stream(self.hub, self.stream_wants), alpaca_poll(self.hub, self.stream_wants)]
        await asyncio.gather(*tasks)

    async def action_quit(self) -> None:
        if isinstance(self.screen, QuitConfirm):
            self.screen.dismiss(True)
            return

        def confirmed(accepted: bool | None) -> None:
            if accepted:
                self.exit()

        self.push_screen(QuitConfirm(), confirmed)

    def action_help_quit(self) -> None:
        self.notify("Press [b]Ctrl+Q[/b] to open quit confirmation.", title="Do you want to quit?")

    def action_help(self) -> None:
        if not isinstance(self.screen, ModalScreen):
            p = self.active_pane()
            self.push_screen(HelpScreen(p.view.hints if p.view else ""))

    # -- layout
    @property
    def panes(self) -> list[Pane]:
        return list(self.query(Pane))

    def active_pane(self) -> Pane:
        """The pane commands go to: the last pane that had focus (the command line itself is outside panes)."""
        ps = self.panes
        for p in ps:
            if p.has_focus_within:
                return p
        if getattr(self, "_active", None) in ps:
            return self._active
        return ps[0]

    def mark_active(self, pane: Pane) -> None:
        self._active = pane
        for p in self.panes:
            p.set_class(p is pane, "active")

    async def set_layout(self, n: int) -> None:
        box = self.query_one("#panes")
        box.set_classes({1: "", 2: "l2", 4: "l4"}[n])
        cur = self.panes
        if len(cur) < n:
            await box.mount_all([Pane() for _ in range(n - len(cur))])
            for p in self.panes[len(cur):]:
                await self._open(p, "/MON" if not cur else "/HELP")
        for p in cur[n:]:
            await p.remove()
        self.cfg.set("general", "layout", n)

    async def action_layout(self, n: int) -> None:
        await self.set_layout(n)

    async def action_close_pane(self) -> None:
        n = len(self.panes)
        if n > 1:
            await self.active_pane().remove()
            await self.set_layout(2 if n > 2 else 1)

    def action_pane(self, d: int) -> None:
        if isinstance(self.screen, ModalScreen):
            self.screen.focus_next() if d > 0 else self.screen.focus_previous()
            return
        cmd = self.query_one("#cmd", Input)
        if self.focused is cmd and d > 0 and cmd.value.strip():
            self.complete(cmd)                       # Tab in a non-empty command line = autocomplete
            return
        ps = self.panes
        cur = self.active_pane()
        i = ps.index(cur)
        if self.focused is not cmd and len(ps) > 1:
            i = (i + d) % len(ps)                    # from the command line, Tab returns to the active pane
        ps[i].focus_view()

    def action_force_refresh(self) -> None:
        self.hub.mem = type(self.hub.mem)()
        for p in self.panes:
            if p.view:
                p.view.reload()

    async def action_back(self) -> None:
        p = self.active_pane()
        if len(p.history) > 1:
            p.history.pop()
            await self._open(p, p.history[-1], push=False)

    def action_cmd(self, c: str) -> None:
        self.run_command(c)

    # -- commands
    def run_command(self, line: str) -> None:
        self.run_worker(self.run_command_async(line))

    async def run_command_async(self, line: str) -> None:
        await self._open(self.active_pane(), line)

    async def _open(self, pane: Pane, line: str, push: bool = True) -> None:
        try:
            func, sec, args = parse(line, self.aliases)
        except ValueError as e:
            if str(e):
                self.hub.warn(str(e))
            return
        if func == "LAYOUT":
            await self.set_layout(int(args[0]) if args and args[0] in "124" else 1)
            return
        if func in SEC_FUNCS and not sec:
            sec = args.pop(0) if args else (pane.view.sec if pane.view else None)
            sec = self.aliases.get(sec, sec) if sec else None
            if not sec:
                self.hub.warn(f"/{func} needs a security, e.g. /{func} AAPL")
                return
        cls = VIEWS.get(func)
        if cls is None:
            cls = type(f"Stub{func}", (StubView,), {"FUNC": func})
        await pane.show(cls(self.hub, sec, args), line.strip().upper(), push)
        self.update_hints()

    # -- command line
    @on(Input.Submitted, "#cmd")
    def submitted(self, e: Input.Submitted) -> None:
        line = e.value.strip()
        e.input.value = ""
        if line:
            self.cmd_hist.append(line)
            self.hist_i = len(self.cmd_hist)
            self.run_command(line)

    def on_key(self, e) -> None:
        cmd = self.query_one("#cmd", Input)
        if self.focused is cmd:
            if e.key in ("up", "down") and self.cmd_hist:
                self.hist_i = max(0, min(len(self.cmd_hist), self.hist_i + (-1 if e.key == "up" else 1)))
                cmd.value = self.cmd_hist[self.hist_i] if self.hist_i < len(self.cmd_hist) else ""
                cmd.cursor_position = len(cmd.value)
                e.stop()
            elif e.key == "escape":
                if cmd.value:
                    cmd.value = ""
                else:
                    self.active_pane().focus_view()
                e.stop()
            else:
                self.tab_state = None
            return
        if isinstance(self.screen, ModalScreen):
            return
        if e.character and (e.character.isupper() or e.character in ":/"):
            cmd.focus()
            cmd.value = "" if e.character == ":" else e.character
            cmd.cursor_position = len(cmd.value)
            e.stop()

    def complete(self, cmd: Input) -> None:
        words = cmd.value.upper().split(" ")
        last = words[-1]
        if (self.tab_state and self.tab_state[0] == " ".join(words[:-1])
                and last == self.tab_state[1][self.tab_state[2]]):
            stem, opts, i = self.tab_state
            i = (i + 1) % len(opts)
        else:
            pool = sorted({"/" + f for f in FUNCS} if last.startswith("/")
                          else set(self.aliases) | set(load_watchlist()))
            opts = [w for w in pool if w.startswith(last)] or [last]
            stem, i = " ".join(words[:-1]), 0
        self.tab_state = (stem, opts, i)
        cmd.value = (stem + " " if stem else "") + opts[i]
        cmd.cursor_position = len(cmd.value)

    # -- bars
    def update_hints(self) -> None:
        p = self.active_pane()
        h = p.view.hints if p.view else ""
        self.query_one("#hints", Static).update(f"{h}   │ Tab pane  Ctrl+1/2/4 layout  ? help  Ctrl+Q quit (confirm)")

    def on_descendant_focus(self, e) -> None:
        node = e.widget if hasattr(e, "widget") else self.focused
        while node is not None and not isinstance(node, Pane):
            node = node.parent
        if node is not None:
            self.mark_active(node)
        self.update_hints()

    def tick(self) -> None:
        now = datetime.now()
        self.query_one("#clock", Static).update(f"{now:%H:%M:%S} │ {datetime.now(ET):%H:%M} ET")
        self.query_one("#mkt", Static).update(market_status())
        iex = "IEX:on" if self.cfg.get("iex", "enabled") and self.hub.alpaca_ok else \
            f"IEX:off({self.hub.alpaca_reason or 'toggled off'})"
        warn = f" │ ⚠ {self.hub.warnings[-1]}" if self.hub.warnings else ""
        self.query_one("#status", Static).update(Text(f"{self.hub.status()} │ {iex}{warn}",
                                                      style=WARN if warn else DIM))


def main() -> None:
    import logging
    import warnings
    warnings.filterwarnings("ignore")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    register()
    FreeTerm().run()


def register() -> None:
    """Import feature modules; each adds its views to VIEWS."""
    from . import boards, charts, depth, fundamentals  # noqa: F401
