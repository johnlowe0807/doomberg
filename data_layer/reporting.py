"""Fiscal statement calculations shared by FA and normalized extraction.

The matrix preserves the existing USD statement conventions. Full native-currency and
unlisted source concepts remain available through reporting/facts/statements.
"""
from datetime import date
M = 'm'

STATEMENTS = {
    "income": ("Income statement", "flow", [
        ("Revenue", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                     "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet"], M),
        ("Cost of revenue", ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"], M),
        ("Gross profit", ["GrossProfit"], M),
        ("R&D", ["ResearchAndDevelopmentExpense"], M),
        ("SG&A", ["SellingGeneralAndAdministrativeExpense"], M),
        ("Operating income", ["OperatingIncomeLoss"], M),
        ("Interest expense", ["InterestExpense", "InterestExpenseNonoperating"], M),
        ("Pretax income", ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                           "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"], M),
        ("Income tax", ["IncomeTaxExpenseBenefit"], M),
        ("Net income", ["NetIncomeLoss", "ProfitLoss"], M),
        ("EPS basic", ["EarningsPerShareBasic"], "eps"),
        ("EPS diluted", ["EarningsPerShareDiluted"], "eps"),
        ("Diluted shares", ["WeightedAverageNumberOfDilutedSharesOutstanding"], M),
    ]),
    "balance": ("Balance sheet", "inst", [
        ("Cash & equivalents", ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"], M),
        ("Short-term investments", ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesCurrent"], M),
        ("Receivables", ["AccountsReceivableNetCurrent"], M),
        ("Inventory", ["InventoryNet"], M),
        ("Current assets", ["AssetsCurrent"], M),
        ("PP&E net", ["PropertyPlantAndEquipmentNet"], M),
        ("Goodwill", ["Goodwill"], M),
        ("Total assets", ["Assets"], M),
        ("Accounts payable", ["AccountsPayableCurrent"], M),
        ("Current liabilities", ["LiabilitiesCurrent"], M),
        ("Long-term debt", ["LongTermDebtNoncurrent", "LongTermDebt"], M),
        ("Total liabilities", ["Liabilities"], M),
        ("Retained earnings", ["RetainedEarningsAccumulatedDeficit"], M),
        ("Shareholders' equity", ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"], M),
    ]),
    "cash": ("Cash flow", "flow", [
        ("Operating cash flow", ["NetCashProvidedByUsedInOperatingActivities"], M),
        ("D&A", ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet"], M),
        ("Stock comp", ["ShareBasedCompensation"], M),
        ("Capex", ["PaymentsToAcquirePropertyPlantAndEquipment"], M),
        ("Investing cash flow", ["NetCashProvidedByUsedInInvestingActivities"], M),
        ("Buybacks", ["PaymentsForRepurchaseOfCommonStock"], M),
        ("Dividends paid", ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"], M),
        ("Financing cash flow", ["NetCashProvidedByUsedInFinancingActivities"], M),
    ]),
}


def fiscal_calendar(facts: dict) -> list[tuple[date, int]]:
    """Identify each filing's own fiscal end, excluding its comparative years."""
    taxo = {**facts.get("facts", {}).get("ifrs-full", {}), **facts.get("facts", {}).get("us-gaap", {})}
    concepts = {c for _, kind, lines in STATEMENTS.values() if kind == "flow" for _, cs, _ in lines for c in cs}
    filings = {}
    for concept in concepts:
        for records in taxo.get(concept, {}).get("units", {}).values():
            for x in records:
                if not str(x.get("form", "")).startswith(("10-K", "20-F", "40-F")) or not x.get("fy"):
                    continue
                try:
                    end, start = date.fromisoformat(x["end"]), date.fromisoformat(x["start"])
                    filed = x["filed"]
                    if not 300 <= (end - start).days <= 400 or x["end"] > filed:
                        continue
                    key = (x.get("accn", filed), int(x["fy"]), filed)
                    filings[key] = max(end, filings.get(key, end))
                except (KeyError, TypeError, ValueError):
                    continue
    anchors = {}
    for (_, fy, filed), end in sorted(filings.items(), key=lambda kv: kv[0][2]):
        anchors.setdefault(end, fy)
    return sorted(anchors.items())


def fiscal_period(end: date, calendar: list[tuple[date, int]]) -> tuple[int, int] | None:
    # Match the nearest known fiscal year-end, then count quarters from it. This also
    # covers the current unfinished year and 52/53-week reporting calendars.
    if not calendar:
        return None
    anchor, year = min(calendar, key=lambda row: abs((row[0] - end).days))
    quarters = round((end - anchor).days / 91.3125)
    if abs((end - anchor).days - quarters * 91.3125) > 25:
        return None
    return year + (quarters + 3) // 4, (quarters - 1) % 4 + 1


def xbrl_line(facts: dict, concepts: list[str], kind: str, annual: bool,
              calendar: list[tuple[date, int]] | None = None) -> dict[str, float]:
    """Use actual fiscal periods; derive standalone cash-flow quarters from YTD facts."""
    calendar = fiscal_calendar(facts) if calendar is None else calendar
    taxo = {**facts.get("facts", {}).get("ifrs-full", {}), **facts.get("facts", {}).get("us-gaap", {})}
    picked, cumulative = {}, {}
    for priority, concept in enumerate(concepts):
        unit = "USD/shares" if concept.startswith("EarningsPerShare") else "shares" if "Shares" in concept else "USD"
        for x in taxo.get(concept, {}).get("units", {}).get(unit, []):
            if not str(x.get("form", "")).startswith(("10-K", "10-Q", "20-F", "40-F")):
                continue
            try:
                end = date.fromisoformat(x["end"])
                period = fiscal_period(end, calendar)
                if period is None:
                    continue
                year, quarter = period
                rank = (x.get("filed", ""), -priority)
                label = str(year) if annual else f"{year}Q{quarter}"
                value = float(x["val"])
                if kind == "inst":
                    if annual and quarter != 4:
                        continue
                else:
                    duration = (end - date.fromisoformat(x["start"])).days
                    if annual:
                        if quarter != 4 or not 300 <= duration <= 400:
                            continue
                    elif not 60 <= duration <= 120:
                        if unit == "USD" and abs(duration - quarter * 91.3125) <= 30:
                            if label not in cumulative or rank > cumulative[label][0]:
                                cumulative[label] = (rank, value)
                        continue
                if label not in picked or rank > picked[label][0]:
                    picked[label] = (rank, value)
            except (KeyError, TypeError, ValueError):
                continue
    out = {label: value for label, (_, value) in picked.items()}
    if not annual and kind == "flow":
        for label, (_, value) in sorted(cumulative.items()):
            year, quarter = label.split("Q")
            q = int(quarter)
            previous = [out.get(f"{year}Q{i}") for i in range(1, q)]
            if label not in out and all(v is not None for v in previous):
                out[label] = value - sum(previous)
    return out


def derive_q4(line: dict[str, float], annual: dict[str, float]) -> None:
    for y, v in annual.items():
        qs = [line.get(f"{y}Q{i}") for i in (1, 2, 3)]
        if f"{y}Q4" not in line and all(q is not None for q in qs):
            line[f"{y}Q4"] = v - sum(qs)


def statement_matrix(facts, statement, annual):
    name, kind, lines = STATEMENTS[statement]
    calendar = fiscal_calendar(facts)
    rows = []
    for label, concepts, unit in lines:
        values = xbrl_line(facts, concepts, kind, annual, calendar)
        if not annual and kind == 'flow' and unit == M and not any('Shares' in c for c in concepts):
            derive_q4(values, xbrl_line(facts, concepts, kind, True, calendar))
        rows.append((label, values, unit))
    if statement == 'cash':
        operating, capex = rows[0][1], rows[3][1]
        rows.insert(4, ('Free cash flow', {period: operating[period]-capex[period] for period in operating if period in capex}, M))
    periods = sorted({period for _, values, _ in rows for period in values}, reverse=True)
    return name, periods, rows
