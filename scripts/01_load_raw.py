"""Step 1: load raw reference files, profile them, save to SQLite as raw tables."""
import io
import json
import sqlite3
from pathlib import Path

import pandas as pd

DATA = Path("data/raw")
DB_PATH = "reference.db"


def load_sec_tickers(path):
    # File shape: {"fields": ["cik","name","ticker","exchange"], "data": [[...], ...]}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return pd.DataFrame(raw["data"], columns=raw["fields"])


def parse_pipe_text(text):
    # Read everything as text. Only empty fields count as missing, so a real
    # ticker such as "NA" or "NULL" is not turned into NaN.
    df = pd.read_csv(io.StringIO(text), sep="|", dtype=str,
                     keep_default_na=False, na_values=[""])
    # The last row is a footer like "File Creation Time: 1106202609:31", not data.
    first_col = df.columns[0]
    is_footer = df[first_col].str.startswith("File Creation Time", na=False)
    print(f"  dropped footer rows: {is_footer.sum()}")
    return df[~is_footer]


def split_combined_file(path):
    # Our original nasdaqlisted.txt contained two files back to back:
    # the nasdaqlisted section, then the otherlisted section (header starts "ACT Symbol|").
    # A clean download has only the first section; then other_text is None.
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("ACT Symbol|")]
    if not starts:
        return "".join(lines), None
    nasdaq_text = "".join(lines[:starts[0]])
    other_text = "".join(lines[starts[0]:])
    return nasdaq_text, other_text


def profile(name, df, ticker_col, exchange_col):
    print(f"\n===== {name} =====")
    print(f"rows: {len(df)}")
    print("\nnulls per column:")
    print(df.isna().sum().to_string())
    dupes = df[df[ticker_col].duplicated(keep=False)].sort_values(ticker_col)
    print(f"\nduplicate tickers: {dupes[ticker_col].nunique()} tickers, {len(dupes)} rows")
    if len(dupes) > 0:
        print(dupes.head(20).to_string())
    print(f"\nvalue counts of {exchange_col}:")
    print(df[exchange_col].value_counts(dropna=False).to_string())


sec = load_sec_tickers(DATA / "company_tickers_exchange.json")
profile("SEC company_tickers_exchange", sec, "ticker", "exchange")

nasdaq_text, other_text = split_combined_file(DATA / "nasdaqlisted.txt")

print("\nparsing nasdaqlisted section")
nasdaq = parse_pipe_text(nasdaq_text)
# nasdaqlisted has no exchange column (all rows are Nasdaq); Market Category is the tier.
profile("nasdaqlisted", nasdaq, "Symbol", "Market Category")
print("\nvalue counts of Test Issue:")
print(nasdaq["Test Issue"].value_counts(dropna=False).to_string())

print("\nparsing otherlisted.txt (clean download)")
with open(DATA / "otherlisted.txt", encoding="utf-8") as f:
    other = parse_pipe_text(f.read())
profile("otherlisted", other, "ACT Symbol", "Exchange")
print("\nvalue counts of Test Issue:")
print(other["Test Issue"].value_counts(dropna=False).to_string())

# Sanity check: compare the clean download with the copy embedded in nasdaqlisted.txt.
if other_text is not None:
    print("\nparsing otherlisted section embedded in nasdaqlisted.txt (for comparison only)")
    other_embedded = parse_pipe_text(other_text)
    clean_symbols = set(other["ACT Symbol"])
    embedded_symbols = set(other_embedded["ACT Symbol"])
    print(f"  clean rows: {len(other)}, embedded rows: {len(other_embedded)}")
    print(f"  only in clean download: {sorted(clean_symbols - embedded_symbols)}")
    print(f"  only in embedded copy:  {sorted(embedded_symbols - clean_symbols)}")

tables = {"raw_sec_tickers": sec, "raw_nasdaqlisted": nasdaq, "raw_otherlisted": other}

with sqlite3.connect(DB_PATH) as conn:
    for table_name, df in tables.items():
        df.to_sql(table_name, conn, if_exists="replace", index=False)
        print(f"saved {table_name}: {len(df)} rows")
