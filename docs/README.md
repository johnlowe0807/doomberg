# freeterm

vibe coded open-source finance data terminal

## Run
```
python3.11 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python -m freeterm      # or: .venv/bin/ft
```
- Screen securities by type with `/EQUITY`, `/ETF`, `/FUND`, `/INDEX`, `/FUTURES`, `/CRYPTO` and `/FX`.
  `/CMDTY` redirects to FUTURES, `/WEI` to INDEX, `/BONDS` to filtered ETFs and `/MOST` to equity movers.
  Categories and themes use filters within these pages. All query Yahoo's screenable universe.
  `/EQUITY .SORT price DESC` requests a market-wide ranking. Equities/ETFs default to US; explicit region/exchange
  conditions override that scope. Crypto defaults to USD pairs. FUTURES covers commodity and financial futures and
  uses Yahoo's reported volume. Prices retain their quoted currencies, displayed in the table.
  Press `.` for commands; `?`, popup `HOME` or `.HELP` lists syntax and available fields.
  Example: `/EQUITY .WHERE price > 10 AND volume >= 1000000 .SORT volume DESC .LIMIT 20`.
  Repeated `.WHERE` and `.SEARCH "text"` clauses use AND. `.SORT`, `.FIELDS` and `.LIMIT` replace earlier settings;
  `.CLEAR [WHERE|SEARCH|SORT|FIELDS|LIMIT|ALL]` resets them. Group OR conditions with parentheses.
  Quote text/tickers (`.WHERE symbol = "BRK.B"`); `.FIELDS symbol,name,price` selects columns.
  Missing values sort last and need `IS NULL` to match. Failed chains preserve all previous screening settings.
  `/GP _symbol="identifier"` preserves an explicit security identifier independently of command names.
  The backend translates supported conditions/sorts to Yahoo, reuses five-minute page caches and fetches additional
  pages sequentially only when needed. Local conditions/quant fields use bounded scans; partial rankings are labelled.
  `[screener].max_pages` controls the scan budget (default 8, maximum 40; pages hold at most 250 rows). Narrowing
  `.WHERE` reduces requests. Ctrl+R forces refresh. Yahoo sessions use app-owned caches under `~/.freeterm/cache/yahoo`.
- Equity fields include valuation, growth, profitability, leverage, liquidity, dividends, shares/float, short interest
  and analyst targets (41 financial fields; `?` lists names, units and periods). For example:
  `/EQUITY .WHERE market_cap > 1000000000000 AND roe > 15 .FIELDS symbol,roe,revenue_growth,debt_equity`.
  Percentages use percentage points (`roe > 15` means 15%); `debt_equity` is a ratio (`< 1` means below 100%).
  Quarterly growth and TTM growth are separate. Supported conditions/sorts run at Yahoo; additional financial lookups
  reuse grouped six-hour snapshots and are capped by `[screener].max_enrich_symbols` (default 25, maximum 100).
  Narrow server-supported filters first. Lookup limits/unavailable fields and partial rankings remain visible.
  The printable [financial fields cheat sheet](FIELDS-CHEATSHEET.md) lists every field, units, periods and examples.
- Query financial data for a security with `/METRICS AAPL`, `/EST AAPL`, `/ANALYST AAPL` and `/HOLD SPY`.
  All use the same dot commands: `/EST AAPL .WHERE period = '+1y' .FIELDS eps_mean,eps_growth,revenue_mean`,
  `/HOLD SPY .WHERE kind = 'holding' AND weight > 5 .SORT weight DESC`, or
  `/ANALYST AAPL .WHERE kind = 'rating_change' .SORT date DESC`.
  `/METRICS AAPL SEC 2020-12-31 .WHERE value IS NOT NULL` calculates SEC metrics using facts filed by that date.
  SEC calculations retain fiscal dates and filing dates; annual reports take precedence at fiscal year ends.
  Yahoo estimates/targets are current snapshots, not historical estimates. Holdings may cover only reported top positions;
  HOLD also includes sector/asset allocations, bond metrics and fees. Unknown reporting dates remain N/A and ambiguous
  provider statistics retain their provider units. CLEAR preserves the selected security/source/date.
  Enter opens details for the selected metric, analyst record, estimate or holding, including fields omitted by FIELDS.
  q/Esc returns to the same table/selection; Shift+Enter on the table opens its security price graph.
  Details reuse the loaded record, including available target ranges, rating counts and EPS revisions.
  Shared data-layer normalization keeps calendar dates (`YYYY-MM-DD`) separate from UTC timestamps.
  Date filters/sorts are chronological; timestamps accept ISO offsets/Z or epoch seconds. Currency units retain GBp/GBP distinctions.
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
  Price, comparison, rates/spreads, Treasury curve, and earnings charts use the reusable `terminal_plot` library.
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
- On a graph, Shift+Left/Right performs binary search within the visible candles. Each jump moves halfway toward
  the chosen bound and remembers the previous cursor as the opposite bound. Reversing direction therefore narrows
  between earlier pivots rather than jumping back toward the wall. For positions 0–100: `100 → Left 50 → Right 75 → Left 62`.
  Rounding favors the chosen bound. Plain arrows, GOTO, and zoom/pan/range changes start a fresh search; `x`/`0` clears it.
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
Yahoo resource requests coordinate one anonymous-session renewal after a final 401, then share a
60-second authentication cooldown; 429s pause them for five minutes. Login cookies are preserved.
Profiles/books use grouped quoteSummary data without the SDK's extra quote/timeseries lookups.

Cleanup runs at startup and at most every ten minutes: 1,500 datasets / 256 MiB of compressed
content, two million OHLCV rows total, 100,000 observations per series, two years without access,
90 days of news and 50,000 search records (one year). Provider and analysis caches each have a
64 MiB budget (1,000/512 entries); expired responses and 30-day analysis results are removed.
SQLite overhead/WAL files can temporarily exceed these logical budgets. Settings/watchlists
are preserved; legacy JSON caches are left untouched and are no longer read.

### Additional Yahoo data APIs (terminal integration deferred)

Use `await hub.query(request)` with classes from `data_layer.query_models`:

| Request | Normalized data / TableQuery dataset |
| --- | --- |
| `OwnershipQuery(symbol)` | Major, institutional, fund and insider ownership/transactions/purchases; `ownership` |
| `CorporateActionsQuery(symbol, start, end)` | Dividends, splits, capital gains; `actions` |
| `SharesQuery(symbol, start, end)` | Historical share-count observations; `shares` |
| `ResearchQuery('sector'\|'industry', key, region='US')` | Overviews, weights, companies, fund lists, performance and report metadata; `research` |
| `StatementsQuery(symbol, 'income'\|'balance'\|'cashflow', 'annual'\|'quarterly'\|'ttm')` | Long-form accounting records; `statements` |
| `EventsQuery(kind, start, end, symbol=None, region=None, max_pages=4)` | Earnings, IPOs, economic releases, splits, per-security dividends; `events` |
| `NewsRecordsQuery(symbols=(), text='', start=None, end=None, limit=100)` | Yahoo feeds/search, security links and deduplicated articles; `articles` |
| `NewsArticleQuery(article_id)` | Retained Yahoo body or summary; no publisher request or sentiment |
| `CompanyQuery(symbol, dataset)` | `profile`, `filings`, `earnings_history`, `sustainability`; `company` |
| `FinancialArchiveQuery(symbol, dataset='estimates', as_of=None)` | Collected fundamentals/estimates/holdings snapshots; `financial_archive` |
| `YahooPricesQuery(symbols, start=None, end=None)` | Captured stream observations or stored Yahoo quote fallback; `yahoo_prices` |

All table datasets support the existing typed WHERE/SEARCH/SORT/FIELDS/LIMIT engine. Supply request arguments
through `TableQuery.parameters`; inspect `hub.queries.table_dataset(name).fields` for types and units.
These are backend APIs; no terminal commands, pages, settings or existing feeds were changed in this batch.

```python
from data_layer.query_models import EventsQuery, NewsRecordsQuery, TableQuery, Condition
events = await hub.query(EventsQuery('economic', '2026-10-26', '2026-10-31', region='US'))
news = await hub.query(NewsRecordsQuery(('AAPL', 'MSFT')))
ownership = await hub.query(TableQuery('ownership', where=Condition('weight', '>', 5),
                                     parameters={'symbol': 'AAPL'}), stored_only=True)
stream = await hub.queries.start_yahoo_stream(['BTC-USD', 'EURUSD=X'])  # opt-in
# ... query YahooPricesQuery or pass on_record=callback when starting the stream ...
await stream.stop()
```

Date windows are start-inclusive/end-exclusive. Calendar dates remain separate from fixed-precision UTC
instants; session/date-only schedules have no invented exact event time. Original timezone, scheduled time,
period/report dates, collection time and conservative availability remain distinct. Profile epoch fields also
have typed timestamp values. Unchanged records retain first availability; corrections start a new availability.
Percentages use percentage points; monetary amounts retain currency; unknown economic/quote units remain explicit.

`Metadata.complete`, `coverage` and `reason` describe bounds, duplicates and failures. Calendars page sequentially
(100 rows/page; default four, maximum twenty); completeness covers Yahoo's returned scope only. News discovery
uses at most 100 items per source/25 securities, then queries retained articles. Missing dates/values stay null.
Yahoo currently supplies no retrospective estimate/holdings archive here: capture cutoffs refer to locally collected
snapshots, not fiscal/report dates. The ESG endpoint returned 404 during live checks and remains explicitly unavailable.

Schema v4 adds indexed domain records, financial captures and stream observations. Raw documents support offline
normalization rebuilding; compatible stored event/share windows can be reused without fetching. Captures retain
up to ten years/10,000 snapshots/128 MiB; stream ticks retain up to 90 days/200,000 records. These budgets supplement
the existing dataset/cache budgets. Streams reconnect/resubscribe, persist off-thread, expose buffer drops and label
old observations DLY/STALE; transport activity does not imply exchange-grade coverage.

## Reusable terminal plots

`terminal_plot` takes supplied data and drawing instructions; it performs no fetching or statistical calculations.
It supports line, area, scatter (including supplied group labels), bar, candle and OHLC series, vertical subplots,
left/right axes, independent comparison scales, guides, crosshairs and explicit limits/log scales. Numeric x values
retain their spacing. Categories retain encounter order; datetime coordinates use UTC. Set `Axes(xkind='datetime')`
for normalized ISO dates/timestamps or epoch seconds. Timestamp strings and Python datetimes need an offset/timezone;
calendar dates use midnight UTC. Nonfinite coordinates leave gaps. Submitted figures copy their arrays so later
caller mutations cannot change queued frames. All native OHLC rows are drawn without aggregation.

```python
from rich.console import Console
from terminal_plot import subplots, render_text, render_image

figure, axes = subplots(title='Supplied clusters')
axes.xlabel, axes.ylabel = 'Feature 1', 'Feature 2'
axes.scatter([0, 1, 10, 11], [1, 2, 8, 9], groups=['A', 'A', 'B', 'B'])
axes.scatter([.5, 10.5], [1.5, 8.5], label='Centers', marker='cross', color='#FFFFFF')
Console().print(render_text(figure, (100, 30)))
render_image(figure, (1000, 600)).save('/tmp/clusters.png')
# Inside FreeTerm: chart_surface.submit(figure) uses the existing text/Sixel/Kitty transport.
```

Declarative callers can construct `Figure`, `Axes`, `Series` and `Guide` directly. `subplots(rows=2)` returns a
figure and a list of axes; set `figure.sharex = True` for aligned x domains. Rendering returns a Rich `Text` or Pillow
image. Terminal text provides coarse stroke/marker sizes; raster output applies pixel sizes. The existing financial
`Scene` adapter remains in `freeterm/pixelcharts.py`; navigation, data preparation and calculations stay in the views.
No new terminal commands or data integrations are introduced by this plotting change.

```bash
.venv/bin/python -m terminal_plot.demo                       # offline cluster example in terminal
.venv/bin/python -m terminal_plot.demo --png /tmp/clusters.png
.venv/bin/python -m unittest discover -s tests -p test_plotting.py -v
```

## Normalized extraction and calculations

`/DATA` lists all registered datasets and their complete field schemas/parameters. Open any with
`/DATA dataset [symbol] [_parameter=value ...]`. `/EXTRACT` carries the active screen's query/context into
this common client. On specialized screens, `.WHERE`, `.FIELDS`, `.CALC`, `.PIVOT`, `.STATS` and `.EXPORT`
open the same extraction client; Esc returns to the specialized layout. Existing table filters still work.

```text
/DATA reporting AAPL _statement=balance
.WHERE metric IN ('assets','liabilities','stockholders_equity')
.PIVOT metric value BY symbol,period_end
.CALC debt_ratio = liabilities / assets
.SORT period_end DESC
.FIELDS symbol,period_end,assets,liabilities,debt_ratio
.EXPORT "/tmp/balance-ratios.json"
```

`reporting` exposes every SEC concept/unit eligible at the selected filing-date cutoff, selecting the latest
filing per actual concept/period. `facts` retains all reported filings/amendments, taxonomy, original concepts,
units, period starts/ends, filing dates and accessions; extraction is not limited to FA's display rows or the
41 predefined metrics. SEC instant facts are accessible as balance data; SEC duration concepts remain
unclassified by statement. For provider-classified statements with every Yahoo line item, use
`/DATA statements AAPL _statement=balance _period=annual` (also income/cashflow, quarterly/TTM where supported).
`/DATA reporting AAPL _source=yahoo _statement=balance` includes annual and quarterly statement records.
`statement_values` exposes the exact shared FA annual/quarterly calculations, including standalone quarters
and free cash flow, as unscaled numbers. It preserves FA's USD convention; facts/reporting/statements retain
native-currency source records and all unlisted concepts. `/EXTRACT` from FA preserves the selected statement
and annual/quarterly mode. Actual fiscal periods remain separate from filing-context FY/FP metadata.
Known cross-provider metric aliases retain original provider fields and naming-basis metadata; unmatched
concepts retain normalized provider names, signs and units. Raw facts retain forward-dated disclosures;
current reporting excludes future measurement periods.

All existing registered financial, ownership, actions, shares, research, events, articles, company, captures
and Yahoo-price fields are queryable. Added access covers native prices, FRED series, quotes, filings, news,
full option chains and shared Greeks, available book levels, auctions, comparisons, selected-range statistics,
technical indicators, ticker calendars, FRED metadata/notes, search and readable documents. `profile_fields` and `source_fields` retain provider
scalar paths/types, including fields without canonical meanings; their unknown units stay explicit.
Use `source_fields _operation=estimate-data`, `holdings-data`, `fundamental-data` or another listed existing
operation for original provider fields beyond the normalized schemas. No new providers are introduced.

Commands execute in written order. `.STATS value BY metric` produces count/missing/sum/mean/min/max/sample
standard deviation, preserving security/source/period/unit/currency partitions. `.CALC` supports arithmetic,
abs/sqrt/log/coalesce, date functions and explicitly ordered lag/diff/pct_change. Nulls and zero denominators
stay null. Pivots retain fiscal periods, currencies, source/taxonomy and input filing/availability lineage;
duplicate cells and incompatible unit additions are errors. Calendar dates never become invented instants.

Leading WHERE/SEARCH/LIMIT applies before retrieval limits. DATA starts at 10,000 rows; EXTRACT preserves the
active table's selection limit. The safety cap is 10,000 input rows, with provider budgets/partial coverage
still explicit. Later LIMIT operations act where written. The terminal displays at most 1,000 selected rows;
exports retain the entire selected result. `.EXPORT` creates JSON with schemas/coverage/provenance, or CSV
with an adjacent `.csv.meta.json`; existing files are never overwritten. `?` lists every source field and
current output field. FIELDS * exposes all available fields at that pipeline stage.

Programmatic access uses `hub.query(ExtractQuery(TableQuery(...), steps=(DataStep(...), ...)))`.
`data_layer.extraction.to_frame(result)` returns numeric/boolean nullable pandas columns, UTC instant columns,
calendar-date objects and schema/provenance attributes. `stored_only=True` uses retained source records;
missing archives remain unavailable. Exported reporting data describes collected/provider coverage and does
not imply original-publication precision or a complete historical point-in-time archive.
