"""Step 6: join 10-K checks to the reconciled ticker table by CIK and summarize."""
import csv
import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = "reference.db"
SUB_PATH = Path("data/raw/sub.txt")
OUT_CSV = Path("output/company_fs_joined.csv")
LISTED = ["matched", "matched_exchange_differs"]
EXCHANGE_GROUP = {"NASDAQ": "Nasdaq", "NYSE": "NYSE"}   # CBOE, TXSE, IEX -> "other"
FOREIGN_FORMS = {"20-F", "20-F/A", "40-F", "40-F/A"}
pd.set_option("display.width", 220)
pd.set_option("display.max_colwidth", 80)


def cik10(value):
    # SEC CIKs appear as 1090872, "14846", 14846.0 or "0000014846" -> "0000014846"
    return str(int(float(value))).zfill(10)


with sqlite3.connect(DB_PATH) as conn:
    tickers = pd.read_sql(
        "select cik, ticker, category, nt_exchange, is_etf from ticker_review "
        "where category in ('matched', 'matched_exchange_differs')", conn)
    checks = pd.read_sql("select * from fs_checks", conn)

# ---------- one row per listed company (CIK) ----------
tickers["cik10"] = tickers["cik"].map(cik10)
tickers["exchange_group"] = tickers["nt_exchange"].map(EXCHANGE_GROUP).fillna("other")
tickers["is_etf"] = tickers["is_etf"].fillna(0).astype(bool)
companies = tickers.groupby("cik10").agg(
    tickers=("ticker", lambda s: ", ".join(sorted(s))),
    n_tickers=("ticker", "count"),
    exchange_group=("exchange_group", lambda s: s.mode().iloc[0]),   # most common exchange
    n_exchange_groups=("exchange_group", "nunique"),
    is_etf_any=("is_etf", "max"),
    ticker_exchange_differs=("category", lambda s: (s == "matched_exchange_differs").any()),
).reset_index()
print(f"listed companies (CIKs with a matched ticker): {len(companies)}, "
      f"on more than one exchange group: {(companies['n_exchange_groups'] > 1).sum()}")

# ---------- one row per company from the 10-K checks (latest period if several) ----------
checks["cik10"] = checks["cik"].map(cik10)
checks["n_10k"] = checks.groupby("cik10")["adsh"].transform("count")
latest = checks.sort_values(["period", "adsh"], ascending=False).drop_duplicates("cik10")


def failure_flags(row):
    flags = []
    if row["check_a"] == "fail":
        flags.append("check_a_fail")
    if row["check_b"] == "fail":
        flags.append("check_b_fail")
    if row["revenue_component_exceeds_total"]:
        flags.append("revenue_component_exceeds_total")
    return flags


def info_notes(row):
    notes = []
    if row["check_a"] == "not_applicable":
        notes.append("check_a_not_applicable")
    if row["check_b"] == "not_applicable":
        notes.append("check_b_not_applicable")
    if row["check_b"] == "explained":
        notes.append(f"check_b_explained ({row['check_b_explained_by']})")
    if row["liabilities_is_derived"]:
        notes.append("liabilities_derived")
    if row["revenue_component_smaller"]:
        notes.append("revenue_component_smaller")
    if row["revenue_not_comparable_industry"]:
        notes.append("revenue_not_comparable_industry")
    if pd.notna(row["revenue_note"]):
        notes.append("revenue_includes_sales_tax")
    if pd.isna(row["revenue_value"]):
        notes.append("no_revenue_value")
    return ", ".join(notes)


latest["filing_flags"] = latest.apply(failure_flags, axis=1)
latest["info_notes"] = latest.apply(info_notes, axis=1)

CHECK_COLS = ["cik10", "adsh", "name", "sic", "sic_group", "period", "fy", "n_10k",
              "Assets", "Liabilities", "StockholdersEquity", "LSE", "NetIncomeLoss",
              "revenue_value", "revenue_source_tag", "revenue_note",
              "check_a", "check_b", "check_b_gap", "check_b_explained_by",
              "filing_flags", "info_notes"]
joined = companies.merge(latest[CHECK_COLS], on="cik10", how="left")
joined["has_10k"] = joined["adsh"].notna()


def all_flags(row):
    flags = list(row["filing_flags"]) if isinstance(row["filing_flags"], list) else []
    if row["ticker_exchange_differs"]:
        flags.append("ticker_exchange_differs")
    return ", ".join(flags)


joined["flags"] = joined.apply(all_flags, axis=1)
joined = joined.drop(columns="filing_flags")
joined["all_checks_pass"] = joined["has_10k"] & (joined["flags"] == "")

# ---------- why no 10-K? look at the other forms each company filed this quarter ----------
sub_all = pd.read_csv(SUB_PATH, sep="\t", dtype=str, keep_default_na=False, na_values=[""],
                      quoting=csv.QUOTE_NONE, usecols=["cik", "form"])
sub_all["cik10"] = sub_all["cik"].map(cik10)
forms_by_cik = sub_all.groupby("cik10")["form"].apply(set)


def no_10k_reason(row):
    if row["has_10k"]:
        return None
    forms = forms_by_cik.get(row["cik10"], set())
    if forms & FOREIGN_FORMS:
        return "foreign filer (20-F / 40-F)"
    if forms & {"10-K/A", "10-KT"}:
        return "10-K/A or 10-KT only"
    if forms & {"10-Q", "10-Q/A"}:
        return "10-Q this quarter (fiscal year ends in another quarter)"
    if forms:
        return "other forms only (" + ", ".join(sorted(forms)) + ")"
    return "no filing in this quarter's FSDS"


joined["no_10k_reason"] = joined.apply(no_10k_reason, axis=1)

# ---------- (a) coverage ----------
print("\n===== (a) listed companies with a 10-K this quarter =====")
print(pd.crosstab(joined["has_10k"].map({True: "has 10-K", False: "no 10-K"}),
                  joined["is_etf_any"].map({True: "ETF", False: "not ETF"}), margins=True).to_string())
print("\nwhy no 10-K:")
print(joined["no_10k_reason"].value_counts().head(12).to_string())
unlisted = checks[~checks["cik10"].isin(companies["cik10"])]["cik10"].nunique()
print(f"\n(for context: 10-K filers this quarter with no matched listed ticker: {unlisted})")

# ---------- (b) pass rate by exchange ----------
with_10k = joined[joined["has_10k"]]
print("\n===== (b) share passing all checks, by exchange =====")
summary = with_10k.groupby("exchange_group").agg(
    companies=("cik10", "count"), pass_all=("all_checks_pass", "sum"))
summary.loc["total"] = summary.sum()
summary["pct_pass"] = (100 * summary["pass_all"] / summary["companies"]).round(1)
print(summary.to_string())
print("\nflag counts (companies with a 10-K):")
print(with_10k["flags"].str.split(", ").explode().loc[lambda s: s != ""].value_counts().to_string())

# ---------- (c) largest flagged companies ----------
print("\n===== (c) 10 largest companies by Assets with any flag =====")
flagged = with_10k[with_10k["flags"] != ""]
print(flagged.nlargest(10, "Assets")[["tickers", "name", "exchange_group", "Assets", "flags"]]
      .to_string(index=False))

with sqlite3.connect(DB_PATH) as conn:
    joined.to_sql("company_fs_joined", conn, if_exists="replace", index=False)
OUT_CSV.parent.mkdir(exist_ok=True)
joined.to_csv(OUT_CSV, index=False)
print(f"\nsaved company_fs_joined ({len(joined)} rows) to SQLite and {OUT_CSV}")
