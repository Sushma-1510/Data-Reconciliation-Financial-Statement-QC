# Notes

## Limitations / Next steps

- 818 10-K filers matched no listed ticker; some are probably CIK changes after restructuring (e.g. XOM). Next step: match them by normalized name.
- revenue_not_comparable_industry uses InterestIncomeExpenseNet, which is too broad: non-financial companies also use it for net interest on debt, so some of the 85 "other" filings may be flagged wrongly.
- Values reported only by segment (no company-wide total) are dropped by the "segments empty" filter, so check (b) can miss a valid explanation (see BlackRock below).
- "Revenues wins" can pick a sub-line when a company tags its total with the contract-revenue tag (see Blue Owl below).
- num.txt has 2 malformed lines (tab inside the segments text, adsh 0001628280-26-020991); both are segmented and outside our tags, so no effect on results.

## Manual checks (2026-11-06, from FSDS pre.txt / num.txt = statements as filed on EDGAR)

- BlackRock (BLK), check (b) fail, gap $5,654M: "Redeemable noncontrolling interests" ($5,427M) is tagged TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests but reported only by segment (SubCo $2,791M + CIPs $2,636M); plus MinorityInterest $227M = gap exactly. Not a data error; our filter dropped it.
- Blue Owl (OWL), revenue_component_exceeds_total: the income statement "Revenues" line ($2,870M) is tagged RevenueFromContractWithCustomerExcludingAssessedTax; the Revenues tag ($568M) is not on the income statement. Our rule picks the wrong value for OWL.
- Atlas Lithium (ATLX), revenue_component_exceeds_total: contract-revenue tag = "Gross revenues" ($121K), Revenues = "Net revenue" ($92K) after sales deductions. Correct tagging, not an anomaly.
