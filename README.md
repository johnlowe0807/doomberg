# freeterm

vibe coded open-source finance data terminal

## Run
```
python3.11 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python -m freeterm      # or: .venv/bin/ft
```
- An uppercase letter opens the command line for a ticker (`AAPL`); `/` opens it for a command
  (`/WEI`, `/GC`, `/FA AAPL`, `AAPL /FA`, `/COMP SPY QQQ`, `/? apple`). Commands require `/`;
  bare words always select symbols in the primary line. The graph popup uses bare commands instead. Press `?` or run `/HELP` for help.
- Search across asset types: `/? gold`, `/? natural gas`, `/? bitcoin`, `/? crypto`, `/? EURUSD`,
  or any Yahoo-supported ticker/name (stocks, ETFs, mutual funds, futures, FX and indexes).
  Filter by type with `/?brent _t=FUTURE`, `/?bitcoin _t=CRYPTO`, or `/?gold _t=ETF`.
  Types: EQUITY, FUTURE, CRYPTOCURRENCY (or CRYPTO), CURRENCY (or FX), ETF, INDEX, MUTUALFUND, OPTION (or OPTIONS).
  Filter every search column with `_s=symbol`, `_n=name`, `_t=type`, `_p=source` (local/alias/Yahoo/EDGAR).
  `/??` lists values and examples. Filters ignore case; use commas for alternatives and quotes for multiword values.
  Stored index matches appear first; Yahoo and SEC discovery refresh the index. Enter opens the selected symbol.
- `/FRED? inflation _f=M _a=SA` searches public FRED pages without an API key. `/FRED??` lists series ID,
  title, frequency, units, adjustment and date filters. Enter charts the series. Searches inspect up to four result
  pages and display at most 100 matches; filters also apply to previously indexed series.
- FRED descriptions: `/FRED DGS10 /DES`, `/DES` from a FRED graph, `/DES FRED:DGS10`, or
  `/DES _fred=DGS10` shows source/release, units, frequency, updates, observed history and explanatory notes.
  Press `g` to return to the graph; PageDown moves focus to the notes.
- HOME previews are fixed at `1D / 5m`; open the selected security to change chart settings.
- Options are hidden from search unless `_t=OPTION` or `_t=OPTIONS` is specified. Use `/? AAPL _t=OPTIONS`
  for up to 100 contracts near spot at the nearest expiry, or add `YYYY-MM-DD` to select an expiry.
  Enter opens the contract's normal price chart. `/OMON AAPL` also opens contracts: `c`/`p` selects call/put,
  arrows select a strike, Enter charts it. Yahoo history availability varies by contract.
- Compare any supported security (including options) with FRED or earnings-surprise series:
  `/COMP AAPL _fred=DGS10`, `/COMP BTC-USD FRED:UNRATE`, or `/COMP AAPL _ern=MSFT _mode=REAL`.
  `_fred=` accepts comma-separated series; `_ern=` accepts comma-separated tickers.
  `NORMALIZED` starts all lines at 0% on their first common observation; `REAL` preserves native values on
  one scale; `STACKED` overlays up to eight series, each with an independent, color-matched scale on the left.
  Numbered headers link each scale column to its trace. `STACK` is also accepted as a mode name.
  Use `_mode=STACKED` at navigation or `MODE STACKED` in the popup (`.MODE STACKED` on the main line).
  All three modes support up to eight series. Sources retain their observation bounds; missing
  values and dates are shown explicitly. `CMP OFF` restores the originating graph.
- Options monitor: `.` opens commands; `HELP` lists values and cached expiries. Use
  `/OMON AAPL .EXP NEXT .SIDE PUT .WIDTH 8`, `.EXP 2026-11-20`, `.STRIKE ATM`, `.STRIKE 350`,
  `.STRIKE NEXT`, or `.UNDERLYING MSFT`. `EXP LIST` shows available dates; `WIDTH` accepts 1–100 strikes
  per side. `OPEN [CALL|PUT] [strike]`, `GRAPH [range] [interval]`, `HIST [range]`, and `BOOK` navigate
  the selected contract; `REFRESH` refreshes its chain. Main-line commands use dots, e.g. `.HIST 1M`.
  `CHAIN` on a contract graph returns to its underlying, expiry, side and strike. Existing keyboard
  selection controls remain available. History depends on Yahoo availability, including for expired contracts. Empty chains are labelled; use
  `EXP NEXT` to select another expiry or `REFRESH` to retry the attempted chain.
- `/DAT` inherits the active security chart's selected range and interval and shows volume, averages, high/low,
  returns and other statistics for that range. Override with `/DAT AAPL 1M 15m`; `1`–`9` and `[`/`]` change the selection.
- `/BOOK AAPL` shows Yahoo's available best bid/ask and reported sizes; full equity depth is unavailable from Yahoo.
  `/BOOK BTC-USD` shows a Coinbase snapshot of up to 20 aggregated levels per side. Snapshots refresh every 15 seconds.
- Optional API keys: copy `.env.example` to `.env` in the project root, then fill in `ALPACA_API_KEY` and
  `ALPACA_API_SECRET` for the IEX overlay. `.env` loads automatically for both launch commands, including from other
  working directories. Existing environment variables take precedence. Restart after editing; `.env` is Git-ignored.
  Keys are validated at startup; missing or rejected Alpaca keys leave the overlay disabled. Other features work without keys.
- Settings: `~/.freeterm/config.toml`, `aliases.toml`, `watchlist.json` (created on first run). Set `[sec].user_agent`
  to include a real contact; SEC asks for one.
- Layout: `Ctrl+1/2/4`, or `/LAYOUT 1|2|4` (macOS Terminal doesn't send Ctrl+digit).
- Charts automatically use pixel graphics in iTerm2 (Sixel) and Kitty, with text fallback elsewhere.
  Candles, indicator curves, and MACD histograms use pixel geometry independent of terminal text size.
  Price, comparison, rates/spreads, Treasury curve, and earnings charts share the same scene model.
  In `[chart]`, `renderer = "auto"` can be overridden with `"text"`, `"sixel"`, or `"tgp"` (restart to apply).
  `font_px = 0` follows terminal cell height; a positive value sets the raster label size.
  `font_path` can point to your terminal font file (macOS defaults to Menlo); text charts use the terminal font.
  With `font_px = 0`, raster labels match the terminal’s reported character-cell size and follow font/viewport changes.
  Positive `font_px` values explicitly override auto sizing; `font_path` matches the font face when needed.
  Stacked scales retain readable label sizes; a pane too small to fit them asks to be enlarged.
  Axis numbers use the brighter foreground color. Candle width fits the complete selected range by default;
  zoom never combines candles. The quote header shows the return over that selected range.
- Press `.` on a graph for its command popup. Enter commands directly, without `/`; `HELP` or `?` opens the command
  list, and Esc/Enter/q returns to typing. Use `IND VWAP RSI MACD`, `IND SMA _p=20,50`, or `IND CLEAR`.
  Security price chart indicators include: `SMA`, `EMA`, `BB` (`BOLLINGER`), `VWAP`, `RSI`, `MACD`, `VOLUME` (`VOL`) and `IEX`.
  `IND VOLUME RSI ON` enables both; `IND CLEAR` clears technical indicators, volume and the global IEX overlay.
  Volume uses `IND VOLUME` / `IND VOL`; the `v` shortcut is removed.
  IEX requires validated keys. Inside an IND list, `IEX` is an indicator; `.IEX` starts a separate chained command.
  Ordered parameter groups use semicolons, skipping VWAP/VOLUME/IEX: `IND VWAP RSI MACD _p=14;12,26,9`. Repeating an active
  indicator updates changed parameters (or restores defaults when omitted); unchanged parameters toggle it off.
  Explicit `ON`/`OFF` applies to the whole list.
  `CMP AAPL AMZN` compares those securities and `CMP OFF` restores the original price graph.
  `IEX ON|OFF|TOGGLE` controls the global validated IEX overlay. Indicator, comparison, timeframe and size commands
  use `.` on the primary command bar and affect its active graph; other panes keep their own settings.
  `/` opens a view, bare words identify securities, `.COMMAND` edits the view, and `_key=value` supplies arguments.
  `AAPL .TIME 1Y .SIZE 2wk` opens AAPL first, then applies both commands in order.
  `AAPL /GP .IND BB` opens a specific view with an indicator; `.IND BB` alone edits the current graph.
  Legacy slash graph commands remain accepted.
- On a graph, Ctrl+Left/Right moves the selection halfway toward that visible edge, rounding toward the edge.
  Plain arrows still step one datapoint. `GOTO 20261023 1030` or `GOTO 2026-10-23T10:30` selects the containing candle;
  `GOTO LEFT 50`, `GOTO RIGHT 25`, `GOTO -50` and `GOTO +25` shift by active candles.
  Use these in the `.` popup, or `.GOTO ...` on the main line. Without a selection, offsets start at the visible
  rightmost candle. Dates use New York time unless an explicit offset/Z is supplied; gaps use the previous observed
  point and out-of-range dates fail. Off-screen targets pan into view while preserving zoom; counts clamp at data edges.
- In the popup, `TIME 2Y` or `TIME 18M 1d` sets a custom complete time window, using cached history and provider limits.
  `SIZE 2d`, `SIZE 2 weeks`, or `SIZE 0.5 years` sets each candle/data point interval while preserving the window.
  Fixed durations span 1 minute–366 days in whole minutes; calendar durations span 1–12 whole months (`0.5y` = `6mo`).
  Use `m` for minutes and `mo` for months. History availability also limits valid combinations; the interval must be
  shorter than the window. `SIZE RESET` uses the window default.
  Chain graph commands in order: `TIME 2Y SIZE 2d IND VWAP RSI`. On the primary bar, use `.TIME 2Y .SIZE 2d`.
  A failure stops the chain, retaining earlier changes. Put `HELP` or `?` last.
  `TF 20240101 20240630` selects dates; `TF 20240101 3M` selects a start plus duration.
  `TF 20261023 0930 4h` selects an intraday window. ISO dates/timestamps also work; times use New York time.
  Date-only ends include the day; timestamp ends are exclusive. Source history limits still apply.
  HELP/completion lists commands for the graph type: FRED supports TIME/TF and native observations;
  comparisons support TIME/TF/CMP/MODE and SIZE when securities are included. Indicators and IEX apply only to eligible price charts.
  Zoom centers on the highlighted candle and preserves that candle’s crosshair.
  Basic navigation keys remain: `1`–`9`, `[`/`]`, `t`, `o`, arrows, `+`/`-`, `h`/`l`, and `0`.
- News: Enter on a headline in `/CN AAPL` or `/TOP` opens a text reader. Use `j/k` to scroll, `/` to search,
  and `q`/Escape to return. If article text is unavailable, the reader shows the feed summary when available.
  Press `o` to request the original in a browser; the keyboard confirmation uses `y`/`Y` to open and
  `n`/`N`, Escape, or Enter to cancel. No mouse or buttons are needed.
- Quit: Ctrl+Q opens a keyboard confirmation popup. Press Ctrl+Q again to quit, or `n`/`N` or Escape to cancel.

## Status
All functions in the brief are built and smoke-tested against live data, with optional Alpaca authentication/streaming also checked. IEX edge cases and physical-terminal graphics QA remain in the handoff.
The TODO list is in `HANDOFF.md` under **Open items**.

## Test
`FREETERM_HOME=/tmp/ftsmoke .venv/bin/python -W ignore tests/smoke.py`

## Data pipeline

Views use typed queries through `DataHub`; provider code and parsers live outside Textual.
`~/.freeterm/data/observations.sqlite3` stores OHLCV/FRED observations, structured XBRL facts,
filing indexes, news and an FTS5 search index in separate tables, with source, observation/fetch
timestamps and content versions. Rolling history refreshes retain captured observations.
News remains raw headlines/summaries and readable article text; no sentiment analysis runs.

Disposable provider responses and derived results live separately in `cache/pipeline.sqlite3`.
Provider policies retain rate limiting/backoff. Analysis keys include parameters, input versions
and calculation versions; chart windows, indicators, comparisons and DAT reuse these results.
Ctrl+R refreshes stored inputs and bypasses older dependent results; OMON REFRESH targets its
expiry/chain. Failed fetches expose STALE fallback metadata and status warnings.

Cleanup runs at startup and at most every ten minutes: 1,500 datasets / 256 MiB of compressed
content, two million OHLCV rows total, 100,000 observations per series, two years without access,
90 days of news and 50,000 search records (one year). Provider and analysis caches each have a
64 MiB budget (1,000/512 entries); expired responses and 30-day analysis results are removed.
SQLite overhead/WAL files can temporarily exceed these logical budgets. Settings/watchlists
are preserved; legacy JSON caches are left untouched and are no longer read.
