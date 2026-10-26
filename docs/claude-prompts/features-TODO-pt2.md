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

Build the data/query backbone first: normalize client pages, expand the data-layer APIs, and expose the corresponding queryable client fields. Fundamental screening metrics, estimates and fund holdings are the priorities. Broader calendars and better news follow the data system. Sections 2–3 are implemented and verified. The Yahoo-backed data-layer portions of §§4–6 were added on 2026-10-25; terminal integration is explicitly deferred. Historical backfill and broader market-hours stream QA remain provider/verification follow-ups.

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

- [ ] Historical estimate/holdings backfill beyond collected snapshots still needs verified provider coverage; Yahoo supplies no retrospective archive through these endpoints. **[x]** Forward collection archives and UTC capture cutoffs are implemented for Yahoo fundamentals, estimates and holdings, preserving original fetch/capture times and explicit availability.
- [x] Ownership data layer (2026-10-25): normalized major/institutional/fund holders, insider transactions, purchase aggregates and rosters via OwnershipQuery and the ownership table. Report dates, shares, percentage-point weights, units and collection availability are distinct. Provider lists remain explicitly partial.
- [x] Corporate-action/share-count data layer (2026-10-25): CorporateActionsQuery retains dividends, splits and capital gains independently of rendered OHLCV; SharesQuery retains historical share observations. Typed dates, ratios/native currencies, dated observations versus publication times, cross-window offline reads and corrections are explicit.
- [x] Sector/industry data layer (2026-10-25): ResearchQuery supplies summaries, industry/market weights, top companies/peers, sector ETFs/funds, performance and available research-report metadata. Typed schemas retain percentage scaling and label curated lists instead of claiming full constituents.
- [x] Yahoo endpoint audit (2026-10-25): existing quote/history/options/indexed discovery and fundamental/estimate/fund APIs are reused. Added normalized annual/quarterly/TTM statement rows, company/profile metadata (including typed epoch fields), Yahoo filing links, reported EPS history and an ESG adapter. ESG returned 404 and remains explicitly unavailable. Calendar convenience methods discard numeric zeroes, so the adapter retains raw Yahoo column IDs/values. Lazy history metadata is reduced to required currency/timezone keys. No external fallback source was added; ISIN/external helpers and SEC/Treasury additions remain outside this Yahoo-only batch. New table fields are discoverable through the backend registry; terminal exposure is deferred.
- [ ] Retain existing data-depth follow-ups in `docs/HANDOFF.md`: fiscal-year labels for SEC facts, optional older EX-27 financial schedules, the Treasury.gov curve source and proper market holidays/early closes.
- [x] Existing FRED/SEC/FiscalData/Coinbase/Alpaca roles are preserved. All new ingestion uses Yahoo/yfinance. Provider coverage limitations and unavailable records are explicit; no duplicate terminal implementations or speculative analytics were added.

### 5. After the data system: better calendars, then better news

- [x] Calendar data layer (2026-10-25): market-wide/symbol earnings, IPOs, economic releases, splits and per-security dividend dates through EventsQuery/events. UTC paging bounds, source schedules, date/session precision, original timezone and fiscal/report dates remain separate. Bounded sequential pages expose total/cap/duplicate/failure coverage. Terminal calendar changes are deferred.
- [x] Yahoo news data layer (2026-10-25): NewsRecordsQuery/articles supports security feeds and search, typed filters, stable article IDs, security associations, publication/fetch/availability times and deduplication. NewsArticleQuery reads retained Yahoo body/summary only; no publisher ingestion or sentiment. Existing terminal news behavior is preserved; new client views are deferred.

### 6. Live pricing prerequisite for any future trading capabilities

- [x] Yahoo streaming data layer (2026-10-25): opt-in start_yahoo_stream controller with subscription changes, reconnect/backoff/resubscription, normalized source-millisecond/receipt timestamps, worker persistence, bounded buffers and explicit stale/delayed/missing coverage. YahooPricesQuery supports stored latest/history and stored Yahoo quote fallback. Existing Coinbase/Alpaca feeds are retained; no terminal setting, wiring or trading capability was added.
- [ ] Broader market-hours stream coverage/delay QA remains open for equities, indices and futures. Live checks received BTC and FX updates; simulated checks cover missing/malformed/out-of-order records, reconnect, subscription changes, shutdown and stale restart reads. Unavailable symbols remain explicit; transport activity never implies exchange-grade coverage. Trading remains future work.

### 7. Analysis navigation, futures classification, visualization and field normalization (2026-10-24 notes)

- [x] **Existing-record financial drill-down (2026-10-25).** Enter on METRICS/EST/ANALYST/HOLD opens the selected full record, including available targets, recommendation counts, rating actions, EPS trends/revisions and provenance. Details reuse loaded data even when FIELDS hides columns. q/Esc restores the same query, row and focus; Shift+Enter explicitly opens the security price graph. Context and unavailable-history limits are explicit. Richer metric-history/calculation-input views remain future work; no new data source was added.
- [x] **Futures classification decision (2026-10-25).** The user chose to derive futures type from existing queryable fields. Leave FUTURES classification unchanged; do not add or maintain a new category field.
- [ ] **Improve visualization across the expanded system — deferred by the user (2026-10-25).** Useful later views include metric histories/comparisons, estimate trends/revisions, holdings/allocation breakdowns and screener distributions. Reuse shared query/visualization infrastructure and preserve units, dates, provenance, coverage and table access.
- [x] **Shared field normalization at adapter/query boundaries (2026-10-25).** Added canonical scalar helpers and schema normalizers for finite numbers, booleans, missing values, source/security-type aliases, currencies, calendar dates and UTC timestamps. Shared typed date/timestamp fields provide chronological WHERE/SORT semantics across security/financial tables. Keep fiscal/report/filing/observation/fetch meanings distinct; preserve GBp versus GBP and explicit percentage/ratio units. Financial record identities and table versions include normalization logic; source-only and legacy-only records upgrade offline with original fetch times/staleness. Original provider documents and low-level Quote/Metadata/time-series contracts remain intact. Help and field reference document the conventions.

The 2026-10-25 normalization and existing-record detail scope is implemented and verified. Visualizations are deferred; futures use existing fields. Existing calendar/news sequencing and the live-pricing prerequisite remain in place.
