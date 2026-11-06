"""Step 7: summary charts (PNG) from the SQLite results."""
import sqlite3
from pathlib import Path

import matplotlib
matplotlib.use("Agg")   # write files only, no window
import matplotlib.pyplot as plt
import pandas as pd

DB_PATH = "reference.db"
OUT = Path("output/charts")
OUT.mkdir(parents=True, exist_ok=True)

# colors: validated categorical slots (blue, aqua, orange) + neutral ink/grid
BLUE, AQUA, ORANGE = "#2a78d6", "#1baf7a", "#eb6834"
SURFACE, INK, INK_2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.family": ["Segoe UI", "DejaVu Sans"], "font.size": 10,
    "text.color": INK, "axes.labelcolor": INK_2, "xtick.color": MUTED, "ytick.color": INK_2,
    "axes.edgecolor": GRID, "axes.spines.top": False, "axes.spines.right": False,
})

with sqlite3.connect(DB_PATH) as conn:
    tickers = pd.read_sql("select review_group from ticker_review", conn)
    checks = pd.read_sql("select sic_group, revenue_value, revenue_not_comparable_industry, check_b "
                         "from fs_checks", conn)
    joined = pd.read_sql("select exchange_group, has_10k, all_checks_pass, check_a, check_b "
                         "from company_fs_joined", conn)

GROUP_ORDER = ["bank", "blank check / SPAC", "REIT", "insurance", "other", "missing SIC"]


def save(fig, name):
    fig.tight_layout()
    fig.savefig(OUT / name, dpi=150)
    plt.close(fig)
    print(f"saved {OUT / name}")


def stacked_share(ax, table, colors):
    # 100% stacked horizontal bars; label segments wide enough to hold text
    shares = table.div(table.sum(axis=1), axis=0) * 100
    left = pd.Series(0.0, index=shares.index)
    for col, color in zip(shares.columns, colors):
        ax.barh(shares.index, shares[col], left=left, color=color, height=0.6,
                edgecolor=SURFACE, linewidth=2, label=col)
        for y, (x0, w) in enumerate(zip(left, shares[col])):
            if w >= 6:
                ax.text(x0 + w / 2, y, f"{w:.0f}%", ha="center", va="center", fontsize=9, color="white")
        left += shares[col]
    ax.set_xlim(0, 100)
    ax.set_xlabel("% of filings")
    ax.invert_yaxis()
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=len(colors), frameon=False)


# ---------- 1. reconciliation categories ----------
counts = tickers["review_group"].value_counts().reindex(
    ["matched", "expected difference", "needs investigation"])
fig, ax = plt.subplots(figsize=(7, 4))
ax.bar(counts.index, counts.values, color=BLUE, width=0.55)
for x, v in enumerate(counts.values):
    ax.text(x, v, f"{v:,}", ha="center", va="bottom", fontsize=10, color=INK)
ax.set_title("SEC vs Nasdaq Trader ticker reconciliation", loc="left", fontsize=12)
ax.set_ylabel("tickers")
ax.grid(axis="y", color=GRID, linewidth=0.8)
ax.set_axisbelow(True)
save(fig, "1_reconciliation.png")

# ---------- 2. revenue coverage by industry group ----------
checks["revenue_status"] = "no revenue - other"
checks.loc[checks["revenue_not_comparable_industry"] == 1, "revenue_status"] = "no revenue - industry not comparable"
checks.loc[checks["revenue_value"].notna(), "revenue_status"] = "has revenue value"
table = pd.crosstab(checks["sic_group"], checks["revenue_status"]).reindex(GROUP_ORDER)[
    ["has revenue value", "no revenue - industry not comparable", "no revenue - other"]]
table.index = [f"{g} (n={n:,})" for g, n in zip(table.index, table.sum(axis=1))]
fig, ax = plt.subplots(figsize=(9, 4.5))
stacked_share(ax, table, [BLUE, AQUA, ORANGE])
ax.set_title("Revenue coverage by industry group (10-K filings, 2026 Q1)", loc="left", fontsize=12)
save(fig, "2_revenue_coverage.png")

# ---------- 3. check (b) by industry group ----------
b = checks[checks["check_b"] != "not_applicable"]
table = pd.crosstab(b["sic_group"], b["check_b"]).reindex(GROUP_ORDER).fillna(0)[["pass", "explained", "fail"]]
table.index = [f"{g} (n={int(n):,})" for g, n in zip(table.index, table.sum(axis=1))]
fig, ax = plt.subplots(figsize=(9, 4.5))
stacked_share(ax, table, [BLUE, AQUA, ORANGE])
ax.set_title("Check (b): Liabilities + Equity vs Total L&E, by industry group", loc="left", fontsize=12)
save(fig, "3_check_b_by_group.png")

# ---------- 4. share passing all checks by exchange ----------
w = joined[joined["has_10k"] == 1].copy()
w["fully_checked"] = (w["check_a"] != "not_applicable") & (w["check_b"] != "not_applicable")
rows = []
for name, part in list(w.groupby("exchange_group")) + [("total", w)]:
    full = part[part["fully_checked"]]
    rows.append({"exchange": name,
                 "all": 100 * part["all_checks_pass"].mean(), "n_all": len(part),
                 "full": 100 * full["all_checks_pass"].mean(), "n_full": len(full)})
r = pd.DataFrame(rows).set_index("exchange").reindex(["NYSE", "Nasdaq", "other", "total"])
fig, ax = plt.subplots(figsize=(8, 4.5))
x = range(len(r))
width = 0.36
for offset, col, ncol, color, label in [(-width / 2, "all", "n_all", BLUE, "all companies with a 10-K"),
                                        (width / 2, "full", "n_full", ORANGE, "fully checked only (a and b ran)")]:
    ax.bar([i + offset for i in x], r[col], width=width, color=color, edgecolor=SURFACE, linewidth=2, label=label)
    for i, (v, n) in enumerate(zip(r[col], r[ncol])):
        ax.text(i + offset, v + 1, f"{v:.1f}%\nn={n:,}", ha="center", va="bottom", fontsize=8, color=INK_2)
ax.set_xticks(list(x), r.index)
ax.set_ylim(0, 115)
ax.set_yticks(range(0, 101, 20))
ax.set_ylabel("% passing all checks")
ax.set_title("Listed companies passing all checks, by exchange", loc="left", fontsize=12)
ax.grid(axis="y", color=GRID, linewidth=0.8)
ax.set_axisbelow(True)
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=2, frameon=False)
save(fig, "4_pass_by_exchange.png")
