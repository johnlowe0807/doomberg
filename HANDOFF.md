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
| `pixelcharts.py` | Shared `Scene`/`Panel`/`Trace` model, pixel geometry rasterizer, Sixel/Kitty transport, text fallback, queued worker rendering and pane-local image cleanup |
| `boards.py` | `QuoteBoard` (WEI), CMDTY, CRYPTO, MOST, FXC matrix, HOME, GC, BTMM, AUCT, `IexView` (IEX toggle) |
| `depth.py` | DES, HP, CN, TOP (RSS + yfinance news), OMON (local Black-Scholes greeks), `SearchView` (`/? text`) |
| `news.py` | News article extraction, `ArticleReader` (reuses filing pager controls), `BrowserConfirm` (keyboard-only: Y opens; N/Esc/Enter cancels). HTTP via DataHub; extraction in a worker thread; article text cached for 1 hour. |
| `fundamentals.py` | EDGAR ticker→CIK, companyfacts → FA (XBRL `frame`-based), submissions (incl. old pages) → FIL, `Pager` (full-screen reader), ERN (+8-K 2.02 → EX-99.1), EVTS |
| `live.py` | Coinbase ticker websocket, Alpaca IEX websocket and REST polling, `iex_eval` (overlay math) |
| `theme.tcss` | the palette (single source of truth) |

## Gotchas learned the hard way
- All chart views build geometry scenes; `ChartSurface` handles transport. Auto uses Sixel/Kitty where detected, with
  text fallback elsewhere and in headless tests. Configure `[chart].renderer` and `font_px`.
- **plotext subplots broadcast.** The text fallback must draw on individual panel objects after `subplots()`.
  `pixelcharts.render_text` also gives every panel the same x-range and y-label width; pixel panels share plot bounds.
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
- Month/year price ranges use calendar offsets (including leap years), rather than approximate day counts.
- Price chart headers report the return from the first valid close to the last valid close in the full selected range,
  labelled with that range. Zoom, pan and crosshair do not change the headline baseline; tables retain their daily moves.
- **Never combine bars, under any circumstances** (user rule: "rendering candles correctly should be the priority").
  Every candle is exactly one bar of the selected interval. `fit_view()` now fits the complete selected range; geometry
  scales candle spacing instead of silently cropping old bars. Reset (`0`), range/interval selection and automatic
  resize preserve the whole range. Zoom-out can return to the complete range, with no minimum spacing cap.
  `candle_px`/`candle_cols` settings are obsolete. Zoom and pan still select native bars without aggregation.
  Candle wicks/bodies, indicators, volume and MACD bars share one x-axis. The volume panel is hidden for indexes.
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
- [x] Selected-range price indicator and candle defaults (2026-10-24): implemented the price movement requirement matching item 2 in `features-list.md` (`todo.md` was absent). Header change/% now uses first/last valid closes across the selected range and stays stable during zoom/pan/crosshair inspection. Default/reset/range changes show all native candles across the complete range; removed fixed-spacing crop/zoom caps and obsolete spacing defaults. Calendar month/year boundaries use date offsets. Verified all range defaults, exact OHLC preservation, pixel full-year output, zoom/reset/resize, return calculations and zero/missing prices; all 30 supported range/interval pairs reused four base downloads without further requests. PTY startup/confirmed exit and the full 27-command live smoke passed, with only the expected missing-Alpaca-key refusal warning.
- [x] Geometry chart migration (2026-10-24): price/intraday, comparison, FRED/spreads, Treasury curves, earnings and home charts now share geometry scenes. Pixel output rasterizes candles, curves, indicators, signed histograms and crosshairs independently of terminal text; the text fallback consumes the same scene. Latest-frame workers, off-thread Sixel encoding, viewport/cell-size changes, pane-specific Kitty cleanup and automatic fallback are implemented. Visually inspected a four-panel raster preview; checked both image transports, four-pane controls/resize/help/close, exact native OHLC preservation, malformed/empty viewport edges, and fallback. PTY startup/confirmed exit and the full 27-command live-data smoke passed; only the expected missing-Alpaca-key refusal warned. Physical iTerm2/Kitty visual QA remains an open item.
- [x] Expanded commodity board (2026-10-24): CMDTY now covers 28 curated Yahoo futures, capped at 50 symbols, sorted by reported contract volume from the most recent daily bar before today's New York date. The volume column includes its date. Daily bars supply quotes and ranking in one sequential batch with a shared five-minute memory/disk cache; Ctrl+R forces a new batch. App startup/confirmed exit, targeted CMDTY/MOST live smoke, and focused sorting/date/cache/cap checks passed. Live snapshot contained 28 quotes and 28 volumes dated 2026-10-23. Reaching 50 rows by adding commodity ETFs is pending the user's preference.
- [x] Expanded search names (2026-10-23): merge duplicate symbols before display, then enrich up to 100 results in one cached Yahoo quote request through `hub.yf_call` (uses yfinance `YfData` for authenticated batch quotes). Prefer `longName` and instrument `quoteType` over abbreviated search/SEC issuer metadata; keep original alias/name matching and fuzzy matches. Removed the table's 80-character name truncation. Live checks verified full Brent futures name and BOIL fund name/ETF type; oil, filtered Brent and filtered gold smoke passed without warnings. Existing rows remain available if enrichment fails.
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
- [ ] Visually check Sixel in iTerm2 and image output in Kitty on physical terminals (font-size changes, modal clipping,
      resize and pane closure). Protocol widgets, generated frames and pane-specific cleanup passed automated checks.
- [ ] FA uses calendar-aligned XBRL frames (CY2023 etc.), so fiscal years that end mid-year are labelled by the nearest
      calendar year. Consider labelling by `fy` from 10-K rows instead.
- [ ] Optional from brief: EX-27 financial data schedule parsing for late-1990s filings (not built).
- [ ] MOST ranks a fixed 40-name large-cap universe (deterministic). `yf.screen("day_gainers")` could replace it with a fallback.
- [ ] GC custom-date overlay uses FRED DGS* series (back to 1962 for most tenors). The treasury.gov par-curve CSV is not used.
- [ ] Text fallback OHLC draws one plotext series per bar and can be slow on large windows; pixel rendering uses geometry directly.
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
