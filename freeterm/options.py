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


def remember_quote(hub, row):
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
                  volume=number(row.get("volume")), ts=stamp, fetched=time.time())
    hub.last[symbol] = quote
    hub.mem.put("q:" + symbol, quote, 30)


async def chain(hub, underlying, expiry):
    import yfinance as yf
    return await hub.yf_call(f"chain:{underlying}:{expiry}", 30,
                             lambda: yf.Ticker(underlying).option_chain(expiry))


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
            remember_quote(hub, row)
            return row
    return None


async def search(hub, query, aliases):
    """Bound discovery to one underlying/expiry; no market-wide chain crawl."""
    import yfinance as yf
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
        exps = await hub.yf_call(f"exps:{underlying}", 300, lambda: yf.Ticker(underlying).options)
    if not exps:
        matches = await hub.yf_call(f"option-underlying:{text}", 600,
            lambda: yf.Search(text, max_results=8, news_count=0, lists_count=0, timeout=12).quotes)
        underlying = next((item["symbol"] for item in matches or []
                           if item.get("quoteType") in ("EQUITY", "ETF") and item.get("symbol")), None)
        if underlying:
            exps = await hub.yf_call(f"exps:{underlying}", 300, lambda: yf.Ticker(underlying).options)
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
