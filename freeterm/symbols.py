"""Asset-wide symbol discovery with an offline seed and unrestricted Yahoo search."""
from __future__ import annotations

import asyncio
import re
import shlex


def normalized(text):
    return re.sub(r"[^a-z0-9]", "", text.casefold())



TYPE_ALIASES = {
    "STOCK": "EQUITY", "STOCKS": "EQUITY", "EQUITIES": "EQUITY",
    "FUTURES": "FUTURE", "COMMODITY": "FUTURE", "COMMODITIES": "FUTURE",
    "CRYPTO": "CRYPTOCURRENCY", "CRYPTOCURRENCIES": "CRYPTOCURRENCY",
    "FX": "CURRENCY", "FOREX": "CURRENCY", "CURRENCIES": "CURRENCY",
    "INDICES": "INDEX", "INDEXES": "INDEX", "ETFS": "ETF",
    "MUTUALFUNDS": "MUTUALFUND", "MUTUAL FUND": "MUTUALFUND",
    "OPTIONS": "OPTION",
}


def canonical_type(value):
    value = value.strip().upper()
    return TYPE_ALIASES.get(value, value)


SEARCH_HELP = """Security search · /? text [filters]
Every displayed column can be filtered:
_s= symbol contains · _n= name contains · _t= type · _p= source/provider
Types: EQUITY (STOCK), FUTURE, CRYPTOCURRENCY (CRYPTO), CURRENCY (FX),
INDEX, ETF, MUTUALFUND, OPTION (OPTIONS), SECURITY.
Sources: local, alias, Yahoo, EDGAR. OPTION discovery uses Yahoo.
Filters combine with AND; comma-separated values within a filter use OR.
Matching ignores case. Quote values containing spaces: _n="natural gas".
/? oil _t=FUTURE _s=CL · /? _t=ETF _n=bond · /? apple _p=Yahoo
/? AAPL _t=OPTION · /? AAPL 2026-12-18 _t=OPTION
Option discovery is bounded to one underlying/expiry and 100 nearby contracts.
Filter-only searches browse the local catalog; text searches also query Yahoo/EDGAR.
Results are limited to 100 matching securities. /?? displays this page.
"""


def parse_filters(query, keys):
    """Parse standalone, single-letter filters, retaining quoted multiword values."""
    words, filters = [], {}
    for token in shlex.split(query):
        match = re.fullmatch(r"_([a-zA-Z][a-zA-Z0-9_]*)=(.*)", token)
        if not match:
            words.append(token)
            continue
        key, value = match.groups()
        key = key.lower()
        if key not in keys:
            raise ValueError(f"Unknown search filter _{key}; available: " + " ".join(f"_{k}=" for k in keys))
        if key in filters or not value or any(not v.strip() for v in value.split(',')):
            raise ValueError(f"Supply one nonempty _{key}= value; separate alternatives with commas")
        filters[key] = value
    return " ".join(words), filters


def matches_filters(row, filters, columns, exact=()):
    for key, wanted in filters.items():
        actual = str(row[columns[key]]).casefold()
        values = [v.strip().casefold() for v in wanted.split(',')]
        if not any(actual == value if key in exact else value in actual for value in values):
            return False
    return True


def parse_search(query):
    """Only standalone _key=value tokens are filters; ordinary terms stay literal."""
    query, filters = parse_filters(query, "sntp")
    if "t" in filters:
        values = [canonical_type(value) for value in filters["t"].split(',')]
        if any(v not in {"EQUITY", "FUTURE", "CRYPTOCURRENCY", "CURRENCY", "INDEX", "ETF", "MUTUALFUND", "OPTION", "SECURITY"} for v in values):
            raise ValueError("Unknown security type; /?? lists supported values")
        if "OPTION" in values and len(values) > 1:
            raise ValueError("Search OPTION separately from other types")
        filters["t"] = ','.join(values)
    if "p" in filters and any(v.strip().upper() not in {"LOCAL", "ALIAS", "YAHOO", "EDGAR"} for v in filters["p"].split(',')):
        raise ValueError("Source must be local, alias, Yahoo or EDGAR; use /?? for help")
    return query, filters


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
    from .options import contract, search as search_options
    if wanted_type == "OPTION":
        rows = await search_options(hub, query, aliases)
        rows = [row for row in rows if matches_filters(row, filters, {"s": 0, "n": 1, "t": 2, "p": 3}, ("t", "p"))]
        if publish:
            publish(rows)
        return rows

    def allowed(row):
        kind = "OPTION" if contract(row[0]) else canonical_type(row[2])
        return kind != "OPTION"

    rows = catalog()
    names = {s: (n, ty) for s, n, ty, _ in rows}
    for alias, symbol in aliases.items():
        name, kind = names.get(symbol, (symbol, "SECURITY"))
        rows.append((symbol, f"{name} ({alias})", kind, "alias"))

    details = {}

    def results(apply_filters=True):
        # Match all names, then merge metadata by symbol. A better name must not
        # change whether an alias or the original provider result matched.
        grouped = {}
        for row in rows:
            grouped.setdefault(row[0].upper(), []).append(row)
        ranked = []
        for symbol, candidates in grouped.items():
            score = min(rank(query, *r[:3]) for r in candidates)
            if score == 99 and not any(r[3] == "Yahoo" for r in candidates):
                continue
            best = min(candidates, key=lambda r: (r[3] == "EDGAR", -len(r[1])))
            name, kind, source = best[1:]
            yahoo = next((r for r in candidates if r[3] == "Yahoo"), None)
            if yahoo and yahoo[2] != "SECURITY":
                kind = yahoo[2]
            detail = details.get(symbol, {})
            if detail.get("longName"):
                name, source = detail["longName"], "Yahoo"
            if detail.get("quoteType"):
                kind = canonical_type(detail["quoteType"])
            row = (symbol, name, kind, source)
            if allowed(row) and (not apply_filters or matches_filters(row, filters, {"s": 0, "n": 1, "t": 2, "p": 3}, ("t", "p"))):
                ranked.append((score, symbol, row))
        return [row for _, _, row in sorted(ranked)[:100]]

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
        if not query or (wanted_type and "EQUITY" not in wanted_type.split(',')):
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
    # One batched request, rather than one profile request per result. Search
    # shortnames and EDGAR issuer names are often not instrument names.
    symbols = [r[0] for r in results(False)]
    if symbols:
        def fetch_names():
            from yfinance.data import YfData
            response = YfData().get_raw_json(
                "https://query1.finance.yahoo.com/v7/finance/quote",
                params={"symbols": ",".join(sorted(symbols)), "formatted": "false"}, timeout=12)
            return (response or {}).get("quoteResponse", {}).get("result", [])

        try:
            quotes = await hub.yf_call("search-names:v1:" + ",".join(sorted(symbols)), 86400, fetch_names)
            details.update((r["symbol"].upper(), r) for r in quotes or [] if r.get("symbol"))
        except Exception as exc:
            hub.warn(f"Search names unavailable: {type(exc).__name__}")
    return results()
