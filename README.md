# freeterm

Keyboard-driven, phosphor-style terminal market monitor. Free data only (yfinance, FRED, Treasury, SEC EDGAR,
Coinbase; Alpaca IEX optional). The full spec is in `CLAUDE.md`.

## Run
```
python3.11 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python -m freeterm      # or: .venv/bin/ft
```
- An uppercase letter opens the command line for a ticker (`AAPL`); `/` opens it for a command
  (`/WEI`, `/GC`, `/FA AAPL`, `AAPL /FA`, `/COMP SPY QQQ`, `/? apple`). Commands require `/`;
  bare words always select symbols. Press `?` or run `/HELP` for help.
- Optional IEX overlay: `export ALPACA_API_KEY=... ALPACA_API_SECRET=...`. The keys are checked at startup. If they're missing or
  rejected, the overlay can't be turned on and the status bar says why.
- Settings: `~/.freeterm/config.toml`, `aliases.toml`, `watchlist.json` (created on first run). Set `[sec].user_agent`
  to include a real contact; SEC asks for one.
- Layout: `Ctrl+1/2/4`, or `/LAYOUT 1|2|4` (macOS Terminal doesn't send Ctrl+digit).
- News: Enter on a headline in `/CN AAPL` or `/TOP` opens a text reader. Use `j/k` to scroll, `/` to search,
  and `q`/Escape to return. If article text is unavailable, the reader shows the feed summary when available.
  Press `o` to request the original in a browser; the keyboard confirmation uses `y`/`Y` to open and
  `n`/`N`, Escape, or Enter to cancel. No mouse or buttons are needed.
- Quit: Ctrl+Q opens a keyboard confirmation popup. Press Ctrl+Q again to quit, or `n`/`N` or Escape to cancel.

## Status
All functions in the brief are built and smoke-tested against live data. The Alpaca IEX path is the exception: it's untested because no keys were available.
The TODO list is in `HANDOFF.md` under **Open items**.

## Test
`FREETERM_HOME=/tmp/ftsmoke .venv/bin/python -W ignore tests/smoke.py`
