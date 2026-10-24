# freeterm: a keyboard-driven, retro terminal market monitor (macOS, Python)

Build a Bloomberg-style TUI for market data. It is NOT a clone: free data only, same look and workflow. Fast, dense, comprehensive, entirely keyboard-controlled, and not ugly.

## Execution rules (token budget is tiny: follow strictly)
- Do not explore, ask questions, write tests, or write docs beyond a short README (run steps + TODO list of unfinished items).
- Write files in few, large writes. No refactor loops.
- Build in the phases below. The app must RUN after each phase. If budget runs low, stop at a working state and list what's unfinished in the README.
- Pick sensible defaults for anything unspecified.

## Stack
- Python 3.11+, `uv` or venv. Run with `python -m freeterm` (also install a `ft` entry point).
- `textual` (UI), `textual-plotext` + `plotext` (charts), `yfinance`, `pandas`, `numpy`, `httpx`, `websockets`, `feedparser`, `beautifulsoup4` + `lxml` (filing text).
- No TA-Lib or scipy. Compute indicators and Black-Scholes greeks with numpy/math.
- All network calls async or in workers. NEVER block the UI thread. First paint under 2s using cached data.
- Settings in `~/.freeterm/config.toml` (thresholds, toggles, theme). Watchlist in `~/.freeterm/watchlist.json`.

## Data sources (priority: 1. free, 2. live, 3. least rate-limited; prefer no key)
| Need | Source | Key? |
|---|---|---|
| Equities, ETFs, indexes, futures/commodities, FX, history, options chains, news, earnings dates and EPS surprise | `yfinance` (batch with `yf.Tickers` / `yf.download`) | No |
| Live US equity prices (IEX feed) | Alpaca market data: REST latest trade/snapshot (`feed=iex`) plus websocket `wss://stream.data.alpaca.markets/v2/iex`. Free plan limits symbols and calls, so respect them (verify current limits in Alpaca docs) | Yes, env `ALPACA_API_KEY` / `ALPACA_API_SECRET` (optional) |
| Live crypto | Coinbase Exchange public websocket (ticker channel) | No |
| Treasury yield curve (1mo to 30y), daily | treasury.gov daily par yield curve (CSV/XML); intraday proxies via yfinance `^IRX ^FVX ^TNX ^TYX` | No |
| Long rate and credit history | FRED CSV endpoint `https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>` (no key). Series: `DGS1MO DGS3MO DGS2 DGS5 DGS10 DGS30` (nominal), `DFII10` (10y TIPS real), `T10Y2Y` (2s10s), `DFF` (fed funds), `SOFR`, `BAMLC0A0CM` (IG spread), `BAMLH0A0HYM2` (HY spread), `MORTGAGE30US` | No |
| Treasury auctions | US Treasury FiscalData API (`api.fiscaldata.treasury.gov`, auctions endpoint) | No |
| Bond ETFs (tradable proxies) | yfinance: `SHY IEF TLT TIP LQD HYG AGG MUB EMB BND` | No |
| Fundamentals back to ~2009, structured | SEC EDGAR XBRL `data.sec.gov/api/xbrl/companyfacts/CIK##########.json`. Ticker to CIK via `sec.gov/files/company_tickers.json` | No (send descriptive User-Agent, respect ~10 req/s) |
| Older fundamentals (pre-XBRL) | The actual filings: 10-K / 10-Q full documents from EDGAR submissions JSON, rendered as text | No |
| Earnings press releases | EDGAR 8-K Item 2.02 filings and their EX-99.1 exhibit | No |
| News | yfinance news plus RSS via feedparser | No |

Rules:
- App must work fully with zero keys. Alpaca only enables the IEX overlay.
- Provider abstraction: `Provider.quote()`, `.history()`, `.options()`, `.stream()`. Fallback chain per asset class. Fail soft: show `N/A` and a status-bar warning, never crash.
- Per-provider rate limiter plus TTL cache (quotes ~5s, history ~60s, fundamentals and filings: persistent disk cache, since they rarely change; options ~30s). Batch requests. Back off on 429s.
- Every quote shows a source tag and freshness: `LIVE` (stream), `DLY` (polled/unofficial), or `STALE`. yfinance is unofficial, so never imply it is exchange-grade.

## Look and feel (NOT orange)
Phosphor-terminal style, cool palette, defined once in a Textual CSS file:
- Background `#000000`. Default text ice-cyan `#5FD7FF`. Dim/gridlines `#3A6B80`.
- Headers and title bars: bold white on deep blue `#0B3D91`.
- Command prompt, selection, cursor: magenta `#FF5FD2`.
- Up `#00FF87`, down `#FF4D6D`. Warnings/stale: yellow `#FFD700`.
- Monospace only, box-drawing borders, zero decoration.
- Layout: top = command line plus clock plus market status. Middle = 1, 2 or 4 tiled panes. Bottom = hint bar plus status (data sources, rate-limit state, latency, IEX toggle state).
- Dense tables, right-aligned numbers, brief flash on price change, up/down arrows.

## Command language (Enter = GO, Esc = back, Up/Down = history, Tab = autocomplete)
Grammar: `<SECURITY> /<FUNC> [args]` or `/<FUNC> [args]` (e.g. `/GP AAPL`). A bare security opens its default screen (quote + chart).
- Uppercase letters open the command line for symbols; `/` opens it for commands. Commands require `/`, so bare words always select symbols, even when they match a function name. Function names below omit the `/` for brevity.
- Securities: `AAPL`, `AAPL US` (suffix accepted and ignored), `/? text` = fuzzy search.
- Aliases in an editable `aliases.toml`: SPX=^GSPC, INDU=^DJI, CCMP=^IXIC, RTY=^RUT, VIX=^VIX, UKX=^FTSE, DAX=^GDAXI, NKY=^N225, GOLD=GC=F, CL1=CL=F, NG1=NG=F, EURUSD=EURUSD=X, BTC=BTC-USD, USGG10YR=^TNX.
- Functions:
  - Core: `DES` profile, `GP` price graph, `GIP` intraday, `HP` historical table, `CN` company news, `MON` watchlist, `COMP A B C` normalized comparison chart.
  - Boards: `WEI` world indices, `MOST` movers, `CMDTY` commodities, `FXC` FX matrix, `CRYPTO`, `TOP` news.
  - Options: `OMON`.
  - Bonds: `GC` Treasury curve, `BTMM` rates and money-market board (bills, notes, fed funds, SOFR, bond ETFs), `SPRD` credit spreads (IG/HY) and 2s10s history, `AUCT` recent Treasury auctions.
  - Fundamentals: `FA` financial statements, `ERN` earnings, `FIL` filings, `EVTS` earnings calendar for watchlist.
  - Other: `IEX` toggle live overlay, `HELP`.
- Unknown input gives a "did you mean" suggestion.

## Bonds
- `GC`: current curve plus overlays for 1 month ago, 1 year ago, and a chosen historical date (from FRED/treasury.gov). Show 2s10s and 3m10y spreads. Toggle table or chart.
- `BTMM`: single-screen board: bill/note/bond yields with daily change, fed funds, SOFR, TIPS real yield and breakeven (nominal minus real), mortgage rate, bond ETF prices.
- `SPRD`: IG and HY spread time series with range controls, same chart controls as `GP`.
- `AUCT`: recent auction results (bid-to-cover, high yield) from FiscalData.
- Any FRED series can be charted with `FRED <SERIES>`.
- Not available free: dealer quotes, swaps, corporate bond trade-level data. Do not fake them. Say so on the screen.

## Fundamentals and earnings (`FA`, `ERN`, `FIL`)
- `FA`: income statement, balance sheet, cash flow. Toggle annual/quarterly, scroll across periods, jump to a year (`FA` then type a year). Source order: EDGAR XBRL companyfacts (deepest history, standardized line items) then yfinance fallback.
- Be honest about depth: structured XBRL exists from roughly 2009 onward. Anything earlier has no free structured source, so when the user jumps to an earlier year, the screen says so and offers `FIL` for that year's 10-K.
- `FIL`: list all filings (10-K, 10-Q, 8-K, etc.) with form filter and year jump, going as far back as EDGAR's submissions index reaches (including the older paginated files). Enter opens the filing as readable text in a full-screen pager: `j/k` scroll, `/` search, `n/N` next/prev match, `g/G` top/bottom, `t` table-of-contents jump on "Item" headings if cheap to implement.
- Optional if cheap: for late-1990s filings, parse the EX-27 financial data schedule into a small standardized table.
- `ERN`: per-ticker earnings history (date, EPS estimate, actual, surprise %), next earnings date, a chart of surprise history, and a list of 8-K Item 2.02 earnings releases that open in the pager.
- `EVTS`: upcoming earnings dates for the watchlist, sorted by date.

## Live IEX overlay (equities, toggleable)
Goal: show when the free yfinance quote is meaningfully behind real trades.
1. Take the yfinance quote (price and quote timestamp).
2. Fetch Alpaca IEX latest trade (price and timestamp): websocket for the active chart symbol and the watchlist up to the free symbol cap, REST polling for the rest.
3. Compute yfinance staleness (now minus yfinance quote timestamp) and price difference (IEX minus yfinance, absolute and %).
4. If the difference is meaningful (config defaults: abs diff > 0.05% OR yfinance staleness > 60s during market hours), render an indicator on top of the chart: a banner like `IEX 187.42 ▲+0.31 (+0.17%) | yf delayed 14m52s`, plus a dashed horizontal line at the IEX price and a marker at the last bar. If not meaningful, show a small neutral `IEX ✓` tag.
- Toggle with `i` on a chart or the `IEX` command (global). State saved in config and shown in the status bar. If no Alpaca keys, the toggle is disabled with a one-line explanation.
- Caveats shown in the UI: IEX is only a small slice of total market volume, so its last trade can differ from the consolidated price. Label it `IEX-only`, and show the IEX trade's own age. Outside regular hours, IEX trades are sparse, so show age rather than implying a live price.
- Applies to US equities and ETFs only. Never block the chart on Alpaca failures.

## Chart controls (`GP`, `GIP`, `COMP`)
- Ranges: `1D 5D 1M 3M 6M YTD 1Y 5Y MAX`. Intervals: `1m 5m 15m 1h 1d 1wk` (auto-limit to what the source supports).
- Types: candle, OHLC, line, area. Toggle volume.
- Overlays/indicators (toggle each, stackable): SMA, EMA, Bollinger Bands, VWAP, RSI, MACD. Parameters editable via a small prompt.
- Log/linear scale. Overlay and compare tickers, normalized to %.
- Crosshair: Left/Right move, shows OHLCV and date. `+`/`-` zoom, `h`/`l` pan, `0` reset. Live-updating last bar when streaming.
- All reachable by single keys, shown in a hint line under the chart.

## Other screens
- `OMON`: expiry selector, calls and puts side by side, strikes centered, ITM shading. Show bid/ask/last/volume/OI/IV. Compute delta/gamma/theta/vega locally (Black-Scholes, using yfinance IV).
- `MON`: editable watchlist. Columns: last, chg, %chg, volume, day range, source tag. Add/remove with `a`/`d`, sort with `s`.
- Home screen: index ticker tape, watchlist, movers, headlines, mini chart of the selected symbol, and a compact rates strip (2y, 10y, 2s10s, fed funds).
- News (`CN`, `TOP`): Enter opens the article in a terminal text reader with scrolling and search. If article text is unavailable, show the feed summary when available. Opening the original in a browser always requires a keyboard-only confirmation popup: `y`/`Y` opens; `n`/`N`, Escape, or Enter cancels. Show the keys as text, not mouse buttons.

## Keybindings (macOS Terminal and iTerm safe: avoid Cmd and Option combos)
- `Tab` / `Shift+Tab` = move pane focus. `Ctrl+1/2/4` = layout. `Ctrl+W` = close pane. `Ctrl+R` = force refresh. `?` = help overlay. `Ctrl+Q` opens the same keyboard-only popup style as browser confirmation: Ctrl+Q again quits, `n`/`N` or Escape cancels. There is no timeout. Plain `q` only closes readers/help.
- Context keys are always listed in the bottom hint bar.

## Build phases
1. **Skeleton**: theme, command bar, pane layout, aliases, yfinance provider with cache, `MON`, quote screen. Runs.
2. **Charts**: `GP`/`GIP` with ranges, intervals, candle/line, volume, crosshair.
3. **Boards**: `WEI`, `CMDTY`, `FXC`, `CRYPTO`, `MOST`, home screen.
4. **Bonds**: `GC`, `BTMM`, `SPRD`, `AUCT`, `FRED`.
5. **Depth**: `DES`, `HP`, `CN`, `TOP`, `OMON` with greeks.
6. **Fundamentals**: EDGAR ticker-to-CIK, `FA` via XBRL, `FIL` with pager, `ERN`, `EVTS`.
7. **Live**: Coinbase websocket, Alpaca IEX websocket plus overlay indicator and toggle.
8. **Advanced charts and polish**: indicators, `COMP`, log scale, `HELP`, did-you-mean, error handling.

## Non-goals
Trading or order entry, accounts, mobile, anything needing a paid key.
