# Handoff: freeterm build state

All 8 phases in `CLAUDE.md` have a working implementation. Every function opens and loads real data in the headless
smoke test (`tests/smoke.py`). What's left is polish and verification, listed under **Open items**.

## Layout (all in `freeterm/`)
| File | Contents |
|---|---|
| `config.py` | `Config` (`get/set(section,key)`, auto-saves `~/.freeterm/config.toml`), aliases (`aliases.toml`), watchlist (`watchlist.json`) |
| `data.py` | `TTLCache`, `DiskCache(ns)`, `RateLimiter` (429 backoff, status text), `Quote` (`.tag` LIVE/DLY/STALE), `Provider` interface, `YFProvider`, `DataHub` (the only thing views call: `quotes`, `history`, `yf_call`, `http`, `validate_alpaca`, `live_fresh`, `status`) |
| `app.py` | `FreeTerm` app, command parser, `Pane` (per-pane Esc history), `View` base, `MonView`, `HelpView`/`HelpScreen` (`?`), `PromptScreen`, NYSE holidays, stream startup |
| `dialogs.py` | Shared buttonless `ConfirmScreen` styling and cancel keys, plus `QuitConfirm`. Browser confirmation subclasses the same dialog. |
| `charts.py` | `ChartView` (GP) + `GIPView`, `CompView`, `FredView`, `SprdView`, `QuoteChartView` (bare security). Indicators (SMA/EMA/BB/VWAP/RSI/MACD), FRED CSV fetch, IEX banner and overlay |
| `boards.py` | `QuoteBoard` (WEI), CMDTY, CRYPTO, MOST, FXC matrix, HOME, GC, BTMM, AUCT, `IexView` (IEX toggle) |
| `depth.py` | DES, HP, CN, TOP (RSS + yfinance news), OMON (local Black-Scholes greeks), `SearchView` (`/? text`) |
| `news.py` | News article extraction, `ArticleReader` (reuses filing pager controls), `BrowserConfirm` (keyboard-only: Y opens; N/Esc/Enter cancels). HTTP via DataHub; extraction in a worker thread; article text cached for 1 hour. |
| `fundamentals.py` | EDGAR ticker→CIK, companyfacts → FA (XBRL `frame`-based), submissions (incl. old pages) → FIL, `Pager` (full-screen reader), ERN (+8-K 2.02 → EX-99.1), EVTS |
| `live.py` | Coinbase ticker websocket, Alpaca IEX websocket and REST polling, `iex_eval` (overlay math) |
| `theme.tcss` | the palette (single source of truth) |

## Gotchas learned the hard way
- **plotext subplots broadcast.** Once a figure has `subplots()`, calling `plt.plot/bar/...` on the *figure* draws into
  every panel (`subplot()` only changes what the figure reports as active). Always draw on the panel objects:
  `P = plt.subplot(1, 1); P.candlestick(...)`. `ChartView._draw` does this; GC and ERN have no subplots.
- Panels only line up when their y-tick labels share one width and they share an x-range. `_draw` collects `yjobs`,
  right-justifies to the widest label, and sets `xlim(-0.5, n-0.5)` on each panel.
- `PlotextPlot.render` is wrapped in `charts.py` (`_safe_render`), so a plotext exception shows a message instead of crashing.
- Pane focus: Tab/Shift+Tab are **priority** app bindings, because Screen's built-in focus_next would otherwise win. The
  command line is outside the panes, so `App.active_pane()` remembers the last focused pane (`mark_active`, `.active` CSS class).
- The command Input uses `select_on_focus=False`. Otherwise the first typed letter, inserted when focus jumps, gets replaced.
- Ctrl+Q opens `QuitConfirm`, using the same popup as browser confirmation. Ctrl+Q again confirms; N/n or Escape cancels. Y and Enter do not quit. There is no timer. Cancel restores the previous screen and focus. Ctrl+C shows quit instructions.
- **Chart bars are derived, not fetched per view** (user asked to avoid rate limits when flipping). `DataHub.bars(sym, rng, iv)`
  picks a base download from `data.BASES` (1m/7d, 5m/60d, 1h/730d, 1d/max), caches it, then resamples and slices
  locally. `ChartView` calls `hub.prefetch_bars()` after first paint, so a chart costs 4 requests and every later
  range/interval flip costs 0 (`tests/ranges.py` asserts this). Concurrent requests for the same base share one download
  (`_inflight`). Availability comes from `base_for()`; there is no separate interval-limit table anymore.
- 1D/5D are trading sessions for exchange-hours symbols, and a rolling 24h/5d window for symbols that trade on weekends (crypto).
- **Never combine bars, under any circumstances** (user rule: "rendering candles correctly should be the priority").
  Every candle is exactly one bar of the selected interval. `fit_view()` shows the most recent native bars that fit at
  `[chart].candle_cols` columns each (default 2, which gives a gap), and the info line says `showing last N/M`. Zoom-out
  (`-`) is capped at one candle per column and says so. `_draw` trims to the newest `cols` bars if the window ever holds
  more (e.g. after a resize), and never aggregates. For more history, pan (h/l) or pick a coarser interval (]).
  plotext candles are 1 column wide, so when bars have ≥2 columns `_draw` draws candles itself
  (wick = `plot`, body = `bar(minimum=...)`) and the bodies widen. `fit_view` waits for layout (width 0 on a cache hit)
  and refits on resize until the user pans or zooms. The volume panel is hidden when the symbol has no volume (indexes).
- **Yahoo 1m volume has bogus prints.** For NVDA, summed 1m volume came to 502M against an official 80M for the day, with single
  minutes claiming 30–50M. `data.flag_bad_volume` (1m base only) blanks bars above 100× the session median and sets
  `BadVol`. The chart then says "N bad Yahoo volume prints hidden". Don't apply it to 5m (clean) or crypto (false positives).

## Conventions
- New function: subclass `View`, set `FUNC`, `hints` and `refresh_secs`. Implement `compose()` and `async load()` (fetch via `self.hub`). Use `key_x()` / `on_key` for single keys. Then `VIEWS.update({...})` at the bottom of the module, and import the module in `app.register()`.
- Uppercase letters start symbol input; `/` starts command input. Both jump to the command line (`:` still focuses it). Commands require `/`: `/GP AAPL` or `AAPL /GP`, `/MON`, `/LAYOUT 2`; security search is `/? apple`. Bare words always mean symbols, including names such as `TOP` that also name commands. Internal `run_command` calls use the same syntax. Single-key controls must be lowercase; modal screens retain their own keys (including pager `/` search and `G`/`N`).
- Saved legacy startup commands migrate once to slash syntax (`general.command_syntax = "slash"`). Autocomplete separates slash commands from symbols. Help text renders without markup so `[/FUNC]` is literal.
- Blocking libraries go through `hub.yf_call` / `asyncio.to_thread`. HTTP goes through `hub.http(name, url, per_sec, headers, ttl, disk_ns, disk_age)`, which registers its limiter in the status bar automatically.
- Fail soft. Exceptions in `load()` become a status-bar warning, and missing data renders `N/A`.

## Alpaca / IEX rule (from the user)
If the keys are missing or fail to validate, the overlay cannot be turned on. This is enforced in three places:
`DataHub.validate_alpaca()` (HTTP 200 required, otherwise `alpaca_ok=False` and `[iex].enabled` forced off),
`App.start_streams()` (Alpaca tasks are only started when validation passes), and `IexView` (refuses with the reason).
`live.alpaca_stream` also disables the overlay if websocket auth fails later.

## Verified (headless, live data, 2026-10-23)
`tests/smoke.py` opens every command: charts (all keys), WEI/CMDTY/CRYPTO/MOST/FXC, GC/BTMM/AUCT/SPRD/FRED,
DES/HP/CN/TOP/OMON, FA (2,270 AAPL filings in FIL), ERN, EVTS, `/? apple`, an unknown ticker (did-you-mean), IEX refusal,
MON and HOME. The Coinbase stream showed `cb:LIVE 12`. SEC accepted the default User-Agent.

## Completed updates
- [x] Search type filters (2026-10-23): `/?brent _t=FUTURE` separates standalone `_key=value` filters from provider query text. `_t` accepts canonical quote types and aliases (CRYPTO, FX, STOCK, FUTURES), case-insensitively; applies before deduplication/limit to local, SEC, Yahoo and fuzzy results. Invalid/duplicate filters show a warning. Filter-only input browses local symbols. Verified parsing, literal non-filter terms, incremental results, filtered UI rows and Enter navigation. Live smoke passed: Brent futures 3, gold ETFs 2, Bitcoin crypto 2, unfiltered Apple 9, no warnings.
- [x] Asset-wide `/?` search (2026-10-23): added `symbols.py` with immediate local futures/commodity, crypto (full names), FX, index, ETF and alias matches. Yahoo searches up to 100 results across supported quote types, including mutual funds and international listings; SEC runs independently. Results rank exact symbols first, deduplicate by canonical ticker, and show type/source. Live smoke passed: gold 100, Bitcoin 23, EURUSD 5, Apple 9, without warnings. Offline checks covered commodity/crypto categories, natural gas, Ripple, exact tickers, duplicate removal, fuzzy Yahoo results and SEC failure.
- [x] Local section navigation (2026-10-23): ERN uses `[` / `]` to switch between EPS and 8-K tables; BTMM uses the same keys for rates and ETFs. Updated hints; Tab/Shift+Tab retain global pane navigation. Headless keyboard checks covered both directions, wraparound, and global pane switching. App startup/confirmed exit and targeted ERN/BTMM live smoke passed (144/30 rows, no warnings).
- [x] Shared confirmation popup (2026-10-23): Ctrl+Q now opens the same buttonless dialog style as browser confirmation. Ctrl+Q again quits; N/n or Escape cancels. This supersedes the earlier timed quit notification. Verified no timeout, Y/Enter cannot quit, cancellation restores input/reader focus, and browser confirmation still works when a quit popup is opened over it. App startup/confirmed exit and targeted `/CN AAPL`, `/TOP`, `/HELP` live smoke checks passed.
- [x] Keyboard-only browser confirmation (2026-10-23): removed buttons; popup displays Y to open and N/Esc/Enter to cancel (letters accept either case). Verified each key, return to the reader, and no leakage into command input. App startup/two-press exit and targeted `/CN AAPL`, `/TOP`, `/HELP` live smoke checks passed.
- [x] Terminal news reader and quit confirmation (2026-10-23): Enter in `/CN` or `/TOP` opens article text (or the available feed summary) in a searchable pager. `o` requests the browser; a confirmation popup defaults to Cancel. Live Yahoo text/transcript, another publisher's article, and blocked-publisher summary fallback were verified. Ctrl+Q requires a second press within 3 seconds, using the Ctrl+C-style notification; Escape cancels. Keyboard checks covered browser approval/cancellation, reader search/return, quit cancellation/expiry, and modal/input focus. App startup and two-press exit plus the full live-data smoke test passed.
- [x] Slash commands and symbol separation (2026-10-23): uppercase letters and `/` focus the command line; commands use `/`, security search uses `/? apple` or `/?apple`. Updated internal navigation, autocomplete, help, startup migration, and smoke inputs. App startup/exit and the full live-data smoke test passed (search returned 9 rows); focused headless checks passed for keyboard routing, ticker/command collisions, pane cycling, modal input, help, history, autocomplete, and startup migration. Alpaca remains untested without keys.

## Open items
- [ ] **Alpaca IEX path is untested** (no keys were available). Test with real free-plan keys: ws auth message shapes, the
      30-symbol cap, the banner text and the dashed line, and REST polling for symbols beyond the cap.
- [ ] Verify interactive behavior in a real terminal (macOS Terminal + iTerm): price flash, focus colors, Tab between panes,
      and that `Ctrl+1/2/4` arrive (Terminal.app usually doesn't send them; `/LAYOUT n` is the fallback).
- [ ] FA uses calendar-aligned XBRL frames (CY2023 etc.), so fiscal years that end mid-year are labelled by the nearest
      calendar year. Consider labelling by `fy` from 10-K rows instead.
- [ ] Optional from brief: EX-27 financial data schedule parsing for late-1990s filings (not built).
- [ ] MOST ranks a fixed 40-name large-cap universe (deterministic). `yf.screen("day_gainers")` could replace it with a fallback.
- [ ] GC custom-date overlay uses FRED DGS* series (back to 1962 for most tenors). The treasury.gov par-curve CSV is not used.
- [ ] OHLC chart type draws one plotext series per bar. It's fine for ≤ ~300 bars but slow on MAX/1wk.
- [ ] Market holidays are hardcoded for 2025–2027 (`app.NYSE_HOLIDAYS`). Early closes are not modeled.

## Smoke test (run after every change)
```
FREETERM_HOME=/tmp/ftsmoke .venv/bin/python -W ignore tests/smoke.py            # all commands
FREETERM_HOME=/tmp/ftsmoke .venv/bin/python -W ignore tests/smoke.py AAPL /GC    # just some
```
Each line prints `command -> pane title, row count, info line, last warnings`. A Traceback means a regression.
`tests/panes.py` checks Tab cycling with 4 and 2 panes, commands landing in the active pane, autocomplete, and `AAPL AMZN`.
`tests/interact.py` drives the keys: FA toggles and pre-2009 year jump → FIL → pager (`G`, `/` search, `n`, TOC), `?` overlay,
OMON expiry stepping, and ERN 8-K → EX-99.1. Both passed on 2026-10-23.
