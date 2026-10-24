"""Selected-range security statistics and available order-book snapshots."""
from __future__ import annotations

import numpy as np
import pandas as pd
from textual.app import ComposeResult
from textual.widgets import DataTable, Static

from .app import VIEWS, View, fbig, fnum
from .charts import DEF_IV, default_interval
from .data import IVS, RANGES, is_range, is_interval, candle_interval, step_interval
from .options import contract, number, snapshot


def statistics(frame, interval):
    data = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=["Close"])
    if data.empty:
        return []
    first, last = data.iloc[0], data.iloc[-1]
    change = float(last.Close - first.Close)
    percent = 100 * change / float(first.Close) if first.Close else None
    volume = data.get("Volume", pd.Series(np.nan, index=data.index))
    fmt = lambda value: fnum(number(value))
    rows = [("Bars", str(len(data))), ("First close", fmt(first.Close)), ("Last close", fmt(last.Close)),
            ("Change over range", fnum(change)), ("Return over range", fnum(percent) + "%"),
            ("Opening price", fmt(first.Open)), ("High over range", fmt(data.High.max())),
            ("Low over range", fmt(data.Low.min())), ("Average close", fmt(data.Close.mean())),
            ("Median close", fmt(data.Close.median())), ("Total volume", fbig(number(volume.sum(min_count=1)))),
            (f"Average volume / {interval} bar", fbig(number(volume.mean()))),
            (f"Median volume / {interval} bar", fbig(number(volume.median()))),
            (f"Maximum volume / {interval} bar", fbig(number(volume.max()))),
            ("Average bar range", fmt((data.High - data.Low).mean())),
            ("Return volatility / bar", fmt(data.Close.pct_change(fill_method=None).std() * 100) + "%"),
            ("Up bars (close > open)", str(int((data.Close > data.Open).sum()))),
            ("Down bars (close < open)", str(int((data.Close < data.Open).sum())))]
    if interval != "1wk":
        sessions = volume.groupby(volume.index.date).sum(min_count=1)
        rows.append(("Average volume / observed day", fbig(number(sessions.mean()))))
    bad = int(data.BadVol.fillna(False).sum()) if "BadVol" in data else 0
    if bad:
        rows.append(("Bad Yahoo volume prints excluded", str(bad)))
    return rows


class DatView(View):
    FUNC = "DAT"
    refresh_secs = 30
    hints = "1-9 range  [ ] interval  statistics use only the selected range  Ctrl+R refresh"

    def compose(self) -> ComposeResult:
        yield Static("", classes="msg", id="dat-info")
        yield DataTable(cursor_type="row")

    def on_mount(self):
        range_index = next((index for index, arg in enumerate(self.args) if is_range(arg)), None)
        self.rng = self.args[range_index] if range_index is not None else "1Y"
        self.iv = next((candle_interval(arg).value for index, arg in enumerate(self.args)
                        if index != range_index and is_interval(arg)), default_interval(self.rng))
        if self.hub.base_for(self.rng, self.iv) is None:
            self.iv = default_interval(self.rng)
        super().on_mount()

    @property
    def title(self):
        return f"{self.sec} DAT {getattr(self, 'rng', '1Y')} {getattr(self, 'iv', '1d')}"

    async def load(self):
        table = self.query_one(DataTable)
        table.clear(columns=True)
        table.add_columns("Metric", "Selected range")
        frame = await self.hub.bars(self.sec, self.rng, self.iv)
        header = self.query_one("#dat-info", Static)
        if frame is None or frame.empty:
            header.update(f"N/A — no {self.iv} history for {self.sec} over {self.rng}")
            return
        for label, value in statistics(frame, self.iv):
            table.add_row(label, value)
        header.update(f"{self.sec} · {self.rng} / {self.iv} · {frame.index[0]:%Y-%m-%d %H:%M} to "
                      f"{frame.index[-1]:%Y-%m-%d %H:%M} · Yahoo DLY · volume in provider units")

    def on_key(self, event):
        key = event.character or event.key
        if key and len(key) == 1 and key in "123456789":
            self.rng = RANGES[int(key) - 1]
            self.iv = DEF_IV[self.rng]
        elif key in ("[", "]"):
            interval = step_interval(self.iv, 1 if key == "]" else -1)
            if self.hub.base_for(self.rng, interval) is None:
                self.hub.warn(f"{interval} history unavailable for {self.rng}")
                event.stop()
                return
            self.iv = interval
        else:
            return
        event.stop()
        self.retitle()
        self.reload()


async def order_book(hub, symbol):
    if symbol.endswith("-USD"):
        raw = await hub.http("coinbase", f"https://api.exchange.coinbase.com/products/{symbol}/book?level=2",
                             ttl=5, retries=0)
        if raw and (raw.get("bids") or raw.get("asks")):
            def levels(side, reverse):
                rows = []
                for entry in raw.get(side, []):
                    price, size = number(entry[0]), number(entry[1])
                    if price is not None and price > 0 and size is not None and size > 0:
                        rows.append((price, size))
                return sorted(rows, reverse=reverse)[:20]
            return levels("bids", True), levels("asks", False), "Coinbase aggregated depth snapshot · top 20 levels · size in base asset"
    if contract(symbol):
        row = await snapshot(hub, symbol)
        info = {} if row is None else row
    else:
        import yfinance as yf
        info = await hub.yf_call(f"book:{symbol}", 15, lambda: yf.Ticker(symbol).info) or {}
    def top(side):
        price = number(info.get(side))
        return [(price, number(info.get(side + "Size")))] if price is not None and price > 0 else []
    return top("bid"), top("ask"), "Yahoo DLY best bid/ask only · full order depth unavailable · size in reported units"


class BookView(View):
    FUNC = "BOOK"
    refresh_secs = 15
    hints = "Bid/ask snapshot · refresh every 15s · Ctrl+R refresh · Esc back"

    def compose(self) -> ComposeResult:
        yield Static("", classes="msg", id="book-info")
        yield DataTable(cursor_type="row")

    async def load(self):
        bids, asks, source = await order_book(self.hub, self.sec)
        table = self.query_one(DataTable)
        table.clear(columns=True)
        table.add_columns("Bid size", "Bid price", "Ask price", "Ask size")
        for index in range(max(len(bids), len(asks))):
            bid = bids[index] if index < len(bids) else (None, None)
            ask = asks[index] if index < len(asks) else (None, None)
            table.add_row(fnum(bid[1], 6), fnum(bid[0], 6), fnum(ask[0], 6), fnum(ask[1], 6))
        spread = asks[0][0] - bids[0][0] if bids and asks else None
        self.query_one("#book-info", Static).update(
            f"{self.sec} · {source} · spread {fnum(spread, 6)}" + (" · N/A — no bid/ask available" if not bids and not asks else ""))


VIEWS.update({"DAT": DatView, "BOOK": BookView})
