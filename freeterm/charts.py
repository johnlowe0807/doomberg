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

from .app import DIM, UP, DOWN, WARN, VIEWS, View, fbig, fnum, colored, tag_text
from .app import regular_hours
from .data import IVS, RANGES, is_range, range_days, range_span_days, slice_range, candle_interval, step_interval
from .live import age, iex_eval
from .pixelcharts import ChartSurface, GraphicsFailed, PixelSizeChanged, Panel, Scene, Trace, color_dual_axes, render_stacked_text
from .options import contract, name as option_name
from . import comparison

PERIOD = {"1D": "1d", "5D": "5d", "1M": "1mo", "3M": "3mo", "6M": "6mo", "YTD": "ytd", "1Y": "1y", "5Y": "5y", "MAX": "max"}
DEF_IV = {"1D": "5m", "5D": "15m", "1M": "1h", "3M": "1d", "6M": "1d", "YTD": "1d", "1Y": "1d", "5Y": "1wk", "MAX": "1wk"}
TYPES = ["candle", "ohlc", "line", "area"]
RGB_UP, RGB_DN, RGB_FG, RGB_Y, RGB_M = (0, 255, 135), (255, 77, 109), (95, 215, 255), (255, 215, 0), (255, 95, 210)
RGB_DIM = (58, 107, 128)
PALETTE = [RGB_FG, RGB_Y, RGB_M, (180, 140, 255), (255, 160, 60), RGB_UP, (160, 220, 255), (0, 190, 190)]


def default_interval(rng):
    if rng in DEF_IV:
        return DEF_IV[rng]
    days = range_span_days(rng)
    return "5m" if days <= 1 else "15m" if days <= 5 else "1h" if days <= 31 else "1d" if days <= 732 else "1wk"


# ---------------------------------------------------------------- indicators
from .analytics import ints, sma, ema, rsi, macd, vwap


# ---------------------------------------------------------------- FRED
async def fred(hub, sid: str) -> pd.Series | None:
    from .query_models import SeriesQuery
    return hub.value(await hub.query(SeriesQuery(sid)))


# ---------------------------------------------------------------- chart view
class ChartView(View):
    """mode: 'px' (OHLCV of self.sec), 'comp' (normalized %), 'series' (FRED ids in self.series)."""
    FUNC = "GP"
    can_focus = True
    mode = "px"
    default_range = "1Y"
    graph_commands_enabled = True
    prefetch_enabled = True
    hints = ". graph commands  1-9 range  [ ] intvl  t type  o log  ←→ xhair  +/- zoom  h/l pan  0 reset"
    refresh_secs = 30

    def compose(self) -> ComposeResult:
        yield Static("", classes="msg", id="info")
        yield Static("", id="rangebar")
        yield ChartSurface(self.app.cfg.get("chart", "renderer", "auto"), id="chart")

    def on_mount(self) -> None:
        c = self.app.cfg
        self.rng = self.default_range
        self.iv = getattr(self, "default_iv", DEF_IV[self.rng])
        self.ctype = c.get("chart", "type", "candle") if self.mode == "px" else "line"
        self.vol = bool(c.get("chart", "volume", True)) and self.mode == "px"
        self.logy = bool(c.get("chart", "log", False))
        self.ind: set[str] = set()
        self.parameters = {name: str(c.get("chart", name)) for name in ("sma", "ema", "bb", "rsi", "macd")}
        self.default_parameters = self.parameters.copy()
        self.command_history = []
        self.compare_refs = getattr(self, "initial_refs", [])
        self.compare_mode = getattr(self, "initial_compare_mode", "NORMALIZED")
        self.comparison_missing = []
        self.raw_comparison = None
        self.original_security = self.sec
        self.price_type, self.price_volume = c.get("chart", "type", "candle"), bool(c.get("chart", "volume", True))
        self.pending_gotos = []
        self.cx: int | None = None
        self.zoom = (0.0, 1.0)
        self.df: pd.DataFrame | None = None
        self.prefetched = False
        self.fit_pending = True       # fit the viewport to native bars on the next data load
        self.series = getattr(self, "series", None) or [a for a in self.args if not is_range(a)]
        self.compare = [a for a in self.args if not is_range(a)] if self.mode == "comp" else []
        for a in self.args:
            if is_range(a):
                self.rng, self.iv = a, default_interval(a)
        self.restore_state = (self.mode, self.sec, list(self.series), self.ctype, self.vol)
        self.restore_ref = ''
        if self.mode == 'comp':
            first = self.compare_refs[0]
            self.restore_ref = first
            self.restore_state = ('series', None, [first[5:]], 'line', False) if first.startswith('FRED:') else \
                ('series', None, [], 'line', False) if first.startswith('ERN:') else ('px', first, [], self.price_type, self.price_volume)
        self.query_one(PlotextPlot).theme = "clear"
        if self.sec and self.mode != "series":
            self.hub.watch[self.sec] = time.time()       # stream priority (Coinbase / Alpaca IEX)
            self.set_interval(2, self.live_tick)
        super().on_mount()

    def live_tick(self) -> None:
        """Repaint the last bar from a stream without refetching history."""
        if self.mode != "px":
            return
        live = self.hub.live_fresh(self.sec) or (self.sec in self.hub.iex and iex_eval(self.hub, self.sec, regular_hours()))
        if live and self.df is not None and len(self.df):
            self.hub.watch[self.sec] = time.time()
            self.live_patch()
            self.draw()

    @property
    def title(self) -> str:
        if self.mode == 'comp' and getattr(self, 'compare_refs', None):
            return f"COMP {' '.join(self.compare_refs)} {self.compare_mode} {getattr(self, 'rng', self.default_range)}"
        if self.mode == "series":
            return f"{self.FUNC} {' '.join(getattr(self, 'series', None) or self.args)}"
        rng = getattr(self, "rng", self.default_range)
        function = "COMP" if self.mode == "comp" else "GP" if self.FUNC == "COMP" else self.FUNC
        return f"{self.sec or ''} {function} {rng} {getattr(self, 'iv', default_interval(rng))}".strip()

    @property
    def uses_price_intervals(self):
        return self.mode == 'px' or (self.mode == 'comp' and bool(comparison.securities(self.compare_refs)))

    def _fix_iv(self) -> None:
        if not self.uses_price_intervals:
            return
        if self.hub.base_for(self.rng, self.iv) is None:
            self.iv = next((iv for iv in IVS if self.hub.base_for(self.rng, iv) is not None), default_interval(self.rng))

    def render_rangebar(self) -> None:
        """Always-visible selector: digit keys pick the range, [ ] step the interval; current ones highlighted."""
        if self.graph_commands_enabled:
            self.hints = '. graph commands  ' + ('MODE scaling  ' if self.mode == 'comp' else '') + '1-9 range  '
            self.hints += '[ ] intvl  ' if self.uses_price_intervals else ''
            self.hints += ('t type  o log  ' if self.mode == 'px' else 't line/area  ') + '←→ xhair  Ctrl+←→ half jump  GOTO date/±n  +/- zoom  h/l pan  0 reset'
            self.app.update_hints()
        on = "bold #000000 on #FF5FD2"
        t = Text()
        for i, r in enumerate(RANGES, 1):
            t.append(f" {i}:", style=on if r == self.rng else DIM)
            t.append(f"{r} ", style=on if r == self.rng else "#5FD7FF")
        if self.rng not in RANGES:
            t.append(f" {self.rng} ", style=on)
        if self.uses_price_intervals:
            t.append(" │ [ ", style=DIM)
            for iv in IVS:
                ok = self.hub.base_for(self.rng, iv) is not None
                t.append(f" {iv} ", style=on if iv == self.iv else ("#5FD7FF" if ok else "#1f3a46"))
            if self.iv not in IVS:
                t.append(f" {self.iv} ", style=on)
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
            self.df = slice_range(df, self.rng)
        elif self.mode == "comp":
            from .query_models import ComparisonQuery
            try:
                result = await self.hub.query(ComparisonQuery(tuple(self.compare_refs), self.rng, self.iv, self.compare_mode))
                data = result.data
                if data is None:
                    self.df = None
                    return self.info(result.meta.reason or 'N/A — no comparison data')
                self.comparison_missing = data['missing']
                if self.comparison_missing:
                    self.hub.warn('Comparison unavailable: ' + ', '.join(self.comparison_missing))
                self.raw_comparison, self.df = data['raw'], data['values']
            except ValueError as error:
                self.df = None
                return self.info(str(error))
        else:
            df = await self.hub.bars(self.sec, self.rng, self.iv)
            if df is None or not len(df):
                if contract(self.sec):
                    quote = self.hub.last.get(self.sec)
                    return self.info(f"{option_name(self.sec)} · last {fnum(quote.price) if quote else 'N/A'} · "
                                     f"Yahoo history unavailable for {self.rng} / {self.iv}")
                return self.info(f"N/A — no history for {self.sec}")
            self.df = df
            self.live_patch()
            if self.fit_pending:
                self.fit_pending = False
                self.fit_view()
        pending, self.pending_gotos = self.pending_gotos, []
        for target in pending:
            try:
                self.goto(target, redraw=False)
            except ValueError as error:
                self.hub.warn(str(error))
                break
        self.draw()
        if self.prefetch_enabled and not self.prefetched and self.mode != "series" and self.sec and not contract(self.sec):
            # after first paint: download the other bases so every range/interval flip is local and instant
            self.prefetched = True
            self.run_worker(self.hub.prefetch_bars(self.sec), group="prefetch")

    def live_patch(self) -> None:
        """Patch the last bar with the freshest quote (stream or poll)."""
        if self.rng.startswith("TF:"):
            return
        q = self.hub.last.get(self.sec)
        if q and q.price and self.df is not None and len(self.df):
            i = self.df.index[-1]
            self.df.loc[i, "Close"] = q.price
            self.df.loc[i, "High"] = max(self.df.loc[i, "High"], q.price)
            self.df.loc[i, "Low"] = min(self.df.loc[i, "Low"], q.price)

    def info(self, msg) -> None:
        if self.df is None:
            self.query_one(ChartSurface).submit(Scene([], []))
        self.query_one("#info", Static).update(msg if isinstance(msg, Text) else Text(str(msg), style=WARN))

    def chart_font(self) -> int:
        return max(8, min(32, int(self.app.cfg.get("chart", "font_px", 12))))

    def execute_graph_command(self, line):
        from .graph_commands import execute
        execute(self, line)

    def open_graph_commands(self):
        from .graph_commands import GraphCommandScreen
        self.app.push_screen(GraphCommandScreen(self))

    def fit_view(self) -> None:
        """Fit the complete selected range; geometry scales native candles to the viewport."""
        if self.query_one(ChartSurface).size.width <= 0:   # not laid out yet (cache hit beat layout): retry
            self.call_after_refresh(self._refit)
            return
        self.auto_fit = True
        self.zoom = (0.0, 1.0)

    def _refit(self) -> None:
        if self.is_mounted and self.query(ChartSurface) and self.df is not None:
            self.fit_view()
            self.draw()

    def on_resize(self) -> None:
        if getattr(self, "df", None) is not None:
            self.call_after_refresh(self._resize_chart)

    def _resize_chart(self) -> None:
        if not self.is_mounted or not self.query(ChartSurface):
            return
        if getattr(self, "auto_fit", False):
            self.fit_view()
        self.draw()

    def on_pixel_size_changed(self, event: PixelSizeChanged) -> None:
        event.stop()
        if getattr(self, "df", None) is not None:
            self.call_after_refresh(self._resize_chart)

    def on_graphics_failed(self, event: GraphicsFailed) -> None:
        event.stop()
        surface = self.query_one(ChartSurface)
        if surface.pixels:
            surface.use_text()
            self.hub.warn(f"pixel chart unavailable; using text: {event.reason}"[:120])
            self.call_after_refresh(self._refit)

    def view_bounds(self) -> tuple[int, int]:
        n = len(self.df) if self.df is not None else 0
        if not n:
            return 0, 0
        lo = max(0, min(n - 1, int(self.zoom[0] * n + 1e-9)))
        hi = min(n, max(lo + 2, int(self.zoom[1] * n + 1e-9)))
        return lo, hi

    def view_df(self) -> pd.DataFrame:
        lo, hi = self.view_bounds()
        return self.df.iloc[lo:hi]

    def select_point(self, position: int) -> None:
        n = len(self.df)
        position = max(0, min(n - 1, position))
        lo, hi = self.view_bounds()
        if not lo <= position < hi:
            count = hi - lo
            lo = max(0, min(n - count, position - count // 2))
            self.zoom = (lo / n, (lo + count) / n)
            self.auto_fit = False
        self.cx = position - lo

    def goto(self, target, *, redraw=True) -> None:
        if self.df is None:
            self.pending_gotos.append(target)
            return
        if not len(self.df):
            raise ValueError('GOTO needs loaded datapoints')
        from .chart_navigation import locate
        lo, hi = self.view_bounds()
        selected = lo + self.cx if self.cx is not None else hi - 1
        position = selected + target.offset if target.offset is not None else locate(
            self.df.index, target.timestamp, self.iv, self.mode == 'px')
        self.select_point(position)
        if redraw:
            self.draw()

    def move_crosshair(self, direction: int, *, half=False) -> None:
        lo, hi = self.view_bounds()
        count = hi - lo
        if not count:
            return
        current = max(0, min(count - 1, self.cx if self.cx is not None else count - 1))
        if half:
            edge = 0 if direction < 0 else count - 1
            current += direction * ((abs(edge - current) + 1) // 2)
        elif self.cx is not None:
            current += direction
        self.cx = max(0, min(count - 1, current))
        self.draw()

    def draw(self) -> None:
        if self.df is None or self.df.empty:
            return
        if self.mode == 'px' and self.ind:
            signature = (id(self.df), tuple(sorted(self.ind)), tuple(sorted(self.parameters.items())),
                         tuple(str(v) for v in self.df.iloc[-1]))
            if signature != getattr(self, '_indicator_signature', None):
                self.run_worker(self._prepare_indicators(signature), exclusive=True, group='indicators')
                return
        try:
            self._draw_scene()
        except Exception as error:
            self.hub.warn(f'{self.title}: chart error {type(error).__name__}: {error}'[:120])

    async def _prepare_indicators(self, signature):
        from .analytics import indicators
        frame, active, parameters = self.df.copy(), set(self.ind), dict(self.parameters)
        try:
            result = await self.hub.derive('indicators', {'active': sorted(active), 'parameters': parameters}, [frame],
                                           lambda: indicators(frame, active, parameters), 'technical-indicators-v1')
            current = (id(self.df), tuple(sorted(self.ind)), tuple(sorted(self.parameters.items())),
                       tuple(str(v) for v in self.df.iloc[-1])) if self.df is not None and len(self.df) else None
            if self.is_mounted and self.mode == 'px' and current == signature:
                self._indicators, self._indicator_signature = result, signature
                self.draw()
        except Exception as error:
            self.hub.warn(f'{self.title}: indicators {type(error).__name__}: {error}'[:120])

    def _draw_scene(self) -> None:
        if self.df is None or not len(self.df):
            return
        full, raw = self.df, self.view_df()
        off = full.index.get_loc(raw.index[0])
        n = len(raw)
        self.shown, self.total = n, len(full)
        self.bad_vol = int(raw["BadVol"].sum()) if "BadVol" in raw else 0
        if self.cx is not None:
            self.cx = max(0, min(n - 1, self.cx))

        def win(series):
            return series.iloc[off:off + n].to_numpy(dtype=float, copy=True)

        main = Panel("Price" if self.mode == "px" else 'Compare ' + self.compare_mode if self.mode == "comp" else "FRED",
                     log=self.logy and self.mode == "px")
        panels = [main]
        candles, iex = None, None
        if self.mode == "px":
            if self.ctype in ("candle", "ohlc"):
                candles = raw[["Open", "High", "Low", "Close"]].to_numpy(dtype=float, copy=True)
            else:
                main.traces.append(Trace("Close", raw.Close.to_numpy(dtype=float, copy=True), RGB_FG))
            features = getattr(self, '_indicators', {}) if self.ind else {}
            overlays = features.get('overlays', [])
            main.traces += [Trace(label, win(series), PALETTE[(j + 1) % len(PALETTE)])
                            for j, (label, series) in enumerate(overlays)]
            ev = None if self.rng.startswith("TF:") else iex_eval(self.hub, self.sec, regular_hours())
            if ev and ev["meaningful"]:
                iex = ev["price"]
            if self.vol and "Volume" in raw and float(raw.Volume.fillna(0).max()) > 0:
                values = raw.Volume.to_numpy(dtype=float, copy=True)
                values[values < 0] = np.nan
                panels.append(Panel("Volume", bars=values,
                                    bar_colors=[RGB_UP if c >= o else RGB_DN for c, o in zip(raw.Close, raw.Open)],
                                    limits=(0, max(1., float(raw.Volume.max())))))
            if "r" in self.ind:
                panels.append(Panel("RSI", [Trace("RSI", win(features['rsi']), RGB_Y)],
                                    limits=(0, 100), guides=[(30, "#00FF87"), (70, "#FF4D6D")]))
            if "m" in self.ind:
                m, sg = features['macd']
                hist = win(m - sg)
                panels.append(Panel("MACD", [Trace("MACD", win(m), RGB_FG), Trace("Signal", win(sg), RGB_Y)],
                                    bars=hist, bar_colors=[RGB_UP if v >= 0 else RGB_DN for v in hist],
                                    guides=[(0, "#3A6B80")]))
        else:
            main.independent = self.mode == 'comp' and self.compare_mode == 'STACKED'
            main.traces = [Trace(f'[{j + 1}] {col}' if main.independent else str(col), raw[col].to_numpy(dtype=float, copy=True), PALETTE[j % len(PALETTE)])
                           for j, col in enumerate(raw.columns)]
        fmt = "%H:%M" if candle_interval(self.iv).minutes < 1440 and self.rng == "1D" else \
            "%m-%d %H:%M" if candle_interval(self.iv).minutes < 1440 else "%Y-%m-%d"
        scene = Scene(panels, [d.strftime(fmt) for d in raw.index], candles, self.ctype, self.cx, iex, self.chart_font())
        self.query_one(ChartSurface).submit(scene)
        self.update_info(raw)
        self.render_rangebar()
        self.retitle()

    def update_info(self, df) -> None:
        t = Text()
        if self.mode == "px":
            if contract(self.sec):
                t.append(f"{option_name(self.sec)}  ", style=DIM)
            q = self.hub.last.get(self.sec)
            closes = self.df.Close.replace([np.inf, -np.inf], np.nan).dropna()
            if len(closes):
                first, last = float(closes.iloc[0]), float(closes.iloc[-1])
                change = last - first
                pct = 100 * change / first if first else None
                t.append(f"{self.sec} {fnum(last)} ", style="bold #FFFFFF")
                t.append_text(colored(change, f"{fnum(change)} ({fnum(pct)}%)"))
                t.append(f" {self.rng} ", style=DIM)
                t.append_text(tag_text(q) if q else Text("DLY", style=DIM))
            ev = None if self.rng.startswith("TF:") else iex_eval(self.hub, self.sec, regular_hours())
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
            suffix = '%' if self.mode == 'comp' and self.compare_mode == 'NORMALIZED' else ''
            t.append(f"{df.index[i]:%Y-%m-%d %H:%M}  ")
            for column in df.columns:
                values = df[column].dropna()
                value = df[column].iloc[i]
                date = df.index[i]
                if self.cx is None and len(values):
                    value, date = values.iloc[-1], values.index[-1]
                t.append(f"{column}: {fnum(None if not np.isfinite(value) else value)}{suffix}")
                if self.cx is None and date != df.index[i]:
                    t.append(f" (as of {date:%Y-%m-%d})", style=DIM)
                t.append('  ')
            if self.mode == 'comp':
                t.append(f'  {self.compare_mode}', style=DIM)
                if self.compare_mode == 'NORMALIZED':
                    t.append(f' · baseline {self.df.index[0]:%Y-%m-%d}', style=DIM)
                if self.comparison_missing:
                    t.append(' · unavailable: ' + ', '.join(self.comparison_missing), style=WARN)
        if getattr(self, "shown", 0) < getattr(self, "total", 0):
            t.append(f"  showing {self.shown}/{self.total} {self.iv} bars · h/l pan · - zoom out", style=WARN)
        if getattr(self, "bad_vol", 0):
            t.append(f"  {self.bad_vol} bad Yahoo volume print{'s' if self.bad_vol > 1 else ''} hidden", style=DIM)
        backend = self.query_one(ChartSurface).backend
        active_indicators = ''.join(sorted(self.ind)) if self.mode == "px" else ''
        t.append(f"  [{self.ctype}{' log' if self.logy else ''}{' ' + active_indicators if active_indicators else ''} · {backend}]", style=DIM)
        self.info(t)

    # -- keys
    def on_key(self, e) -> None:
        if not self.graph_commands_enabled:
            return
        k = e.character or e.key
        if k == ".":
            self.open_graph_commands()
        elif k and k in "123456789" and len(k) == 1:
            self.pending_gotos.clear()
            self.rng = RANGES[int(k) - 1]
            self.iv, self.zoom, self.cx, self.fit_pending = DEF_IV[self.rng], (0.0, 1.0), None, True
            self._fix_iv()
            self.render_rangebar()
            self.retitle()
            self.reload()
        elif k in ("[", "]") and self.uses_price_intervals:
            interval = step_interval(self.iv, 1 if k == "]" else -1)
            if self.hub.base_for(self.rng, interval) is not None:
                self.pending_gotos.clear()
                self.iv, self.zoom, self.cx, self.fit_pending = interval, (0.0, 1.0), None, True
                self.render_rangebar()
                self.retitle()
                self.reload()
            else:
                self.hub.warn(f"{interval} bars not available for {self.rng} (free intraday history is limited)")
        elif k == "t":
            self.ctype = TYPES[(TYPES.index(self.ctype) + 1) % len(TYPES)] if self.mode == "px" else ("area" if self.ctype != "area" else "line")
            self.fit_view()
            self.draw()
        elif k == "o" and self.mode == "px":
            self.logy = not self.logy
            self.draw()
        elif k in ("left", "right", "ctrl+left", "ctrl+right"):
            self.move_crosshair(1 if k.endswith("right") else -1, half=k.startswith("ctrl+"))
        elif k == "x":
            self.pending_gotos.clear()
            self.cx = None
            self.draw()
        elif k in ("+", "-", "=") and self.df is not None and len(self.df):
            a, b = self.zoom
            self.auto_fit = False
            n = len(self.df)
            selected = int(a * n) + self.cx if self.cx is not None else None
            w = (b - a) * (0.6 if k in "+=" else 1.6)
            w = min(1.0, max(min(1.0, 10 / n), w))
            if selected is not None:
                # Anchor the new viewport on the inspected native candle, then translate its index.
                a = max(0., min(1. - w, (selected + .5) / n - w / 2))
            else:
                a = 1.0 - w if b >= 1.0 else max(0.0, min(1.0 - w, (a + b) / 2 - w / 2))
            self.zoom = (a, a + w)
            if selected is not None:
                self.cx = selected - int(a * n)
            self.draw()
        elif k in ("h", "l"):
            self.auto_fit = False
            a, b = self.zoom
            d = (b - a) * 0.2 * (1 if k == "l" else -1)
            d = max(-a, min(1 - b, d))
            self.zoom = (a + d, b + d)
            self.draw()
        elif k == "0":
            self.pending_gotos.clear()
            self.cx = None
            self.fit_view()
            self.draw()
        else:
            return
        e.stop()

RGB_HEX_M = "#FF5FD2"

# plotext builds the figure lazily inside PlotextPlot.render(), outside our try/except in draw().
# Wrap it so a plotext error renders as a message + status warning instead of crashing the app.
_plot_render = PlotextPlot.render


def _safe_render(self):
    try:
        scene = getattr(self.parent, 'scene', None)
        if scene is not None and scene.panels and scene.panels[0].independent:
            return render_stacked_text(self, scene)
        return color_dual_axes(_plot_render(self), getattr(self.parent, 'scene', None))
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

    def __init__(self, hub, sec, args):
        refs, selected_mode = comparison.parse(([sec] if sec else []) + args, {})
        prices = comparison.securities(refs)
        super().__init__(hub, prices[0] if prices else None, [arg for arg in args if is_range(arg)])
        self.initial_refs, self.initial_compare_mode = refs, selected_mode


class FredView(ChartView):
    FUNC = "FRED"
    mode = "series"
    default_range = "5Y"
    hints = ". graph commands  1-9 range  t line/area  ←→ xhair  +/- zoom  h/l pan  0 reset  /FRED? search"


    async def load(self) -> None:
        if self.mode == 'series' and not self.series:
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
        if self.mode != 'px':
            return await super().load()
        qs = await self.hub.quotes([self.sec])
        if self.sec not in qs:
            if contract(self.sec):
                self.info(f"{option_name(self.sec)} — no Yahoo quote available")
                return
            import difflib
            from .fundamentals import edgar_tickers
            pool = list(self.app.aliases) + list(await edgar_tickers(self.hub))
            sug = difflib.get_close_matches(self.sec, pool, n=4, cutoff=0.6)
            self.info(f"N/A — no data for {self.sec}." + (f" Did you mean: {', '.join(sug)}?" if sug else "")
                      + f"  Try /? {self.sec.lower()} to search.")
            return
        await super().load()


class HomePreview(QuoteChartView):
    """Fixed one-session, five-minute preview; open the security for chart controls."""
    default_range = "1D"
    default_iv = "5m"
    graph_commands_enabled = False
    prefetch_enabled = False
    can_focus = False
    hints = ""

    def on_mount(self):
        self.args = []
        super().on_mount()
        self.rng, self.iv = "1D", "5m"
        self.query_one("#rangebar").display = False

    def on_key(self, event):
        pass

    def execute_graph_command(self, line):
        raise ValueError("HOME preview is fixed at 1D / 5m; open the security for chart controls")


VIEWS.update({"GP": ChartView, "GIP": GIPView, "COMP": CompView, "FRED": FredView, "SPRD": SprdView, "Q": QuoteChartView})
