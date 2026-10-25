"""Screening command adapter; parsing/validation/selection belong to the data layer."""
from data_layer.query_language import VERBS, apply_commands


def execute(view, line):
    candidate, show_help = apply_commands(view.table_query, line, view.dataset)
    if candidate != view.table_query:
        view.table_query = candidate
        view.reload()
    if show_help:
        from .graph_commands import GraphHelpScreen
        view.app.push_screen(GraphHelpScreen(view))
        return 'HELP'


def help_text(view):
    from data_layer.query_language import format_query
    fields = '\n'.join(f'  {name:<24} {spec.kind:<8} {spec.title}' + (f' [{spec.unit}]' if spec.unit else '') + (f' ({spec.period})' if spec.period else '') for name, spec in view.dataset.fields.items())
    return '''Security screening · shared query engine
WHERE expression — append a condition (AND with earlier WHERE commands)
SEARCH "text" — append a text search over symbol/name/type
SORT field [ASC|DESC], field [ASC|DESC] — replace ordering
FIELDS symbol, name, price — replace displayed columns; FIELDS * restores defaults
LIMIT n — maximum displayed rows (1–10000); selection/sorting happen first
CLEAR [WHERE|SEARCH|SORT|FIELDS|LIMIT|ALL] — reset a clause or the query
HELP / ? / HOME — this overview and field list
Operators: = != <> > >= < <= IN (...) LIKE CONTAINS IS NULL IS NOT NULL
AND binds before OR; use parentheses and NOT for explicit grouping.
Quote text/tickers: symbol = "BRK.B" · name CONTAINS "oil" · symbol IN ('AAPL', 'MSFT')
LIKE uses % for any text and _ for one character. Text comparisons ignore case.
Missing values never match comparisons, including NOT; use IS NULL explicitly.
Missing sort values appear last with ASC or DESC. %Chg is in percentage points.
Dot chains validate together: an error leaves all screening settings unchanged.
Examples:
  /EQUITY .WHERE price > 10 AND volume >= 1000000 .SORT volume DESC .LIMIT 20
  /FUTURES .SORT volume DESC .FIELDS symbol,name,price,volume,currency
  /EQUITY .WHERE exchange IN ('NYQ','NMS') .SORT price DESC
  /EQUITY .WHERE region = 'jp' .SORT price DESC
  /EQUITY .WHERE market_cap > 1000000000000 AND roe > 15 .FIELDS symbol,roe,revenue_growth
  /EST AAPL .WHERE period = '0y' .FIELDS eps_mean,eps_growth,revenue_mean
  /ANALYST AAPL .WHERE kind = 'rating_change' .SORT date DESC
  /HOLD SPY .WHERE kind = 'holding' AND weight > 5 .SORT weight DESC
  /METRICS AAPL SEC 2020-12-31 .WHERE value IS NOT NULL
  /CRYPTO .SEARCH "bitcoin" .SORT market_cap DESC
  .WHERE (pct_change > 2 OR pct_change < -2) AND price IS NOT NULL
Pages: /EQUITY /ETF /FUND /INDEX /FUTURES /CRYPTO /FX
/CMDTY redirects to FUTURES; /WEI to INDEX; /BONDS to filtered ETFs; /MOST to equity movers.
/FXC retains its matrix; /OMON retains option navigation.
/METRICS, /EST, /ANALYST and /HOLD query data for a selected security.
METRICS defaults to Yahoo; SEC [YYYY-MM-DD] selects SEC facts filed by that date.
Yahoo estimates/targets are current snapshots, not historical point-in-time estimates.
HOLD includes reported positions, sectors, asset classes, bond statistics and fees.
Reported positions may be top holdings only; unknown report dates stay N/A.
Percent units use percentage points; debt_equity is a ratio (1 means 100%).
Quarterly growth and TTM growth are separate fields; financial periods/currencies stay explicit.
Financial lookups are bounded ([screener].max_enrich_symbols defaults to 25).
Narrow server-supported conditions before local financial conditions to reduce requests.
All pages query Yahoo's screenable market universe; no fixed symbol lists are used.
Equities/ETFs default to US; explicit region or exchange conditions replace that default.
Funds default to exchange NAS. Crypto defaults to USD pairs; explicit currency conditions replace it.
FUTURES covers commodity and financial futures. Volume is Yahoo's reported volume.
Prices retain their quoted currency; cross-currency rankings do not convert values.
Supported WHERE/SORT operations execute at Yahoo before paging. Other conditions run locally
over sequential cached pages; incomplete rankings and lower-bound match counts are labelled.
Scans default to eight pages of at most 250 rows ([screener].max_pages, 1–40).
Narrow provider-supported WHERE conditions to reduce scanning. Results cache for five minutes;
Ctrl+R forces refresh. Failed Yahoo queries retain cached pages or show N/A, never a curated fallback.
Repeated SEARCH terms use AND. SORT/FIELDS/LIMIT replace their earlier settings.
Available fields for this page:
''' + fields + '\n\nActive query:\n' + format_query(view.table_query) + (
        '\n\nLast query status:\n' + view.result.meta.reason if view.result and view.result.meta.reason else '')
