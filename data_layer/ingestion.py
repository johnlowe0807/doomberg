"""Provider ingestion only: network calls, worker dispatch, rate limits and backoff."""
from __future__ import annotations
import asyncio
import time
from .query_models import Quote, ResourceQuery

class RateLimiter:
    """Min-interval limiter with exponential backoff on 429 / rate-limit errors."""

    def __init__(self, name: str, per_sec: float, concurrency: int = 4) -> None:
        self.name, self.gap = name, 1.0 / per_sec
        self.sem = asyncio.Semaphore(concurrency)
        self.slots = asyncio.Lock()
        self.started = {}
        self.next_ok = 0.0
        self.backoff_until = 0.0
        self.penalty = 0.0
        self.latency = 0.0
        self.err = ""

    async def __aenter__(self):
        await self.sem.acquire()
        try:
            async with self.slots:
                while True:
                    wait = max(self.next_ok, self.backoff_until) - time.time()
                    if wait <= 0:
                        break
                    await asyncio.sleep(wait)
                self.next_ok = time.time() + self.gap
            self.started[asyncio.current_task()] = time.time()
            return self
        except BaseException:
            self.sem.release()
            raise

    async def __aexit__(self, et, error, tb):
        started = self.started.pop(asyncio.current_task(), time.time())
        if error is None:
            self.latency, self.err, self.penalty = time.time() - started, '', 0.0
        elif 'rate' in type(error).__name__.lower() or '429' in str(error):
            self.penalty = min(300.0, max(5.0, self.penalty * 2))
            self.backoff_until = time.time() + self.penalty
            self.err = '429'
        elif not isinstance(error, asyncio.CancelledError):
            self.err = 'err'
        self.sem.release()
        return False

    def state(self) -> str:
        left = self.backoff_until - time.time()
        if left > 0:
            return f"{self.name}:backoff {left:.0f}s"
        if self.err:
            return f"{self.name}:{self.err}"
        return f"{self.name}:ok {self.latency * 1000:.0f}ms"



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




class Ingestion:
    def __init__(self, config, limiters, yf_provider):
        self.cfg, self.limiters, self.yf = config, limiters, yf_provider
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=25, follow_redirects=True)
        return self._client

    async def http(self, source, url, *, as_json=True, per_sec=2, headers=None, retries=2):
        limiter = self.limiters.setdefault(source, RateLimiter(source, per_sec, 2))
        for attempt in range(retries + 1):
            try:
                async with limiter:
                    response = await self.client.get(url, headers=headers)
                    response.raise_for_status()
                return await asyncio.to_thread(response.json) if as_json else response.text
            except Exception as error:
                if attempt == retries or '429' not in str(error):
                    raise

    async def resource(self, query: ResourceQuery):
        source, op, p = query.source, query.operation, query.params
        if source != 'yf':
            headers = dict(p.get('headers') or {})
            if source == 'sec':
                headers.update({'User-Agent': self.cfg.get('sec', 'user_agent'), 'Accept-Encoding': 'gzip, deflate'})
            if source == 'alpaca':
                import os
                headers.update({'APCA-API-KEY-ID': os.environ.get('ALPACA_API_KEY', ''),
                                'APCA-API-SECRET-KEY': os.environ.get('ALPACA_API_SECRET', '')})
            return await self.http(source, p['url'], as_json=p.get('as_json', True),
                                   per_sec=p.get('per_sec', 8 if source == 'sec' else 2), headers=headers,
                                   retries=p.get('retries', 2))
        def fetch():
            import yfinance as yf
            symbol = p.get('symbol')
            ticker = yf.Ticker(symbol) if symbol else None
            if op in ('profile', 'book'):
                return ticker.info
            if op == 'expiries':
                return ticker.options
            if op == 'chain':
                return ticker.option_chain(p['expiry'])
            if op == 'earnings':
                return ticker.get_earnings_dates(limit=28)
            if op == 'calendar':
                return ticker.calendar
            if op == 'financials':
                return getattr(ticker, p['statement'])
            if op == 'news':
                return ticker.news
            if op == 'screen':
                return yf.screen(p['screen'], count=50)
            if op == 'search':
                return yf.Search(p['text'], max_results=p.get('limit', 100), news_count=0,
                                 lists_count=0, enable_fuzzy_query=True, timeout=12).quotes
            if op == 'names':
                from yfinance.data import YfData
                result = YfData().get_raw_json('https://query1.finance.yahoo.com/v7/finance/quote',
                    params={'symbols': ','.join(sorted(p['symbols'])), 'formatted': 'false'}, timeout=12)
                return (result or {}).get('quoteResponse', {}).get('result', [])
            if op == 'commodity':
                from .normalization import commodity_snapshot
                frame = yf.download(p['symbols'], period='5d', interval='1d', group_by='ticker',
                                    auto_adjust=False, progress=False, threads=False)
                return commodity_snapshot(frame, p['symbols'], p['date'])
            raise ValueError(f'Unknown Yahoo operation {op}')
        async with self.limiters['yf']:
            return await asyncio.to_thread(fetch)

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None
