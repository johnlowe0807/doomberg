"""Phase 7 live data: Coinbase crypto ticker stream, Alpaca IEX trades (websocket + REST) and overlay math.

Alpaca runs only when hub.alpaca_ok is True AFTER DataHub.validate_alpaca(); otherwise nothing here touches Alpaca.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from datetime import datetime

from .data import Quote

CB_WS = "wss://ws-feed.exchange.coinbase.com"
CB_PRODUCTS = "https://api.exchange.coinbase.com/products"
ALP_WS = "wss://stream.data.alpaca.markets/v2/iex"
ALP_REST = "https://data.alpaca.markets/v2/stocks/trades/latest"


def is_crypto(s: str) -> bool:
    return s.endswith("-USD")


def is_us_equity(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{1,5}([.-][A-Z])?", s or "")) and not is_crypto(s)


def iso_ts(t: str | None) -> float:
    if not t:
        return time.time()
    t = re.sub(r"(\.\d{6})\d+", r"\1", t).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(t).timestamp()
    except ValueError:
        return time.time()


def age(sec: float) -> str:
    sec = max(0, int(sec))
    if sec >= 3600:
        return f"{sec // 3600}h{sec % 3600 // 60:02d}m"
    return f"{sec // 60}m{sec % 60:02d}s" if sec >= 60 else f"{sec}s"


# ---------------------------------------------------------------- Coinbase
async def coinbase_stream(hub, want) -> None:
    """want(): list of yahoo-style crypto symbols (BTC-USD …). Reconnects with backoff; never raises."""
    import websockets
    valid = None
    backoff = 1
    while True:
        try:
            if valid is None:
                prods = await hub.http("coinbase", CB_PRODUCTS, 2, ttl=86400, disk_ns="cb", disk_age=86400)
                valid = {p["id"] for p in prods or [] if p.get("status") == "online"} or None
            syms = sorted({s for s in want() if is_crypto(s) and (valid is None or s in valid)})
            if not syms:
                await asyncio.sleep(10)
                continue
            async with websockets.connect(CB_WS, ping_interval=20, max_size=2 ** 22) as ws:
                await ws.send(json.dumps({"type": "subscribe", "product_ids": syms, "channels": ["ticker"]}))
                hub.streams["cb"] = f"LIVE {len(syms)}"
                backoff = 1
                t_check = time.time()
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=60)
                    m = json.loads(raw)
                    if m.get("type") == "ticker":
                        f = lambda k: float(m[k]) if m.get(k) not in (None, "") else None
                        s = m["product_id"]
                        hub.last[s] = Quote(s, price=f("price"), prev_close=f("open_24h"), volume=f("volume_24h"),
                                            day_low=f("low_24h"), day_high=f("high_24h"), ts=iso_ts(m.get("time")),
                                            fetched=time.time(), source="coinbase", live=True)
                    elif m.get("type") == "error":
                        hub.warn(f"coinbase: {m.get('message')} {m.get('reason', '')}"[:100])
                    if time.time() - t_check > 30:        # resubscribe if the wanted set grew
                        t_check = time.time()
                        new = sorted({s for s in want() if is_crypto(s) and (valid is None or s in valid)})
                        if set(new) - set(syms):
                            break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            hub.streams["cb"] = f"retry({type(e).__name__})"
            await asyncio.sleep(backoff)
            backoff = min(60, backoff * 2)


# ---------------------------------------------------------------- Alpaca IEX
def _alp_headers() -> dict:
    return {"APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
            "APCA-API-SECRET-KEY": os.environ.get("ALPACA_API_SECRET", "")}


def iex_symbols(hub, want) -> tuple[list[str], list[str]]:
    """Split wanted equities into (websocket ≤ cap, REST-polled rest). Earlier = higher priority."""
    cap = max(0, min(30, int(hub.cfg.get("iex", "ws_symbol_cap", 30))))
    seen, eq = set(), []
    for s in want():
        if is_us_equity(s) and s not in seen:
            seen.add(s)
            eq.append(s)
    return eq[:cap], eq[cap:]


def iex_enabled(hub) -> bool:
    return bool(hub.alpaca_ok and hub.cfg.get("iex", "enabled"))


def set_iex(hub, mode="TOGGLE") -> str:
    mode = mode.upper()
    if mode not in ("ON", "OFF", "TOGGLE"):
        raise ValueError("Use /IEX ON, /IEX OFF or /IEX TOGGLE")
    if not hub.alpaca_ok:
        hub.cfg.set("iex", "enabled", False)
        if mode != "OFF":
            raise ValueError(f"IEX overlay unavailable: {hub.alpaca_reason}. Set valid ALPACA_API_KEY/ALPACA_API_SECRET and restart.")
    enabled = False if mode == "OFF" else True if mode == "ON" else not hub.cfg.get("iex", "enabled")
    hub.cfg.set("iex", "enabled", enabled)
    return f"IEX overlay {'ON' if enabled else 'OFF'} (IEX-only trades; small slice of volume)."


async def alpaca_stream(hub, want) -> None:
    import websockets
    backoff = 2
    while True:
        if not iex_enabled(hub):
            hub.streams.pop("iex", None)
            await asyncio.sleep(3)
            continue
        try:
            async with websockets.connect(ALP_WS, ping_interval=20) as ws:
                welcome = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if not any(m.get("T") == "success" and m.get("msg") == "connected" for m in welcome):
                    raise RuntimeError("Alpaca stream connection rejected")
                h = _alp_headers()
                await ws.send(json.dumps({"action": "auth", "key": h["APCA-API-KEY-ID"], "secret": h["APCA-API-SECRET-KEY"]}))
                auth = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if any(m.get("T") == "error" for m in auth):
                    msg = next(m.get("msg") for m in auth if m.get("T") == "error")
                    hub.streams["iex"] = f"ws:{msg}"
                    if any(m.get("code") in (401, 402, 409) for m in auth) or "auth" in str(msg).lower():
                        hub.alpaca_ok, hub.alpaca_reason = False, f"Alpaca stream auth failed ({msg})"
                        hub.cfg.set("iex", "enabled", False)
                        return
                    await asyncio.sleep(30)
                    continue
                if not any(m.get("T") == "success" and m.get("msg") == "authenticated" for m in auth):
                    raise RuntimeError("Alpaca stream did not confirm authentication")
                subs: list[str] = []
                hub.streams["iex"] = "authenticated"
                backoff = 2
                while iex_enabled(hub):
                    ws_syms, _ = iex_symbols(hub, want)
                    if set(ws_syms) != set(subs):
                        drop = sorted(set(subs) - set(ws_syms))
                        if drop:
                            await ws.send(json.dumps({"action": "unsubscribe", "trades": drop}))
                        if ws_syms:
                            await ws.send(json.dumps({"action": "subscribe", "trades": ws_syms}))
                        subs = ws_syms
                        hub.streams["iex"] = f"subscribing {len(subs)}"
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        continue
                    for m in json.loads(raw):
                        if m.get("T") == "t":
                            record = iex_trade(m, "ws")
                            if record:
                                hub.iex[m["S"]] = record
                        elif m.get("T") == "subscription":
                            hub.streams["iex"] = f"LIVE {len(m.get('trades', []))}"
                        elif m.get("T") == "error":
                            hub.warn(f"alpaca ws: {m.get('msg')}")
                            if m.get("code") in (401, 402, 409):
                                hub.alpaca_ok, hub.alpaca_reason = False, f"Alpaca stream rejected ({m.get('msg')})"
                                hub.cfg.set("iex", "enabled", False)
                                return
                            raise RuntimeError("Alpaca stream subscription failed")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            hub.streams["iex"] = f"retry({type(e).__name__})"
            await asyncio.sleep(backoff)
            backoff = min(120, backoff * 2)


async def alpaca_poll(hub, want) -> None:
    """REST latest-trade polling for wanted symbols beyond the websocket cap (and as a ws warm-up)."""
    while True:
        secs = max(5, float(hub.cfg.get("iex", "rest_poll_secs", 15)))
        try:
            if iex_enabled(hub):
                ws_syms, rest = iex_symbols(hub, want)
                batch = rest + [s for s in ws_syms if time.time() - hub.iex.get(s, {}).get("ts", 0) > 60]
                for i in range(0, len(batch), 100):
                    chunk = batch[i:i + 100]
                    url = f"{ALP_REST}?symbols={','.join(chunk)}&feed=iex"
                    d = await hub.http("alpaca", url, 3, _alp_headers(), ttl=max(1, secs - 1), retries=1)
                    for s, tr in ((d or {}).get("trades") or {}).items():
                        record = iex_trade(tr, "rest")
                        if record and record["ts"] >= hub.iex.get(s, {}).get("ts", 0):
                            hub.iex[s] = record
        except asyncio.CancelledError:
            raise
        except Exception as e:
            hub.warn(f"alpaca poll: {type(e).__name__}")
        await asyncio.sleep(secs)


def iex_trade(trade, via: str) -> dict | None:
    """Reject missing/invalid trade data instead of inventing a fresh timestamp."""
    try:
        price = float(trade["p"])
        stamp = datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", trade["t"]).replace("Z", "+00:00")).timestamp()
        if price > 0 and math.isfinite(price) and math.isfinite(stamp):
            return {"price": price, "ts": stamp, "via": via}
    except (TypeError, KeyError, ValueError, AttributeError):
        return None
    return None


def iex_eval(hub, sym: str, regular_hours: bool) -> dict | None:
    """Compare the IEX last trade with the yfinance quote. Returns None when there's nothing to show."""
    if not iex_enabled(hub) or not is_us_equity(sym):
        return None
    iex, q = hub.iex.get(sym), hub.last.get(sym)
    if not iex or not q or not q.price:
        return None
    now = time.time()
    diff = iex["price"] - q.price
    pct = 100 * diff / q.price
    yf_stale = now - (q.ts or q.fetched)
    meaningful = abs(pct) > float(hub.cfg.get("iex", "abs_diff_pct", 0.05)) or \
        (regular_hours and yf_stale > float(hub.cfg.get("iex", "stale_secs", 60)))
    return {"price": iex["price"], "diff": diff, "pct": pct, "yf_stale": yf_stale, "iex_age": now - iex["ts"],
            "meaningful": meaningful}
