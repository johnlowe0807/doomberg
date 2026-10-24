"""Phase 5 depth screens: DES, HP, CN, TOP, OMON (local Black-Scholes greeks), /search."""
from __future__ import annotations

import asyncio
import difflib
import math
import time
from datetime import datetime, timezone

import pandas as pd
from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import DataTable, Static

from .app import DIM, ET, WARN, VIEWS, View, colored, fbig, fnum
from .charts import DEF_IV, PERIOD, RANGES


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
    hints = "Company profile (yfinance .info, unofficial)  /GP chart  /FA financials  /ERN earnings"

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row", show_header=False)
        yield Static("", classes="msg", id="summary")

    async def load(self) -> None:
        import yfinance as yf
        info = await self.hub.yf_call(f"info:{self.sec}", 3600, lambda: yf.Ticker(self.sec).info)
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
def norm_yf_news(items) -> list[dict]:
    """yfinance has shipped two news shapes: flat dicts and {'content': {...}}. Handle both."""
    out = []
    for it in items or []:
        c = it.get("content") or it
        url = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url")
               or c.get("link") or "")
        t = c.get("pubDate") or c.get("displayTime") or c.get("providerPublishTime")
        try:
            ts = datetime.fromtimestamp(t, timezone.utc) if isinstance(t, (int, float)) else pd.Timestamp(t).to_pydatetime()
        except Exception:
            ts = None
        src = (c.get("provider") or {}).get("displayName") or c.get("publisher") or "Yahoo"
        if c.get("title"):
            out.append({"title": c["title"], "src": src, "ts": ts, "url": url,
                        "summary": c.get("summary") or c.get("description") or ""})
    return out


async def rss(hub, url: str) -> list[dict]:
    import feedparser
    raw = await hub.http("rss", url, 2, {"User-Agent": "Mozilla/5.0 freeterm"}, ttl=300, as_json=False, retries=0)
    if not raw:
        return []
    f = await asyncio.to_thread(feedparser.parse, raw)
    name = (f.feed.get("title") or url.split("/")[2])[:18]
    out = []
    for e in f.entries:
        tp = e.get("published_parsed") or e.get("updated_parsed")
        ts = datetime.fromtimestamp(time.mktime(tp), timezone.utc) if tp else None
        out.append({"title": e.get("title", ""), "src": name, "ts": ts, "url": e.get("link", ""),
                    "summary": e.get("summary", "")})
    return out


class NewsView(View):
    FUNC = "TOP"
    hints = "Enter read article  o in reader: browser (confirm)  Ctrl+R refresh"
    refresh_secs = 300

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row")

    async def fetch(self) -> list[dict]:
        lists = await asyncio.gather(*(rss(self.hub, u) for u in self.app.cfg.get("news", "feeds", [])))
        return [x for l in lists for x in l]

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
        import yfinance as yf
        yfn = await self.hub.yf_call(f"news:{self.sec}", 300, lambda: yf.Ticker(self.sec).news)
        feed = await rss(self.hub, f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={self.sec}&region=US&lang=en-US")
        return norm_yf_news(yfn) + feed


# ---------------------------------------------------------------- OMON
def _ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def _npdf(x): return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def bs_greeks(S, K, T, r, sigma, call: bool):
    """Black-Scholes greeks. theta per calendar day, vega per 1 vol point."""
    if not (S and K and T > 0 and sigma and sigma > 0):
        return None
    st = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + sigma * sigma / 2) * T) / st
    d2 = d1 - st
    gamma = _npdf(d1) / (S * st)
    vega = S * _npdf(d1) * math.sqrt(T) / 100
    if call:
        delta = _ncdf(d1)
        theta = (-S * _npdf(d1) * sigma / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * _ncdf(d2)) / 365
    else:
        delta = _ncdf(d1) - 1
        theta = (-S * _npdf(d1) * sigma / (2 * math.sqrt(T)) + r * K * math.exp(-r * T) * _ncdf(-d2)) / 365
    return delta, gamma, theta, vega


class OmonView(View):
    FUNC = "OMON"
    hints = "[ ] expiry  +/- strikes  greeks: local Black-Scholes on yfinance IV  ITM shaded"
    refresh_secs = 30
    SIDE = ("Bid", "Ask", "Last", "Vol", "OI", "IV", "Δ", "Γ", "Θ", "ν")

    def compose(self) -> ComposeResult:
        yield Static("", classes="msg", id="hdr")
        yield DataTable(cursor_type="row", id="chain")

    def on_mount(self) -> None:
        self.exp_i = 0
        self.width = int(self.app.cfg.get("options", "strikes_each_side", 12))
        super().on_mount()

    async def load(self) -> None:
        import yfinance as yf
        sym = self.sec
        exps = await self.hub.yf_call(f"exps:{sym}", 300, lambda: yf.Ticker(sym).options)
        if not exps:
            self.query_one("#hdr", Static).update(Text(f"N/A — no listed options for {sym}", style=WARN))
            return
        self.exp_i = max(0, min(self.exp_i, len(exps) - 1))
        exp = exps[self.exp_i]
        ch = await self.hub.yf_call(f"chain:{sym}:{exp}", 30, lambda: yf.Ticker(sym).option_chain(exp))
        qs = await self.hub.quotes([sym, "^IRX"])
        S = qs[sym].price if sym in qs else None
        irx = qs.get("^IRX")
        r = irx.price / 100 if irx and irx.price else float(self.app.cfg.get("options", "risk_free_fallback", 0.045))
        exp_dt = pd.Timestamp(exp).tz_localize(ET) + pd.Timedelta(hours=16)
        T = max((exp_dt - pd.Timestamp.now(tz=ET)).total_seconds(), 60) / (365 * 86400)
        hdr = Text(f"{sym} spot {fnum(S)}  expiry {exp} ({self.exp_i + 1}/{len(exps)})  DTE {T * 365:.1f}  r {r * 100:.2f}% (^IRX)",
                   style="bold #FFFFFF")
        self.query_one("#hdr", Static).update(hdr)
        if ch is None:
            return
        calls = ch.calls.set_index("strike")
        puts = ch.puts.set_index("strike")
        strikes = sorted(set(calls.index) | set(puts.index))
        if S:
            atm = min(range(len(strikes)), key=lambda i: abs(strikes[i] - S))
            strikes = strikes[max(0, atm - self.width): atm + self.width + 1]
        t = self.query_one("#chain", DataTable)
        t.clear(columns=True)
        t.add_columns(*[f"C {c}" for c in reversed(self.SIDE)], "STRIKE", *[f"P {c}" for c in self.SIDE])
        for k in strikes:
            row = []
            for side, df, is_call in (("C", calls, True), ("P", puts, False)):
                if k in df.index:
                    o = df.loc[k]
                    o = o.iloc[0] if isinstance(o, pd.DataFrame) else o
                    iv = float(o.get("impliedVolatility") or 0)
                    g = bs_greeks(S, k, T, r, iv, is_call) if iv > 0.001 else None
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
        if S and t.row_count:
            t.move_cursor(row=min(self.width, t.row_count - 1))

    def on_key(self, e) -> None:
        k = e.character
        if k in ("[", "]"):
            self.exp_i = max(0, self.exp_i + (1 if k == "]" else -1))
        elif k in ("+", "=", "-"):
            self.width = max(3, self.width + (4 if k != "-" else -4))
        else:
            return
        e.stop()
        self.reload()


# ---------------------------------------------------------------- search
class SearchView(View):
    FUNC = "SEARCH"
    hints = "Enter open security  (EDGAR company names + Yahoo search + aliases)"

    @property
    def title(self) -> str:
        return f"SEARCH {' '.join(self.args)}"

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row")

    async def load(self) -> None:
        from .fundamentals import edgar_tickers
        q = " ".join(self.args).strip()
        ql = q.lower()
        rows: list[tuple[str, str, str]] = []
        for k, v in self.app.aliases.items():
            if ql in k.lower() or ql in v.lower():
                rows.append((k, v, "alias"))
        tick = await edgar_tickers(self.hub)
        scored = []
        for sym, (_, name) in tick.items():
            n = name.lower()
            if ql == sym.lower():
                scored.append((0, sym, name))
            elif ql in n:
                scored.append((1 + n.index(ql) / 100, sym, name))
        scored.sort()
        rows += [(s, n, "EDGAR") for _, s, n in scored[:40]]
        try:
            import yfinance as yf
            res = await self.hub.yf_call(f"search:{ql}", 600, lambda: yf.Search(q, max_results=15).quotes)
            for r in res or []:
                if r.get("symbol"):
                    rows.append((r["symbol"], r.get("longname") or r.get("shortname") or "", r.get("quoteType", "yahoo")))
        except Exception:
            pass
        if not rows:
            names = {n.lower(): s for s, (_, n) in tick.items()}
            for m in difflib.get_close_matches(ql, list(names), n=10, cutoff=0.5):
                rows.append((names[m], m, "fuzzy"))
        t = self.query_one(DataTable)
        t.clear(columns=True)
        t.add_columns("Symbol", "Name", "Type")
        seen = set()
        for s, n, ty in rows:
            if s not in seen:
                seen.add(s)
                t.add_row(Text(s, style="bold"), n[:60], Text(ty, style=DIM), key=s)
        if not rows:
            self.hub.warn(f"No matches for {q!r}")

    def on_data_table_row_selected(self, e: DataTable.RowSelected) -> None:
        self.app.run_command(e.row_key.value)


VIEWS.update({"DES": DesView, "HP": HpView, "CN": CnView, "TOP": NewsView, "OMON": OmonView, "SEARCH": SearchView})
