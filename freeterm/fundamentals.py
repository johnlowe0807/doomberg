"""Phase 6: EDGAR (ticker→CIK, XBRL companyfacts, submissions), FA, FIL + pager, ERN, EVTS."""
from __future__ import annotations

import asyncio
import re

import pandas as pd
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, OptionList, Static, TextArea
from textual.widgets.text_area import Selection
from textual_plotext import PlotextPlot

from .app import DIM, UP, DOWN, WARN, VIEWS, View, colored, fnum, load_watchlist
from .charts import RGB_UP, RGB_DN

SEC_RATE = 8.0  # SEC asks for <=10 req/s


def sec_headers(hub) -> dict:
    return {"User-Agent": hub.cfg.get("sec", "user_agent"), "Accept-Encoding": "gzip, deflate"}


async def sec_get(hub, url: str, disk_ns: str, disk_age: float | None, as_json: bool = True):
    return await hub.http("sec", url, SEC_RATE, sec_headers(hub), ttl=3600, disk_ns=disk_ns, disk_age=disk_age, as_json=as_json)


async def edgar_tickers(hub) -> dict[str, tuple[int, str]]:
    d = await sec_get(hub, "https://www.sec.gov/files/company_tickers.json", "sec", 7 * 86400)
    return {v["ticker"].upper(): (int(v["cik_str"]), v["title"]) for v in (d or {}).values()}


async def cik_for(hub, sym: str) -> int | None:
    t = await edgar_tickers(hub)
    hit = t.get(sym.upper()) or t.get(sym.upper().replace(".", "-")) or t.get(sym.upper().replace("-", "."))
    return hit[0] if hit else None


async def companyfacts(hub, cik: int):
    return await sec_get(hub, f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", "secfacts", 86400)


async def filings(hub, cik: int) -> list[dict]:
    """All filings from the submissions index, including the older paginated files."""
    d = await sec_get(hub, f"https://data.sec.gov/submissions/CIK{cik:010d}.json", "secsub", 86400)
    if not d:
        return []
    pages = [d["filings"]["recent"]]
    for f in d["filings"].get("files", []):
        p = await sec_get(hub, f"https://data.sec.gov/submissions/{f['name']}", "secsub", None)  # old pages never change
        if p:
            pages.append(p)
    out = []
    for pg in pages:
        n = len(pg.get("accessionNumber", []))
        col = lambda k, i: (pg.get(k) or [""] * n)[i]
        for i in range(n):
            out.append({"acc": col("accessionNumber", i), "date": col("filingDate", i), "form": col("form", i),
                        "doc": col("primaryDocument", i), "desc": col("primaryDocDescription", i),
                        "items": col("items", i), "period": col("reportDate", i)})
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def html_to_text(raw: str) -> str:
    from bs4 import BeautifulSoup
    raw = re.sub(r"<TEXT>\s*begin \d{3} .*?end\s*</TEXT>", "<TEXT>[binary attachment omitted]</TEXT>", raw, flags=re.S)
    if not re.search(r"<(html|body|div|p|table)\b", raw[:200000], re.I):
        return raw
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup.find_all(["script", "style", "ix:header", "head"]):
        tag.decompose()
    for tag in soup.find_all(style=re.compile(r"display:\s*none", re.I)):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for td in soup.find_all(["td", "th"]):
        td.append("  ")
    for tag in soup.find_all(["p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "document"]):
        tag.append("\n")
    lines = [re.sub(r"[ \t\xa0​]+", " ", l).strip() for l in soup.get_text("").splitlines()]
    out, blank = [], 0
    for l in lines:
        blank = blank + 1 if not l else 0
        if blank <= 1:
            out.append(l)
    return "\n".join(out).strip()


async def filing_text(hub, cik: int, f: dict, exhibit_991: bool = False) -> str:
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{f['acc'].replace('-', '')}"
    doc = f["doc"]
    if exhibit_991:
        idx = await sec_get(hub, f"{base}/index.json", "secidx", None)
        names = [i["name"] for i in (idx or {}).get("directory", {}).get("item", [])]
        pick = [n for n in names if re.search(r"ex-?99[._-]?0?1|ex991|exhibit99", n, re.I) and re.search(r"\.(htm|html|txt)$", n, re.I)]
        pick = pick or [n for n in names if "99" in n and re.search(r"\.(htm|html|txt)$", n, re.I)]
        doc = pick[0] if pick else doc
    url = f"{base}/{doc}" if doc else f"https://www.sec.gov/Archives/edgar/data/{cik}/{f['acc']}.txt"
    raw = await sec_get(hub, url, "secdocs", None, as_json=False)
    if not raw:
        return f"Could not fetch {url}"
    return f"{url}\n\n" + await asyncio.to_thread(html_to_text, raw)


# ---------------------------------------------------------------- pager
class PagerArea(TextArea):
    def __init__(self, text: str, pager: "Pager") -> None:
        super().__init__(text, read_only=True, show_line_numbers=False, soft_wrap=True)
        self.pager = pager

    async def _on_key(self, e) -> None:
        k = e.character or e.key
        h = max(1, self.size.height - 2)
        acts = {
            "j": lambda: self.move_cursor_relative(rows=1), "down": lambda: self.move_cursor_relative(rows=1),
            "k": lambda: self.move_cursor_relative(rows=-1), "up": lambda: self.move_cursor_relative(rows=-1),
            "space": lambda: self.move_cursor_relative(rows=h), " ": lambda: self.move_cursor_relative(rows=h),
            "b": lambda: self.move_cursor_relative(rows=-h), "pagedown": lambda: self.move_cursor_relative(rows=h),
            "pageup": lambda: self.move_cursor_relative(rows=-h),
            "g": lambda: self.move_cursor((0, 0)), "G": lambda: self.move_cursor((self.document.line_count - 1, 0)),
            "/": self.pager.ask_search, "n": lambda: self.pager.find(1), "N": lambda: self.pager.find(-1),
            "t": self.pager.toc, "q": self.pager.close, "escape": self.pager.close,
        }
        if k in acts:
            e.stop()
            e.prevent_default()
            acts[k]()
            self.pager.update_hdr()
        elif e.is_printable:
            e.stop()
            e.prevent_default()


class TocScreen(ModalScreen[int]):
    BINDINGS = [Binding("escape", "dismiss(-1)", show=False)]

    def __init__(self, items: list[tuple[int, str]]) -> None:
        super().__init__()
        self.items = items

    def compose(self) -> ComposeResult:
        yield OptionList(*[f"{ln + 1:>6}  {txt[:90]}" for ln, txt in self.items])

    def on_option_list_option_selected(self, e: OptionList.OptionSelected) -> None:
        self.dismiss(self.items[e.option_index][0])


class Pager(ModalScreen):
    """Full-screen filing reader. j/k scroll, / search, n/N next/prev, g/G top/bottom, t Item TOC, q/Esc close."""
    DEFAULT_CSS = """
    Pager { background: #000000; }
    Pager #phdr { height: 1; background: #0B3D91; color: #FFFFFF; text-style: bold; padding: 0 1; }
    Pager #phint { height: 1; background: #0B3D91; color: #FFFFFF; padding: 0 1; }
    Pager TextArea { height: 1fr; background: #000000; color: #5FD7FF; border: none; }
    Pager #psearch { height: 1; border: none; background: #1a0016; color: #FF5FD2; display: none; }
    TocScreen { align: center middle; }
    TocScreen OptionList { width: 100; height: 80%; border: solid #FF5FD2; background: #000000; }
    """

    def __init__(self, title: str, text: str) -> None:
        super().__init__()
        self.ptitle, self.text = title, text
        self.lines = text.splitlines()
        self.term = ""

    def compose(self) -> ComposeResult:
        yield Static(self.ptitle, id="phdr")
        yield PagerArea(self.text, self)
        yield Input(placeholder="search…", id="psearch")
        yield Static("j/k scroll  space/b page  g/G top/bottom  / search  n/N next/prev  t Item TOC  q close", id="phint")

    def on_mount(self) -> None:
        self.query_one(PagerArea).focus()
        self.update_hdr()

    def update_hdr(self) -> None:
        ta = self.query_one(PagerArea)
        row = ta.cursor_location[0]
        pct = 100 * row / max(1, len(self.lines) - 1)
        s = f"  /{self.term}" if self.term else ""
        self.query_one("#phdr", Static).update(f"{self.ptitle}   line {row + 1}/{len(self.lines)} ({pct:.0f}%){s}")

    def close(self) -> None:
        self.dismiss()

    def ask_search(self) -> None:
        i = self.query_one("#psearch", Input)
        i.display = True
        i.value = self.term
        i.focus()

    @on(Input.Submitted, "#psearch")
    def searched(self, e: Input.Submitted) -> None:
        e.input.display = False
        self.term = e.value.strip()
        self.query_one(PagerArea).focus()
        self.find(1)

    def find(self, d: int) -> None:
        if not self.term:
            return
        ta = self.query_one(PagerArea)
        row, col = ta.cursor_location
        term = self.term.lower()
        n = len(self.lines)
        for k in range(n + 1):
            if d > 0:
                r = (row + k) % n
                line = self.lines[r].lower()
                j = line.find(term, col + 1 if k == 0 else 0)
            else:
                # cursor sits at the end of the current match; end=col-1 excludes it
                r = (row - k) % n
                line = self.lines[r].lower()
                j = line.rfind(term, 0, max(0, col - 1) if k == 0 else len(line))
            if j >= 0:
                ta.selection = Selection((r, j), (r, j + len(term)))
                ta.scroll_cursor_visible(center=True)
                self.update_hdr()
                return
        self.app.hub.warn(f"pager: {self.term!r} not found")

    def toc(self) -> None:
        items = [(i, l.strip()) for i, l in enumerate(self.lines)
                 if re.match(r"^\s*item\s+\d{1,2}[a-c]?\s*[\.:\-—–]?\s*\S", l, re.I) and len(l) < 160]
        if not items:
            self.app.hub.warn("pager: no 'Item' headings found")
            return

        def jump(ln: int | None) -> None:
            if ln is not None and ln >= 0:
                ta = self.query_one(PagerArea)
                ta.move_cursor((ln, 0))
                ta.scroll_cursor_visible(center=False, animate=False)
                ta.focus()
                self.update_hdr()
        self.app.push_screen(TocScreen(items), jump)


async def open_filing(view: View, cik: int, f: dict, exhibit: bool = False) -> None:
    view.hub.warn(f"loading {f['form']} {f['date']} …")
    text = await filing_text(view.hub, cik, f, exhibit)
    view.app.push_screen(Pager(f"{view.sec} {f['form']} {f['date']} {f['acc']}{' EX-99.1' if exhibit else ''}", text))


# ---------------------------------------------------------------- FA
M = "m"  # display in millions
STATEMENTS = {
    "income": ("Income statement", "flow", [
        ("Revenue", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                     "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet"], M),
        ("Cost of revenue", ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"], M),
        ("Gross profit", ["GrossProfit"], M),
        ("R&D", ["ResearchAndDevelopmentExpense"], M),
        ("SG&A", ["SellingGeneralAndAdministrativeExpense"], M),
        ("Operating income", ["OperatingIncomeLoss"], M),
        ("Interest expense", ["InterestExpense", "InterestExpenseNonoperating"], M),
        ("Pretax income", ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                           "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"], M),
        ("Income tax", ["IncomeTaxExpenseBenefit"], M),
        ("Net income", ["NetIncomeLoss", "ProfitLoss"], M),
        ("EPS basic", ["EarningsPerShareBasic"], "eps"),
        ("EPS diluted", ["EarningsPerShareDiluted"], "eps"),
        ("Diluted shares", ["WeightedAverageNumberOfDilutedSharesOutstanding"], M),
    ]),
    "balance": ("Balance sheet", "inst", [
        ("Cash & equivalents", ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"], M),
        ("Short-term investments", ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesCurrent"], M),
        ("Receivables", ["AccountsReceivableNetCurrent"], M),
        ("Inventory", ["InventoryNet"], M),
        ("Current assets", ["AssetsCurrent"], M),
        ("PP&E net", ["PropertyPlantAndEquipmentNet"], M),
        ("Goodwill", ["Goodwill"], M),
        ("Total assets", ["Assets"], M),
        ("Accounts payable", ["AccountsPayableCurrent"], M),
        ("Current liabilities", ["LiabilitiesCurrent"], M),
        ("Long-term debt", ["LongTermDebtNoncurrent", "LongTermDebt"], M),
        ("Total liabilities", ["Liabilities"], M),
        ("Retained earnings", ["RetainedEarningsAccumulatedDeficit"], M),
        ("Shareholders' equity", ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"], M),
    ]),
    "cash": ("Cash flow", "flow", [
        ("Operating cash flow", ["NetCashProvidedByUsedInOperatingActivities"], M),
        ("D&A", ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet"], M),
        ("Stock comp", ["ShareBasedCompensation"], M),
        ("Capex", ["PaymentsToAcquirePropertyPlantAndEquipment"], M),
        ("Investing cash flow", ["NetCashProvidedByUsedInInvestingActivities"], M),
        ("Buybacks", ["PaymentsForRepurchaseOfCommonStock"], M),
        ("Dividends paid", ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"], M),
        ("Financing cash flow", ["NetCashProvidedByUsedInFinancingActivities"], M),
    ]),
}


def xbrl_line(facts: dict, concepts: list[str], kind: str, annual: bool) -> dict[str, float]:
    """Merge alternate concepts per period using XBRL `frame` (CY2023 / CY2023Q1 / CY2023Q4I)."""
    taxo = {**facts.get("facts", {}).get("ifrs-full", {}), **facts.get("facts", {}).get("us-gaap", {})}
    out: dict[str, float] = {}
    for c in concepts:
        f = taxo.get(c)
        if not f:
            continue
        for unit, arr in f.get("units", {}).items():
            for x in arr:
                fr = x.get("frame") or ""
                if kind == "inst":
                    m = re.fullmatch(r"CY(\d{4})Q(\d)I", fr)
                    if not m:
                        continue
                    if annual:
                        if not str(x.get("form", "")).startswith(("10-K", "20-F", "40-F")):
                            continue
                        lab = m.group(1)
                    else:
                        lab = f"{m.group(1)}Q{m.group(2)}"
                else:
                    m = re.fullmatch(r"CY(\d{4})" if annual else r"CY(\d{4})Q(\d)", fr)
                    if not m:
                        continue
                    lab = m.group(1) if annual else f"{m.group(1)}Q{m.group(2)}"
                out.setdefault(lab, float(x["val"]))
    return out


def derive_q4(line: dict[str, float], annual: dict[str, float]) -> None:
    for y, v in annual.items():
        qs = [line.get(f"{y}Q{i}") for i in (1, 2, 3)]
        if f"{y}Q4" not in line and all(q is not None for q in qs):
            line[f"{y}Q4"] = v - sum(qs)


class FaView(View):
    FUNC = "FA"
    hints = "i income  b balance  c cash flow  t annual/qtr  ←→ periods  type a year to jump  f filings"

    def compose(self) -> ComposeResult:
        yield Static("", classes="msg", id="fahdr")
        yield DataTable(cursor_type="cell", fixed_columns=1, id="fa")

    def on_mount(self) -> None:
        self.stmt, self.annual, self.ybuf, self.facts, self.src = "income", True, "", None, ""
        self.cik = None
        super().on_mount()

    async def load(self) -> None:
        self.cik = await cik_for(self.hub, self.sec)
        if self.cik:
            self.facts = await companyfacts(self.hub, self.cik)
        self.render_stmt()

    def render_stmt(self) -> None:
        if self.facts:
            name, kind, lines = STATEMENTS[self.stmt]
            rows = []
            for lab, concepts, unit in lines:
                d = xbrl_line(self.facts, concepts, kind, self.annual)
                if not self.annual and kind == "flow" and unit == M:
                    derive_q4(d, xbrl_line(self.facts, concepts, kind, True))
                rows.append((lab, d, unit))
            if self.stmt == "cash":
                ocf, capex = rows[0][1], rows[3][1]
                rows.insert(4, ("Free cash flow", {p: ocf[p] - capex.get(p, 0) for p in ocf}, M))
            periods = sorted({p for _, d, _ in rows for p in d}, reverse=True)
            self.src = "SEC EDGAR XBRL (frames; calendar-aligned)"
            self.fill(name, periods, rows)
        else:
            self.run_worker(self.yf_fallback(), exclusive=True, group="fa")

    def fill(self, name: str, periods: list[str], rows) -> None:
        self.periods = periods
        t = self.query_one("#fa", DataTable)
        t.clear(columns=True)
        t.add_columns("USD m", *periods)
        for lab, d, unit in rows:
            cells = [Text(lab, style="bold")]
            for p in periods:
                v = d.get(p)
                if v is None:
                    cells.append(Text("—", style=DIM, justify="right"))
                else:
                    v = v / 1e6 if unit == M else v
                    cells.append(Text(fnum(v, 2 if unit == "eps" else 0), justify="right", style=DOWN if v < 0 else ""))
            t.add_row(*cells)
        span = f"{periods[-1]}–{periods[0]}" if periods else "no data"
        self.query_one("#fahdr", Static).update(Text(
            f"{name} · {'annual' if self.annual else 'quarterly'} · {span} · source: {self.src}", style="bold #FFFFFF"))

    async def yf_fallback(self) -> None:
        import yfinance as yf
        attr = {"income": "income_stmt", "balance": "balance_sheet", "cash": "cashflow"}[self.stmt]
        attr = attr if self.annual else "quarterly_" + attr
        df = await self.hub.yf_call(f"fa:{self.sec}:{attr}", 3600, lambda: getattr(yf.Ticker(self.sec), attr))
        if df is None or df.empty:
            self.query_one("#fahdr", Static).update(Text(f"N/A — no fundamentals for {self.sec} (EDGAR or yfinance)", style=WARN))
            return
        periods = [f"{c:%Y-%m}" if hasattr(c, "strftime") else str(c) for c in df.columns]
        rows = [(str(idx), {p: (None if pd.isna(v) else float(v)) for p, v in zip(periods, df.loc[idx])}, M) for idx in df.index]
        rows = [(l, {k: v for k, v in d.items() if v is not None}, u) for l, d, u in rows]
        self.src = "yfinance (fallback, ~4-5 periods)"
        self.fill(STATEMENTS[self.stmt][0], periods, rows)

    def jump_year(self, y: str) -> None:
        cols = [i for i, p in enumerate(getattr(self, "periods", [])) if p.startswith(y)]
        t = self.query_one("#fa", DataTable)
        if cols:
            t.move_cursor(column=cols[0] + 1)
            return
        earliest = self.periods[-1][:4] if getattr(self, "periods", None) else "?"
        msg = (f"No structured data for {y}: free XBRL starts ~2009 (earliest here: {earliest}). "
               f"Press f to open {y} 10-K filings in FIL.")
        self.fil_year = y
        self.query_one("#fahdr", Static).update(Text(msg, style=WARN))

    def on_key(self, e) -> None:
        k = e.character
        if k and k.isdigit():
            self.ybuf = (self.ybuf + k)[-4:]
            if len(self.ybuf) == 4:
                self.jump_year(self.ybuf)
                self.ybuf = ""
        elif k in ("i", "b", "c"):
            self.stmt = {"i": "income", "b": "balance", "c": "cash"}[k]
            self.render_stmt()
        elif k == "t":
            self.annual = not self.annual
            self.render_stmt()
        elif k == "f":
            y = getattr(self, "fil_year", "")
            self.app.run_command(f"{self.sec} /FIL 10-K {y}".strip())
        else:
            return
        e.stop()


# ---------------------------------------------------------------- FIL
class FilView(View):
    FUNC = "FIL"
    hints = "Enter read  f form filter  type a year to jump  a all forms"

    def compose(self) -> ComposeResult:
        yield Static("", classes="msg", id="filhdr")
        yield DataTable(cursor_type="row", id="fil")

    def on_mount(self) -> None:
        self.form = next((a for a in self.args if not a.isdigit()), "")
        self.year = next((a for a in self.args if a.isdigit() and len(a) == 4), "")
        self.ybuf, self.rows, self.cik = "", [], None
        super().on_mount()

    async def load(self) -> None:
        self.cik = await cik_for(self.hub, self.sec)
        if not self.cik:
            self.query_one("#filhdr", Static).update(Text(f"N/A — {self.sec} not found in EDGAR ticker map", style=WARN))
            return
        self.query_one("#filhdr", Static).update(Text("loading EDGAR submissions index…", style=DIM))
        self.all = await filings(self.hub, self.cik)
        self.render_rows()

    def render_rows(self) -> None:
        f = self.form.upper()
        self.rows = [r for r in self.all if not f or r["form"].upper() == f or r["form"].upper().startswith(f + "/")]
        t = self.query_one("#fil", DataTable)
        t.clear(columns=True)
        t.add_columns("Filed", "Form", "Period", "Description", "Items", "Accession")
        for i, r in enumerate(self.rows):
            t.add_row(r["date"], Text(r["form"], style="bold #FFD700"), r["period"] or "", (r["desc"] or "")[:40],
                      r["items"] or "", Text(r["acc"], style=DIM), key=str(i))
        span = f"{self.all[-1]['date'][:4]}–{self.all[0]['date'][:4]}" if self.all else ""
        self.query_one("#filhdr", Static).update(Text(
            f"{len(self.rows)} filings{' (' + f + ')' if f else ''} · EDGAR index {span} · CIK {self.cik}", style="bold #FFFFFF"))
        if self.year:
            self.jump(self.year)

    def jump(self, y: str) -> None:
        for i, r in enumerate(self.rows):
            if r["date"][:4] <= y:
                self.query_one("#fil", DataTable).move_cursor(row=i)
                return

    def on_key(self, e) -> None:
        k = e.character
        if k and k.isdigit():
            self.ybuf = (self.ybuf + k)[-4:]
            if len(self.ybuf) == 4:
                self.jump(self.ybuf)
                self.ybuf = ""
        elif k == "f":
            from .app import PromptScreen

            def setf(v: str) -> None:
                self.form = v or ""
                self.render_rows()
            self.app.push_screen(PromptScreen("Form filter (10-K, 10-Q, 8-K, DEF 14A … blank = all):", self.form), setf)
        elif k == "a":
            self.form = ""
            self.render_rows()
        else:
            return
        e.stop()

    def on_data_table_row_selected(self, e: DataTable.RowSelected) -> None:
        f = self.rows[int(e.row_key.value)]
        self.run_worker(open_filing(self, self.cik, f), group="pager")


# ---------------------------------------------------------------- ERN
class ErnView(View):
    FUNC = "ERN"
    hints = "Tab to 8-K list, Enter opens the EX-99.1 press release"

    def compose(self) -> ComposeResult:
        yield Static("", classes="msg", id="ernhdr")
        with Horizontal():
            yield DataTable(cursor_type="row", id="eps")
            yield PlotextPlot(id="surp")
        yield DataTable(cursor_type="row", id="k8")

    def on_mount(self) -> None:
        self.query_one(PlotextPlot).theme = "clear"
        self.k8, self.cik = [], None
        super().on_mount()

    async def load(self) -> None:
        import yfinance as yf
        ed = await self.hub.yf_call(f"ed:{self.sec}", 3600, lambda: yf.Ticker(self.sec).get_earnings_dates(limit=28))
        t = self.query_one("#eps", DataTable)
        t.clear(columns=True)
        t.add_columns("Date", "EPS est", "EPS act", "Surprise %")
        nxt = None
        past = []
        if ed is not None and len(ed):
            now = pd.Timestamp.now(tz=ed.index.tz)
            for idx, r in ed.iterrows():
                est, act = r.get("EPS Estimate"), r.get("Reported EPS")
                sp = r.get("Surprise(%)")
                if idx > now and pd.isna(act):
                    nxt = idx
                f = lambda v: None if v is None or pd.isna(v) else float(v)
                t.add_row(f"{idx:%Y-%m-%d}", Text(fnum(f(est)), justify="right"), Text(fnum(f(act)), justify="right"),
                          colored(f(sp), fnum(f(sp))))
                if f(sp) is not None:
                    past.append((idx, f(sp)))
        self.query_one("#ernhdr", Static).update(Text(
            f"{self.sec} next earnings: {nxt:%Y-%m-%d %H:%M %Z}" if nxt is not None else f"{self.sec} next earnings: N/A",
            style="bold #FFFFFF"))
        p = self.query_one(PlotextPlot)
        plt = p.plt
        plt.clear_figure()
        past = sorted(past)[-16:]
        if past:
            plt.bar([d.strftime("%y-%m") for d, _ in past], [v for _, v in past],
                    color=[RGB_UP if v >= 0 else RGB_DN for _, v in past], marker="▌")
            plt.title("EPS surprise %")
        p.refresh()
        self.cik = await cik_for(self.hub, self.sec)
        k = self.query_one("#k8", DataTable)
        k.clear(columns=True)
        k.add_columns("Filed", "Form", "Items", "8-K Item 2.02 earnings releases (EDGAR)")
        if self.cik:
            fl = await filings(self.hub, self.cik)
            self.k8 = [f for f in fl if f["form"].startswith("8-K") and "2.02" in (f["items"] or "")]
            for i, f in enumerate(self.k8):
                k.add_row(f["date"], f["form"], f["items"], Text("Enter → EX-99.1", style=DIM), key=str(i))

    @on(DataTable.RowSelected, "#k8")
    def open8k(self, e: DataTable.RowSelected) -> None:
        f = self.k8[int(e.row_key.value)]
        self.run_worker(open_filing(self, self.cik, f, exhibit=True), group="pager")


# ---------------------------------------------------------------- EVTS
class EvtsView(View):
    FUNC = "EVTS"
    hints = "Upcoming earnings for watchlist (yfinance calendar)  Enter opens ERN"

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row")

    async def load(self) -> None:
        import yfinance as yf
        syms = [s for s in load_watchlist() if not any(c in s for c in "^=") and not s.endswith("-USD")]

        async def one(s):
            cal = await self.hub.yf_call(f"cal:{s}", 3600, lambda: yf.Ticker(s).calendar)
            d = (cal or {}).get("Earnings Date") if isinstance(cal, dict) else None
            d = d[0] if isinstance(d, (list, tuple)) and d else d
            return s, d, cal if isinstance(cal, dict) else {}
        res = await asyncio.gather(*(one(s) for s in syms))
        t = self.query_one(DataTable)
        t.clear(columns=True)
        t.add_columns("Date", "Symbol", "EPS est (avg)", "Rev est (avg)", "Days")
        today = pd.Timestamp.today().normalize()
        rows = sorted([r for r in res if r[1] is not None], key=lambda r: pd.Timestamp(r[1]))
        for s, d, cal in rows:
            days = (pd.Timestamp(d) - today).days
            t.add_row(f"{pd.Timestamp(d):%Y-%m-%d}", Text(s, style="bold"), Text(fnum(cal.get("Earnings Average")), justify="right"),
                      Text(fnum((cal.get("Revenue Average") or 0) / 1e9) + "B" if cal.get("Revenue Average") else "N/A", justify="right"),
                      Text(str(days), style=UP if 0 <= days <= 7 else DIM, justify="right"), key=s)
        for s, d, _ in res:
            if d is None:
                t.add_row("N/A", s, "", "", "", key=s)

    def on_data_table_row_selected(self, e: DataTable.RowSelected) -> None:
        self.app.run_command(f"{e.row_key.value} /ERN")


VIEWS.update({"FA": FaView, "FIL": FilView, "ERN": ErnView, "EVTS": EvtsView})
