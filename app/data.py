"""Compute every dashboard figure from reference.db, output/ and the raw files' metadata.

Nothing here is typed in by hand: each number is counted from a table or file at request time.
"""
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "reference.db"
OUT = ROOT / "output"
RAW = ROOT / "data" / "raw"
REVIEW_FILES = {"review_sec_only.csv": "ticker", "review_needs_investigation.csv": "ticker",
                "review_check_b_failures.csv": "adsh"}
GROUP_ORDER = ["bank", "blank check / SPAC", "REIT", "insurance", "other", "missing SIC"]


def records(df):
    # DataFrame -> list of dicts with None instead of NaN (JSON-safe)
    return df.astype(object).where(df.notna(), None).to_dict("records")


def cik10(value):
    return str(int(float(value))).zfill(10)


def file_date(path):
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d") if path.exists() else None


def nasdaq_file_time(path):
    # Nasdaq Trader footer: "File Creation Time: 1106202609:31" (MMDDYYYYHH:MM)
    if not path.exists():
        return None
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        m = re.match(r"File Creation Time: (\d{10}:\d{2})", line)
        if m:
            return datetime.strptime(m.group(1), "%m%d%Y%H:%M").strftime("%Y-%m-%d %H:%M")
    return None


def read_review(file_name):
    df = pd.read_csv(OUT / file_name, dtype=str, keep_default_na=False)
    if "status" not in df.columns:
        df["status"] = "(none)"
    return df


def dashboard():
    with sqlite3.connect(DB) as conn:
        tickers = pd.read_sql("select * from ticker_review", conn)
        checks = pd.read_sql("select * from fs_checks", conn)
        joined = pd.read_sql("select * from company_fs_joined", conn)
        sub_10k = pd.read_sql("select filed from fs_sub_10k", conn)
        table_rows = {name: conn.execute(f'select count(*) from "{name}"').fetchone()[0]
                      for (name,) in conn.execute("select name from sqlite_master where type='table'")}

    # ---------- as-of dates ----------
    filed = pd.to_datetime(sub_10k["filed"], format="%Y%m%d")
    quarter = f"{filed.max().year} Q{(filed.max().month - 1) // 3 + 1}"
    asof = [
        {"source": "SEC company tickers JSON", "value": f"file date {file_date(RAW / 'company_tickers_exchange.json')}"},
        {"source": "Nasdaq Trader nasdaqlisted.txt", "value": f"created {nasdaq_file_time(RAW / 'nasdaqlisted.txt')}"},
        {"source": "Nasdaq Trader otherlisted.txt", "value": f"created {nasdaq_file_time(RAW / 'otherlisted.txt')}"},
        {"source": f"SEC FSDS {quarter}", "value": f"10-Ks filed {filed.min():%Y-%m-%d} to {filed.max():%Y-%m-%d}"},
    ]

    # ---------- headline figures ----------
    groups = tickers["review_group"].value_counts()
    w = joined[joined["has_10k"] == 1].copy()
    w["fully_checked"] = (w["check_a"] != "not_applicable") & (w["check_b"] != "not_applicable")
    full = w[w["fully_checked"]]
    a_ran = checks[checks["check_a"] != "not_applicable"]
    b_ran = checks[checks["check_b"] != "not_applicable"]
    b_fail = b_ran[b_ran["check_b"] == "fail"]
    has_rev = int(checks["revenue_value"].notna().sum())
    unlisted_filers = checks.loc[~checks["cik"].map(cik10).isin(joined["cik10"]), "cik"].nunique()

    investigate = set(tickers.loc[tickers["review_group"] == "needs investigation", "ticker"])
    reviews = []
    for name, key in REVIEW_FILES.items():
        if (OUT / name).exists():
            r = read_review(name)
            reviews.append({"file": name, "key": key, "rows": len(r),
                            "open": int((r["status"] != "no_longer_needs_review").sum()),
                            "with_verdict": int((r["my_verdict"] != "").sum()),
                            # how many of the "needs investigation" tickers this file holds
                            "needs_investigation": int(r[key].isin(investigate).sum()) if key == "ticker" else 0})

    # Nasdaq Trader symbols matched to SEC (unique symbols). "matched" = same exchange too;
    # "matched_exchange_differs" = ticker matched but the exchange disagrees.
    nt_side = tickers[tickers["nt_ticker_original"].notna()]
    nt_matched = nt_side[nt_side["category"].isin(["matched", "matched_exchange_differs"])]

    kpis = {
        "companies_with_10k": len(w), "listed_companies": len(joined),
        "pass_all": int(w["all_checks_pass"].sum()),
        "fully_checked": len(full), "pass_all_fully_checked": int(full["all_checks_pass"].sum()),
        "tickers_total": len(tickers), "matched": int(groups.get("matched", 0)),
        "nt_symbols": int(nt_side["ticker"].nunique()),
        "nt_matched": int(nt_matched["ticker"].nunique()),
        "nt_matched_same_exchange": int(nt_matched.loc[nt_matched["category"] == "matched", "ticker"].nunique()),
        "nt_matched_exchange_differs": int(
            nt_matched.loc[nt_matched["category"] == "matched_exchange_differs", "ticker"].nunique()),
        "expected_difference": int(groups.get("expected difference", 0)),
        "needs_investigation": int(groups.get("needs investigation", 0)),
        "investigation_reasons": records(
            tickers[tickers["review_group"] == "needs investigation"]["reason"].value_counts()
            .rename_axis("reason").reset_index(name="count")),
        "filings_10k": len(checks), "quarter": quarter,
        "check_b_tested": len(b_ran), "check_b_fail": len(b_fail),
        "check_b_fail_spac": int((b_fail["sic_group"] == "blank check / SPAC").sum()),
        "check_a_tested": len(a_ran), "check_a_fail": int((a_ran["check_a"] == "fail").sum()),
        "revenue_with_value": has_rev, "unlisted_10k_filers": int(unlisted_filers),
        "review_open": sum(r["open"] for r in reviews),
        "review_with_verdict": sum(r["with_verdict"] for r in reviews),
        "reviews": reviews,
    }

    # ---------- pass rate by data-quality dimension ----------
    listed_pairs = tickers[tickers["category"].isin(["matched", "matched_exchange_differs"])]
    sec_side = tickers[tickers["sec_ticker_original"].notna()]
    dimensions = [
        {"name": "Completeness", "rule": "10-K reports a revenue value", "source": "fs_checks",
         "passed": has_rev, "tested": len(checks)},
        {"name": "Consistency", "rule": "Liabilities + Equity = Total L&E, check (b) pass or explained",
         "source": "fs_checks", "passed": int(b_ran["check_b"].isin(["pass", "explained"]).sum()),
         "tested": len(b_ran)},
        {"name": "Consistency", "rule": "Assets = Total L&E, check (a)", "source": "fs_checks",
         "passed": int((a_ran["check_a"] == "pass").sum()), "tested": len(a_ran)},
        {"name": "Consistency", "rule": "Matched tickers: SEC exchange agrees with Nasdaq Trader",
         "source": "ticker_review (matched + matched_exchange_differs)",
         "passed": int((listed_pairs["category"] == "matched").sum()), "tested": len(listed_pairs)},
        {"name": "Uniqueness", "rule": "SEC ticker appears once after normalization", "source": "ticker_review",
         "passed": int(len(sec_side) - sec_side["ticker"].duplicated(keep=False).sum()), "tested": len(sec_side)},
    ]

    # ---------- check (b) across all filings ----------
    check_b_counts = checks["check_b"].value_counts()
    check_b = [{"label": k, "count": int(check_b_counts.get(k, 0))}
               for k in ["pass", "explained", "fail", "not_applicable"]]

    # ---------- industry breakdown ----------
    rev_status = pd.Series("no revenue - other", index=checks.index)
    rev_status[checks["revenue_not_comparable_industry"] == 1] = "no revenue - industry not comparable"
    rev_status[checks["revenue_value"].notna()] = "has revenue value"
    rev = pd.crosstab(checks["sic_group"], rev_status).reindex(GROUP_ORDER).fillna(0).astype(int)
    b_grp = pd.crosstab(b_ran["sic_group"], b_ran["check_b"]).reindex(GROUP_ORDER).fillna(0).astype(int)
    industry = {
        "revenue": [{"group": g, **{c: int(rev.loc[g].get(c, 0)) for c in
                    ["has revenue value", "no revenue - industry not comparable", "no revenue - other"]}}
                    for g in rev.index],
        "check_b": [{"group": g, **{c: int(b_grp.loc[g].get(c, 0)) for c in ["pass", "explained", "fail"]}}
                    for g in b_grp.index],
    }
    by_exchange = []
    for name, part in list(w.groupby("exchange_group")) + [("total", w)]:
        f = part[part["fully_checked"]]
        by_exchange.append({"exchange": name, "companies": len(part), "pass": int(part["all_checks_pass"].sum()),
                            "fully_checked": len(f), "pass_fully_checked": int(f["all_checks_pass"].sum())})

    # ---------- flagged issuers with their check values ----------
    detail_cols = ["adsh", "Liabilities", "StockholdersEquity", "LSE", "MinorityInterest", "EquityInclNCI",
                   "TempEquityInclNCI", "TempEquityParent", "TempEquityExclAPIC", "TempEquityRedemptionReq",
                   "RedeemableNCI", "RedeemableNCICommon", "RedeemableNCIPreferred", "liabilities_derived",
                   "Revenues", "RevenueFromContracts", "RevenueInclTax", "check_a_gap", "check_b_explained_by"]
    flagged = w[w["flags"].notna() & (w["flags"] != "")]
    flagged = flagged[["cik10", "adsh", "tickers", "name", "exchange_group", "sic_group", "period", "Assets",
                       "NetIncomeLoss", "revenue_value", "revenue_source_tag", "check_a", "check_b",
                       "check_b_gap", "flags", "info_notes"]].merge(checks[detail_cols], on="adsh", how="left")
    flagged = flagged.sort_values("Assets", ascending=False)

    # ---------- rules applied (decisions, with real counts) ----------
    src = checks["revenue_source_tag"].fillna("none").value_counts()
    explained = checks["check_b_explained_by"].dropna().value_counts()
    rules_applied = {
        "revenue_sources": [{"tag": k, "count": int(v)} for k, v in src.items()],
        "spac_by_name": int(tickers["spac_by_name"].sum()),
        "spac_by_structure": int(tickers["spac_by_structure"].sum()),
        "spac_either": int(tickers["likely_spac"].sum()),
        "explained_total": int(explained.sum()),
        "explained_top": [{"by": k, "count": int(v)} for k, v in explained.head(4).items()],
        "exchange_tested": len(listed_pairs),
        "exchange_agree": int((listed_pairs["category"] == "matched").sum()),
    }

    # ---------- validation rules ----------
    both_rev = checks["Revenues"].notna() & checks["RevenueFromContracts"].notna()
    rules = [
        {"id": "DQ-01", "rule": "Balance sheet identity", "expression": "Assets = LiabilitiesAndStockholdersEquity",
         "dimension": "Consistency", "scope": "fs_checks", "tested": len(a_ran),
         "failures": kpis["check_a_fail"]},
        {"id": "DQ-02", "rule": "Liabilities breakdown", "expression":
         "Liabilities + StockholdersEquity = LiabilitiesAndStockholdersEquity (allowlisted NCI / temporary equity explain gaps)",
         "dimension": "Consistency", "scope": "fs_checks", "tested": len(b_ran), "failures": len(b_fail)},
        {"id": "DQ-03", "rule": "Exchange cross-reconciliation", "expression": "SEC exchange = Nasdaq Trader exchange group",
         "dimension": "Consistency", "scope": "ticker_review", "tested": len(listed_pairs),
         "failures": int((listed_pairs["category"] == "matched_exchange_differs").sum())},
        {"id": "DQ-04", "rule": "Revenue line coverage", "expression": "Revenues, contract revenue (excl. or incl. tax) present",
         "dimension": "Completeness", "scope": "fs_checks", "tested": len(checks),
         "failures": int(checks["revenue_value"].isna().sum())},
        {"id": "DQ-05", "rule": "Revenue component integrity", "expression": "Contract revenue <= Revenues (when both reported)",
         "dimension": "Accuracy", "scope": "fs_checks", "tested": int(both_rev.sum()),
         "failures": int(checks["revenue_component_exceeds_total"].sum())},
        {"id": "DQ-06", "rule": "Ticker present in both lists", "expression":
         "Unmatched ticker has an evidence-based reason (OTC, ETF, SPAC, fund, bank)", "dimension": "Completeness",
         "scope": "ticker_review", "tested": len(tickers), "failures": kpis["needs_investigation"]},
    ]

    # ---------- lineage ----------
    def csv_rows(name):
        p = OUT / name
        return len(pd.read_csv(p, dtype=str, keep_default_na=False)) if p.exists() else None

    lineage = [
        {"stage": "Ingestion", "title": "SEC company tickers", "detail": "company_tickers_exchange.json",
         "rows": table_rows.get("raw_sec_tickers"), "unit": "tickers", "script": "01_load_raw.py"},
        {"stage": "Ingestion", "title": "Nasdaq Trader lists", "detail": "nasdaqlisted.txt + otherlisted.txt",
         "rows": (table_rows.get("raw_nasdaqlisted") or 0) + (table_rows.get("raw_otherlisted") or 0),
         "unit": "symbols", "script": "01_load_raw.py"},
        {"stage": "Ingestion", "title": f"SEC FSDS {quarter}", "detail": "sub.txt + num.txt, 10-K filings",
         "rows": table_rows.get("fs_sub_10k"), "unit": "10-K filings", "script": "04_fs_extract.py"},
        {"stage": "Reconciliation", "title": "Ticker reconciliation", "detail": "normalize, match, reasons",
         "rows": table_rows.get("ticker_review"), "unit": "tickers", "script": "02_match_tickers.py + 03_review.py"},
        {"stage": "Checks", "title": "Financial statement checks", "detail": "revenue source, check (a), check (b)",
         "rows": table_rows.get("fs_checks"), "unit": "filings", "script": "05_fs_checks.py"},
        {"stage": "Publication", "title": "Company table", "detail": "joined by zero-padded CIK",
         "rows": table_rows.get("company_fs_joined"), "unit": "listed companies", "script": "06_join.py"},
    ]
    lineage_table = [
        {"artifact": n, "kind": "SQLite table", "rows": table_rows[n]} for n in
        ["raw_sec_tickers", "raw_nasdaqlisted", "raw_otherlisted", "ticker_match", "ticker_review",
         "fs_sub_10k", "fs_values", "fs_values_current", "fs_checks", "company_fs_joined"] if n in table_rows
    ] + [{"artifact": n, "kind": "CSV", "rows": csv_rows(n)} for n in
         ["review_sec_only.csv", "review_check_b_failures.csv", "company_fs_joined.csv"]]

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "db_written": datetime.fromtimestamp(DB.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        "asof": asof, "kpis": kpis, "dimensions": dimensions, "check_b": check_b,
        "industry": industry, "by_exchange": by_exchange, "flagged": records(flagged),
        "rules_applied": rules_applied, "rules": rules, "lineage": lineage, "lineage_table": lineage_table,
    }


def ticker_rows():
    with sqlite3.connect(DB) as conn:
        df = pd.read_sql(
            "select ticker, sec_ticker_original, nt_ticker_original, category, reason, review_group, "
            "sec_name, nt_name, sec_exchange_original, nt_exchange_detail, is_etf, name_score, match_rule, cik "
            "from ticker_review", conn)
    df["cik"] = df["cik"].astype("Int64")
    return records(df)


def review_rows():
    out = []
    for name, key in REVIEW_FILES.items():
        if (OUT / name).exists():
            out.append({"file": name, "key": key, "rows": records(read_review(name))})
    return out
