"""Reference data QC dashboard.

Run from the project root:  streamlit run app/dashboard.py
Every number on every page is read from reference.db, output/ or the raw files' own metadata.
"""
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "reference.db"
OUT = ROOT / "output"
RAW = ROOT / "data" / "raw"
sys.path.insert(0, str(ROOT / "scripts"))
from review_file import update_verdicts  # noqa: E402  (shared merge-safe save logic)

# validated categorical slots (blue, aqua, orange) + neutral ink, same as the PNG charts
BLUE, AQUA, ORANGE = "#2a78d6", "#1baf7a", "#eb6834"
INK_2, GRID, SURFACE = "#52514e", "#e1e0d9", "#fcfcfb"
GROUP_ORDER = ["bank", "blank check / SPAC", "REIT", "insurance", "other", "missing SIC"]
REVIEW_FILES = {  # file -> key column used by the merge rule
    "review_sec_only.csv": "ticker",
    "review_check_b_failures.csv": "adsh",
}

st.set_page_config(page_title="Reference Data QC", page_icon=":material/fact_check:", layout="wide")
st.markdown("""
<style>
  .block-container { padding-top: 2rem; max-width: 1400px; }
  [data-testid="stMetricValue"] { font-size: 1.7rem; }
  [data-testid="stSidebar"] .asof { font-size: 0.8rem; color: #52514e; line-height: 1.5; }
  h1 { font-size: 1.7rem !important; }
  h3 { font-size: 1.1rem !important; margin-top: 0.6rem; }
</style>
""", unsafe_allow_html=True)


# ---------- data access (cached; nothing computed here is typed in by hand) ----------
@st.cache_data
def read_table(name):
    with sqlite3.connect(DB) as conn:
        return pd.read_sql(f'select * from "{name}"', conn)


@st.cache_data
def count_rows(name):
    with sqlite3.connect(DB) as conn:
        return conn.execute(f'select count(*) from "{name}"').fetchone()[0]


def table_exists(name):
    with sqlite3.connect(DB) as conn:
        return conn.execute("select 1 from sqlite_master where type='table' and name=?", (name,)).fetchone() is not None


def read_review(file_name):
    # not cached: the workbench must always show what is on disk
    return pd.read_csv(OUT / file_name, dtype=str, keep_default_na=False)


def nasdaq_file_time(path):
    # Nasdaq Trader footer: "File Creation Time: 1106202609:31" (MMDDYYYYHH:MM)
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        m = re.match(r"File Creation Time: (\d{10}:\d{2})", line)
        if m:
            return datetime.strptime(m.group(1), "%m%d%Y%H:%M").strftime("%Y-%m-%d %H:%M")
    return None


def file_date(path):
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


if not DB.exists():
    st.error(f"{DB.name} not found. Run scripts 01-07 first (see README, 'How to run').")
    st.stop()

tickers = read_table("ticker_review")
checks = read_table("fs_checks")
joined = read_table("company_fs_joined")
sub_10k = read_table("fs_sub_10k")


# ---------- shared helpers ----------
def pct(part, whole):
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def style_fig(fig, height=380):
    fig.update_layout(template="plotly_white", height=height, margin=dict(l=10, r=10, t=40, b=10),
                      paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, font=dict(color=INK_2),
                      legend=dict(orientation="h", yanchor="top", y=-0.15, x=0))
    fig.update_xaxes(gridcolor=GRID)
    fig.update_yaxes(gridcolor=GRID)
    return fig


def stacked_share_fig(table, colors, title):
    # 100% stacked horizontal bars; hover shows count and share
    shares = table.div(table.sum(axis=1), axis=0) * 100
    labels = [f"{g} (n={int(n):,})" for g, n in zip(table.index, table.sum(axis=1))]
    fig = go.Figure()
    for col, color in zip(table.columns, colors):
        fig.add_bar(y=labels, x=shares[col], name=col, orientation="h", marker_color=color,
                    marker_line=dict(color=SURFACE, width=2), customdata=table[col],
                    text=[f"{v:.0f}%" if v >= 6 else "" for v in shares[col]], textposition="inside",
                    hovertemplate="%{y}<br>" + col + ": %{customdata:,} (%{x:.1f}%)<extra></extra>")
    fig.update_layout(barmode="stack", title=title, xaxis=dict(range=[0, 100], title="% of filings"))
    fig.update_yaxes(autorange="reversed")
    return style_fig(fig)


def companies_with_10k():
    w = joined[joined["has_10k"] == 1].copy()
    w["fully_checked"] = (w["check_a"] != "not_applicable") & (w["check_b"] != "not_applicable")
    return w


def revenue_status(df):
    status = pd.Series("no revenue - other", index=df.index)
    status[df["revenue_not_comparable_industry"] == 1] = "no revenue - industry not comparable"
    status[df["revenue_value"].notna()] = "has revenue value"
    return status


# ---------- sidebar: navigation + as-of dates ----------
PAGES = ["Overview", "Reconciliation", "Financial checks", "Flagged companies",
         "Exception workbench", "Data lineage"]
with st.sidebar:
    st.markdown("### Reference Data QC")
    page = st.radio("Navigate", PAGES, label_visibility="collapsed")
    st.divider()
    st.markdown("**Data as of**")
    asof = []
    sec_json = RAW / "company_tickers_exchange.json"
    asof.append(("SEC tickers JSON", f"file date {file_date(sec_json)}" if sec_json.exists() else "file not found"))
    for name in ["nasdaqlisted.txt", "otherlisted.txt"]:
        p = RAW / name
        t = nasdaq_file_time(p) if p.exists() else None
        asof.append((f"Nasdaq Trader {name}", f"created {t}" if t else "file not found"))
    filed = pd.to_datetime(sub_10k["filed"], format="%Y%m%d")
    sub_path = RAW / "sub.txt"
    fsds = f"10-Ks filed {filed.min():%Y-%m-%d} to {filed.max():%Y-%m-%d}"
    if sub_path.exists():
        fsds += f"; file date {file_date(sub_path)}"
    asof.append(("SEC FSDS", fsds))
    st.markdown("<div class='asof'>" + "<br>".join(f"<b>{k}</b>: {v}" for k, v in asof) + "</div>",
                unsafe_allow_html=True)
    st.divider()
    st.caption(f"All figures read from {DB.name} and output/. "
               f"Database last written {datetime.fromtimestamp(DB.stat().st_mtime):%Y-%m-%d %H:%M}.")


# =====================================================================
# 1. Overview
# =====================================================================
if page == "Overview":
    st.title("Overview")
    groups = tickers["review_group"].value_counts()
    w = companies_with_10k()
    full = w[w["fully_checked"]]
    b_ok = full["check_b"].isin(["pass", "explained"]).sum()
    has_rev = checks["revenue_value"].notna().sum()

    c = st.columns(6)
    c[0].metric("Matched tickers", f"{groups.get('matched', 0):,}", border=True,
                help="ticker_review.review_group = matched")
    c[1].metric("Expected differences", f"{groups.get('expected difference', 0):,}", border=True,
                help="OTC, ETFs, likely SPACs, mutual funds, non-SEC-filer banks")
    c[2].metric("Needs investigation", f"{groups.get('needs investigation', 0):,}", border=True,
                help="Reasons: delisted, renamed, unexplained")
    c[3].metric("10-K filings", f"{len(checks):,}", border=True, help="fs_checks rows (2026 Q1 FSDS)")
    c[4].metric("Revenue coverage", pct(has_rev, len(checks)), border=True,
                help=f"{has_rev:,} of {len(checks):,} 10-Ks have a revenue value")
    c[5].metric("Check (b) pass rate", pct(b_ok, len(full)), border=True,
                help=f"Pass or explained, among {len(full):,} fully checked listed companies "
                     "(both check (a) and (b) ran)")

    left, right = st.columns(2)
    with left:
        counts = groups.reindex(["matched", "expected difference", "needs investigation"]).fillna(0)
        fig = go.Figure(go.Bar(x=counts.index, y=counts.values, marker_color=BLUE,
                               text=[f"{int(v):,}" for v in counts.values], textposition="outside",
                               hovertemplate="%{x}: %{y:,} tickers<extra></extra>"))
        fig.update_layout(title="SEC vs Nasdaq Trader ticker reconciliation", yaxis_title="tickers", showlegend=False)
        st.plotly_chart(style_fig(fig), width="stretch")
    with right:
        table = pd.crosstab(checks["sic_group"], revenue_status(checks)).reindex(GROUP_ORDER).fillna(0)
        table = table.reindex(columns=["has revenue value", "no revenue - industry not comparable",
                                       "no revenue - other"], fill_value=0)
        st.plotly_chart(stacked_share_fig(table, [BLUE, AQUA, ORANGE], "Revenue coverage by industry group"),
                        width="stretch")

    left, right = st.columns(2)
    with left:
        b = checks[checks["check_b"] != "not_applicable"]
        table = pd.crosstab(b["sic_group"], b["check_b"]).reindex(GROUP_ORDER).fillna(0)
        table = table.reindex(columns=["pass", "explained", "fail"], fill_value=0)
        st.plotly_chart(stacked_share_fig(table, [BLUE, AQUA, ORANGE], "Check (b) results by industry group"),
                        width="stretch")
    with right:
        rows = []
        for name, part in list(w.groupby("exchange_group")) + [("total", w)]:
            f = part[part["fully_checked"]]
            rows.append((name, 100 * part["all_checks_pass"].mean(), len(part),
                         100 * f["all_checks_pass"].mean() if len(f) else 0, len(f)))
        r = pd.DataFrame(rows, columns=["exchange", "all", "n_all", "full", "n_full"]).set_index("exchange")
        r = r.reindex([x for x in ["NYSE", "Nasdaq", "other", "total"] if x in r.index])
        fig = go.Figure()
        fig.add_bar(x=r.index, y=r["all"], name="all companies with a 10-K", marker_color=BLUE,
                    customdata=r["n_all"], text=[f"{v:.1f}%" for v in r["all"]], textposition="outside",
                    hovertemplate="%{x}: %{y:.1f}% of %{customdata:,}<extra></extra>")
        fig.add_bar(x=r.index, y=r["full"], name="fully checked only", marker_color=ORANGE,
                    customdata=r["n_full"], text=[f"{v:.1f}%" for v in r["full"]], textposition="outside",
                    hovertemplate="%{x}: %{y:.1f}% of %{customdata:,}<extra></extra>")
        fig.update_layout(barmode="group", title="Share passing all checks, by exchange",
                          yaxis=dict(range=[0, 110], title="% passing all checks"))
        st.plotly_chart(style_fig(fig), width="stretch")

# =====================================================================
# 2. Reconciliation
# =====================================================================
elif page == "Reconciliation":
    st.title("Reconciliation: SEC vs Nasdaq Trader")
    df = tickers.copy()
    df["reason"] = df["reason"].fillna("(none)")
    f1, f2, f3, f4 = st.columns([1, 1, 1, 1.2])
    group_sel = f1.multiselect("Review group", sorted(df["review_group"].unique()))
    cat_sel = f2.multiselect("Category", sorted(df["category"].unique()))
    reason_sel = f3.multiselect("Reason", sorted(df["reason"].unique()))
    search = f4.text_input("Search ticker or name", placeholder="e.g. BRK-B, Hilton")
    etf = st.radio("ETFs", ["all", "ETF only", "exclude ETFs"], horizontal=True)

    if group_sel:
        df = df[df["review_group"].isin(group_sel)]
    if cat_sel:
        df = df[df["category"].isin(cat_sel)]
    if reason_sel:
        df = df[df["reason"].isin(reason_sel)]
    if etf == "ETF only":
        df = df[df["is_etf"] == 1]
    elif etf == "exclude ETFs":
        df = df[df["is_etf"] != 1]
    if search:
        s = search.strip().lower()
        fields = ["ticker", "sec_ticker_original", "nt_ticker_original", "sec_name", "nt_name"]
        df = df[df[fields].fillna("").apply(lambda col: col.str.lower().str.contains(s, regex=False)).any(axis=1)]

    st.subheader("Category x reason")
    st.dataframe(pd.crosstab(df["category"], df["reason"], margins=True, margins_name="total"),
                 width="stretch")
    st.subheader(f"Tickers ({len(df):,})")
    cols = ["ticker", "sec_ticker_original", "nt_ticker_original", "category", "reason", "review_group",
            "sec_name", "nt_name", "sec_exchange_original", "nt_exchange_detail", "is_etf",
            "name_score", "match_rule", "cik"]
    st.dataframe(df[cols], hide_index=True, width="stretch", height=480,
                 column_config={"is_etf": st.column_config.CheckboxColumn("ETF"),
                                "name_score": st.column_config.NumberColumn("name score", format="%.3f"),
                                "cik": st.column_config.NumberColumn("CIK", format="%d")})

# =====================================================================
# 3. Financial checks
# =====================================================================
elif page == "Financial checks":
    st.title("Financial statement checks (10-K, 2026 Q1)")
    groups_sel = st.multiselect("Industry group", GROUP_ORDER, default=GROUP_ORDER)
    df = checks[checks["sic_group"].isin(groups_sel)]

    def check_table(col):
        t = pd.crosstab(df["sic_group"], df[col]).reindex([g for g in GROUP_ORDER if g in groups_sel]).fillna(0)
        t = t.reindex(columns=[c for c in ["pass", "explained", "fail", "not_applicable"] if c in t.columns])
        t.loc["total"] = t.sum()
        ran = t.drop(columns="not_applicable", errors="ignore").sum(axis=1)
        fails = t["fail"] if "fail" in t.columns else pd.Series(0, index=t.index)  # check (a) may have none
        t["fail rate (of checks run)"] = [pct(f, n) for f, n in zip(fails, ran)]
        return t

    left, right = st.columns(2)
    with left:
        st.subheader("Check (a): Assets = Liabilities and Equity")
        st.dataframe(check_table("check_a"), width="stretch")
    with right:
        st.subheader("Check (b): Liabilities + Equity = Liabilities and Equity")
        st.dataframe(check_table("check_b"), width="stretch")

    st.subheader("How check (b) gaps were explained")
    expl = df["check_b_explained_by"].dropna().value_counts().rename_axis("explained by").reset_index(name="filings")
    st.dataframe(expl, hide_index=True, width="stretch")

    st.subheader("Revenue source")
    left, right = st.columns([1, 1.4])
    with left:
        src = df["revenue_source_tag"].fillna("none").value_counts().rename_axis("revenue_source_tag")
        src = src.reset_index(name="filings")
        src["share"] = [pct(n, len(df)) for n in src["filings"]]
        st.dataframe(src, hide_index=True, width="stretch")
        flags = pd.DataFrame({
            "revenue flag": ["revenue_component_smaller (expected)", "revenue_component_exceeds_total (anomaly)",
                             "revenue_not_comparable_industry", "revenue includes sales tax"],
            "filings": [int(df["revenue_component_smaller"].sum()), int(df["revenue_component_exceeds_total"].sum()),
                        int(df["revenue_not_comparable_industry"].sum()), int(df["revenue_note"].notna().sum())],
        })
        st.dataframe(flags, hide_index=True, width="stretch")
    with right:
        st.dataframe(pd.crosstab(df["sic_group"], df["revenue_source_tag"].fillna("none"), margins=True,
                                 margins_name="total"), width="stretch")

# =====================================================================
# 4. Flagged companies
# =====================================================================
elif page == "Flagged companies":
    st.title("Flagged companies")
    w = companies_with_10k()
    flagged = w[w["flags"].notna() & (w["flags"] != "")].copy()
    flagged["Assets ($B)"] = flagged["Assets"] / 1e9
    all_flags = sorted({f for fl in flagged["flags"] for f in fl.split(", ")})
    flag_sel = st.multiselect("Flag", all_flags)
    if flag_sel:
        flagged = flagged[flagged["flags"].apply(lambda fl: any(f in fl.split(", ") for f in flag_sel))]
    st.caption(f"{len(flagged):,} listed companies with at least one failure flag. Click a column header to sort.")
    st.dataframe(flagged.sort_values("Assets", ascending=False)[
        ["tickers", "name", "exchange_group", "sic_group", "Assets ($B)", "flags", "info_notes"]],
        hide_index=True, width="stretch", height=380,
        column_config={"Assets ($B)": st.column_config.NumberColumn(format="%.2f")})

    st.subheader("Company detail")
    options = flagged.sort_values("Assets", ascending=False)
    if options.empty:
        st.info("No companies match this filter.")
        st.stop()
    labels = [f"{t} - {n}" for t, n in zip(options["tickers"], options["name"])]
    choice = st.selectbox("Company", range(len(options)), format_func=lambda i: labels[i])
    company = options.iloc[choice]
    filing = checks[checks["adsh"] == company["adsh"]].iloc[0]

    c = st.columns(4)
    c[0].metric("Check (a)", filing["check_a"], border=True)
    c[1].metric("Check (b)", filing["check_b"], border=True)
    c[2].metric("Check (b) gap", f"{filing['check_b_gap']:,.0f}" if pd.notna(filing["check_b_gap"]) else "—",
                border=True)
    c[3].metric("Revenue source", filing["revenue_source_tag"] or "none", border=True)

    def money(v):
        return f"{v:,.0f}" if pd.notna(v) else "— (not reported)"

    left, right = st.columns(2)
    with left:
        st.markdown("**Balance sheet values used by the checks (USD)**")
        bs = ["Assets", "Liabilities", "StockholdersEquity", "LSE", "MinorityInterest", "EquityInclNCI",
              "TempEquityInclNCI", "TempEquityParent", "TempEquityExclAPIC", "TempEquityRedemptionReq",
              "RedeemableNCI", "RedeemableNCICommon", "RedeemableNCIPreferred", "liabilities_derived"]
        st.dataframe(pd.DataFrame({"item": bs, "value": [money(filing[c]) for c in bs]}),
                     hide_index=True, width="stretch")
    with right:
        st.markdown("**Revenue and income (USD)**")
        inc = ["Revenues", "RevenueFromContracts", "RevenueInclTax", "revenue_value", "NetIncomeLoss"]
        st.dataframe(pd.DataFrame({"item": inc, "value": [money(filing[c]) for c in inc]}),
                     hide_index=True, width="stretch")
        st.markdown("**Filing**")
        info = {
            "Company (SEC)": filing["name"], "CIK": filing["cik"], "Accession (adsh)": filing["adsh"],
            "Period": filing["period"], "Fiscal year": filing["fy"], "Industry group": filing["sic_group"],
            "Flags": company["flags"], "Notes": company["info_notes"] or "—",
            "Check (b) explained by": filing["check_b_explained_by"] or "—",
        }
        st.dataframe(pd.DataFrame({"field": info.keys(), "value": [str(v) for v in info.values()]}),
                     hide_index=True, width="stretch")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(filing['cik'])}/{filing['adsh'].replace('-', '')}/"
        st.link_button("Open filing on SEC EDGAR", url)

# =====================================================================
# 5. Exception workbench
# =====================================================================
elif page == "Exception workbench":
    st.title("Exception workbench")
    available = [f for f in REVIEW_FILES if (OUT / f).exists()]
    if not available:
        st.warning("No review files found in output/.")
        st.stop()
    file_name = st.radio("Review file", available, horizontal=True)
    key = REVIEW_FILES[file_name]
    full = read_review(file_name)
    if "status" not in full.columns:          # files written before the merge rule
        full["status"] = "(none)"

    f1, f2 = st.columns(2)
    statuses = sorted(full["status"].unique())
    status_sel = f1.multiselect("Status", statuses, default=statuses)
    verdict_sel = f2.radio("Verdict", ["all", "no verdict yet", "has verdict"], horizontal=True)
    view = full[full["status"].isin(status_sel)]
    if verdict_sel == "no verdict yet":
        view = view[view["my_verdict"] == ""]
    elif verdict_sel == "has verdict":
        view = view[view["my_verdict"] != ""]

    done = (full["my_verdict"] != "").sum()
    st.progress(done / len(full) if len(full) else 0, text=f"{done:,} of {len(full):,} rows have a verdict")
    st.caption("Only my_verdict and my_note are editable. Saving writes them back by "
               f"'{key}'; all other rows and columns in the file stay unchanged.")

    edited = st.data_editor(
        view, hide_index=True, width="stretch", height=480, key=f"editor_{file_name}",
        disabled=[c for c in view.columns if c not in ("my_verdict", "my_note")],
        column_config={"my_verdict": st.column_config.TextColumn("my_verdict", width="medium"),
                       "my_note": st.column_config.TextColumn("my_note", width="large")})

    before = view.set_index(key)[["my_verdict", "my_note"]]
    after = edited.set_index(key)[["my_verdict", "my_note"]].fillna("")
    changed = after[(after != before).any(axis=1)].reset_index()
    st.write(f"Unsaved changes: **{len(changed)}**")
    if st.button("Save verdicts", type="primary", disabled=changed.empty):
        try:
            n = update_verdicts(OUT / file_name, key, changed)
            st.success(f"Saved {n} row(s) to output/{file_name}.")
            st.rerun()
        except PermissionError:
            st.error(f"output/{file_name} is locked - close it in Excel and try again.")

# =====================================================================
# 6. Data lineage
# =====================================================================
elif page == "Data lineage":
    st.title("Data lineage")

    def rows(table):
        return count_rows(table) if table_exists(table) else None

    def csv_rows(name):
        p = OUT / name
        return len(pd.read_csv(p, dtype=str, keep_default_na=False)) if p.exists() else None

    # stage, artifact, kind, produced by, rows (None = not recorded in any output)
    lineage = [
        ("source", "company_tickers_exchange.json", "raw file", "SEC download", rows("raw_sec_tickers")),
        ("source", "nasdaqlisted.txt", "raw file", "Nasdaq Trader download", rows("raw_nasdaqlisted")),
        ("source", "otherlisted.txt", "raw file", "Nasdaq Trader download", rows("raw_otherlisted")),
        ("source", "sub.txt / num.txt / pre.txt", "raw files", "SEC FSDS 2026q1", None),
        ("table", "raw_sec_tickers", "SQLite", "01_load_raw.py", rows("raw_sec_tickers")),
        ("table", "raw_nasdaqlisted", "SQLite", "01_load_raw.py", rows("raw_nasdaqlisted")),
        ("table", "raw_otherlisted", "SQLite", "01_load_raw.py", rows("raw_otherlisted")),
        ("table", "ticker_match", "SQLite", "02_match_tickers.py", rows("ticker_match")),
        ("table", "ticker_review", "SQLite", "03_review.py", rows("ticker_review")),
        ("output", "review_sec_only.csv", "CSV", "03_review.py", csv_rows("review_sec_only.csv")),
        ("table", "fs_sub_10k", "SQLite", "04_fs_extract.py", rows("fs_sub_10k")),
        ("table", "fs_values", "SQLite", "04_fs_extract.py", rows("fs_values")),
        ("table", "fs_values_current", "SQLite", "04_fs_extract.py", rows("fs_values_current")),
        ("table", "fs_checks", "SQLite", "05_fs_checks.py", rows("fs_checks")),
        ("output", "review_check_b_failures.csv", "CSV", "05_fs_checks.py", csv_rows("review_check_b_failures.csv")),
        ("table", "company_fs_joined", "SQLite + CSV", "06_join.py", rows("company_fs_joined")),
        ("output", "charts/*.png", "PNG", "07_charts.py", len(list((OUT / "charts").glob("*.png")))),
        ("output", "report.html", "HTML", "08_report.py", None),
    ]
    lin = pd.DataFrame(lineage, columns=["stage", "artifact", "kind", "produced by", "rows"])

    def label(name, n):
        return f"{name}\\n{n:,} rows" if n is not None else name

    r = dict(zip(lin["artifact"], lin["rows"]))
    dot = f"""
    digraph lineage {{
      rankdir=LR; node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, color="#c3c2b7"];
      edge [color="#898781"];
      node [fillcolor="#cde2fb"];
      sec [label="{label('SEC tickers JSON', r['company_tickers_exchange.json'])}"];
      nql [label="{label('nasdaqlisted.txt', r['nasdaqlisted.txt'])}"];
      oth [label="{label('otherlisted.txt', r['otherlisted.txt'])}"];
      fsds [label="SEC FSDS 2026q1\\nsub / num / pre"];
      node [fillcolor="#f3f2ee"];
      s01 [label="01_load_raw"]; s02 [label="02_match_tickers"]; s03 [label="03_review"];
      s04 [label="04_fs_extract"]; s05 [label="05_fs_checks"]; s06 [label="06_join"];
      s07 [label="07_charts"]; s08 [label="08_report"];
      node [fillcolor="#d4f0e4"];
      raw [label="raw_* tables"];
      tm [label="{label('ticker_match', r['ticker_match'])}"];
      tr [label="{label('ticker_review', r['ticker_review'])}"];
      rso [label="{label('review_sec_only.csv', r['review_sec_only.csv'])}"];
      fv [label="{label('fs_values_current', r['fs_values_current'])}"];
      fc [label="{label('fs_checks', r['fs_checks'])}"];
      rcb [label="{label('review_check_b_failures.csv', r['review_check_b_failures.csv'])}"];
      cj [label="{label('company_fs_joined', r['company_fs_joined'])}"];
      ch [label="charts (PNG)"]; rep [label="report.html"];
      sec -> s01; nql -> s01; oth -> s01; s01 -> raw; raw -> s02; s02 -> tm; tm -> s03;
      s03 -> tr; s03 -> rso; fsds -> s04; s04 -> fv; fv -> s05; s05 -> fc; s05 -> rcb;
      fc -> s06; tr -> s06; s06 -> cj; cj -> s07; fc -> s07; tr -> s07; s07 -> ch;
      ch -> s08; cj -> s08; s08 -> rep;
    }}"""
    st.graphviz_chart(dot, width="stretch")
    st.dataframe(lin, hide_index=True, width="stretch",
                 column_config={"rows": st.column_config.NumberColumn(format="%d")})
    st.caption("Rows left empty are not recorded in any output (e.g. FSDS raw file sizes).")
