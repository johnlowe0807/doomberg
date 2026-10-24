"""Phase 2/8 charts: GP, GIP, COMP, FRED/SPRD series charts, indicators (numpy/pandas only)."""
from __future__ import annotations

import io
import math
import time

import numpy as np
import pandas as pd
from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import Static
from textual_plotext import PlotextPlot

from .app import DIM, UP, DOWN, WARN, VIEWS, View, fbig, fnum, colored, tag_text, PromptScreen
from .app import regular_hours
from .data import IVS, RANGE_DAYS, RANGES, DiskCache, RateLimiter
from .live import age, iex_eval

PERIOD = {"1D": "1d", "5D": "5d", "1M": "1mo", "3M": "3mo", "6M": "6mo", "YTD": "ytd", "1Y": "1y", "5Y": "5y", "MAX": "max"}
DEF_IV = {"1D": "5m", "5D": "15m", "1M": "1h", "3M": "1d", "6M": "1d", "YTD": "1d", "1Y": "1d", "5Y": "1wk", "MAX": "1wk"}
TYPES = ["candle", "ohlc", "line", "area"]
RGB_UP, RGB_DN, RGB_FG, RGB_Y, RGB_M = (0, 255, 135), (255, 77, 109), (95, 215, 255), (255, 215, 0), (255, 95, 210)
RGB_DIM = (58, 107, 128)
PALETTE = [RGB_FG, RGB_Y, RGB_M, (180, 140, 255), (255, 160, 60), RGB_UP]


# ---------------------------------------------------------------- indicators
def ints(s: str) -> list[float]:
    return [float(x) for x in str(s).replace(" ", "").split(",") if x]


def sma(c, n): return c.rolling(int(n)).mean()
def ema(c, n): return c.ewm(span=int(n), adjust=False).mean()


def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def macd(c, f=12, s=26, sig=9):
    m = ema(c, f) - ema(c, s)
    return m, ema(m, sig)


def vwap(df):
    tp = (df.High + df.Low + df.Close) / 3
    day = df.index.date if hasattr(df.index, "date") else 0
    pv = (tp * df.Volume).groupby(day).cumsum()
    v = df.Volume.groupby(day).cumsum().replace(0, np.nan)
    return pv / v


# ---------------------------------------------------------------- FRED
async def fred(hub, sid: str) -> pd.Series | None:
    """FRED series via keyless CSV; disk-cached 6h. Returns float Series indexed by date."""
    disk = DiskCache("fred")
    raw = disk.get(sid, 6 * 3600)
    if raw is None:
        import httpx
        lim = hub.limiters.setdefault("fred", RateLimiter("fred", 2, 2))
        try:
            async with lim:
                async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
                    r = await c.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}")
                    r.raise_for_status()
            raw = r.text
            disk.put(sid, raw)
        except Exception as e:
            hub.warn(f"FRED {sid}: {type(e).__name__}")
            raw = disk.get(sid)  # stale fallback
            if raw is None:
                return None
    df = pd.read_csv(io.StringIO(raw), na_values=".")
    s = pd.Series(pd.to_numeric(df.iloc[:, 1], errors="coerce").values, index=pd.to_datetime(df.iloc[:, 0]), name=sid)
    return s.dropna()


# ---------------------------------------------------------------- chart view
class ChartView(View):
    """mode: 'px' (OHLCV of self.sec), 'comp' (normalized %), 'series' (FRED ids in self.series)."""
    FUNC = "GP"
    can_focus = True
    mode = "px"
    default_range = "1Y"
    hints = "1-9 range  [ ] intvl  t type  v vol  o log  ←→ xhair  +/- zoom  h/l pan  0 reset  s e b w r m ind  p params  c compare"
    refresh_secs = 30

    def compose(self) -> ComposeResult:
        yield Static("", classes="msg", id="info")
        yield Static("", id="rangebar")
        yield PlotextPlot(id="plot")

    def on_mount(self) -> None:
        c = self.app.cfg
        self.rng = self.default_range
        self.iv = DEF_IV[self.rng]
        self.ctype = c.get("chart", "type", "candle") if self.mode == "px" else "line"
        self.vol = bool(c.get("chart", "volume", True)) and self.mode == "px"
        self.logy = bool(c.get("chart", "log", False))
        self.ind: set[str] = set()
        self.cx: int | None = None
        self.zoom = (0.0, 1.0)
        self.df: pd.DataFrame | None = None
        self.prefetched = False
        self.fit_pending = True       # fit the viewport to native bars on the next data load
        self.series = getattr(self, "series", None) or [a for a in self.args if a not in RANGES]
        self.compare = [a for a in self.args if a not in RANGES] if self.mode == "comp" else []
        for a in self.args:
            if a in RANGES:
                self.rng, self.iv = a, DEF_IV[a]
        self.query_one(PlotextPlot).theme = "clear"
        if self.sec and self.mode == "px":
            self.hub.watch[self.sec] = time.time()       # stream priority (Coinbase / Alpaca IEX)
            self.set_interval(2, self.live_tick)
        super().on_mount()

    def live_tick(self) -> None:
        """Repaint the last bar from a stream without refetching history."""
        live = self.hub.live_fresh(self.sec) or (self.sec in self.hub.iex and iex_eval(self.hub, self.sec, regular_hours()))
        if live and self.df is not None and len(self.df):
            self.hub.watch[self.sec] = time.time()
            self.live_patch()
            self.draw()

    @property
    def title(self) -> str:
        if self.mode == "series":
            return f"{self.FUNC} {' '.join(getattr(self, 'series', None) or self.args)}"
        rng = getattr(self, "rng", self.default_range)
        return f"{self.sec or ''} {self.FUNC} {rng} {getattr(self, 'iv', DEF_IV[rng])}".strip()

    def _fix_iv(self) -> None:
        if self.mode == "series":
            return
        while self.hub.base_for(self.rng, self.iv) is None and self.iv != IVS[-1]:
            self.iv = IVS[IVS.index(self.iv) + 1]

    def render_rangebar(self) -> None:
        """Always-visible selector: digit keys pick the range, [ ] step the interval; current ones highlighted."""
        on = "bold #000000 on #FF5FD2"
        t = Text()
        for i, r in enumerate(RANGES, 1):
            t.append(f" {i}:", style=on if r == self.rng else DIM)
            t.append(f"{r} ", style=on if r == self.rng else "#5FD7FF")
        if self.mode != "series":
            t.append(" │ [ ", style=DIM)
            for iv in IVS:
                ok = self.hub.base_for(self.rng, iv) is not None
                t.append(f" {iv} ", style=on if iv == self.iv else ("#5FD7FF" if ok else "#1f3a46"))
            t.append(" ]", style=DIM)
        self.query_one("#rangebar", Static).update(t)

    async def load(self) -> None:
        self._fix_iv()
        if self.mode == "series":
            ss = [await fred(self.hub, s) for s in self.series]
            ss = [s for s in ss if s is not None]
            if not ss:
                return self.info("N/A — FRED unavailable")
            df = pd.concat(ss, axis=1).ffill()
            start = df.index[-1] - pd.Timedelta(days=RANGE_DAYS[self.rng]) if self.rng != "YTD" else pd.Timestamp(df.index[-1].year, 1, 1)
            self.df = df[df.index >= start]
        elif self.mode == "comp":
            cols = {}
            for s in [self.sec] + self.compare:
                h = await self.hub.bars(s, self.rng, self.iv)
                if h is not None and len(h):
                    cols[s] = h.Close
            if not cols:
                return self.info("N/A")
            df = pd.concat(cols, axis=1).ffill().dropna(how="all")
            self.df = (df / df.bfill().iloc[0] - 1) * 100
        else:
            df = await self.hub.bars(self.sec, self.rng, self.iv)
            if df is None or not len(df):
                return self.info(f"N/A — no history for {self.sec}")
            self.df = df
            self.live_patch()
            if self.fit_pending:
                self.fit_pending = False
                self.fit_view()
        self.draw()
        if not self.prefetched and self.mode != "series" and self.sec:
            # after first paint: download the other bases so every range/interval flip is local and instant
            self.prefetched = True
            self.run_worker(self.hub.prefetch_bars(self.sec), group="prefetch")

    def live_patch(self) -> None:
        """Patch the last bar with the freshest quote (stream or poll)."""
        q = self.hub.last.get(self.sec)
        if q and q.price and self.df is not None and len(self.df):
            i = self.df.index[-1]
            self.df.loc[i, "Close"] = q.price
            self.df.loc[i, "High"] = max(self.df.loc[i, "High"], q.price)
            self.df.loc[i, "Low"] = min(self.df.loc[i, "Low"], q.price)

    def info(self, msg) -> None:
        self.query_one("#info", Static).update(msg if isinstance(msg, Text) else Text(str(msg), style=WARN))

    def chart_cols(self) -> int:
        """Columns available for bars (plot width minus y-axis labels and frame)."""
        return max(10, self.query_one(PlotextPlot).size.width - 12)

    def fit_view(self) -> None:
        """Candles/OHLC are never combined: if the native bars don't fit at candle_cols columns each, show the most
        recent ones that do (pan with h/l for the rest). Lines/areas can always show everything."""
        n = len(self.df) if self.df is not None else 0
        if self.query_one(PlotextPlot).size.width <= 0:   # not laid out yet (cache hit beat layout): retry
            self.call_after_refresh(self._refit)
            return
        self.auto_fit = True
        fit = self.chart_cols() // max(1, int(self.app.cfg.get("chart", "candle_cols", 2)))
        if self.mode == "px" and self.ctype in ("candle", "ohlc") and n > fit:
            self.zoom = (1.0 - fit / n, 1.0)
        else:
            self.zoom = (0.0, 1.0)

    def _refit(self) -> None:
        if self.df is not None:
            self.fit_view()
            self.draw()

    def on_resize(self) -> None:
        if getattr(self, "auto_fit", False) and self.df is not None:
            self.fit_view()
            self.draw()

    def view_df(self) -> pd.DataFrame:
        n = len(self.df)
        lo, hi = int(self.zoom[0] * n), max(int(self.zoom[1] * n), int(self.zoom[0] * n) + 2)
        return self.df.iloc[lo:hi]

    def draw(self) -> None:
        try:
            self._draw()
        except Exception as e:  # fail soft: a bad chart must never take the app down
            self.hub.warn(f"{self.title}: chart error {type(e).__name__}: {e}"[:120])

    def _draw(self) -> None:
        if self.df is None or not len(self.df):
            return
        full, raw = self.df, self.view_df()
        off = full.index.get_loc(raw.index[0]) if len(raw) else 0
        n0 = len(raw)
        plot = self.query_one(PlotextPlot)
        plt = plot.plt
        plt.clear_figure()
        # NEVER combine bars (user rule): every candle is exactly one bar of the selected interval. A terminal
        # fits at most one candle per column, so if the window holds more, keep its most recent `cols` bars;
        # zoom-out is capped at that in on_key, and the user pans (h/l) for the rest.
        cols = self.chart_cols()
        if self.mode == "px" and self.ctype in ("candle", "ohlc") and n0 > cols:
            off += n0 - cols
            raw, n0 = raw.iloc[-cols:], cols
        self.shown, self.total = n0, len(full)
        self.bad_vol = int(raw["BadVol"].sum()) if "BadVol" in raw else 0
        df = raw

        def win(series: pd.Series) -> pd.Series:
            """Indicator values for the visible window."""
            return series.iloc[off:off + n0].reset_index(drop=True)

        lower = []
        if self.mode == "px":
            if self.vol and "Volume" in df and float(df.Volume.fillna(0).max()) > 0:   # indexes often have none
                lower.append("vol")
            lower += [kk for kk in ("r", "m") if kk in self.ind]
        # plotext gotcha: once a figure has subplots, calling a draw method on the *figure* broadcasts it to
        # every panel. Always draw on the panel objects (P = price panel, S = lower panels).
        if lower:
            plt.subplots(1 + len(lower), 1)
            P = plt.subplot(1, 1)
        else:
            P = plt
        n = len(df)
        x = list(range(n))
        step = max(1, n // 6)
        fmt = "%H:%M" if self.iv in ("1m", "5m", "15m") and self.rng == "1D" else "%m-%d %H:%M" if self.iv in ("1m", "5m", "15m", "1h") else "%Y-%m-%d"
        ticks = (x[::step], [d.strftime(fmt) for d in df.index[::step]])
        logy = self.logy and self.mode == "px"
        T = (lambda v: [float(np.log10(a)) if a and a > 0 else 0.0 for a in v]) if logy else list
        yjobs = []  # (panel, positions, labels): applied at the end with one shared label width so panels align
        if self.mode == "px":
            if self.ctype == "candle" and n <= cols // 4:
                # room to spare: draw candles ourselves so bodies widen with the space (plotext's are 1 column).
                # plotext rounds bar edges outward (up to ~+2 columns), so leave a 3-column margin and cap at 60% of the
                # slot: there is always at least one empty column between neighbouring bodies.
                per = cols / max(1, n)
                body_w = min(0.6, max(1, int(per) - 3) / per)
                for i, (o, h, l, c) in enumerate(zip(df.Open, df.High, df.Low, df.Close)):
                    col = RGB_UP if c >= o else RGB_DN
                    P.plot([i, i], T([l, h]), color=col, marker="│")
                    lo_b, hi_b = T([min(o, c), max(o, c)])
                    P.bar([i], [hi_b], minimum=lo_b, width=body_w, color=col, marker="█")
            elif self.ctype == "candle":
                P.candlestick(x, {"Open": T(df.Open), "Close": T(df.Close), "High": T(df.High), "Low": T(df.Low)},
                              colors=[RGB_UP, RGB_DN])
            elif self.ctype == "ohlc":
                for i, (h, l, c, o) in enumerate(zip(df.High, df.Low, df.Close, df.Open)):
                    P.plot([i, i], T([l, h]), color=RGB_UP if c >= o else RGB_DN, marker="│")
                P.scatter(x, T(df.Close), marker="─", color=RGB_FG)
            else:
                P.plot(x, T(df.Close), color=RGB_FG, fillx=self.ctype == "area", marker="braille")
            c_full = full.Close
            cfg = self.app.cfg
            ov = []
            if "s" in self.ind:
                ov += [(f"SMA{int(p)}", sma(c_full, p)) for p in ints(cfg.get("chart", "sma"))]
            if "e" in self.ind:
                ov += [(f"EMA{int(p)}", ema(c_full, p)) for p in ints(cfg.get("chart", "ema"))]
            if "b" in self.ind:
                n_, kb = (ints(cfg.get("chart", "bb")) + [20, 2])[:2]
                m, sd = sma(c_full, n_), c_full.rolling(int(n_)).std()
                ov += [("BB+", m + kb * sd), ("BBm", m), ("BB-", m - kb * sd)]
            if "w" in self.ind and "Volume" in full:
                ov.append(("VWAP", vwap(full)))
            for j, (lab, sr) in enumerate(ov):
                ys = win(sr)
                ok = ~ys.isna()
                if ok.any():
                    P.plot([i for i, b in zip(x, ok) if b], T(ys[ok].clip(lower=1e-9)) if logy else ys[ok].tolist(),
                           color=PALETTE[(j + 1) % len(PALETTE)], label=lab, marker="braille")
            self.iex_overlay(P, df, n, T)
            lo_, hi_ = float(df.Low.min()), float(df.High.max())
            if logy:  # plotext's own yscale("log") crashes at render; plot log10 values and relabel the axis
                yt = list(np.linspace(np.log10(lo_), np.log10(hi_), 6))
                yjobs.append((P, yt, [fnum(10 ** v) for v in yt]))
            else:
                yt = list(np.linspace(lo_, hi_, 6))
                yjobs.append((P, yt, [fnum(v) for v in yt]))
        else:
            for j, col in enumerate(df.columns):
                P.plot(x, df[col].ffill().tolist(), color=PALETTE[j % len(PALETTE)], label=str(col), marker="braille",
                       fillx=self.ctype == "area" and j == 0)
        if self.cx is not None:
            self.cx = max(0, min(n - 1, self.cx))
            P.vline(self.cx, RGB_M)
        # date labels go on the bottom-most panel only
        P.xticks(*ticks) if not lower else P.xticks(ticks[0], [""] * len(ticks[0]))
        sub_h = max(5, plot.size.height // 5)
        for kk, kind in enumerate(lower, start=2):
            S = plt.subplot(kk, 1)
            S.plotsize(None, max(7, plot.size.height // 4) if kind == "vol" else sub_h)
            if kind == "vol":
                up = [(i, v) for i, (v, c, o) in enumerate(zip(df.Volume, df.Close, df.Open)) if c >= o and v > 0]
                dn = [(i, v) for i, (v, c, o) in enumerate(zip(df.Volume, df.Close, df.Open)) if c < o and v > 0]
                for pts, col in ((up, RGB_UP), (dn, RGB_DN)):
                    if pts:
                        S.bar([i for i, _ in pts], [v for _, v in pts], color=col, marker="▌", width=0.6)
                vmax = float(df.Volume.max() or 1)
                S.ylim(0, vmax)
                yjobs.append((S, [0, vmax / 2, vmax], ["0", fbig(vmax / 2), fbig(vmax)]))
            elif kind == "r":
                S.plot(x, win(rsi(full.Close, ints(self.app.cfg.get("chart", "rsi"))[0])).fillna(50).tolist(),
                       color=RGB_Y, label="RSI", marker="braille")
                S.hline(70, RGB_DN)
                S.hline(30, RGB_UP)
                S.ylim(0, 100)
                yjobs.append((S, [30, 50, 70], ["30", "50", "70"]))
            elif kind == "m":
                f, s_, gg = (ints(self.app.cfg.get("chart", "macd")) + [12, 26, 9])[:3]
                m, sg = macd(full.Close, f, s_, gg)
                mw, sw = win(m).fillna(0), win(sg).fillna(0)
                S.plot(x, mw.tolist(), color=RGB_FG, label="MACD", marker="braille")
                S.plot(x, sw.tolist(), color=RGB_Y, label="signal", marker="braille")
                S.hline(0, RGB_DIM)
                lo_, hi_ = float(min(mw.min(), sw.min())), float(max(mw.max(), sw.max()))
                yjobs.append((S, [lo_, (lo_ + hi_) / 2, hi_], [fnum(lo_), fnum((lo_ + hi_) / 2), fnum(hi_)]))
            if self.cx is not None:
                S.vline(self.cx, RGB_M)
            S.xticks(*ticks) if kk == len(lower) + 1 else S.xticks(ticks[0], [""] * len(ticks[0]))
        width = max((len(l) for _, _, labs in yjobs for l in labs), default=0)
        for panel, pos, labs in yjobs:
            panel.yticks(pos, [l.rjust(width) for l in labs])
        for panel in [P] + [plt.subplot(kk, 1) for kk in range(2, len(lower) + 2)]:
            panel.xlim(-0.5, n - 0.5)   # same x-range everywhere (bars pad it otherwise) so ticks line up
        plot.refresh()
        self.update_info(df)
        self.render_rangebar()
        self.retitle()

    def iex_overlay(self, plt, df, n, T=list) -> None:
        """Phase 7 hook: dashed IEX line + marker when hub.iex has a meaningfully different trade."""
        ev = iex_eval(self.hub, self.sec, regular_hours())
        if not ev or not ev["meaningful"]:
            return
        px = T([ev["price"]])[0]
        plt.scatter(list(range(0, n, 3)), [px] * len(range(0, n, 3)), marker="─", color=RGB_Y)
        plt.scatter([n - 1], [px], marker="◆", color=RGB_Y)

    def update_info(self, df) -> None:
        t = Text()
        if self.mode == "px":
            q = self.hub.last.get(self.sec)
            if q:
                t.append(f"{self.sec} {fnum(q.price)} ", style="bold #FFFFFF")
                t.append_text(colored(q.change, f"{fnum(q.change)} ({fnum(q.pct)}%)"))
                t.append(" ")
                t.append_text(tag_text(q))
            ev = iex_eval(self.hub, self.sec, regular_hours())
            if ev and ev["meaningful"]:
                arrow = "▲" if ev["diff"] >= 0 else "▼"
                t.append(f"  IEX-only {fnum(ev['price'])} {arrow}{ev['diff']:+.2f} ({ev['pct']:+.2f}%) | yf delayed "
                         f"{age(ev['yf_stale'])} | IEX trade age {age(ev['iex_age'])}", style=f"bold {WARN}")
            elif ev:
                t.append(f"  IEX ✓ (age {age(ev['iex_age'])})", style=DIM)
            if self.cx is not None:
                r, d = df.iloc[self.cx], df.index[self.cx]
                t.append(f"  │ {d:%Y-%m-%d %H:%M} O {fnum(r.Open)} H {fnum(r.High)} L {fnum(r.Low)} C {fnum(r.Close)} V {fbig(r.get('Volume'))}", style=RGB_HEX_M)
        else:
            i = self.cx if self.cx is not None else len(df) - 1
            t.append(f"{df.index[i]:%Y-%m-%d}  " + "  ".join(f"{c}: {fnum(df[c].iloc[i])}{'%' if self.mode == 'comp' else ''}" for c in df.columns))
        if getattr(self, "shown", 0) < getattr(self, "total", 0):
            t.append(f"  showing last {self.shown}/{self.total} {self.iv} bars · h/l pan · - zoom out", style=WARN) \
                if self.zoom[1] >= 1.0 else t.append(f"  showing {self.shown}/{self.total} bars · h/l pan", style=WARN)
        if getattr(self, "bad_vol", 0):
            t.append(f"  {self.bad_vol} bad Yahoo volume print{'s' if self.bad_vol > 1 else ''} hidden", style=DIM)
        t.append(f"  [{self.ctype}{' log' if self.logy else ''}{' ' + ''.join(sorted(self.ind)) if self.ind else ''}]", style=DIM)
        self.info(t)

    # -- keys
    def on_key(self, e) -> None:
        k = e.character or e.key
        if k and k in "123456789" and len(k) == 1:
            self.rng = RANGES[int(k) - 1]
            self.iv, self.zoom, self.cx, self.fit_pending = DEF_IV[self.rng], (0.0, 1.0), None, True
            self._fix_iv()
            self.render_rangebar()
            self.retitle()
            self.reload()
        elif k in ("[", "]") and self.mode != "series":
            i = max(0, min(len(IVS) - 1, IVS.index(self.iv) + (1 if k == "]" else -1)))
            if self.hub.base_for(self.rng, IVS[i]) is not None:
                self.iv, self.zoom, self.cx, self.fit_pending = IVS[i], (0.0, 1.0), None, True
                self.render_rangebar()
                self.retitle()
                self.reload()
            else:
                self.hub.warn(f"{IVS[i]} bars not available for {self.rng} (free intraday history is limited)")
        elif k == "t":
            self.ctype = TYPES[(TYPES.index(self.ctype) + 1) % len(TYPES)] if self.mode == "px" else ("area" if self.ctype != "area" else "line")
            self.fit_view()
            self.draw()
        elif k == "v":
            self.vol = not self.vol
            self.draw()
        elif k == "o":
            self.logy = not self.logy
            self.draw()
            self.draw()
        elif k in ("left", "right"):
            n = len(self.view_df()) if self.df is not None else 0
            self.cx = (n - 1 if self.cx is None else self.cx + (1 if k == "right" else -1))
            self.draw()
        elif k == "x":
            self.cx = None
            self.draw()
        elif k in ("+", "-", "=") and self.df is not None and len(self.df):
            a, b = self.zoom
            self.auto_fit = False
            n = len(self.df)
            w = (b - a) * (0.6 if k in "+=" else 1.6)
            w_max = 1.0
            if self.mode == "px" and self.ctype in ("candle", "ohlc"):
                w_max = min(1.0, self.chart_cols() / n)        # one native candle per column, never combined
            if k == "-" and (b - a) >= w_max - 1e-9 and w_max < 1.0:
                self.hub.warn(f"zoomed out as far as native {self.iv} candles allow ({self.chart_cols()} fit); "
                              f"h/l to pan, or ] for a coarser interval")
            w = min(w_max, max(min(1.0, 10 / n), w))
            a = 1.0 - w if b >= 1.0 else max(0.0, min(1.0 - w, (a + b) / 2 - w / 2))   # keep the latest bar pinned
            self.zoom = (a, a + w)
            self.draw()
        elif k in ("h", "l"):
            self.auto_fit = False
            a, b = self.zoom
            d = (b - a) * 0.2 * (1 if k == "l" else -1)
            d = max(-a, min(1 - b, d))
            self.zoom = (a + d, b + d)
            self.draw()
        elif k == "0":
            self.cx = None
            self.fit_view()
            self.draw()
        elif k in ("s", "e", "b", "w", "r", "m") and self.mode == "px":
            self.ind ^= {k}
            self.draw()
        elif k == "p":
            c = self.app.cfg
            cur = " ".join(f"{n}={c.get('chart', n)}" for n in ("sma", "ema", "bb", "rsi", "macd"))
            self.app.push_screen(PromptScreen("Indicator params (name=a,b ...):", cur), self._set_params)
        elif k == "c" and self.mode in ("px", "comp"):
            self.app.push_screen(PromptScreen("Compare tickers (space separated):"), self._set_compare)
        elif k == "i":
            self.app.run_command("/IEX")
        else:
            return
        e.stop()

    def _set_params(self, v: str) -> None:
        for kv in (v or "").split():
            if "=" in kv:
                n, val = kv.split("=", 1)
                if n.lower() in ("sma", "ema", "bb", "rsi", "macd"):
                    self.app.cfg.set("chart", n.lower(), val)
        self.draw()

    def _set_compare(self, v: str) -> None:
        if v:
            al = self.app.aliases
            self.compare = [al.get(s, s) for s in v.upper().split()]
            self.mode, self.vol = "comp", False
            self.reload()


RGB_HEX_M = "#FF5FD2"

# plotext builds the figure lazily inside PlotextPlot.render(), outside our try/except in draw().
# Wrap it so a plotext error renders as a message + status warning instead of crashing the app.
_plot_render = PlotextPlot.render


def _safe_render(self):
    try:
        return _plot_render(self)
    except Exception as e:
        try:
            self.app.hub.warn(f"chart render {type(e).__name__}: {e}"[:120])
        except Exception:
            pass
        return Text(f"chart unavailable ({type(e).__name__})", style=WARN)


PlotextPlot.render = _safe_render


class GIPView(ChartView):
    FUNC = "GIP"
    default_range = "1D"
    refresh_secs = 15


class CompView(ChartView):
    FUNC = "COMP"
    mode = "comp"

    def on_mount(self) -> None:
        if not self.sec and self.args:
            self.sec, self.args = self.args[0], self.args[1:]
        self.args = [self.app.aliases.get(a, a) for a in self.args]
        super().on_mount()


class FredView(ChartView):
    FUNC = "FRED"
    mode = "series"
    default_range = "5Y"
    hints = "1-9 range  t line/area  ←→ xhair  +/- zoom  h/l pan  0 reset"


    async def load(self) -> None:
        if not self.series:
            return self.info("Usage: /FRED <SERIES>  e.g. /FRED DGS10 · /FRED CPIAUCSL · /FRED UNRATE")
        await super().load()


class SprdView(FredView):
    FUNC = "SPRD"
    series = ["BAMLC0A0CM", "BAMLH0A0HYM2", "T10Y2Y"]
    default_range = "1Y"
    hints = FredView.hints + "   (ICE BofA OAS: FRED only carries ~3y)"


class QuoteChartView(ChartView):
    """Default screen for a bare security: quote header + chart."""
    FUNC = "Q"
    refresh_secs = 15

    async def load(self) -> None:
        qs = await self.hub.quotes([self.sec])
        if self.sec not in qs:
            import difflib
            from .fundamentals import edgar_tickers
            pool = list(self.app.aliases) + list(await edgar_tickers(self.hub))
            sug = difflib.get_close_matches(self.sec, pool, n=4, cutoff=0.6)
            self.info(f"N/A — no data for {self.sec}." + (f" Did you mean: {', '.join(sug)}?" if sug else "")
                      + f"  Try /? {self.sec.lower()} to search.")
            return
        await super().load()


VIEWS.update({"GP": ChartView, "GIP": GIPView, "COMP": CompView, "FRED": FredView, "SPRD": SprdView, "Q": QuoteChartView})
