"""Step 8: build output/report.html - one self-contained page (charts embedded as base64)."""
import base64
import html
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

DB_PATH = "reference.db"
OUTPUT = Path("output")
CHARTS = OUTPUT / "charts"
JOINED_CSV = OUTPUT / "company_fs_joined.csv"
REVIEW_FILES = [OUTPUT / "review_sec_only.csv", OUTPUT / "review_needs_investigation.csv",
                OUTPUT / "review_check_b_failures.csv"]
REPORT = OUTPUT / "report.html"
CHART_FILES = [
    ("1_reconciliation.png", "SEC vs Nasdaq Trader ticker reconciliation"),
    ("2_revenue_coverage.png", "Revenue coverage by industry group"),
    ("3_check_b_by_group.png", "Check (b) results by industry group"),
    ("4_pass_by_exchange.png", "Share passing all checks, by exchange"),
]

# ---------- read results (nothing hardcoded) ----------
with sqlite3.connect(DB_PATH) as conn:
    tickers = pd.read_sql("select review_group from ticker_review", conn)
    checks = pd.read_sql("select sic_group, revenue_value, check_a, check_b from fs_checks", conn)
joined = pd.read_csv(JOINED_CSV, keep_default_na=False, na_values=[""])


def pct(part, whole):
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


groups = tickers["review_group"].value_counts()
with_10k = joined[joined["has_10k"]]
fully_checked = with_10k[(with_10k["check_a"] != "not_applicable") & (with_10k["check_b"] != "not_applicable")]
b_ran = checks[checks["check_b"] != "not_applicable"]
b_fail = b_ran[b_ran["check_b"] == "fail"]
spac_fail = (b_fail["sic_group"] == "blank check / SPAC").sum()
has_revenue = checks["revenue_value"].notna().sum()

# (label, value, source) - source says where the number comes from
summary = [
    ("Tickers reconciled (SEC + Nasdaq Trader)", f"{len(tickers):,}", "reference.db: ticker_review"),
    ("Matched", f"{groups.get('matched', 0):,}", "ticker_review.review_group"),
    ("Expected difference", f"{groups.get('expected difference', 0):,}", "ticker_review.review_group"),
    ("Needs investigation", f"{groups.get('needs investigation', 0):,}", "ticker_review.review_group"),
    ("10-K filings (2026 Q1)", f"{len(checks):,}", "reference.db: fs_checks"),
    ("Revenue coverage", f"{pct(has_revenue, len(checks))} ({has_revenue:,} filings)", "fs_checks.revenue_value"),
    ("Check (a) Assets = L+SE: failures",
     f"{(checks['check_a'] == 'fail').sum():,} of {(checks['check_a'] != 'not_applicable').sum():,}",
     "fs_checks.check_a"),
    ("Check (b) L + SE = L+SE: failures", f"{len(b_fail):,} of {len(b_ran):,} (SPACs: {spac_fail:,})",
     "fs_checks.check_b"),
    ("Listed companies with a 10-K", f"{len(with_10k):,} of {len(joined):,}", JOINED_CSV.name),
    ("Pass all checks (fully checked: both checks ran)",
     f"{pct(fully_checked['all_checks_pass'].sum(), len(fully_checked))} of {len(fully_checked):,}",
     JOINED_CSV.name),
    ("Pass all checks (all companies with a 10-K, incl. checks not run)",
     f"{pct(with_10k['all_checks_pass'].sum(), len(with_10k))} of {len(with_10k):,}", JOINED_CSV.name),
]
for path in REVIEW_FILES:
    if path.exists():
        review = pd.read_csv(path, dtype=str, keep_default_na=False)
        open_rows = (review["status"] != "no_longer_needs_review").sum()
        reviewed = (review["my_verdict"] != "").sum()
        summary.append((f"Manual review: {path.name}", f"{open_rows:,} open, {reviewed:,} with a verdict", path.name))

top = with_10k[with_10k["flags"].notna()].nlargest(10, "Assets")

# ---------- build HTML ----------
e = html.escape


def table(headers, rows, numeric_cols=()):
    head = "".join(f'<th class="{"num" if i in numeric_cols else ""}">{e(h)}</th>' for i, h in enumerate(headers))
    body = "".join(
        "<tr>" + "".join(f'<td class="{"num" if i in numeric_cols else ""}">{e(str(c))}</td>'
                         for i, c in enumerate(r)) + "</tr>"
        for r in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def chart(file_name, alt):
    data = base64.b64encode((CHARTS / file_name).read_bytes()).decode("ascii")
    return f'<figure><img src="data:image/png;base64,{data}" alt="{e(alt)}"></figure>'


summary_html = table(["Measure", "Value", "Source"], summary, numeric_cols=(1,))
top_html = table(
    ["Tickers", "Company", "Exchange", "Assets ($B)", "Flags"],
    [(r["tickers"], r["name"], r["exchange_group"], f"{r['Assets'] / 1e9:,.1f}", r["flags"])
     for _, r in top.iterrows()],
    numeric_cols=(3,))
charts_html = "\n".join(chart(f, alt) for f, alt in CHART_FILES)

page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reference Data QC Report</title>
<style>
  :root {{ --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781; --line: #e1e0d9; --surface: #fcfcfb; --page: #f9f9f7; }}
  body {{ margin: 0; background: var(--page); color: var(--ink);
         font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width: 960px; margin: 0 auto; padding: 32px 16px 48px; }}
  h1 {{ font-size: 1.6rem; margin: 0 0 4px; }}
  h2 {{ font-size: 1.15rem; margin: 40px 0 12px; }}
  .meta {{ color: var(--muted); font-size: 0.85rem; margin: 0; }}
  .scroll {{ overflow-x: auto; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }}
  th {{ color: var(--ink-2); font-weight: 600; white-space: nowrap; }}
  tr:last-child td {{ border-bottom: none; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  td:last-child {{ color: var(--ink-2); }}
  figure {{ margin: 0 0 16px; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 8px; }}
  img {{ display: block; max-width: 100%; height: auto; }}
</style>
</head>
<body>
<main>
<h1>US Equity Reference Data Reconciliation + Financial Statement QC</h1>
<p class="meta">Generated {datetime.now():%Y-%m-%d %H:%M} by scripts/08_report.py from reference.db and output/</p>

<h2>Headline numbers</h2>
{summary_html}

<h2>10 largest flagged companies (by total assets)</h2>
{top_html}

<h2>Charts</h2>
{charts_html}
</main>
</body>
</html>
"""
REPORT.write_text(page, encoding="utf-8")
print(f"wrote {REPORT} ({REPORT.stat().st_size / 1024:,.0f} KB)")
print("summary:")
for label, value, _ in summary:
    print(f"  {label}: {value}")
