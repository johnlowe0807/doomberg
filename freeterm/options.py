"""Option contract discovery and cached Yahoo chain quotes."""
from __future__ import annotations

import math
import re
import time
from datetime import datetime


def contract(symbol):
    if not isinstance(symbol, str):
        return None
    match = re.fullmatch(r"([A-Z][A-Z0-9.]{0,9})(\d{6})([CP])(\d{8})", (symbol or "").upper())
    if not match:
        return None
    root, expiry, side, strike = match.groups()
    try:
        expiry = datetime.strptime(expiry, "%y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None
    return {"symbol": symbol.upper(), "underlying": root, "expiry": expiry,
            "side": "Call" if side == "C" else "Put", "strike": int(strike) / 1000}


def name(symbol):
    option = contract(symbol)
    return (f"{option['underlying']} {option['expiry']} {option['side']} {option['strike']:g}"
            if option else symbol)


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def remember_quote(hub, row, metadata=None):
    from .data import Quote
    symbol, last = row.get("contractSymbol"), number(row.get("lastPrice"))
    if not symbol or last is None or last < 0:
        return
    change = number(row.get("change"))
    stamp = row.get("lastTradeDate")
    try:
        stamp = number(stamp.timestamp())
    except (AttributeError, ValueError):
        stamp = None
    quote = Quote(symbol, price=last, prev_close=last - change if change is not None else None,
                  volume=number(row.get("volume")), ts=stamp,
                  fetched=(metadata or {}).get("fetched_at", time.time()),
                  stale=(metadata or {}).get("freshness", "fresh") != "fresh")
    hub.last[symbol] = quote
    if not quote.stale:
        hub.mem.put("q:" + symbol, quote, max(0, 30 - (time.time() - quote.fetched)))
    return quote


async def chain(hub, underlying, expiry):
    return await hub.resource("yf", "chain", symbol=underlying, expiry=expiry)


async def snapshot(hub, symbol):
    option = contract(symbol)
    if option is None:
        return None
    found = await chain(hub, option["underlying"], option["expiry"])
    if found is None:
        return None
    for frame in (found.calls, found.puts):
        if frame is None or "contractSymbol" not in frame:
            continue
        rows = frame[frame.contractSymbol == symbol]
        if not rows.empty:
            row = rows.iloc[0]
            quote = remember_quote(hub, row, getattr(found, "freeterm", None))
            if quote:
                await hub.publish_quote(quote)
            return row
    return None


async def search(hub, query, aliases):
    """Bound discovery to one underlying/expiry; no market-wide chain crawl."""
    query = query.strip().upper()
    query = aliases.get(query, query)
    exact = contract(query)
    if exact:
        row = await snapshot(hub, query)
        return [(query, name(query), "OPTION", "Yahoo")] if row is not None else []
    words = query.split()
    expiry = next((word for word in words if re.fullmatch(r"\d{4}-\d{2}-\d{2}", word)), None)
    text = " ".join(word for word in words if word != expiry)
    if not text:
        raise ValueError("Options search needs an underlying: /? AAPL _t=OPTIONS [YYYY-MM-DD]")
    underlying = aliases.get(text, text)
    exps = None
    if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}", underlying):
        exps = await hub.resource("yf", "expiries", symbol=underlying)
    if not exps:
        matches = await hub.resource("yf", "search", text=text, limit=8)
        underlying = next((item["symbol"] for item in matches or []
                           if item.get("quoteType") in ("EQUITY", "ETF") and item.get("symbol")), None)
        if underlying:
            exps = await hub.resource("yf", "expiries", symbol=underlying)
    if not exps:
        return []
    expiry = expiry or exps[0]
    if expiry not in exps:
        raise ValueError(f"No listed expiry {expiry} for {underlying}; nearest is {exps[0]}")
    found = await chain(hub, underlying, expiry)
    if found is None:
        return []
    spot = number((getattr(found, "underlying", None) or {}).get("regularMarketPrice"))
    rows = [row for frame in (found.calls, found.puts) if frame is not None for _, row in frame.iterrows()
            if contract(row.get("contractSymbol"))]
    rows.sort(key=lambda row: (abs((number(row.get("strike")) or 0) - spot) if spot is not None else 0,
                               row["contractSymbol"]))
    matches = []
    for row in rows[:100]:
        remember_quote(hub, row)
        symbol = row["contractSymbol"]
        matches.append((symbol, name(symbol), "OPTION", "Yahoo"))
    return matches
