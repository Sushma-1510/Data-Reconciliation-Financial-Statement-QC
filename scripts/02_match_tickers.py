"""Step 2: normalize tickers across SEC and Nasdaq Trader lists, then match them."""
import sqlite3

import pandas as pd

DB_PATH = "reference.db"
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 60)

# ---------- ticker normalization ----------
# Canonical form = SEC style: BASE or BASE-SUFFIX (BRK-B, FITB-PM, KRSP-WT).
# otherlisted ACT Symbol uses "." for classes/warrants/units/rights and "$" for preferreds.
ACT_DOT_SUFFIX = {"W": "WT", "U": "UN", "R": "RI"}  # warrant, unit, right


def canonical_ticker(symbol):
    s = symbol.strip().upper()
    if "$" in s:                          # preferred: FITB$M -> FITB-PM, ETI$ -> ETI-P
        base, series = s.split("$", 1)
        return f"{base}-P{series}"
    if "." in s:                          # BF.B -> BF-B, KRSP.W -> KRSP-WT
        base, suffix = s.split(".", 1)
        return f"{base}-{ACT_DOT_SUFFIX.get(suffix, suffix)}"
    return s                              # already canonical (BRK-B, NVDA, NWCLW)


def suffix_type(ticker):
    # Security type read from the canonical suffix. Nasdaq-native tickers (e.g. NWCLW)
    # carry no separator, so they fall into "no suffix".
    if "-" not in ticker:
        return "no suffix"
    suffix = ticker.split("-", 1)[1]
    if suffix.startswith("P"):
        return "preferred"
    if suffix.startswith("WT"):
        return "warrant"
    if suffix == "UN":
        return "unit"
    if suffix.startswith("R"):            # RI = rights, RW = rights when-issued
        return "right"
    return "share class"


# ---------- exchange vocabulary ----------
# exchange_detail = most specific name; exchange_group = level SEC reports at.
OTHERLISTED_EXCHANGE = {
    "N": "NYSE", "A": "NYSE American", "P": "NYSE Arca", "M": "NYSE Texas",
    "Z": "CBOE", "V": "IEX", "F": "TXSE",
}
SEC_EXCHANGE = {"Nasdaq": "NASDAQ", "NYSE": "NYSE", "CBOE": "CBOE", "TXSE": "TXSE", "OTC": "OTC"}
# SEC labels NYSE American and NYSE Arca listings as plain "NYSE" (checked by cross-tab).
EXCHANGE_GROUP = {"NYSE American": "NYSE", "NYSE Arca": "NYSE", "NYSE Texas": "NYSE"}

with sqlite3.connect(DB_PATH) as conn:
    sec_raw = pd.read_sql("select * from raw_sec_tickers", conn)
    nasdaq_raw = pd.read_sql("select * from raw_nasdaqlisted", conn)
    other_raw = pd.read_sql("select * from raw_otherlisted", conn)

# ---------- SEC side ----------
sec = pd.DataFrame({
    "cik": sec_raw["cik"],
    "sec_name": sec_raw["name"],
    "sec_ticker_original": sec_raw["ticker"],
    "ticker": sec_raw["ticker"].map(canonical_ticker),
    "sec_exchange_original": sec_raw["exchange"],
    "sec_exchange": sec_raw["exchange"].map(SEC_EXCHANGE),   # None stays NaN
})

# ---------- Nasdaq Trader side (nasdaqlisted + otherlisted) ----------
nasdaq = pd.DataFrame({
    "nt_ticker_original": nasdaq_raw["Symbol"],
    "ticker": nasdaq_raw["Symbol"].map(canonical_ticker),
    "nt_name": nasdaq_raw["Security Name"],
    "nt_exchange_original": "Nasdaq " + nasdaq_raw["Market Category"],
    "nt_exchange_detail": "NASDAQ",
    "is_etf": nasdaq_raw["ETF"] == "Y",
    "test_issue": nasdaq_raw["Test Issue"],
    "nt_source": "nasdaqlisted",
})
other = pd.DataFrame({
    "nt_ticker_original": other_raw["ACT Symbol"],
    "ticker": other_raw["ACT Symbol"].map(canonical_ticker),
    "nt_name": other_raw["Security Name"],
    "nt_exchange_original": other_raw["Exchange"],
    "nt_exchange_detail": other_raw["Exchange"].map(OTHERLISTED_EXCHANGE),
    "is_etf": other_raw["ETF"] == "Y",
    "test_issue": other_raw["Test Issue"],
    "nt_source": "otherlisted",
})
nt = pd.concat([nasdaq, other], ignore_index=True)
print(f"Nasdaq Trader rows: {len(nt)}, test issues excluded: {(nt['test_issue'] == 'Y').sum()}")
nt = nt[nt["test_issue"] != "Y"].drop(columns="test_issue")
nt["nt_exchange"] = nt["nt_exchange_detail"].replace(EXCHANGE_GROUP)

unmapped = nt[nt["nt_exchange_detail"].isna()]["nt_exchange_original"].unique()
print(f"unmapped otherlisted exchange codes: {list(unmapped)}")

# canonical tickers must be unique within each side, or the join would multiply rows
print(f"duplicate canonical tickers: SEC {sec['ticker'].duplicated().sum()}, "
      f"Nasdaq Trader {nt['ticker'].duplicated().sum()}")

# ---------- one CIK, several tickers (expected, not an error) ----------
sec["suffix_type"] = sec["ticker"].map(suffix_type)
tickers_per_cik = sec.groupby("cik")["ticker"].count()
multi_ciks = tickers_per_cik[tickers_per_cik > 1].index
print(f"\nCIKs with more than one ticker: {len(multi_ciks)} "
      f"(covering {sec['cik'].isin(multi_ciks).sum()} tickers)")
print("tickers per CIK:")
print(tickers_per_cik.value_counts().sort_index().to_string())
print("suffix types among multi-ticker CIKs:")
print(sec[sec["cik"].isin(multi_ciks)]["suffix_type"].value_counts().to_string())

# ---------- match ----------
merged = sec.merge(nt, on="ticker", how="outer", indicator=True)
# outer join leaves cik empty for nasdaq_only rows; "Int64" keeps it an integer with gaps
merged["cik"] = merged["cik"].astype("Int64")


def match_category(row):
    if row["_merge"] == "both":
        if row["sec_exchange"] == row["nt_exchange"]:
            return "matched"
        return "matched_exchange_differs"
    if row["_merge"] == "left_only":
        if row["sec_exchange"] == "OTC":
            return "expected_otc"
        return "sec_only"
    return "nasdaq_only"


merged["category"] = merged.apply(match_category, axis=1)
merged = merged.drop(columns="_merge")

print("\n===== match categories =====")
print(merged["category"].value_counts().to_string())
print("\nETFs per category:")
print(merged.groupby("category")["is_etf"].sum().astype(int).to_string())

differs = merged[merged["category"] == "matched_exchange_differs"]
print("\nmatched_exchange_differs: SEC exchange vs Nasdaq Trader exchange")
print(pd.crosstab(differs["sec_exchange_original"].fillna("<null>"), differs["nt_exchange_detail"]).to_string())

print("\nsec_only by SEC exchange:")
print(merged[merged["category"] == "sec_only"]["sec_exchange_original"].fillna("<null>").value_counts().to_string())
print("\nnasdaq_only by exchange / ETF:")
print(pd.crosstab(merged[merged["category"] == "nasdaq_only"]["nt_exchange_detail"],
                  merged[merged["category"] == "nasdaq_only"]["is_etf"]).to_string())

# ---------- manual review samples (not yet called errors) ----------
sec_cols = ["cik", "sec_name", "sec_ticker_original", "ticker", "sec_exchange_original"]
nt_cols = ["nt_ticker_original", "ticker", "nt_name", "nt_exchange_detail", "is_etf"]
for cat, cols in [("sec_only", sec_cols), ("nasdaq_only", nt_cols)]:
    rows = merged[merged["category"] == cat]
    print(f"\n===== 15 random {cat} rows (of {len(rows)}) =====")
    print(rows[cols].sample(min(15, len(rows)), random_state=42).to_string())

# nasdaq_only is almost all ETFs, so also list every non-ETF row
nasdaq_only = merged[merged["category"] == "nasdaq_only"]
non_etf = nasdaq_only[nasdaq_only["is_etf"] == False]  # is_etf is empty on SEC-only rows
print(f"\n===== all non-ETF nasdaq_only rows ({len(non_etf)}) =====")
print(non_etf[nt_cols].to_string())

print("\n===== all matched_exchange_differs rows =====")
print(differs[["cik", "sec_name", "ticker", "sec_exchange_original", "nt_name", "nt_exchange_detail", "is_etf"]].to_string())

with sqlite3.connect(DB_PATH) as conn:
    merged.to_sql("ticker_match", conn, if_exists="replace", index=False)
print(f"\nsaved ticker_match: {len(merged)} rows")
