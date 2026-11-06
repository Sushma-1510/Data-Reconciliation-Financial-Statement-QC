"""Step 5: 10-K checks - missing-revenue profile, revenue source, balance sheet ties."""
import sqlite3
from itertools import product
from pathlib import Path

import pandas as pd

from review_file import write_review_file

DB_PATH = "reference.db"
REVIEW_CSV = Path("output/review_check_b_failures.csv")
TOLERANCE = 0.001          # balance checks: gap up to 0.1% of the larger value counts as equal
REVENUE_TOLERANCE = 0.01   # revenue tags: differ by more than 1% -> needs review
pd.set_option("display.width", 220)
pd.set_option("display.max_colwidth", 50)

# tag -> qtrs it must have (0 = balance sheet date, 4 = full year)
NEEDED_TAGS = {
    "Assets": "0", "Liabilities": "0", "StockholdersEquity": "0", "LiabilitiesAndStockholdersEquity": "0",
    "Revenues": "4", "RevenueFromContractWithCustomerExcludingAssessedTax": "4", "NetIncomeLoss": "4",
    # used only to explain gaps in check (b)
    "MinorityInterest": "0",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": "0",
    "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests": "0",
    "TemporaryEquityCarryingAmountAttributableToParent": "0",
    "RedeemableNoncontrollingInterestEquityCarryingAmount": "0",
    "TemporaryEquityValueExcludingAdditionalPaidInCapital": "0",
    "TemporaryEquityAggregateAmountOfRedemptionRequirement": "0",
    "RedeemableNoncontrollingInterestEquityCommonCarryingAmount": "0",
    "RedeemableNoncontrollingInterestEquityPreferredCarryingAmount": "0",
    "RevenueFromContractWithCustomerIncludingAssessedTax": "4",   # third revenue source
}
SHORT = {  # shorter column names for readability
    "LiabilitiesAndStockholdersEquity": "LSE",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "RevenueFromContracts",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": "EquityInclNCI",
    "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests": "TempEquityInclNCI",
    "TemporaryEquityCarryingAmountAttributableToParent": "TempEquityParent",
    "RedeemableNoncontrollingInterestEquityCarryingAmount": "RedeemableNCI",
    "TemporaryEquityValueExcludingAdditionalPaidInCapital": "TempEquityExclAPIC",
    "TemporaryEquityAggregateAmountOfRedemptionRequirement": "TempEquityRedemptionReq",
    "RedeemableNoncontrollingInterestEquityCommonCarryingAmount": "RedeemableNCICommon",
    "RedeemableNoncontrollingInterestEquityPreferredCarryingAmount": "RedeemableNCIPreferred",
    "RevenueFromContractWithCustomerIncludingAssessedTax": "RevenueInclTax",
}
# bank-style income tags: mark "other" filings reporting these as not comparable
BANK_INCOME_TAGS = ["InterestAndDividendIncomeOperating", "InterestIncomeExpenseNet"]

with sqlite3.connect(DB_PATH) as conn:
    sub = pd.read_sql("select adsh, cik, name, sic, period, fy from fs_sub_10k", conn)
    current = pd.read_sql("select adsh, tag, qtrs, value from fs_values_current", conn)

# ---------- one row per filing, one column per needed tag ----------
needed = current[current["tag"].map(NEEDED_TAGS) == current["qtrs"]]
print(f"duplicate (filing, tag) rows among needed tags: {needed.duplicated(['adsh', 'tag']).sum()}")
values = needed.pivot(index="adsh", columns="tag", values="value").rename(columns=SHORT)
f = sub.merge(values, on="adsh", how="left")
for col in [SHORT.get(t, t) for t in NEEDED_TAGS]:
    if col not in f.columns:
        f[col] = float("nan")


def relative_gap(a, b):
    # |a - b| as a share of the larger absolute value; 0 when both are 0
    larger = pd.concat([a.abs(), b.abs()], axis=1).max(axis=1)
    return ((a - b).abs() / larger).fillna(0)


# ---------- 1. who has no revenue tag? ----------
def sic_group(sic):
    if pd.isna(sic):
        return "missing SIC"
    sic = int(sic)
    if 6020 <= sic <= 6036:
        return "bank"
    if 6300 <= sic <= 6399:
        return "insurance"
    if sic == 6798:
        return "REIT"
    if sic == 6770:
        return "blank check / SPAC"
    return "other"


f["sic_group"] = pd.to_numeric(f["sic"], errors="coerce").map(sic_group)
f["has_revenue_tag"] = f["Revenues"].notna() | f["RevenueFromContracts"].notna()
no_rev = f[~f["has_revenue_tag"]]

print(f"\n===== 1. filings with neither revenue tag: {len(no_rev)} =====")
by_group = pd.DataFrame({
    "no_revenue_tag": no_rev["sic_group"].value_counts(),
    "all_10k": f["sic_group"].value_counts(),
}).fillna(0).astype(int)
by_group["pct_of_no_rev"] = (100 * by_group["no_revenue_tag"] / len(no_rev)).round(1)
by_group["pct_of_group_missing"] = (100 * by_group["no_revenue_tag"] / by_group["all_10k"]).round(1)
print(by_group.sort_values("no_revenue_tag", ascending=False).to_string())

full_year = current[(current["qtrs"] == "4") & current["adsh"].isin(no_rev["adsh"])]
print(f"\n15 most common full-year USD tags in these {len(no_rev)} filings:")
top = full_year.groupby("tag")["adsh"].nunique().nlargest(15)
print(pd.DataFrame({"filings": top, "pct": (100 * top / len(no_rev)).round(1)}).to_string())

print("\ntop 5 full-year tags per SIC group:")
group_of = no_rev.set_index("adsh")["sic_group"]
full_year = full_year.assign(sic_group=full_year["adsh"].map(group_of))
for group, rows in full_year.groupby("sic_group"):
    n = (no_rev["sic_group"] == group).sum()
    top5 = rows.groupby("tag")["adsh"].nunique().nlargest(5)
    print(f"\n  {group} ({n} filings)")
    for tag, count in top5.items():
        print(f"    {count:>5}  {tag}")

# industry revenue that isn't comparable to a revenue line: banks, plus "other" filings
# that report a bank-style income tag (with an actual value)
bank_style = full_year[full_year["tag"].isin(BANK_INCOME_TAGS) & full_year["value"].notna()]
other_bank_style = set(bank_style.loc[bank_style["sic_group"] == "other", "adsh"])
f["revenue_not_comparable_industry"] = ~f["has_revenue_tag"] & (
    (f["sic_group"] == "bank") | f["adsh"].isin(other_bank_style))
print(f"\nrevenue_not_comparable_industry: {f['revenue_not_comparable_industry'].sum()} filings "
      f"(banks {((f['sic_group'] == 'bank') & ~f['has_revenue_tag']).sum()}, "
      f"other with bank-style income {len(other_bank_style)})")

# ---------- 2. revenue: which tag is used ----------
both = f["Revenues"].notna() & f["RevenueFromContracts"].notna()
# order of preference: Revenues, then contract revenue excl. tax, then contract revenue incl. tax
f["revenue_value"] = f["Revenues"].fillna(f["RevenueFromContracts"]).fillna(f["RevenueInclTax"])
f["revenue_source_tag"] = None
f.loc[f["RevenueInclTax"].notna(), "revenue_source_tag"] = "RevenueFromContractWithCustomerIncludingAssessedTax"
f.loc[f["RevenueFromContracts"].notna(), "revenue_source_tag"] = "RevenueFromContractWithCustomerExcludingAssessedTax"
f.loc[f["Revenues"].notna(), "revenue_source_tag"] = "Revenues"
f["revenue_note"] = None
f.loc[f["revenue_source_tag"] == "RevenueFromContractWithCustomerIncludingAssessedTax", "revenue_note"] = \
    "includes sales taxes - exclude from strict comparisons"
f["revenue_tag_gap"] = relative_gap(f["Revenues"], f["RevenueFromContracts"]).where(both)
differs = both & (f["revenue_tag_gap"] > REVENUE_TOLERANCE)
# contract revenue is one part of total revenue: smaller is expected, larger is an anomaly
f["revenue_component_smaller"] = differs & (f["RevenueFromContracts"] < f["Revenues"])
f["revenue_component_exceeds_total"] = differs & (f["RevenueFromContracts"] > f["Revenues"])

print(f"\n===== 2. revenue source =====")
print(f["revenue_source_tag"].fillna("none").value_counts().to_string())
has_value = f["revenue_value"].notna()
print(f"revenue coverage: {has_value.sum()} of {len(f)} filings ({100 * has_value.mean():.1f}%); "
      f"without revenue: {(~has_value).sum()}, of which marked not comparable (industry): "
      f"{(~has_value & f['revenue_not_comparable_industry']).sum()}")
print(f"filings with both tags: {both.sum()}, identical: {(f['revenue_tag_gap'] == 0).sum()}, "
      f"differ by more than {REVENUE_TOLERANCE:.0%}: {differs.sum()}")
print(f"  revenue_component_smaller (expected): {f['revenue_component_smaller'].sum()}")
print(f"  revenue_component_exceeds_total (anomaly): {f['revenue_component_exceeds_total'].sum()}")
print(f[f["revenue_component_exceeds_total"]][
    ["name", "Revenues", "RevenueFromContracts", "revenue_tag_gap"]].to_string(index=False))

# ---------- 3a. Assets vs LiabilitiesAndStockholdersEquity (independent) ----------
has_a = f["Assets"].notna() & f["LSE"].notna()
f["check_a_gap"] = (f["Assets"] - f["LSE"]).where(has_a)
f["check_a"] = "not_applicable"
f.loc[has_a, "check_a"] = "fail"
f.loc[has_a & (relative_gap(f["Assets"], f["LSE"]) <= TOLERANCE), "check_a"] = "pass"

print(f"\n===== 3a. Assets vs LiabilitiesAndStockholdersEquity (tolerance {TOLERANCE:.1%}) =====")
print(f["check_a"].value_counts().to_string())
print(f"exact matches: {(f['check_a_gap'] == 0).sum()}")
print(f[f["check_a"] == "fail"][["name", "sic_group", "Assets", "LSE", "check_a_gap"]].to_string(index=False))

# ---------- 3b. Liabilities + StockholdersEquity vs LSE (reported values only) ----------
# Liabilities derived from LSE - Equity is stored separately and never used here.
derive = f["Liabilities"].isna() & f["LSE"].notna() & f["StockholdersEquity"].notna()
f["liabilities_derived"] = (f["LSE"] - f["StockholdersEquity"]).where(derive)
f["liabilities_is_derived"] = derive


def gap_options(row):
    # Amounts allowed to sit between liabilities and parent equity, grouped by kind.
    # Within a kind the tags are alternative measures of the same amount, so an
    # explanation uses at most ONE option per kind (no double counting).
    def val(col):
        return row[col] if pd.notna(row[col]) else None

    nci = {"MinorityInterest": val("MinorityInterest")}
    if val("EquityInclNCI") is not None:
        nci["EquityInclNCI - StockholdersEquity"] = row["EquityInclNCI"] - row["StockholdersEquity"]
    temp = {col: val(col) for col in
            ["TempEquityInclNCI", "TempEquityParent", "TempEquityExclAPIC", "TempEquityRedemptionReq"]}
    redeemable = {col: val(col) for col in ["RedeemableNCI", "RedeemableNCICommon", "RedeemableNCIPreferred"]}
    if val("RedeemableNCICommon") is not None and val("RedeemableNCIPreferred") is not None:
        redeemable["RedeemableNCICommon + RedeemableNCIPreferred"] = (
            row["RedeemableNCICommon"] + row["RedeemableNCIPreferred"])
    kinds = [nci, temp, redeemable]
    # keep only options that have a value; add "not used" (None) to every kind
    return [[(name, v) for name, v in kind.items() if v is not None] + [None] for kind in kinds]


def find_explanation(gap, allowed, row):
    candidates = []
    for choice in product(*gap_options(row)):          # one option (or none) per kind
        used = [c for c in choice if c is not None]
        if used and abs(gap - sum(v for _, v in used)) <= allowed:
            candidates.append(used)
    if not candidates:
        return None
    simplest = min(candidates, key=len)                  # fewest components wins
    return " + ".join(name for name, _ in simplest)


def check_b(row):
    if pd.isna(row["Liabilities"]) or pd.isna(row["StockholdersEquity"]) or pd.isna(row["LSE"]):
        return pd.Series({"check_b": "not_applicable", "check_b_gap": None, "check_b_explained_by": None})
    gap = row["LSE"] - (row["Liabilities"] + row["StockholdersEquity"])
    allowed = TOLERANCE * abs(row["LSE"])
    if abs(gap) <= allowed:
        return pd.Series({"check_b": "pass", "check_b_gap": gap, "check_b_explained_by": None})
    explanation = find_explanation(gap, allowed, row)
    if explanation:
        return pd.Series({"check_b": "explained", "check_b_gap": gap, "check_b_explained_by": explanation})
    return pd.Series({"check_b": "fail", "check_b_gap": gap, "check_b_explained_by": None})


f[["check_b", "check_b_gap", "check_b_explained_by"]] = f.apply(check_b, axis=1)

print(f"\n===== 3b. Liabilities + StockholdersEquity vs LSE (all three reported) =====")
print(f["check_b"].value_counts().to_string())
print("\nexplained by:")
print(f["check_b_explained_by"].value_counts().to_string())
fails = f[f["check_b"] == "fail"].copy()
fails["gap_pct_of_lse"] = (100 * fails["check_b_gap"] / fails["LSE"]).round(2)
print(f"\nfailures by SIC group:")
print(fails["sic_group"].value_counts().to_string())
print("\n10 largest failures (by % of LSE):")
print(fails.reindex(fails["gap_pct_of_lse"].abs().sort_values(ascending=False).index).head(10)[
    ["name", "sic_group", "Liabilities", "StockholdersEquity", "LSE", "check_b_gap", "gap_pct_of_lse"]
].to_string(index=False))

print(f"\nderived liabilities (flagged, not used in 3b): {derive.sum()} filings")

# remaining failures go to a manual review file (merged by adsh, never overwritten)
review = pd.DataFrame({
    "adsh": fails["adsh"], "cik": fails["cik"], "name": fails["name"], "sic_group": fails["sic_group"],
    "Liabilities": fails["Liabilities"], "StockholdersEquity": fails["StockholdersEquity"],
    "LSE": fails["LSE"], "check_b_gap": fails["check_b_gap"], "gap_pct_of_lse": fails["gap_pct_of_lse"],
}).sort_values(["sic_group", "name"])
out = write_review_file(review, REVIEW_CSV, key="adsh")
print(f"\nwrote {REVIEW_CSV}: {len(out)} rows")
print(out["status"].value_counts().to_string())

with sqlite3.connect(DB_PATH) as conn:
    f.to_sql("fs_checks", conn, if_exists="replace", index=False)
print(f"\nsaved fs_checks: {len(f)} rows")
