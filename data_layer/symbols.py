"""Asset-wide symbol discovery with an offline seed and unrestricted Yahoo search."""
from __future__ import annotations

import asyncio
import re
import shlex


def normalized(text):
    return re.sub(r"[^a-z0-9]", "", text.casefold())



from .field_values import TYPE_ALIASES, security_type


def canonical_type(value):
    return security_type(value) or 'SECURITY'


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
Search reads the stored index; text searches refresh discovery from Yahoo/EDGAR.
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
    from .catalog import BOARDS
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
    from .query_models import SearchQuery
    text, filters = parse_search(query)
    request = SearchQuery('security', text, filters)
    hub.queries.aliases = aliases
    if publish:
        initial = await hub.queries.query(request, stored_only=True)
        publish(initial.data or [])
    result = await hub.query(request)
    return result.data or []
