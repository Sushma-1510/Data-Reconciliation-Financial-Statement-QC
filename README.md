# US Equity Reference Data Reconciliation + Financial Statement QC

## Problem

- The SEC and Nasdaq Trader each publish a list of US-listed tickers. They disagree about the same companies: ticker formats differ (BRK-B vs BRK.B, FITB-PM vs FITB$M), exchanges differ, and each list has tickers the other doesn't.
- SEC financial statement data has gaps: companies use different tags for the same line item, and some items are missing altogether.
- This project reconciles the two ticker lists, checks 10-K balance sheets and revenue for internal consistency, and separates expected differences from issues that need investigation.

## Data sources

| Source | Link | Downloaded | Rows |
|---|---|---|---|
| SEC company tickers with exchange | https://www.sec.gov/files/company_tickers_exchange.json | 2026-11-06 | 10,409 tickers |
| Nasdaq Trader `nasdaqlisted.txt` | https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt | 2026-11-06 | 5,610 symbols |
| Nasdaq Trader `otherlisted.txt` | https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt | 2026-11-06 | 7,666 symbols |
| SEC Financial Statement Data Sets, 2026 Q1 | https://www.sec.gov/dera/data/financial-statement-data-sets | 2026-11-06 (files dated 2026-04-09) | 6,169 filings (4,262 10-Ks), 3,690,955 values |

- The original `nasdaqlisted.txt` turned out to contain `otherlisted.txt` appended to it. `01_load_raw.py` splits it, and it also works with a clean download.

## Pipeline

- `01_load_raw.py`: loads the SEC ticker JSON and both Nasdaq Trader files, profiles them, and saves them unchanged to SQLite.
- `02_match_tickers.py`: normalizes tickers to SEC style, maps exchange codes to one set of names, outer-joins the lists and assigns 5 match categories.
- `03_review.py`: scores name similarity (difflib + prefix rule), flags SPACs by name and by structure, gives a reason to every unmatched row, and writes `output/review_sec_only.csv`.
- `04_fs_extract.py`: keeps 10-Ks from FSDS and filters values (no segments, no coregistrant, USD, full year or balance sheet date, current period); profiles tag coverage.
- `05_fs_checks.py`: picks the revenue source, runs check (a) Assets = L+SE and check (b) L + SE = L+SE, and writes `output/review_check_b_failures.csv`.
- `06_join.py`: joins 10-K results to listed tickers by zero-padded CIK; reports coverage and pass rates; writes `output/company_fs_joined.csv`.
- `07_charts.py`: draws 4 summary charts to `output/charts/`.
- `08_report.py`: builds `output/report.html`, a single offline page with the charts, headline numbers and largest flagged companies.
- `review_file.py` (helper): rewrites review CSVs without losing `my_verdict` / `my_note`.

## Findings

- **Tickers:** of 16,004 reconciled tickers, 7,640 match and 8,214 are expected differences (5,582 ETFs not in the SEC list, 2,569 OTC, plus SPACs, mutual funds and banks). **150 need investigation.**
- **Revenue:** coverage is **71.4%** (3,045 of 4,262 10-Ks). The gaps are mostly by design: 84% of banks report interest income instead of revenue, and 99% of SPACs have no revenue.
- **Balance sheet:** check (a) Assets = L+SE has no failures (4,118 pass). Check (b) has **130 failures** out of 3,424 filings, **64 of them SPACs**.
- **Pass rate:** **97.2%** of 3,443 listed companies with a 10-K pass all checks, but only **96.5%** when counting the 2,795 where both checks actually ran.
- **XOM:** the ticker now points to the new holding company's CIK, while the 10-K was filed under the old CIK, so a pure CIK join reports "no 10-K".
- **Manual checks:**
  - **BlackRock's check (b) failure is caused by our filter.** Its $5.65B gap is redeemable NCI reported only in parts, plus $227M NCI.
  - **Blue Owl's revenue is wrong in our output.** Its total ($2.87B) uses the contract-revenue tag, so "Revenues wins" picks a $568M sub-line.

## Decisions

- Full list: [DECISIONS.md](DECISIONS.md)
- **No circular checks.** Check (a) compares two reported totals. Check (b) runs only where Liabilities, Equity and L+SE are all reported. Liabilities derived as L+SE minus Equity are flagged and never used in check (b).
- **Explanation allowlist.** A check (b) gap is explained only by named NCI, temporary-equity or redeemable-NCI tags, at most one per kind. A tag is never accepted just because its value equals the gap.
- **Expected difference vs needs investigation.** A reason is assigned only with evidence in the data. Expected: OTC, ETFs, likely SPACs, mutual funds, non-SEC-filer banks. Needs investigation: delisted, renamed, unexplained.

## Limitations

- **SPAC tags:** many SPAC check (b) failures are probably redeemable shares tagged outside the allowlist. The trust amount often equals the gap, but asset tags are not accepted as explanations.
- **Totals reported only in parts:** the "segments empty" filter drops values that a company reports only by segment, so check (b) can miss a valid explanation (BlackRock).
- **Revenues precedence:** "Revenues wins" can pick a sub-line when the income statement total uses the contract-revenue tag (Blue Owl).
- **818 unmatched 10-K filers:** 818 10-K filers matched no listed ticker. Some are probably CIK changes after restructuring (e.g. XOM). Next step: match them by normalized name.
- **InterestIncomeExpenseNet is too broad:** non-financial companies also use it for net interest on debt, so some of the 85 "other" filings may be marked `revenue_not_comparable_industry` wrongly.
- **Malformed rows:** `num.txt` has 2 malformed lines (a tab inside the segments text). Both are rows the filters drop anyway, so results are unaffected.

## How to run

- `data/raw/` is not in the repo. Download the sources into it first:
  - `company_tickers_exchange.json` from the SEC link above. SEC requires a User-Agent header with your name and email for scripted downloads.
  - `nasdaqlisted.txt` and `otherlisted.txt` from Nasdaq Trader.
  - The 2026 Q1 zip from the SEC Financial Statement Data Sets page; unzip `sub.txt`, `num.txt` and `pre.txt` into `data/raw/`.
- Install: `pip install -r requirements.txt` (tested with Python 3.13).
- Run from the project root, in order:
  - `python scripts/01_load_raw.py`
  - `python scripts/02_match_tickers.py`
  - `python scripts/03_review.py`
  - `python scripts/04_fs_extract.py` (reads the 560 MB `num.txt`; takes a few minutes)
  - `python scripts/05_fs_checks.py`
  - `python scripts/06_join.py`
  - `python scripts/07_charts.py`
  - `python scripts/08_report.py`
- Results go to `reference.db` (SQLite) and `output/`. Rerunning keeps your `my_verdict` / `my_note` entries in the review CSVs.
- Dashboard (after the scripts have run): `python app/server.py`, then open http://localhost:8000 (HTML/CSS front end in `app/static/`, standard-library Python server).
