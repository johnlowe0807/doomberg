# Financial fields cheat sheet

## Query essentials

Pages: `/EQUITY`, `/ETF`, `/FUND`, `/INDEX`, `/FUTURES`, `/CRYPTO`, `/FX`.
`/CMDTY` redirects to FUTURES; `/WEI` to INDEX; `/BONDS` filters ETFs; `/MOST` filters equities.
The 41 financial fields below belong to EQUITY and equity mover screens.
Press `?`, or enter `HELP` / `HOME` in the `.` popup, for the active page's complete field list.

| Command | Effect |
| --- | --- |
| `.WHERE roe > 15 AND debt_equity < 1` | Add a condition; repeated WHERE clauses use AND |
| `.SEARCH "apple"` | Add a text search over the page's search columns |
| `.SORT market_cap DESC, symbol ASC` | Replace ordering; missing values sort last |
| `.FIELDS symbol,price,roe` | Replace displayed columns; `FIELDS *` restores defaults |
| `.LIMIT 20` | Set the maximum displayed rows after selection and sorting |
| `.CLEAR WHERE` / `.CLEAR ALL` | Reset a clause or the query; retain the selected security/source/date |

Comparisons: `= != <> > >= < <= IN (...) LIKE CONTAINS IS NULL IS NOT NULL`.
Use `AND`, `OR`, `NOT` and parentheses. Quote text and tickers: `symbol = 'BRK.B'`.
Text comparisons ignore case. Missing values need `IS NULL`; `!=` does not match missing values.
A dot-command chain validates as a whole before changing settings.

Dates (`period_end`, `filing_date`, `report_date`, `observed_date`) use `YYYY-MM-DD`.
Timestamps (`observed_at`, `fetched_at`, analyst `event_at`) use UTC ISO instants; filters accept
an explicit offset/Z or epoch seconds. They compare chronologically, with missing values last.
Analyst `date` remains an alias for `event_at`. SEC filing dates and short-interest observation dates
do not imply a known observation time. Currency codes preserve minor units: GBp is pence, GBP is pounds.
Enter on METRICS/EST/ANALYST/HOLD opens the full selected record, including fields omitted from the table.
q/Esc returns to the same query/selection; Shift+Enter opens the selected security's price graph.
Details use the existing record; they do not supply new metric/target histories or new data sources.

```text
/EQUITY .WHERE market_cap > 1000000000000 AND roe > 15 .SORT market_cap DESC .FIELDS symbol,price,roe,debt_equity .LIMIT 20
/FUTURES .SORT volume DESC .FIELDS symbol,name,price,volume,currency
/EST AAPL .WHERE period = '+1y' .FIELDS period,period_end,eps_mean,eps_growth,revenue_mean,currency,revenue_currency
/ANALYST AAPL .WHERE kind = 'rating_change' .SORT date DESC
/HOLD SPY .WHERE kind = 'holding' AND weight > 5 .SORT weight DESC
/METRICS AAPL .WHERE metric = 'roe' .FIELDS metric,value,unit,period,period_end
/METRICS AAPL SEC 2020-12-31 .WHERE value IS NOT NULL
```

**Units:** percent fields use percentage points: `15` means **15%**, not 0.15.
`debt_equity` is a ratio: `1` means debt equals equity (100%).
Currency values are actual units, not millions/billions. Prices use quoted currency;
financial totals/EPS can use a different financial-reporting currency. No currency conversion occurs.

## Equity financial fields

Use these names directly in WHERE, SORT and FIELDS on EQUITY / equity movers.
On METRICS they appear as values of the `metric` column; the numeric column is `value`.
TTM = trailing twelve months. Latest-quarter growth compares with the same quarter a year earlier.
Forward fields are provider estimates; their forecast horizon can be unspecified.

### Valuation

| Field | Meaning | Unit | Period |
| --- | --- | --- | --- |
| `pe` | P/E TTM | ratio | TTM |
| `forward_pe` | Forward P/E | ratio | Forward estimate |
| `price_book` | Price/Book | ratio | Latest balance sheet |
| `peg` | PEG (5Y) | ratio | Forward estimate |
| `ev_ebitda` | EV/EBITDA | ratio | TTM |
| `enterprise_value` | Enterprise Value | currency | Snapshot |

### Earnings and growth

| Field | Meaning | Unit | Period |
| --- | --- | --- | --- |
| `eps_ttm` | EPS TTM | currency/share | TTM |
| `eps_forward` | EPS Forward | currency/share | Forward estimate |
| `revenue_ttm` | Revenue TTM | currency | TTM |
| `net_income_ttm` | Net Income TTM | currency | TTM |
| `ebitda_ttm` | EBITDA TTM | currency | TTM |
| `revenue_growth` | Revenue Growth (quarter YoY) | percent | Latest quarter, YoY |
| `earnings_growth` | Earnings Growth (quarter YoY) | percent | Latest quarter, YoY |
| `eps_growth_ttm` | EPS Growth TTM YoY | percent | TTM |
| `revenue_growth_ttm` | Revenue Growth TTM YoY | percent | TTM |

### Margins and returns

| Field | Meaning | Unit | Period |
| --- | --- | --- | --- |
| `gross_margin` | Gross Margin | percent | TTM |
| `operating_margin` | Operating Margin | percent | TTM |
| `profit_margin` | Profit Margin | percent | TTM |
| `ebitda_margin` | EBITDA Margin | percent | TTM |
| `roe` | ROE | percent | TTM |
| `roa` | ROA | percent | TTM |

### Leverage and cash flow

| Field | Meaning | Unit | Period |
| --- | --- | --- | --- |
| `debt_equity` | Debt/Equity | ratio | Latest balance sheet |
| `current_ratio` | Current Ratio | ratio | Latest balance sheet |
| `quick_ratio` | Quick Ratio | ratio | Latest balance sheet |
| `free_cash_flow` | Free Cash Flow TTM | currency | TTM |
| `operating_cash_flow` | Operating Cash Flow TTM | currency | TTM |

### Dividends

| Field | Meaning | Unit | Period |
| --- | --- | --- | --- |
| `dividend_yield` | Dividend Yield TTM | percent | TTM |
| `forward_dividend_yield` | Dividend Yield Forward | percent | Forward estimate |
| `dividend_per_share` | Dividend/Share TTM | currency/share | TTM |
| `payout_ratio` | Payout Ratio | percent | TTM |

### Risk, shares and short interest

| Field | Meaning | Unit | Period |
| --- | --- | --- | --- |
| `beta` | Beta | ratio | Snapshot |
| `shares_outstanding` | Shares Outstanding | shares | Latest balance sheet |
| `float_shares` | Float Shares | shares | Snapshot |
| `short_float` | Short Float | percent | Short-interest snapshot |
| `short_ratio` | Days to Cover | days | Short-interest snapshot |

### Analysts

| Field | Meaning | Unit | Period |
| --- | --- | --- | --- |
| `target_mean` | Analyst Target Mean | currency/share | Forward estimate |
| `target_median` | Analyst Target Median | currency/share | Forward estimate |
| `target_low` | Analyst Target Low | currency/share | Forward estimate |
| `target_high` | Analyst Target High | currency/share | Forward estimate |
| `analyst_count` | Analyst Count | count | Forward estimate |
| `recommendation_mean` | Recommendation (1=buy, 5=sell) | rating | Forward estimate |

### Other equity fields

| Group | Fields |
| --- | --- |
| Identity | `symbol`, `name`, `type`, `region`, `exchange`, `currency`, `sector`, `industry` |
| Quotes | `price`, `previous_close`, `change`, `pct_change`, `volume`, `day_low`, `day_high`, `market_cap` |
| Quote provenance | `source`, `freshness`, `observed_at`, `fetched_at` |
| Financial provenance | `financial_source`, `financial_period_end`, `financial_filing_date`, `financial_fetched_at`, `financial_currency` |

`pct_change` is also in percentage points. Quote observation/fetch times on security screeners are Unix seconds.
Financial timestamp/date fields are text. Financial metadata summarizes available metrics; use METRICS for each metric's period and filing date.
ETF/FUND pages also have `category`; available quote columns vary by security type.

## Estimates and analyst fields

`/EST AAPL` contains `kind = 'estimate'` records.
Periods: `0q` current fiscal quarter, `+1q` next fiscal quarter,
`0y` current fiscal year, `+1y` next fiscal year. Read `period_end` for the actual fiscal date.

| Group | Fields | Units / interpretation |
| --- | --- | --- |
| EPS consensus | `eps_mean`, `eps_low`, `eps_high`, `eps_year_ago` | Currency/share in `currency` |
| EPS growth / coverage | `eps_growth`, `eps_analysts` | Growth %, analyst count |
| Revenue consensus | `revenue_mean`, `revenue_low`, `revenue_high`, `revenue_year_ago` | Currency in `revenue_currency` |
| Revenue growth / coverage | `revenue_growth`, `revenue_analysts` | Forecast period growth %, analyst count |
| EPS consensus history | `eps_current`, `eps_7d_ago`, `eps_30d_ago`, `eps_60d_ago`, `eps_90d_ago` | Currency/share; earlier consensus for this forecast period |
| EPS revisions | `eps_up_7d`, `eps_up_30d`, `eps_down_7d`, `eps_down_30d` | Counts of upward/downward revisions |
| Growth comparisons | `stock_growth`, `industry_growth`, `sector_growth`, `index_growth` | Provider growth estimates, % |

`revenue_growth` on EST describes the selected forecast period. On EQUITY it describes the actual latest quarter's YoY growth.
Consensus history fields are provider-supplied comparisons within a current snapshot, not a historical estimate archive.

`/ANALYST AAPL` uses the following row kinds. Columns unrelated to a row's kind remain missing.

| `kind` | Fields | Interpretation |
| --- | --- | --- |
| `targets` | `target_mean`, `target_median`, `target_low`, `target_high`, `analyst_count`, `recommendation`, `recommendation_mean` | Price targets in `currency` per share; mean rating 1 = buy through 5 = sell |
| `recommendation` | `period`, `strong_buy`, `buy`, `hold`, `sell`, `strong_sell` | Counts by rating category for the provider's period |
| `rating_change` | `date`, `firm`, `from_grade`, `to_grade`, `action` | Dated upgrades, downgrades and initiations; provider grade/action text |

Shared fields: `symbol`, `kind`, `period`, `period_end`, `currency`, `revenue_currency`,
`is_estimate`, `source`, `freshness`, `observed_at`, `fetched_at`.
Dates/timestamps on these per-security tables use text.

## Holdings and allocations

`/HOLD SPY` queries the provider's reported positions, allocations and fund statistics.

| Field | Meaning |
| --- | --- |
| `fund` | Selected fund ticker |
| `kind` | `holding`, `asset`, `sector`, `bond_rating`, `bond_metric`, `equity_metric`, `fund_metric` |
| `symbol`, `name` | Position security or allocation/statistic label |
| `weight` | Portfolio/allocation weight, percentage points (`5` = 5%) |
| `value`, `unit` | Statistic value and its declared unit; read together |
| `currency` | Currency when the provider discloses it |
| `report_date` | Disclosed holdings/reporting date; missing stays N/A |
| `source`, `freshness`, `observed_at`, `fetched_at` | Provenance and snapshot timestamps |

```text
/HOLD SPY .WHERE kind = 'sector' .SORT weight DESC
/HOLD SPY .WHERE kind = 'fund_metric' .FIELDS name,value,unit
/ANALYST AAPL .WHERE kind = 'targets' .FIELDS target_mean,target_low,target_high,analyst_count,currency
```

Positions can be limited to the top ten disclosed holdings. Do not infer a complete portfolio or require weights to sum to 100%.
For ambiguous provider statistics, `unit = 'provider_value'` preserves the value without assuming or inverting a ratio.

## Metric records, dates and coverage

METRICS fields: `symbol`, `metric`, `name`, `value`, `unit`, `period`, `period_end`, `fiscal_year`,
`filing_date`, `currency`, `is_estimate`, `source`, `freshness`, `observed_at`, `fetched_at`.
Yahoo is the default; `SEC [YYYY-MM-DD]` uses eligible facts filed by that date.
SEC supports a subset of metrics. Missing/unsupported data remains N/A.
A financial period end is not its publication date; unknown Yahoo filing/report dates stay missing.

Supported WHERE conditions and primary sorts execute at Yahoo before results are retrieved.
Other conditions use bounded scans and grouped per-security financial lookups.
Page caches last five minutes; financial snapshots refresh at six hours (metrics), one hour (estimates),
and 24 hours (holdings). Ctrl+R forces a refresh.
Defaults: `[screener].max_pages = 8` (up to 250 rows/page), `max_enrich_symbols = 25`.
Narrow provider-supported filters first. Local scan/enrichment limits can produce partial rankings or missing fields;
the page reports coverage. Remote filtering can succeed even if Yahoo omits the metric from returned quote rows.
Freshness is the fetch status, not a promise that the underlying financial report or market quote is current.
Yahoo failures retain stored data with STALE metadata, or show N/A when nothing is stored.
