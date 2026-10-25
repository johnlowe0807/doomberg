1. [x] Architecture change.

Re-architect freeterm around a query-and-analysis data pipeline in one coordinated migration. Read `docs/CLAUDE.md`, `docs/HANDOFF.md`, and the existing app first, then implement the full change—not just a proposal.

Separate provider ingestion, normalization, storage/indexing, feature/query services, and Textual rendering. Add a typed query interface backed by purpose-appropriate storage for time series, fundamentals/filings, news, and indexed search; preserve source, observation timestamp, fetch time, and freshness metadata. Route existing features through this interface, keeping the CLI’s commands and UX as stable as possible. Search should query its index, and news should remain unanalyzed until sentiment is explicitly added.

Implement caching as distinct layers, not as a substitute for the data store:
- Keep a short-lived provider-response cache with per-source/data-type freshness policies to prevent duplicate requests and reduce rate-limit pressure; retain rate limiting and 429 backoff.
- Persist normalized observations so queries and future analytics can reuse data across runs without refetching. Use storage suited to the data and practical for this app; do not force all records into one generic table or cache format.
- Cache expensive query and derived-analysis results using stable keys that include query/parameters, input-data versions or timestamps, and calculation version. Invalidate or bypass results when inputs, parameters, or calculation logic change; forced refresh must update the relevant stored data and dependent results.
- Make stale-cache fallback explicit in result metadata/status, and bound cache growth with a simple retention/cleanup policy.

Support future computationally heavy analytics by allowing them to query stored observations and reuse correctly versioned derived results, without building speculative analytics now.

Choose the simplest robust architecture that fits the project, preserve zero-key operation and async/non-blocking UI behavior, and make failures and stale data visible. Update directly related docs, keep the app runnable, and validate the migration with the project’s prescribed smoke test. Avoid unrelated cleanup.

## Next roadmap (2026-10-24)

Build the data/query backbone first: normalize client pages, expand the data-layer APIs, and expose the corresponding queryable client fields. Fundamental screening metrics, estimates and fund holdings are the priorities. Broader calendars and better news follow the data system. Sections 2–3 are implemented and verified; sections 4–6 remain planned work.

### 2. Normalize client screener pages and queries

- [x] Organize screener pages by security type and use the same universal query instructions across them. Categories, sectors and themes belong in filters rather than separate screener implementations.
- [x] Make `/FUTURES` the canonical futures screener. Commodities are futures and must be queried within this page, alongside other futures; retire the separate commodity screener identity. Decide how the old `/CMDTY` command redirects during migration and update navigation, help and completion consistently.
- [x] Keep `/CRYPTO` as its own security-type page, retaining Coinbase as the crypto live-data source. Keep provider selection behind the common data/query interface.
- [x] Keep WHERE, SEARCH, SORT, FIELDS, LIMIT and CLEAR consistent across pages and future tools. Preserve literal tickers and the existing dot-command/popup workflow.
- [x] Preserve provider-wide query planning: translate supported operations to the provider, reuse cached pages and fetch sequentially only as needed. Local/quant calculations must retain explicit scan bounds and coverage; never present a ranking over a small seed list as market-wide.

### 3. Expand data-layer APIs and queryable client fields (highest priority)

Track both deliverables for each dataset: **(a) typed data-layer query/provider APIs and normalized storage**, and **(b) discoverable, queryable client fields and useful views**. A value appearing in a raw provider payload or `/DES` does not complete its integration into screening.

- [x] **Fundamental screening metrics — data APIs:** expand Yahoo/SEC ingestion and normalized queries for valuation, growth, profitability, leverage, liquidity, dividends, share/float data, beta and short-interest metrics. Include trailing/forward P/E, P/B, EV/EBITDA, revenue/EPS growth, margins, ROA/ROE, debt ratios, free cash flow and dividend yield where available.
- [x] **Fundamental screening metrics — client fields:** register these metrics in the universal field schema so WHERE, SORT and FIELDS can use them consistently. Reuse existing `/DES` data where appropriate; prioritize this before less important datasets.
- [x] **Estimates — data APIs:** add earnings/revenue estimates, estimate periods, EPS trends/revisions, growth estimates, analyst counts, price-target distributions, recommendations and upgrades/downgrades. Retain source dates and distinguish estimates from reported results.
- [x] **Estimates — client fields:** expose normalized forward estimates, revisions and analyst measures through the common query interface and relevant security views.
- [x] **Fund holdings — data APIs:** add ETF/mutual-fund holdings, constituent identifiers, weights and reporting dates, sector allocations, asset-class exposure, available bond characteristics and fund expense information.
- [x] **Fund holdings — client fields:** make holdings and allocation records queryable, including constituent/weight filters and sorting; link funds to their holdings without implementing another query language.
- [x] Define consistent field names, types, units, currencies, percentage scaling and missing-value behavior. Preserve annual/quarterly/TTM distinctions, fiscal periods, observation/report/filing dates, provenance and freshness; support as-of use for future quant work.
- [x] Keep provider-specific names and unsupported-operation handling inside adapters/planners. Prefer batching and server-side work when efficient and kinder to the API; preserve stored/offline reuse, versioned calculations, rate limiting and backoff.
- [x] The shared API/registry supports additional adapters where verified coverage gaps require them. These integrations use yfinance/SEC without adding duplicate client fetch paths; no additional external provider was needed.

Completed verification: actual CLI startup/confirmed exit, full 27-command live smoke, four type-page/redirect checks, six financial-view live smoke commands, and focused normalization/storage/offline/coverage/UI checks. Yahoo estimates/targets remain current snapshots; SEC supports filing-date cutoffs.

### 4. Remaining data additions to retain in the backlog

- [ ] Historical estimate/holdings archives for point-in-time work beyond current provider snapshots; retain original availability and reporting dates and verify source coverage before promising historical backtests.
- [ ] Ownership: major/institutional/mutual-fund holders, insider transactions, insider purchases and insider ownership rosters.
- [ ] Corporate actions: dedicated dividend, split and capital-gain histories, plus historical share counts; retain these as normalized queryable datasets rather than discarding them during OHLCV normalization.
- [ ] Sector/industry research: summaries, industry weights, constituent/peer companies and sector-related fund lists.
- [ ] Audit additional provider fields and endpoints for useful data not yet exposed. Record whether each item is already fetched, needs normalization, needs client fields or needs a new source, so existing data is not rebuilt unnecessarily.
- [ ] Retain existing data-depth follow-ups in `docs/HANDOFF.md`: fiscal-year labels for SEC facts, optional older EX-27 financial schedules, the Treasury.gov curve source and proper market holidays/early closes.
- [ ] Preserve the existing source roles: FRED macro/rate/credit history, SEC financials/filings, Treasury FiscalData auctions, Coinbase crypto prices/depth and Alpaca IEX overlays. Distinguish missing integrations from provider coverage limitations.

### 5. After the data system: better calendars, then better news

- [ ] Expand calendars beyond watchlist earnings: market-wide earnings, IPOs, economic releases and splits. Use the new data APIs, normalized event dates/time zones, security links and the common querying conventions.
- [ ] Improve news coverage, search and filtering, security associations, deduplication, publication/fetch metadata and article access. Build on the normalized news store and shared query APIs; retain readable-text/summary fallbacks. Sentiment/analysis remains a separate explicitly scoped feature.

### 6. Live pricing prerequisite for any future trading capabilities

- [ ] Add yfinance/Yahoo live price streaming through the data layer **before adding any trading capabilities**. Integrate subscriptions, reconnection/resubscription, timestamps, source/freshness labels and stale/offline fallback; retain the existing Coinbase and Alpaca feeds.
- [ ] Verify stream coverage, delays and behavior across supported security types. Keep unavailable/delayed data explicit and do not imply exchange-grade coverage. Trading remains future work; this TODO records the required ordering.

### 7. Analysis navigation, futures classification, visualization and field normalization (2026-10-24 notes)

- [ ] **Keep analysis/metric drill-down in context.** Clicking or pressing Enter on an analysis or metric currently opens the underlying security's price graph. Open the selected metric/analysis details and, where available, its historical observations or underlying input records instead. Preserve the security, selected metric, period, source, calculation and query context; make returning to the originating table straightforward. Keep price-chart navigation an explicit action. Audit the relevant analysis/metrics tables for this behavior. Where historical data is unavailable, say so; current estimate/holdings snapshots must not imply a historical archive.
- [ ] **Classify futures for querying.** Add a normalized futures-category field to the data-layer schema and expose it on FUTURES for WHERE, SORT and FIELDS. Support useful categories such as energy, metals, agriculture, equity indices, rates and currencies, with explicit unknown values and consistent provider mappings. Keep these within the FUTURES page. Choose/document the canonical field name and category values; use the existing universal commands to filter and sort by futures type, including secondary sorts such as volume or expiry where available.
- [ ] **Improve visualization across the expanded system.** Plan and build useful views for the newly connected datasets: metric histories/comparisons, estimate trends/revisions, holdings and allocation breakdowns, and screener distributions or heatmaps. Reuse the shared visualization/query infrastructure, keyboard navigation and dot-command workflow. Show units, dates, source/freshness and coverage; choose displays that fit the available data and retain table access. Prioritize visual exploration and drill-down beyond the underlying security's price chart.
- [ ] **Audit and normalize fields at the data-layer adapter boundary.** Provider plugs should map equivalent concepts to shared field names, types, units and values before storage/querying, rather than each frontend interpreting provider formats. Start with inconsistent dates: canonical date-only values and UTC-aware timestamps, preserving their different meanings and original time-zone information where needed; sort chronologically using typed values rather than display strings. Keep observation, fetch, fiscal-period, filing, event and holdings-report dates distinct. Audit numeric/percentage scaling, currencies, enums, aliases and missing values across providers and security types. The same WHERE/SORT/FIELDS syntax and canonical field semantics must work across types wherever the concept is shared; unsupported fields remain explicit. Cover this in the common schema/help and migrate/version stored normalized data and derived caches when representations change.

These are backlog notes only; no implementation is included in this update. Existing calendar/news sequencing and the live-pricing prerequisite above remain in place.
