"""Asset-wide symbol discovery with an offline seed and unrestricted Yahoo search."""
from __future__ import annotations

import asyncio
import re


def normalized(text):
    return re.sub(r"[^a-z0-9]", "", text.casefold())



TYPE_ALIASES = {
    "STOCK": "EQUITY", "STOCKS": "EQUITY", "EQUITIES": "EQUITY",
    "FUTURES": "FUTURE", "COMMODITY": "FUTURE", "COMMODITIES": "FUTURE",
    "CRYPTO": "CRYPTOCURRENCY", "CRYPTOCURRENCIES": "CRYPTOCURRENCY",
    "FX": "CURRENCY", "FOREX": "CURRENCY", "CURRENCIES": "CURRENCY",
    "INDICES": "INDEX", "INDEXES": "INDEX", "ETFS": "ETF",
    "MUTUALFUNDS": "MUTUALFUND", "MUTUAL FUND": "MUTUALFUND",
}


def canonical_type(value):
    value = value.strip().upper()
    return TYPE_ALIASES.get(value, value)


def parse_search(query):
    """Only standalone _key=value tokens are filters; ordinary terms stay literal."""
    words, filters = [], {}
    for token in query.split():
        match = re.fullmatch(r"_([a-zA-Z][a-zA-Z0-9_]*)=(.*)", token)
        if not match:
            words.append(token)
            continue
        key, value = match.groups()
        key = key.lower()
        if key != "t":
            raise ValueError(f"Unknown search filter _{key}; use _t=TYPE")
        if not value or not re.fullmatch(r"[a-zA-Z]+", value):
            raise ValueError("Type filter needs a type, e.g. _t=FUTURE or _t=CRYPTO")
        if key in filters:
            raise ValueError("Use one _t=TYPE filter per search")
        filters[key] = canonical_type(value)
    return " ".join(words), filters


def catalog():
    # Reuse the boards so additions there automatically become searchable.
    from .boards import BOARDS
    types = {"WEI": "INDEX", "CMDTY": "FUTURE", "CRYPTO": "CRYPTOCURRENCY",
             "FXL": "CURRENCY", "MOSTU": "EQUITY"}
    rows = [(s, n, types[k], "local") for k, entries in BOARDS.items() for s, n in entries]
    coins = {"BTC": "Bitcoin", "ETH": "Ethereum Ether", "SOL": "Solana", "XRP": "XRP Ripple",
             "BNB": "BNB Binance Coin", "DOGE": "Dogecoin", "ADA": "Cardano", "AVAX": "Avalanche",
             "LINK": "Chainlink", "LTC": "Litecoin", "DOT": "Polkadot", "BCH": "Bitcoin Cash",
             "USDT": "Tether", "USDC": "USD Coin", "SHIB": "Shiba Inu", "XLM": "Stellar",
             "UNI": "Uniswap", "ETC": "Ethereum Classic", "ATOM": "Cosmos", "XMR": "Monero"}
    rows += [(f"{s}-USD", n, "CRYPTOCURRENCY", "local") for s, n in coins.items()]
    rows += [(s, n, "ETF", "local") for s, n in [
        ("SPY", "SPDR S&P 500 ETF"), ("QQQ", "Invesco Nasdaq 100 ETF"),
        ("GLD", "SPDR Gold Shares ETF"), ("SLV", "iShares Silver Trust ETF"),
        ("TLT", "iShares 20+ Year Treasury Bond ETF"), ("HYG", "iShares High Yield Corporate Bond ETF"),
        ("LQD", "iShares Investment Grade Corporate Bond ETF"), ("AGG", "iShares Aggregate Bond ETF")]]
    rows += [(s, n, "INDEX", "local") for s, n in [
        ("^IRX", "13 Week Treasury Bill Yield"), ("^FVX", "5 Year Treasury Yield"),
        ("^TNX", "10 Year Treasury Yield"), ("^TYX", "30 Year Treasury Yield")]]
    rows += [("NG=F", "Natural Gas", "FUTURE", "local"),
             ("CL=F", "West Texas Intermediate WTI Crude Oil", "FUTURE", "local"),
             ("BZ=F", "Brent Crude Oil", "FUTURE", "local")]
    return rows


def rank(query, symbol, name, kind=""):
    q = normalized(query)
    s, n = normalized(symbol), normalized(name)
    if q == s:
        return 0
    if q == normalized(symbol.split("-")[0].split("=")[0]):
        return 1
    if s.startswith(q):
        return 2
    if q in n:
        return 3
    if q in s:
        return 4
    words = query.casefold().split()
    categories = {"FUTURE": "commodity commodities futures", "CRYPTOCURRENCY": "crypto cryptocurrency cryptocurrencies",
                  "CURRENCY": "forex fx currency currencies", "INDEX": "index indices indexes",
                  "ETF": "etf exchange traded fund funds", "EQUITY": "stock stocks equity equities",
                  "MUTUALFUND": "mutual fund funds"}
    haystack = f"{symbol} {name} {kind} {categories.get(kind, '')}".casefold()
    if words and all(w in haystack for w in words):
        return 5
    return 99


async def search_symbols(hub, query, aliases, publish=None):
    from .fundamentals import edgar_tickers
    import yfinance as yf

    query, filters = parse_search(query)
    wanted_type = filters.get("t")

    def allowed(row):
        return wanted_type is None or canonical_type(row[2]) == wanted_type

    rows = catalog()
    names = {s: (n, ty) for s, n, ty, _ in rows}
    for alias, symbol in aliases.items():
        name, kind = names.get(symbol, (symbol, "SECURITY"))
        rows.append((symbol, f"{name} ({alias})", kind, "alias"))

    def results():
        # Exact tickers outrank name matches; prefer descriptive names on duplicates.
        ranked = sorted((r for r in rows if allowed(r)), key=lambda r: (rank(query, *r[:3]), r[3] == "EDGAR", -len(r[1]), r[0]))
        seen, out = set(), []
        for row in ranked:
            if row[0].upper() not in seen and rank(query, *row[:3]) < 99:
                seen.add(row[0].upper())
                out.append(row)
        return out[:100]

    if publish:
        publish(results())

    async def yahoo():
        if not query:
            return []
        found = await hub.yf_call(f"asset-search:v2:{query.casefold()}", 600,
            lambda: yf.Search(query, max_results=100, news_count=0, lists_count=0,
                              enable_fuzzy_query=True, timeout=12).quotes)
        for r in found or []:
            if r.get("symbol"):
                symbol = r["symbol"]
                name = r.get("longname") or r.get("shortname") or symbol
                kind = canonical_type(r.get("quoteType") or r.get("typeDisp") or "SECURITY")
                # Yahoo may correct spelling: retain those provider-ranked matches too.
                rows.append((symbol, name, kind, "Yahoo"))
        return found or []

    async def edgar():
        if not query or wanted_type not in (None, "EQUITY"):
            return
        tickers = await edgar_tickers(hub)
        rows.extend((s, n, "EQUITY", "EDGAR") for s, (_, n) in tickers.items()
                    if rank(query, s, n, "EQUITY") < 99)

    async def guarded(fetch):
        try:
            await fetch()
        except Exception as exc:
            hub.warn(f"Search source unavailable: {type(exc).__name__}")
        if publish:
            publish(results())

    await asyncio.gather(guarded(yahoo), guarded(edgar))
    out = results()
    seen = {r[0].upper() for r in out}
    for row in rows:
        if row[3] == "Yahoo" and allowed(row) and row[0].upper() not in seen:
            out.append(row)
            seen.add(row[0].upper())
    return out[:100]
