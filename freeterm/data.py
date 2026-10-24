"""Data layer: TTL/disk cache, rate limiter, Quote model, providers, DataHub facade.

Views call DataHub only. Providers are swappable per asset class (fallback chain).
All blocking libs (yfinance) run via asyncio.to_thread; never call them on the UI thread.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, asdict

from .config import CACHE_DIR, Config


# ---------------------------------------------------------------- cache / limiter
class TTLCache:
    def __init__(self) -> None:
        self._d: dict[str, tuple[float, object]] = {}

    def get(self, key: str):
        hit = self._d.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        return None

    def put(self, key: str, value, ttl: float) -> None:
        self._d[key] = (time.time() + ttl, value)


class DiskCache:
    """JSON disk cache for slow-changing data (fundamentals, filings, FRED)."""

    def __init__(self, ns: str) -> None:
        self.dir = CACHE_DIR / ns
        self.dir.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str):
        return self.dir / ("".join(c if c.isalnum() or c in "-_." else "_" for c in key) + ".json")

    def get(self, key: str, max_age: float | None = None):
        try:
            d = json.loads(self._p(key).read_text())
            if max_age is None or time.time() - d["t"] < max_age:
                return d["v"]
        except Exception:
            pass
        return None

    def put(self, key: str, value) -> None:
        try:
            self._p(key).write_text(json.dumps({"t": time.time(), "v": value}))
        except Exception:
            pass


class RateLimiter:
    """Min-interval limiter with exponential backoff on 429 / rate-limit errors."""

    def __init__(self, name: str, per_sec: float, concurrency: int = 4) -> None:
        self.name, self.gap = name, 1.0 / per_sec
        self.sem = asyncio.Semaphore(concurrency)
        self.next_ok = 0.0
        self.backoff_until = 0.0
        self.penalty = 0.0
        self.latency = 0.0
        self.err = ""

    async def __aenter__(self):
        await self.sem.acquire()
        wait = max(self.next_ok, self.backoff_until) - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        self.next_ok = time.time() + self.gap
        self._t0 = time.time()
        return self

    async def __aexit__(self, et, e, tb):
        self.sem.release()
        if e is None:
            self.latency, self.err, self.penalty = time.time() - self._t0, "", 0.0
        elif "rate" in type(e).__name__.lower() or "429" in str(e):
            self.penalty = min(300.0, max(5.0, self.penalty * 2))
            self.backoff_until = time.time() + self.penalty
            self.err = "429"
        else:
            self.err = "err"
        return False

    def state(self) -> str:
        left = self.backoff_until - time.time()
        if left > 0:
            return f"{self.name}:backoff {left:.0f}s"
        if self.err:
            return f"{self.name}:{self.err}"
        return f"{self.name}:ok {self.latency * 1000:.0f}ms"


# ---------------------------------------------------------------- model
@dataclass
class Quote:
    symbol: str
    price: float | None = None
    prev_close: float | None = None
    volume: float | None = None
    day_low: float | None = None
    day_high: float | None = None
    ts: float | None = None        # time of last trade/bar (epoch)
    fetched: float = 0.0           # when we got it
    source: str = "yf"
    live: bool = False             # came from a stream

    @property
    def change(self):
        return None if self.price is None or self.prev_close is None else self.price - self.prev_close

    @property
    def pct(self):
        c = self.change
        return None if c is None or not self.prev_close else 100 * c / self.prev_close

    @property
    def tag(self) -> str:
        age = time.time() - self.fetched
        if self.live and age < 30:
            return "LIVE"
        return "STALE" if age > 300 else "DLY"


# ---------------------------------------------------------------- providers
class Provider:
    """Interface. Implement per source; unimplemented methods raise NotImplementedError."""
    name = "base"

    async def quote(self, symbols: list[str]) -> dict[str, Quote]: raise NotImplementedError
    async def history(self, symbol: str, period: str, interval: str): raise NotImplementedError
    async def options(self, symbol: str, expiry: str | None = None): raise NotImplementedError
    async def stream(self, symbols: list[str], on_quote) -> None: raise NotImplementedError


def _sub(df, sym):
    """Pick one ticker's columns out of a yf.download frame (handles MultiIndex either way)."""
    import pandas as pd
    if not isinstance(df.columns, pd.MultiIndex):
        return df
    if sym in df.columns.get_level_values(0):
        return df[sym]
    if sym in df.columns.get_level_values(1):
        return df.xs(sym, axis=1, level=1)
    return None


class YFProvider(Provider):
    name = "yf"

    def __init__(self, limiter: RateLimiter) -> None:
        self.lim = limiter

    async def quote(self, symbols):
        def work():
            import yfinance as yf
            kw = dict(group_by="ticker", progress=False, threads=True, auto_adjust=False)
            d = yf.download(symbols, period="5d", interval="1d", **kw)
            try:
                m = yf.download(symbols, period="1d", interval="1m", **kw)
            except Exception:
                m = None
            out, now = {}, time.time()
            for s in symbols:
                q = Quote(s, fetched=now)
                dd = _sub(d, s)
                if dd is not None and "Close" in dd:
                    dd = dd.dropna(subset=["Close"])
                    if len(dd):
                        last = dd.iloc[-1]
                        q.price, q.volume = float(last["Close"]), float(last.get("Volume", 0) or 0)
                        q.day_low, q.day_high = float(last["Low"]), float(last["High"])
                        q.ts = dd.index[-1].timestamp()
                        if len(dd) > 1:
                            q.prev_close = float(dd.iloc[-2]["Close"])
                mm = _sub(m, s) if m is not None else None
                if mm is not None and "Close" in mm:
                    mm = mm.dropna(subset=["Close"])
                    if len(mm):
                        q.price, q.ts = float(mm.iloc[-1]["Close"]), mm.index[-1].timestamp()
                if q.price is not None:
                    out[s] = q
            return out
        async with self.lim:
            return await asyncio.to_thread(work)

    async def history(self, symbol, period="1y", interval="1d"):
        def work():
            import yfinance as yf
            return yf.Ticker(symbol).history(period=period, interval=interval)
        async with self.lim:
            return await asyncio.to_thread(work)


# ---------------------------------------------------------------- chart bars
RANGES = ["1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "5Y", "MAX"]
RANGE_DAYS = dict(zip(RANGES, [1, 5, 31, 92, 183, 366, 366, 1830, 99999]))
IVS = ["1m", "5m", "15m", "1h", "1d", "1wk"]
IV_MIN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 1440, "1wk": 10080}
# (interval, yfinance period, days covered, cache ttl seconds): yfinance's own per-interval history limits.
# Four requests per symbol cover every range/interval combination the chart offers.
BASES = [("1m", "7d", 7, 60), ("5m", "60d", 60, 120), ("1h", "730d", 730, 300), ("1d", "max", 99999, 900)]
_RULE = {"5m": "5min", "15m": "15min", "1h": "60min", "1d": "1D", "1wk": "W-FRI"}


def resample_ohlcv(df, iv: str):
    r = df.resample(_RULE[iv], label="right" if iv == "1wk" else "left", closed="right" if iv == "1wk" else "left")
    out = r.agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum", "BadVol": "max"})
    out = out.dropna(subset=["Close"])
    out["BadVol"] = out["BadVol"].fillna(False).astype(bool)
    return out


def flag_bad_volume(df):
    """Yahoo's 1-minute equity data contains bogus volume prints (single minutes claiming 30-60% of the day;
    summed 1m volume reaching 2-5x the official daily volume). Bars above 100x their session's median volume
    are blanked (NaN) and flagged in BadVol so the chart can say how many it hid. Measured on NVDA/AAPL/SPY:
    afterwards 1m volume sums to 0.7-0.9x daily volume, matching Yahoo's clean 5m data. A real closing auction
    is far below 100x the median. Only applied to the 1m base: the 5m base is clean and crypto 5m false-flags."""
    vol = df["Volume"]
    med = vol.groupby(vol.index.normalize()).transform("median")
    bad = (med > 0) & (vol > 100 * med)
    out = df.copy()
    out.loc[bad, "Volume"] = float("nan")
    out["BadVol"] = bad
    return out


def slice_range(df, rng: str):
    if rng == "MAX" or not len(df):
        return df
    idx = df.index
    import pandas as pd
    if rng in ("1D", "5D"):
        n = 1 if rng == "1D" else 5
        if (idx.dayofweek >= 5).any():            # trades on weekends (crypto): rolling 24h / 5d window
            return df[idx >= idx[-1] - pd.Timedelta(days=n)]
        days = idx.normalize().unique()           # exchange hours: last 1 / 5 sessions
        return df[idx.normalize() >= days[-min(n, len(days))]]
    if rng == "YTD":
        start = pd.Timestamp(year=idx[-1].year, month=1, day=1, tz=idx.tz)
    else:
        start = idx[-1] - pd.Timedelta(days=RANGE_DAYS[rng])
    return df[idx >= start]


# ---------------------------------------------------------------- hub
class DataHub:
    """Single facade the UI talks to. Fail soft: return {} / None, record status."""

    QUOTE_TTL, HIST_TTL = 5.0, 60.0

    def __init__(self, config: Config) -> None:
        self.cfg = config
        self.mem = TTLCache()
        self.disk = DiskCache("snap")
        self.limiters = {"yf": RateLimiter("yf", 2.0, 4)}
        self.yf = YFProvider(self.limiters["yf"])
        self.quote_chain: list[Provider] = [self.yf]   # TODO phase 7: Coinbase for crypto, Alpaca IEX overlay
        self.last: dict[str, Quote] = {}
        self.warnings: list[str] = []
        self.streams: dict[str, str] = {}          # stream name -> state, shown in status bar
        self.iex: dict[str, dict] = {}             # sym -> {"price", "ts", "via"} (Alpaca IEX last trade)
        self.watch: dict[str, float] = {}          # sym -> last time a view asked for it (stream priority)
        self._inflight: dict[str, asyncio.Future] = {}
        snap = self.disk.get("quotes") or {}
        for s, d in snap.items():                      # first paint from disk; shows STALE until refreshed
            try:
                self.last[s] = Quote(**d)
            except TypeError:
                pass
        self.alpaca_ok = bool(os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_API_SECRET"))
        self.alpaca_reason = "" if self.alpaca_ok else "no ALPACA_API_KEY/ALPACA_API_SECRET"
        # NOTE: alpaca_ok must ALSO be confirmed by validate_alpaca() before the IEX overlay may turn on.

    def warn(self, msg: str) -> None:
        self.warnings = (self.warnings + [msg])[-5:]

    async def validate_alpaca(self) -> None:
        """GET latest IEX trade; anything but 200 disables the overlay permanently for the session."""
        if not self.alpaca_ok:
            return
        try:
            import httpx
            h = {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
                 "APCA-API-SECRET-KEY": os.environ["ALPACA_API_SECRET"]}
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get("https://data.alpaca.markets/v2/stocks/SPY/trades/latest?feed=iex", headers=h)
            if r.status_code != 200:
                self.alpaca_ok, self.alpaca_reason = False, f"Alpaca keys rejected (HTTP {r.status_code})"
        except Exception as e:
            self.alpaca_ok, self.alpaca_reason = False, f"Alpaca unreachable ({type(e).__name__})"
        if not self.alpaca_ok and self.cfg.get("iex", "enabled"):
            self.cfg.set("iex", "enabled", False)

    def cached_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {s: self.last[s] for s in symbols if s in self.last}

    async def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        need = [s for s in symbols if self.mem.get("q:" + s) is None and not self.live_fresh(s)]
        if need:
            for prov in self.quote_chain:
                try:
                    got = await prov.quote(need)
                except Exception as e:
                    self.warn(f"{prov.name} quote failed: {type(e).__name__}")
                    continue
                for s, q in got.items():
                    if self.live_fresh(s):      # a stream update landed while we were polling
                        continue
                    self.last[s] = q
                    self.mem.put("q:" + s, q, self.QUOTE_TTL)
                need = [s for s in need if s not in got]
                if not need:
                    break
            self.disk.put("quotes", {s: asdict(q) for s, q in self.last.items()})
        return self.cached_quotes(symbols)

    async def history(self, symbol: str, period="1y", interval="1d"):
        key = f"h:{symbol}:{period}:{interval}"
        hit = self.mem.get(key)
        if hit is not None:
            return hit
        try:
            df = await self.yf.history(symbol, period, interval)
        except Exception as e:
            self.warn(f"history {symbol} failed: {type(e).__name__}")
            return None
        self.mem.put(key, df, self.HIST_TTL)
        return df

    # -- chart bars: a few base downloads per symbol; every range/interval is derived locally from them
    def base_for(self, rng: str, iv: str):
        """Coarsest base dataset that can produce `iv` bars covering `rng`, or None if no free source can."""
        need, days = IV_MIN[iv], RANGE_DAYS[rng]
        if need >= days * 1440:           # one bar (or less) per range is not a chart: 1D@1d, 5D@1wk
            return None
        best = None
        for biv, period, cover, ttl in BASES:
            if need % IV_MIN[biv] == 0 and cover >= days:
                best = (biv, period, ttl)
        return best

    async def _base(self, sym: str, biv: str, period: str, ttl: float):
        key = f"b:{sym}:{biv}"
        hit = self.mem.get(key)
        if hit is not None:
            return hit
        inflight = self._inflight.get(key)
        if inflight is not None:          # someone is already downloading it: share the result
            return await asyncio.shield(inflight)
        fut = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        df = None
        try:
            df = await self.yf.history(sym, period, biv)
            if df is not None and len(df):
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
                df = flag_bad_volume(df) if biv == "1m" else df.assign(BadVol=False)
                self.mem.put(key, df, ttl)
        except Exception as e:
            self.warn(f"bars {sym} {biv}: {type(e).__name__}")
            df = None
        finally:
            fut.set_result(df)
            self._inflight.pop(key, None)
        return df

    async def bars(self, sym: str, rng: str, iv: str):
        """OHLCV for (range, interval), built from a cached base download. Returns a private copy or None."""
        b = self.base_for(rng, iv)
        if b is None:
            return None
        df = await self._base(sym, *b)
        if df is None or not len(df):
            return None
        if iv != b[0]:
            df = resample_ohlcv(df, iv)
        return slice_range(df, rng).copy()

    async def prefetch_bars(self, sym: str) -> None:
        """Warm every base for a symbol (coarse first: daily ranges are flipped most), one at a time."""
        for biv, period, _cover, ttl in reversed(BASES):
            await self._base(sym, biv, period, ttl)

    async def yf_call(self, key: str, ttl: float, fn):
        """Run a blocking yfinance callable in a thread under the yf limiter, memory-cached for ttl seconds."""
        hit = self.mem.get(key)
        if hit is not None:
            return hit
        try:
            async with self.limiters["yf"]:
                v = await asyncio.to_thread(fn)
        except Exception as e:
            self.warn(f"yf {key}: {type(e).__name__}")
            return None
        self.mem.put(key, v, ttl)
        return v

    @property
    def client(self):
        if getattr(self, "_client", None) is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=25, follow_redirects=True)
        return self._client

    async def http(self, name: str, url: str, per_sec: float = 2.0, headers: dict | None = None,
                   ttl: float = 60, disk_ns: str | None = None, disk_age: float | None = None,
                   as_json: bool = True, retries: int = 2):
        """Rate-limited GET with memory cache, optional persistent disk cache, 429 backoff, stale fallback."""
        hit = self.mem.get(url)
        if hit is not None:
            return hit
        disk = DiskCache(disk_ns) if disk_ns else None
        if disk:
            v = disk.get(url, disk_age)
            if v is not None:
                self.mem.put(url, v, ttl)
                return v
        lim = self.limiters.setdefault(name, RateLimiter(name, per_sec, 2))
        for attempt in range(retries + 1):
            try:
                async with lim:
                    r = await self.client.get(url, headers=headers)
                    if r.status_code == 429:
                        raise RuntimeError("429 rate limited")
                    r.raise_for_status()
                v = r.json() if as_json else r.text
                break
            except Exception as e:
                if attempt == retries or "429" not in str(e):
                    self.warn(f"{name}: {type(e).__name__} {str(e)[:60]}")
                    return disk.get(url) if disk else None   # stale beats nothing
        self.mem.put(url, v, ttl)
        if disk:
            disk.put(url, v)
        return v

    def live_fresh(self, s: str) -> bool:
        q = self.last.get(s)
        return bool(q and q.live and time.time() - q.fetched < 30)

    def status(self) -> str:
        parts = [l.state() for l in self.limiters.values()] + [f"{k}:{v}" for k, v in self.streams.items()]
        return " ".join(parts)
