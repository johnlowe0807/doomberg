"""Settings, aliases and watchlist persisted under ~/.freeterm."""
from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

HOME = Path(os.environ.get("FREETERM_HOME", Path.home() / ".freeterm"))
CACHE_DIR = HOME / "cache"
CONFIG_PATH = HOME / "config.toml"
ALIASES_PATH = HOME / "aliases.toml"
WATCHLIST_PATH = HOME / "watchlist.json"

DEFAULT_CONFIG: dict = {
    "general": {"layout": 1, "quote_refresh_secs": 10, "start_command": "/HOME"},
    "iex": {
        "enabled": False,
        "abs_diff_pct": 0.05,
        "stale_secs": 60,
        "ws_symbol_cap": 30,
        "rest_poll_secs": 15,
    },
    "chart": {
        "renderer": "auto",  # auto (Sixel/Kitty when supported), text, sixel, tgp
        "font_px": 12,       # chart labels are independent of the terminal font
        "type": "candle",
        "volume": True,
        "log": False,
        "sma": "20,50",
        "ema": "21",
        "bb": "20,2",
        "rsi": "14",
        "macd": "12,26,9",
    },
    "options": {"strikes_each_side": 12, "risk_free_fallback": 0.045},
    "sec": {"user_agent": "freeterm/0.1 personal-research freeterm@example.com"},
    "news": {
        "feeds": [
            "https://www.cnbc.com/id/100003114/device/rss/rss.html",
            "https://feeds.content.dowjones.io/public/rss/mw_topstories",
            "https://www.cnbc.com/id/10000664/device/rss/rss.html",
            "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        ]
    },
    "theme": {"name": "phosphor"},
}

DEFAULT_ALIASES = {
    "SPX": "^GSPC", "INDU": "^DJI", "CCMP": "^IXIC", "RTY": "^RUT", "VIX": "^VIX",
    "UKX": "^FTSE", "DAX": "^GDAXI", "NKY": "^N225", "GOLD": "GC=F", "CL1": "CL=F",
    "NG1": "NG=F", "EURUSD": "EURUSD=X", "BTC": "BTC-USD", "USGG10YR": "^TNX",
    "ETH": "ETH-USD", "SOL": "SOL-USD", "CAC": "^FCHI", "HSI": "^HSI", "SX5E": "^STOXX50E",
    "SILVER": "SI=F", "HG1": "HG=F", "USDJPY": "JPY=X", "GBPUSD": "GBPUSD=X", "DXY": "DX-Y.NYB",
}

DEFAULT_WATCHLIST = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM",
                     "SPY", "QQQ", "TLT", "GLD", "BTC-USD"]


def _fmt(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[\n" + "".join(f"  {_fmt(x)},\n" for x in v) + "]"
    return json.dumps(str(v))


def dump_toml(d: dict) -> str:
    out = []
    flat = {k: v for k, v in d.items() if not isinstance(v, dict)}
    for k, v in flat.items():
        out.append(f"{k} = {_fmt(v)}")
    for sec, body in d.items():
        if isinstance(body, dict):
            out.append(f"\n[{sec}]")
            for k, v in body.items():
                out.append(f'{k if k.replace("_", "").isalnum() else json.dumps(k)} = {_fmt(v)}')
    return "\n".join(out).lstrip() + "\n"


def _read_toml(p: Path) -> dict:
    try:
        return tomllib.loads(p.read_text())
    except Exception:
        return {}


class Config:
    def __init__(self) -> None:
        HOME.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        user = _read_toml(CONFIG_PATH)
        self.data = {s: dict(v) for s, v in DEFAULT_CONFIG.items()}
        for s, v in user.items():
            if isinstance(v, dict):
                self.data.setdefault(s, {}).update(v)
        if not CONFIG_PATH.exists():
            self.save()

    def get(self, section: str, key: str, default=None):
        return self.data.get(section, {}).get(key, default)

    def set(self, section: str, key: str, value) -> None:
        self.data.setdefault(section, {})[key] = value
        self.save()

    def save(self) -> None:
        try:
            CONFIG_PATH.write_text(dump_toml(self.data))
        except OSError:
            pass


def load_aliases() -> dict[str, str]:
    if not ALIASES_PATH.exists():
        try:
            ALIASES_PATH.write_text("# NAME = \"yahoo symbol\"  (edit freely)\n" + dump_toml(DEFAULT_ALIASES))
        except OSError:
            pass
    data = _read_toml(ALIASES_PATH) or dict(DEFAULT_ALIASES)
    return {k.upper(): str(v) for k, v in data.items() if not isinstance(v, dict)}


def load_watchlist() -> list[str]:
    try:
        wl = json.loads(WATCHLIST_PATH.read_text())
        if isinstance(wl, list):
            clean = list(dict.fromkeys(str(s).upper() for s in wl))   # dedupe, keep order
            if len(clean) != len(wl):
                save_watchlist(clean)
            return clean
    except Exception:
        pass
    save_watchlist(DEFAULT_WATCHLIST)
    return list(DEFAULT_WATCHLIST)


def save_watchlist(wl: list[str]) -> None:
    try:
        WATCHLIST_PATH.write_text(json.dumps(wl, indent=1))
    except OSError:
        pass
