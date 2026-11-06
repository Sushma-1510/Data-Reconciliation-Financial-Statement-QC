"""Step 3: explain unmatched rows, score name similarity, export rows for manual review."""
import difflib
import re
import sqlite3
from pathlib import Path

import pandas as pd

from review_file import write_review_file

DB_PATH = "reference.db"
REVIEW_CSV = Path("output/review_sec_only.csv")
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 70)

with sqlite3.connect(DB_PATH) as conn:
    df = pd.read_sql("select * from ticker_match", conn)
df["cik"] = df["cik"].astype("Int64")

# ---------- name normalization ----------
# Legal forms and security descriptions that say nothing about which company it is.
NAME_NOISE = [
    "common stock", "ordinary shares", "american depositary shares", "depositary shares",
    "class a", "class b", "class c",
    "incorporated", "inc", "corporation", "corp", "company", "co",
    "limited", "ltd", "plc", "llc",
]
NOISE_PATTERN = re.compile(r"\b(" + "|".join(NAME_NOISE) + r")\b")


def normalize_name(name):
    if name is None or pd.isna(name):
        return ""
    s = name.lower()
    s = re.sub(r"/[a-z]+/?", " ", s)      # SEC state tags: "/DE/", "/NEW/", "/OLD"
    s = s.split(" - ")[0]                  # Nasdaq style: "Hilton ... - Common Stock"
    s = re.sub(r"[^a-z0-9]+", " ", s)      # punctuation -> space
    s = NOISE_PATTERN.sub(" ", s)          # drop legal forms / security descriptions
    return " ".join(s.split())             # collapse repeated spaces


df["sec_name_norm"] = df["sec_name"].map(normalize_name)
df["nt_name_norm"] = df["nt_name"].map(normalize_name)

# ---------- 2. name similarity for matched pairs ----------
is_pair = df["category"].isin(["matched", "matched_exchange_differs"])


def name_score(row):
    return difflib.SequenceMatcher(None, row["sec_name_norm"], row["nt_name_norm"]).ratio()


# Single words too common to identify a company on their own.
GENERIC_WORDS = {"american", "first", "united", "global", "national", "general",
                 "international", "pacific", "capital"}


def match_rule(row):
    # "prefix": one name is the start of the other, e.g. "keycorp" vs
    # "keycorp each representing a 1 40th ...". The shorter name must be long enough
    # (2+ words or 6+ characters) so short names like "kkr" don't match by accident,
    # and must not be just a generic word like "american".
    shorter, longer = sorted([row["sec_name_norm"], row["nt_name_norm"]], key=len)
    long_enough = len(shorter.split()) >= 2 or len(shorter) >= 6
    not_generic = shorter not in GENERIC_WORDS
    # compare whole words: "hilton world" must not match "hilton worldwide"
    starts_with = longer == shorter or longer.startswith(shorter + " ")
    if shorter and long_enough and not_generic and starts_with:
        return "prefix"
    return "fuzzy"


df.loc[is_pair, "name_score"] = df[is_pair].apply(name_score, axis=1).round(3)
df.loc[is_pair, "match_rule"] = df[is_pair].apply(match_rule, axis=1)

pairs = df[is_pair]
print(f"===== name similarity, {len(pairs)} matched pairs =====")
print(pairs["name_score"].describe().round(3).to_string())
bins = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.999, 1.0]
print("\nscore distribution:")
print(pd.cut(pairs["name_score"], bins=bins, include_lowest=True).value_counts().sort_index().to_string())

print("\nmatch_rule counts:")
print(pairs["match_rule"].value_counts().to_string())
print("\nscore distribution, fuzzy pairs only:")
fuzzy = pairs[pairs["match_rule"] == "fuzzy"]
print(pd.cut(fuzzy["name_score"], bins=bins, include_lowest=True).value_counts().sort_index().to_string())

print("\n25 lowest-scoring fuzzy pairs:")
print(fuzzy.nsmallest(25, "name_score")[
    ["ticker", "sec_name", "nt_name", "sec_name_norm", "nt_name_norm", "name_score"]
].to_string())

# ---------- SPAC evidence ----------
# by name: "Acquisition", an optional Roman numeral I-X as a whole word, then a corporate
# suffix. Matches "Southport Acquisition Corp. II" and "Aeon Acquisition I Corp."
ROMAN = r"(?:i|ii|iii|iv|v|vi|vii|viii|ix|x)"
SUFFIX = r"(?:corp|corporation|company|co|inc|ltd|limited|plc|llc)"
SPAC_NAME_PATTERN = rf"\bacquisition\s+(?:{ROMAN}\s+)?{SUFFIX}\b"
df["spac_by_name"] = (
    df["sec_name"].fillna("").str.contains(SPAC_NAME_PATTERN, case=False, regex=True)
    | df["nt_name"].fillna("").str.contains(SPAC_NAME_PATTERN, case=False, regex=True)
)


def is_unit(ticker):
    return ticker.endswith("-UN") or (len(ticker) == 5 and ticker.isalpha() and ticker.endswith("U"))


def is_warrant_or_right(ticker):
    if "-" in ticker:
        return ticker.split("-", 1)[1] in ("WT", "WTA", "RI", "RW")
    return len(ticker) == 5 and ticker.isalpha() and ticker[-1] in ("W", "R")


# by structure: same CIK has a unit ticker AND a warrant/right ticker (SEC rows only)
sec_rows = df[df["cik"].notna()]
ciks_with_unit = set(sec_rows.loc[sec_rows["ticker"].map(is_unit), "cik"])
ciks_with_wr = set(sec_rows.loc[sec_rows["ticker"].map(is_warrant_or_right), "cik"])
spac_ciks = ciks_with_unit & ciks_with_wr
df["spac_by_structure"] = df["cik"].isin(spac_ciks)
df["likely_spac"] = df["spac_by_name"] | df["spac_by_structure"]

# ---------- 3. reasons for unmatched / differing rows ----------
NEEDS_REASON = ["sec_only", "nasdaq_only", "matched_exchange_differs"]
BANK_PATTERN = r"\bbank\b|savings"

# renamed: a sec_only name equals a nasdaq_only name (same company, different ticker)
sec_only_names = set(df.loc[df["category"] == "sec_only", "sec_name_norm"]) - {""}
nasdaq_only_names = set(df.loc[df["category"] == "nasdaq_only", "nt_name_norm"]) - {""}
renamed_names = sec_only_names & nasdaq_only_names


def reason(row):
    category = row["category"]
    if category not in NEEDS_REASON:
        return None
    sec_name = row["sec_name"] if pd.notna(row["sec_name"]) else ""
    nt_name = row["nt_name"] if pd.notna(row["nt_name"]) else ""
    name_on_this_side = row["sec_name_norm"] if category == "sec_only" else row["nt_name_norm"]

    if category in ("sec_only", "nasdaq_only") and name_on_this_side in renamed_names:
        return "renamed"
    if "/OLD" in sec_name.upper():
        return "delisted"
    if row["likely_spac"]:
        return "likely_spac"
    if category == "sec_only" and re.fullmatch(r"[A-Z]{4}X", row["ticker"]):
        return "mutual_fund"
    if (category == "nasdaq_only" and row["is_etf"] == 0
            and re.search(BANK_PATTERN, nt_name, flags=re.IGNORECASE)):
        return "non_sec_filer_bank"
    if category == "nasdaq_only" and row["is_etf"] == 1:
        return "etf_expected_not_in_sec_list"
    return "unexplained"


df["reason"] = df.apply(reason, axis=1)

EXPECTED_REASONS = ["likely_spac", "mutual_fund", "non_sec_filer_bank", "etf_expected_not_in_sec_list"]
INVESTIGATE_REASONS = ["delisted", "renamed", "unexplained"]


def review_group(row):
    if row["category"] == "matched":
        return "matched"
    if row["category"] == "expected_otc" or row["reason"] in EXPECTED_REASONS:
        return "expected difference"
    return "needs investigation"


df["review_group"] = df.apply(review_group, axis=1)

counts = df[df["category"].isin(NEEDS_REASON)]
print("\n===== SPAC evidence (rows needing a reason) =====")
print(pd.crosstab(counts["spac_by_name"], counts["spac_by_structure"], margins=True).to_string())
print(f"by name only: {(counts['spac_by_name'] & ~counts['spac_by_structure']).sum()}, "
      f"by structure only: {(~counts['spac_by_name'] & counts['spac_by_structure']).sum()}, "
      f"both: {(counts['spac_by_name'] & counts['spac_by_structure']).sum()}")
print(f"(all rows: by name {df['spac_by_name'].sum()}, by structure {df['spac_by_structure'].sum()}, "
      f"either {df['likely_spac'].sum()})")
structure_only = df[df["spac_by_structure"] & ~df["spac_by_name"]]
print("\n5 rows caught only by structure, one per CIK (rows needing a reason first):")
pick = pd.concat([structure_only[structure_only["category"].isin(NEEDS_REASON)], structure_only])
pick = pick.drop_duplicates(subset="cik").head(5)
print(pick[["category", "ticker", "cik", "sec_name"]].to_string())
for cik in pick["cik"].unique():
    print(f"  CIK {cik} tickers: {sorted(df.loc[df['cik'] == cik, 'ticker'])}")

print("\n===== category x reason =====")
print(pd.crosstab(counts["category"], counts["reason"], margins=True).to_string())

print("\n===== review groups =====")
print(df["review_group"].value_counts().to_string())
grouped = df[df["review_group"] != "matched"].copy()
grouped["detail"] = grouped["reason"].fillna(grouped["category"])  # expected_otc has no reason
print("\nby group and detail:")
print(grouped.groupby(["review_group", "detail"]).size().to_string())

print("\nrenamed rows (same normalized name, different ticker):")
print(df[df["reason"] == "renamed"][["category", "ticker", "sec_name", "nt_name", "cik"]].to_string())

# ---------- 1. manual review file for sec_only rows that SEC says are listed ----------
review = df[(df["category"] == "sec_only") & df["sec_exchange_original"].notna()]
review = pd.DataFrame({
    "ticker": review["ticker"],
    "cik": review["cik"].astype(str),
    "sec_name": review["sec_name"],
    "sec_exchange": review["sec_exchange_original"],
})


def merge_with_existing(new, existing):
    # Keep the reviewer's my_verdict / my_note by ticker. Rows that dropped out of the
    # review set are kept (not deleted) and marked in the status column.
    merged = new.merge(existing, on="ticker", how="outer", suffixes=("", "_old"), indicator=True)
    for col in ["cik", "sec_name", "sec_exchange"]:
        merged[col] = merged[col].fillna(merged[col + "_old"])   # dropped rows keep old details
    merged["status"] = merged["_merge"].map({
        "both": "needs_review", "left_only": "new", "right_only": "no_longer_needs_review"})
    merged["my_verdict"] = merged["my_verdict"].fillna("")
    merged["my_note"] = merged["my_note"].fillna("")
    columns = ["ticker", "cik", "sec_name", "sec_exchange", "status", "my_verdict", "my_note"]
    return merged[columns].sort_values(["status", "sec_exchange", "ticker"])


if REVIEW_CSV.exists():
    # read everything as text so empty notes stay "" and tickers like "NA" stay text
    existing = pd.read_csv(REVIEW_CSV, dtype=str, keep_default_na=False)
    if "status" not in existing.columns:
        existing["status"] = ""
    out = merge_with_existing(review, existing)
else:
    out = review.assign(status="new", my_verdict="", my_note="")
REVIEW_CSV.parent.mkdir(exist_ok=True)
out.to_csv(REVIEW_CSV, index=False)
print(f"\nwrote {REVIEW_CSV}: {len(out)} rows")
print(out["status"].value_counts().to_string())

# ---------- every other "needs investigation" row gets its own review file ----------
# (rows already in review_sec_only.csv are left out, so no ticker is in two files)
INVESTIGATION_CSV = Path("output/review_needs_investigation.csv")
inv = df[(df["review_group"] == "needs investigation") & ~df["ticker"].isin(out["ticker"])]
inv_review = pd.DataFrame({
    "ticker": inv["ticker"],
    "category": inv["category"],
    "reason": inv["reason"],
    "cik": inv["cik"].map(lambda v: "" if pd.isna(v) else str(int(v))),
    "sec_name": inv["sec_name"],
    "nt_name": inv["nt_name"],
    "sec_exchange": inv["sec_exchange_original"],
    "nt_exchange": inv["nt_exchange_detail"],
}).sort_values(["category", "ticker"])
inv_out = write_review_file(inv_review, INVESTIGATION_CSV, key="ticker")
print(f"\nneeds investigation: {(df['review_group'] == 'needs investigation').sum()} "
      f"({(df['review_group'] == 'needs investigation').sum() - len(inv)} in {REVIEW_CSV.name}, "
      f"{len(inv)} in {INVESTIGATION_CSV.name})")
print(inv_review.groupby(["category", "reason"]).size().to_string())

with sqlite3.connect(DB_PATH) as conn:
    df.to_sql("ticker_review", conn, if_exists="replace", index=False)
print(f"\nsaved ticker_review: {len(df)} rows")
