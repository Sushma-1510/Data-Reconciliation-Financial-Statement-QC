"""Step 4: extract key 10-K balance sheet and income statement values from SEC FSDS (2026q1)."""
import csv
import sqlite3
from pathlib import Path

import pandas as pd

DATA = Path("data/raw")
DB_PATH = "reference.db"
pd.set_option("display.width", 200)

BALANCE_SHEET_TAGS = ["Assets", "Liabilities", "StockholdersEquity", "LiabilitiesAndStockholdersEquity"]
INCOME_TAGS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "NetIncomeLoss"]
ALL_TAGS = BALANCE_SHEET_TAGS + INCOME_TAGS
# qtrs = number of quarters a value covers: 0 = point in time (balance sheet), 4 = full year
EXPECTED_QTRS = {tag: "0" for tag in BALANCE_SHEET_TAGS} | {tag: "4" for tag in INCOME_TAGS}

# FSDS files are tab-delimited and not quoted. Read every field as text; only empty = missing.
READ_OPTIONS = dict(sep="\t", dtype=str, keep_default_na=False, na_values=[""],
                    quoting=csv.QUOTE_NONE, encoding="utf-8")

# ---------- sub.txt: one row per filing ----------
sub = pd.read_csv(DATA / "sub.txt", **READ_OPTIONS)
print(f"sub.txt filings: {len(sub)}")
sub_10k = sub[sub["form"] == "10-K"][
    ["adsh", "cik", "name", "sic", "form", "period", "fy", "fp", "fye", "filed", "prevrpt"]
]
print(f"10-K filings: {len(sub_10k)}, companies: {sub_10k['cik'].nunique()}")
twice = sub_10k[sub_10k["cik"].duplicated(keep=False)]
print("companies with more than one 10-K this quarter:")
print(twice[["adsh", "cik", "name", "period", "fy", "filed"]].to_string(index=False))

# ---------- num.txt: one row per reported value ----------
NUM_COLS = ["adsh", "tag", "version", "ddate", "qtrs", "uom", "segments", "coreg", "value"]
tenk_adsh = set(sub_10k["adsh"])
pieces = []
wide_pieces = []   # every tag, same filters - for profiling and gap explanations later
total_rows = 0
for chunk in pd.read_csv(DATA / "num.txt", usecols=NUM_COLS, chunksize=1_000_000, **READ_OPTIONS):
    total_rows += len(chunk)
    chunk = chunk[chunk["adsh"].isin(tenk_adsh)]
    pieces.append(chunk[chunk["tag"].isin(ALL_TAGS)])
    wide_pieces.append(chunk[chunk["segments"].isna() & chunk["coreg"].isna()
                             & (chunk["uom"] == "USD") & chunk["qtrs"].isin(["0", "4"])])
num = pd.concat(pieces, ignore_index=True)
print(f"\nnum.txt rows: {total_rows}")

# wide table: all tags, consolidated, USD, current period, point-in-time or full year
wide = pd.concat(wide_pieces, ignore_index=True).merge(sub_10k[["adsh", "period"]], on="adsh")
wide = wide[wide["ddate"] == wide["period"]].drop(columns=["segments", "coreg", "period"])
wide["value"] = wide["value"].astype(float)
print(f"wide table (all tags, same filters): {len(wide)} rows, {wide['tag'].nunique()} distinct tags")


def step(label, df):
    print(f"  {label:<45} rows {len(df):>7}   filings {df['adsh'].nunique():>5}")
    return df


print("filter funnel:")
num = step("1-2. 10-K filings, 7 tags", num)
num = step("3. segments empty (company-wide total)", num[num["segments"].isna()])
num = step("4. coreg empty (consolidated entity)", num[num["coreg"].isna()])
num = step("5. uom = USD", num[num["uom"] == "USD"])
num = step("6. qtrs = 0 (balance sheet) / 4 (income)", num[num["qtrs"] == num["tag"].map(EXPECTED_QTRS)])
num = num.merge(sub_10k[["adsh", "period"]], on="adsh")
num = step("7. ddate = filing period (current year)", num[num["ddate"] == num["period"]])

num["value"] = num["value"].astype(float)
num["is_custom_tag"] = num["version"] == num["adsh"]   # company extension, not us-gaap
print(f"\ncustom (company-defined) tags among kept rows: {num['is_custom_tag'].sum()}")
print("versions:", num["version"].value_counts().to_dict())

# a filing should report each tag once; count where it doesn't (rules come later)
dupes = num[num.duplicated(subset=["adsh", "tag"], keep=False)]
conflicting = dupes.groupby(["adsh", "tag"])["value"].nunique()
print(f"\n(filing, tag) pairs reported more than once: {dupes.groupby(['adsh', 'tag']).ngroups}, "
      f"of which with different values: {(conflicting > 1).sum()}")

# ---------- profile: how many filings report each tag ----------
# A tag can be present with an empty value (XBRL "nil"). Count only real values.
n_filings = len(sub_10k)
with_value = num[num["value"].notna()]
print(f"\n===== filings reporting each tag with a value (out of {n_filings} 10-Ks) =====")
coverage = with_value.groupby("tag")["adsh"].nunique().reindex(ALL_TAGS).fillna(0).astype(int)
empty = num[num["value"].isna()].groupby("tag")["adsh"].nunique().reindex(ALL_TAGS).fillna(0).astype(int)
print(pd.DataFrame({"filings": coverage, "pct": (100 * coverage / n_filings).round(1),
                    "tag_present_but_empty": empty}).to_string())

reported = with_value.groupby("adsh")["tag"].apply(set).reindex(sub_10k["adsh"]).apply(
    lambda tags: tags if isinstance(tags, set) else set())
print(f"\nfilings with none of the 7 tags: {(reported.apply(len) == 0).sum()}")


def combo(a, b):
    has_a = reported.apply(lambda t: a in t)
    has_b = reported.apply(lambda t: b in t)
    return {"both": int((has_a & has_b).sum()), f"{a} only": int((has_a & ~has_b).sum()),
            f"{b} only": int((~has_a & has_b).sum()), "neither": int((~has_a & ~has_b).sum())}


print("\nrevenue tags:", combo("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"))
print("liabilities vs equity:", combo("Liabilities", "StockholdersEquity"))
print("assets vs liabilities+equity:", combo("Assets", "LiabilitiesAndStockholdersEquity"))

with sqlite3.connect(DB_PATH) as conn:
    sub_10k.to_sql("fs_sub_10k", conn, if_exists="replace", index=False)
    num.to_sql("fs_values", conn, if_exists="replace", index=False)
    wide.to_sql("fs_values_current", conn, if_exists="replace", index=False)
print(f"\nsaved fs_sub_10k: {len(sub_10k)} rows, fs_values: {len(num)} rows, "
      f"fs_values_current: {len(wide)} rows")
