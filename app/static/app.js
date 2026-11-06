// Security Master DQ Scorecard - renders figures served by app/server.py.
// No number is typed in here: everything comes from /api/* (reference.db + output/).
"use strict";

// ---------- helpers ----------
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => (n === null || n === undefined ? "—" : Number(n).toLocaleString("en-US"));
const pct = (a, b, d = 1) => (b ? `${((100 * a) / b).toFixed(d)}%` : "n/a");
const billions = (v) => (v === null || v === undefined ? "—" : `$${(v / 1e9).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}B`);
const money = (v) => (v === null || v === undefined ? "—" : `$${Math.round(v).toLocaleString("en-US")}`);
const COLORS = { c1: "var(--c1)", c2: "var(--c2)", c3: "var(--c3)", na: "var(--c-na)" };

async function getJSON(url, options) {
  const res = await fetch(url, options);
  if (!(res.headers.get("Content-Type") || "").includes("application/json")) {
    // page opened without app/server.py (e.g. VS Code Live Server or as a file)
    throw new Error("NO_SERVER");
  }
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

function toast(message, isError = false) {
  const t = $("toast");
  t.textContent = message;
  t.className = "toast" + (isError ? " error" : "");
  t.hidden = false;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => (t.hidden = true), 3500);
}

const state = { d: null, tickers: null, review: null, flagExchange: "all", flagSort: { key: "Assets", dir: -1 },
  flagShowAll: false, recon: { group: "", category: "", reason: "", etf: "all", q: "" },
  wb: { file: null, status: "", verdict: "all", q: "" }, edits: {} };

// ---------- sidebar, header ----------
function renderChrome(d) {
  const k = d.kpis;
  $("db-written").textContent = d.db_written.slice(5);
  $("asof").innerHTML = d.asof.map((a) => `<dt>${esc(a.source)}</dt><dd>${esc(a.value)}</dd>`).join("");
  $("nav-open").textContent = fmt(k.review_open);
  $("open-pill").textContent = `${fmt(k.review_open)} open exceptions`;
  $("quarter-pill").textContent = `10-K ${k.quarter}`;
  $("head-quarter").textContent = `10-K ${k.quarter} · FSDS`;
  $("meta-line").innerHTML =
    `<span class="last-run">Last run: ${esc(d.db_written)}</span> (reference.db last written)` +
    ` · page loaded ${esc(d.generated_at)} · source tables: <code>ticker_review</code> <code>fs_checks</code> <code>company_fs_joined</code>`;
  $("foot").textContent = `All figures computed at ${d.generated_at} from reference.db and output/. Nothing on this page is hardcoded.`;
}

// ---------- KPI tiles ----------
function renderKpis(d) {
  const k = d.kpis;
  const reasons = k.investigation_reasons.map((r) => `${fmt(r.count)} ${esc(r.reason)}`).join(" · ");
  const reviews = k.reviews.map((r) => `${fmt(r.open)} in ${esc(r.file)}`).join(" + ");
  const invFiles = k.reviews.filter((r) => r.needs_investigation).map((r) => `${fmt(r.needs_investigation)} in ${esc(r.file)}`).join(" · ");
  const tiles = [
    { label: "Pass all checks (fully checked)", badge: `<span class="pill pill-gray">n=${fmt(k.fully_checked)}</span>`,
      value: pct(k.pass_all_fully_checked, k.fully_checked),
      desc: `${fmt(k.pass_all_fully_checked)} of ${fmt(k.fully_checked)} listed companies where both check (a) and (b) ran pass all checks.`,
      foot: `<span>All with a 10-K (incl. checks not run): <b>${pct(k.pass_all, k.companies_with_10k)}</b></span><span>n=${fmt(k.companies_with_10k)}</span>` },
    { label: "Nasdaq symbols matched to SEC", badge: `<span class="pill pill-blue">unique symbols</span>`,
      value: fmt(k.nt_matched), small: `/ ${fmt(k.nt_symbols)}`,
      desc: `${fmt(k.nt_matched)} of ${fmt(k.nt_symbols)} Nasdaq Trader symbols matched to an SEC ticker: ` +
            `${fmt(k.nt_matched_same_exchange)} on the same exchange, ${fmt(k.nt_matched_exchange_differs)} where the exchange differs.`,
      foot: `<span>${fmt(k.tickers_total)} tickers reconciled in total</span><a href="#reconciliation">Explore →</a>` },
    { label: "Needs investigation", badge: "",
      value: fmt(k.needs_investigation), bad: true, desc: reasons,
      foot: `<span>${invFiles}</span><a href="#workbench">Review →</a>` },
    { label: "Check (b) failures", badge: `<span class="pill pill-red">${fmt(k.check_b_fail_spac)} SPACs</span>`,
      value: fmt(k.check_b_fail), small: `/ ${fmt(k.check_b_tested)}`, bad: true,
      desc: `L + SE ≠ total L&E after allowlisted explanations. ${pct(k.check_b_fail_spac, k.check_b_fail)} are SPACs.`,
      foot: `<span>fs_checks</span><a href="#rules">Rules →</a>` },
    { label: "Steward queue", badge: `<span class="pill pill-red">${fmt(k.review_open)} open</span>`,
      value: fmt(k.review_open), bad: true, desc: `${reviews}.`,
      foot: `<span>${fmt(k.review_with_verdict)} with a verdict</span><a href="#workbench">Triage now →</a>` },
  ];
  $("kpis").innerHTML = tiles.map((t) => `
    <div class="kpi">
      <div class="kpi-top"><span class="kpi-label">${t.label}</span>${t.badge}</div>
      <div class="kpi-value ${t.bad ? "bad" : ""}">${t.value}${t.small ? `<small>${t.small}</small>` : ""}</div>
      <div class="kpi-desc">${t.desc}</div>
      <div class="kpi-foot">${t.foot}</div>
    </div>`).join("");
}

// ---------- data-quality dimensions + donut ----------
function renderQuality(d) {
  $("dimensions").innerHTML = d.dimensions.map((x) => {
    const defects = x.tested - x.passed;
    const share = x.tested ? (100 * x.passed) / x.tested : 0;
    return `
      <div>
        <div class="dim-top">
          <div class="dim-name">${esc(x.name)} <span>— ${esc(x.rule)}</span></div>
          <div class="dim-stat"><b class="${defects ? "bad" : "good"}">${pct(x.passed, x.tested)} pass</b> · ${fmt(defects)} defects of ${fmt(x.tested)}</div>
        </div>
        <div class="bar" role="img" aria-label="${esc(x.name)} ${pct(x.passed, x.tested)} pass"><div class="bar-fill" style="width:${share}%"></div></div>
        <div class="dim-source">${esc(x.source)}</div>
      </div>`;
  }).join("");

  const total = d.check_b.reduce((s, x) => s + x.count, 0);
  const colors = { pass: COLORS.c1, explained: COLORS.c2, fail: COLORS.c3, not_applicable: COLORS.na };
  let start = 0;
  const stops = d.check_b.map((x) => {
    const end = start + (360 * x.count) / total;
    const stop = `${colors[x.label]} ${start}deg ${end}deg`;
    start = end;
    return stop;
  });
  const k = d.kpis;
  $("donut-sub").textContent = `Check (b) outcome for all ${fmt(total)} 10-K filings, ${k.quarter}.`;
  $("donut").innerHTML = `
    <div class="donut" style="background:conic-gradient(${stops.join(",")})" role="img" aria-label="Check (b) results">
      <div class="donut-center"><b>${fmt(total)}</b><span>10-K filings</span></div>
    </div>
    <ul class="legend">${d.check_b.map((x) => `
      <li><span class="swatch" style="background:${colors[x.label]}"></span>${esc(x.label.replace("_", " "))}
      <b>${fmt(x.count)} · ${pct(x.count, total)}</b></li>`).join("")}</ul>`;
  $("side-stats").innerHTML = [
    ["Check (a): Assets = total L&E", `${fmt(k.check_a_fail)} failures / ${fmt(k.check_a_tested)}`, k.check_a_fail > 0],
    ["Check (b): L + SE = total L&E", `${fmt(k.check_b_fail)} failures / ${fmt(k.check_b_tested)}`, k.check_b_fail > 0],
    ["Revenue line coverage", `${pct(k.revenue_with_value, k.filings_10k)} (${fmt(k.revenue_with_value)})`, false],
    ["SPAC share of check (b) failures", `${fmt(k.check_b_fail_spac)} (${pct(k.check_b_fail_spac, k.check_b_fail)})`, false],
    ["10-K filers with no listed ticker", fmt(k.unlisted_10k_filers), false],
  ].map(([l, v, bad]) => `<li>${esc(l)}<b class="${bad ? "bad" : ""}">${v}</b></li>`).join("");
}

// ---------- industry breakdown ----------
function stackRows(rows, cols, colors) {
  return `<div class="stack-rows">${rows.map((r) => {
    const n = cols.reduce((s, c) => s + r[c], 0);
    const segs = cols.map((c, i) => {
      const w = n ? (100 * r[c]) / n : 0;
      return w > 0 ? `<div style="width:${w}%;background:${colors[i]}" title="${esc(c)}: ${fmt(r[c])} (${w.toFixed(1)}%)">${w >= 9 ? Math.round(w) + "%" : ""}</div>` : "";
    }).join("");
    return `<div><div class="stack-label"><b>${esc(r.group)}</b><span>n=${fmt(n)}</span></div><div class="stack">${segs}</div></div>`;
  }).join("")}</div>
  <div class="legend-row">${cols.map((c, i) => `<span><i class="swatch" style="background:${colors[i]}"></i>${esc(c)}</span>`).join("")}</div>`;
}

function renderIndustry(d) {
  $("ind-revenue").innerHTML = stackRows(d.industry.revenue,
    ["has revenue value", "no revenue - industry not comparable", "no revenue - other"], [COLORS.c1, COLORS.c2, COLORS.c3]);
  $("ind-checkb").innerHTML = stackRows(d.industry.check_b, ["pass", "explained", "fail"], [COLORS.c1, COLORS.c2, COLORS.c3]);
  $("ind-exchange").innerHTML = d.by_exchange.map((x) => {
    const a = x.companies ? (100 * x.pass) / x.companies : 0;
    const f = x.fully_checked ? (100 * x.pass_fully_checked) / x.fully_checked : 0;
    return `<div class="ex-block"><div class="ex-name">${esc(x.exchange)}</div><div class="pair">
      <div class="pair-row"><span>all (n=${fmt(x.companies)})</span><div class="track"><div style="width:${a}%;background:${COLORS.c1}"></div></div><b>${pct(x.pass, x.companies)}</b></div>
      <div class="pair-row"><span>fully checked (n=${fmt(x.fully_checked)})</span><div class="track"><div style="width:${f}%;background:${COLORS.c3}"></div></div><b>${pct(x.pass_fully_checked, x.fully_checked)}</b></div>
    </div></div>`;
  }).join("") + `<div class="legend-row"><span><i class="swatch" style="background:${COLORS.c1}"></i>all companies with a 10-K</span><span><i class="swatch" style="background:${COLORS.c3}"></i>fully checked only (a and b ran)</span></div>`;
}

// ---------- flagged issuers ----------
const FLAG_COLS = [
  { key: "tickers", label: "Ticker(s)" }, { key: "name", label: "Issuer legal entity" },
  { key: "exchange_group", label: "Exchange" }, { key: "sic_group", label: "Industry" },
  { key: "Assets", label: "Total assets", num: true }, { key: "revenue_value", label: "Revenue", num: true },
  { key: "check_b_gap", label: "Check (b) gap", num: true }, { key: "flags", label: "Flags" },
];

function renderFlagged() {
  const d = state.d;
  const exchanges = ["all", ...new Set(d.flagged.map((r) => r.exchange_group))];
  $("exchange-chips").innerHTML = exchanges.map((e) =>
    `<button class="chip" data-ex="${esc(e)}" aria-pressed="${state.flagExchange === e}">${esc(e === "all" ? "All" : e)}</button>`).join("");

  let rows = d.flagged.filter((r) => state.flagExchange === "all" || r.exchange_group === state.flagExchange);
  const { key, dir } = state.flagSort;
  rows = [...rows].sort((a, b) => {
    const x = a[key], y = b[key];
    if (x === null) return 1;
    if (y === null) return -1;
    return (typeof x === "number" ? x - y : String(x).localeCompare(String(y))) * dir;
  });
  const shown = state.flagShowAll ? rows : rows.slice(0, 10);
  const combined = shown.reduce((s, r) => s + (r.Assets || 0), 0);
  $("flag-combined").textContent = `${billions(combined)} combined assets shown`;

  $("flag-table").innerHTML = `<thead><tr>${FLAG_COLS.map((c) =>
    `<th class="sortable ${c.num ? "num" : ""}" data-key="${c.key}">${c.label}${key === c.key ? `<span class="arrow">${dir > 0 ? "▲" : "▼"}</span>` : ""}</th>`).join("")}</tr></thead>
    <tbody>${shown.map((r) => `
      <tr class="clickable" data-adsh="${esc(r.adsh)}">
        <td class="tick">${esc(r.tickers)}</td><td>${esc(r.name)}</td><td>${esc(r.exchange_group)}</td>
        <td class="dimmed">${esc(r.sic_group)}</td><td class="num">${billions(r.Assets)}</td>
        <td class="num">${r.revenue_value === null ? '<span class="dimmed">—</span>' : billions(r.revenue_value)}</td>
        <td class="num">${r.check_b_gap === null ? '<span class="dimmed">—</span>' : billions(r.check_b_gap)}</td>
        <td>${String(r.flags).split(", ").map((f) => `<span class="flag">${esc(f)}</span>`).join("")}</td>
      </tr>`).join("")}</tbody>`;
  $("flag-count").textContent = `Showing ${fmt(shown.length)} of ${fmt(rows.length)} flagged issuers`;
  $("flag-toggle").textContent = state.flagShowAll ? "Show top 10" : `Show all ${fmt(rows.length)}`;
  $("flag-toggle").hidden = rows.length <= 10;
}

function openDrawer(adsh) {
  const r = state.d.flagged.find((x) => x.adsh === adsh);
  if (!r) return;
  const kv = (pairs) => `<div class="kv">${pairs.map(([k, v]) =>
    `<div>${esc(k)}</div><div class="${v === null || v === undefined ? "na" : ""}">${v === null || v === undefined ? "not reported" : esc(v)}</div>`).join("")}</div>`;
  const m = (v) => (v === null || v === undefined ? null : money(v));
  $("drawer-eyebrow").textContent = `${r.tickers} · ${r.exchange_group} · ${r.sic_group}`;
  $("drawer-title").textContent = r.name;
  $("drawer-body").innerHTML = `
    <p>${String(r.flags).split(", ").map((f) => `<span class="flag">${esc(f)}</span>`).join("")}</p>
    <h3>Checks</h3>${kv([["Check (a)", r.check_a], ["Check (a) gap", m(r.check_a_gap)], ["Check (b)", r.check_b],
      ["Check (b) gap", m(r.check_b_gap)], ["Explained by", r.check_b_explained_by], ["Notes", r.info_notes || null]])}
    <h3>Balance sheet values used (USD)</h3>${kv([["Assets", m(r.Assets)], ["Liabilities", m(r.Liabilities)],
      ["StockholdersEquity", m(r.StockholdersEquity)], ["Liabilities and equity (LSE)", m(r.LSE)],
      ["MinorityInterest", m(r.MinorityInterest)], ["Equity incl. NCI", m(r.EquityInclNCI)],
      ["Temporary equity incl. NCI", m(r.TempEquityInclNCI)], ["Temporary equity (parent)", m(r.TempEquityParent)],
      ["Temporary equity excl. APIC", m(r.TempEquityExclAPIC)], ["Temporary equity redemption req.", m(r.TempEquityRedemptionReq)],
      ["Redeemable NCI", m(r.RedeemableNCI)], ["Redeemable NCI common", m(r.RedeemableNCICommon)],
      ["Redeemable NCI preferred", m(r.RedeemableNCIPreferred)], ["Liabilities (derived, not used in b)", m(r.liabilities_derived)]])}
    <h3>Revenue and income (USD)</h3>${kv([["Revenues", m(r.Revenues)], ["Contract revenue excl. tax", m(r.RevenueFromContracts)],
      ["Contract revenue incl. tax", m(r.RevenueInclTax)], ["Revenue used", m(r.revenue_value)],
      ["Revenue source tag", r.revenue_source_tag], ["NetIncomeLoss", m(r.NetIncomeLoss)]])}
    <h3>Filing</h3>${kv([["CIK", r.cik10], ["Accession (adsh)", r.adsh], ["Period", r.period]])}
    <a class="btn" target="_blank" rel="noopener" href="https://www.sec.gov/Archives/edgar/data/${Number(r.cik10)}/${esc(r.adsh.replaceAll("-", ""))}/">Open filing on SEC EDGAR ↗</a>`;
  $("drawer").classList.add("open");
  $("drawer").setAttribute("aria-hidden", "false");
  $("drawer-backdrop").hidden = false;
}

function closeDrawer() {
  $("drawer").classList.remove("open");
  $("drawer").setAttribute("aria-hidden", "true");
  $("drawer-backdrop").hidden = true;
}

// ---------- reconciliation ----------
function selectField(id, label, options, value) {
  return `<label class="field">${label}<select id="${id}"><option value="">All</option>${options.map((o) =>
    `<option ${o === value ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></label>`;
}

function renderReconFilters() {
  const t = state.tickers;
  const uniq = (k) => [...new Set(t.map((r) => r[k] ?? "(none)"))].sort();
  const f = state.recon;
  $("recon-filters").innerHTML =
    selectField("rf-group", "Review group", uniq("review_group"), f.group) +
    selectField("rf-category", "Category", uniq("category"), f.category) +
    selectField("rf-reason", "Reason", uniq("reason"), f.reason) +
    `<label class="field">ETFs<select id="rf-etf"><option value="all">All</option><option value="etf">ETF only</option><option value="no">Exclude ETFs</option></select></label>` +
    `<label class="field">Search<input id="rf-q" type="search" placeholder="Ticker or name" value="${esc(f.q)}"></label>`;
  $("rf-etf").value = f.etf;
  for (const [id, k] of [["rf-group", "group"], ["rf-category", "category"], ["rf-reason", "reason"], ["rf-etf", "etf"]]) {
    $(id).addEventListener("change", (e) => { state.recon[k] = e.target.value; renderRecon(); });
  }
  $("rf-q").addEventListener("input", (e) => { state.recon.q = e.target.value; $("global-search").value = e.target.value; renderRecon(); });
}

function renderRecon() {
  if (!state.tickers) return;
  const f = state.recon;
  const q = f.q.trim().toLowerCase();
  const rows = state.tickers.filter((r) =>
    (!f.group || r.review_group === f.group) &&
    (!f.category || r.category === f.category) &&
    (!f.reason || (r.reason ?? "(none)") === f.reason) &&
    (f.etf === "all" || (f.etf === "etf" ? r.is_etf === 1 : r.is_etf !== 1)) &&
    (!q || [r.ticker, r.sec_ticker_original, r.nt_ticker_original, r.sec_name, r.nt_name]
      .some((v) => v && String(v).toLowerCase().includes(q))));

  // category x reason matrix
  const cats = [...new Set(rows.map((r) => r.category))].sort();
  const reasons = [...new Set(rows.map((r) => r.reason ?? "(none)"))].sort();
  const count = {};
  rows.forEach((r) => { const k = r.category + "|" + (r.reason ?? "(none)"); count[k] = (count[k] || 0) + 1; });
  const colTotal = (re) => cats.reduce((s, c) => s + (count[c + "|" + re] || 0), 0);
  $("recon-matrix").innerHTML = `<thead><tr><th>Category \\ reason</th>${reasons.map((r) => `<th class="num">${esc(r)}</th>`).join("")}<th class="num">Total</th></tr></thead>
    <tbody>${cats.map((c) => {
      const cells = reasons.map((re) => count[c + "|" + re] || 0);
      return `<tr><td><b>${esc(c)}</b></td>${cells.map((n) => `<td class="num ${n ? "" : "zero"}">${fmt(n)}</td>`).join("")}<td class="num total">${fmt(cells.reduce((s, n) => s + n, 0))}</td></tr>`;
    }).join("")}
    <tr class="total"><td>Total</td>${reasons.map((re) => `<td class="num">${fmt(colTotal(re))}</td>`).join("")}<td class="num">${fmt(rows.length)}</td></tr></tbody>`;

  const LIMIT = 300;
  $("recon-title").textContent = `Tickers — showing ${fmt(Math.min(LIMIT, rows.length))} of ${fmt(rows.length)}`;
  $("recon-table").innerHTML = `<thead><tr><th>Ticker</th><th>SEC original</th><th>Nasdaq Trader original</th><th>Category</th><th>Reason</th>
    <th>SEC name</th><th>Nasdaq Trader name</th><th>SEC exch.</th><th>NT exch.</th><th>ETF</th><th class="num">Name score</th></tr></thead>
    <tbody>${rows.slice(0, LIMIT).map((r) => `<tr>
      <td class="tick">${esc(r.ticker)}</td><td class="mono">${esc(r.sec_ticker_original ?? "—")}</td><td class="mono">${esc(r.nt_ticker_original ?? "—")}</td>
      <td>${esc(r.category)}</td><td>${esc(r.reason ?? "—")}</td><td>${esc(r.sec_name ?? "—")}</td><td class="dimmed">${esc(r.nt_name ?? "—")}</td>
      <td>${esc(r.sec_exchange_original ?? "—")}</td><td>${esc(r.nt_exchange_detail ?? "—")}</td><td>${r.is_etf === 1 ? "ETF" : ""}</td>
      <td class="num">${r.name_score === null ? "—" : Number(r.name_score).toFixed(3)}</td></tr>`).join("")}</tbody>`;
}

// ---------- lineage, rules ----------
function renderLineage(d) {
  $("flow").innerHTML = d.lineage.map((x) => `
    <div class="flow-card stage-${esc(x.stage)}">
      <div class="flow-stage">${esc(x.stage)}</div>
      <div class="flow-title">${esc(x.title)}</div>
      <div class="flow-detail">${esc(x.detail)}</div>
      <div class="flow-rows">${fmt(x.rows)} <span>${esc(x.unit)}</span></div>
      <div class="flow-script">${esc(x.script)}</div>
    </div>`).join("");
  const ra = d.rules_applied;
  const li = (items) => `<ul>${items.map(([l, v]) => `<li><span title="${esc(l)}">${esc(l)}</span><b>${v}</b></li>`).join("")}</ul>`;
  $("rules-applied").innerHTML = `
    <div class="rule-card"><h4>Revenue source precedence <span class="pill pill-blue">${fmt(d.kpis.revenue_with_value)} with value</span></h4>
      <p>Revenues, then contract revenue excl. tax, then incl. tax.</p>${li(ra.revenue_sources.map((s) => [s.tag, fmt(s.count)]))}</div>
    <div class="rule-card"><h4>Exchange mapping <span class="pill pill-green">${pct(ra.exchange_agree, ra.exchange_tested)}</span></h4>
      <p>Nasdaq Trader N / A / P / M grouped as NYSE, Z as CBOE, F as TXSE.</p>${li([["tickers matched to SEC", fmt(ra.exchange_tested)], ["… same exchange (category matched)", fmt(ra.exchange_agree)], ["… exchange differs", fmt(ra.exchange_tested - ra.exchange_agree)]])}</div>
    <div class="rule-card"><h4>SPAC classification <span class="pill pill-amber">${fmt(ra.spac_either)} tagged</span></h4>
      <p>"Acquisition [I-X] Corp" in the name, or one CIK with unit + warrant/right tickers.</p>${li([["by name", fmt(ra.spac_by_name)], ["by structure", fmt(ra.spac_by_structure)], ["either", fmt(ra.spac_either)]])}</div>
    <div class="rule-card"><h4>Check (b) explanation allowlist <span class="pill pill-blue">${fmt(ra.explained_total)} explained</span></h4>
      <p>Only NCI, temporary-equity and redeemable-NCI tags; at most one per kind.</p>${li(ra.explained_top.map((x) => [x.by, fmt(x.count)]))}</div>`;
  $("lineage-table").innerHTML = `<thead><tr><th>Artifact</th><th>Kind</th><th class="num">Rows</th></tr></thead><tbody>${d.lineage_table.map((x) =>
    `<tr><td class="mono">${esc(x.artifact)}</td><td>${esc(x.kind)}</td><td class="num">${fmt(x.rows)}</td></tr>`).join("")}</tbody>`;
}

function renderRules(d) {
  $("rules-table").innerHTML = `<thead><tr><th>Rule ID</th><th>Rule expression &amp; scope</th><th>Dimension</th><th class="num">Tested entities</th><th class="num">Failures</th><th class="num">Pass rate</th><th>Status</th></tr></thead>
    <tbody>${d.rules.map((r) => `<tr>
      <td class="tick">${esc(r.id)}</td>
      <td><b>${esc(r.rule)}</b><div class="dimmed">${esc(r.expression)} · <span class="mono">${esc(r.scope)}</span></div></td>
      <td>${esc(r.dimension)}</td><td class="num">${fmt(r.tested)}</td>
      <td class="num">${r.failures ? `<b style="color:var(--bad)">${fmt(r.failures)}</b>` : fmt(r.failures)}</td>
      <td class="num">${pct(r.tested - r.failures, r.tested)}</td>
      <td>${r.failures ? '<span class="status bad">⚠ failures found</span>' : '<span class="status ok">✓ no failures</span>'}</td></tr>`).join("")}</tbody>`;
}

// ---------- exception workbench ----------
function currentReview() { return state.review.find((r) => r.file === state.wb.file); }

function renderWbFilters() {
  const files = state.review.map((r) => r.file);
  if (!state.wb.file) state.wb.file = files[0];
  const rev = currentReview();
  const statuses = [...new Set(rev.rows.map((r) => r.status))].sort();
  $("wb-filters").innerHTML =
    `<div class="chips">${files.map((f) => `<button class="chip" data-file="${esc(f)}" aria-pressed="${f === state.wb.file}">${esc(f)}</button>`).join("")}</div>` +
    selectField("wb-status", "Status", statuses, state.wb.status) +
    `<label class="field">Verdict<select id="wb-verdict"><option value="all">All</option><option value="none">No verdict yet</option><option value="has">Has verdict</option></select></label>` +
    `<label class="field">Search<input id="wb-q" type="search" placeholder="Key or name" value="${esc(state.wb.q)}"></label>`;
  $("wb-verdict").value = state.wb.verdict;
  $("wb-status").addEventListener("change", (e) => { state.wb.status = e.target.value; renderWb(); });
  $("wb-verdict").addEventListener("change", (e) => { state.wb.verdict = e.target.value; renderWb(); });
  $("wb-q").addEventListener("input", (e) => { state.wb.q = e.target.value; renderWb(); });
  document.querySelectorAll("#wb-filters [data-file]").forEach((b) => b.addEventListener("click", () => {
    state.wb.file = b.dataset.file; state.wb.status = ""; renderWbFilters(); renderWb();
  }));
}

function renderWb() {
  const rev = currentReview();
  const key = rev.key;
  const edits = state.edits[rev.file] || {};
  const q = state.wb.q.trim().toLowerCase();
  const rows = rev.rows.filter((r) => {
    const verdict = (edits[r[key]]?.my_verdict ?? r.my_verdict);
    return (!state.wb.status || r.status === state.wb.status) &&
      (state.wb.verdict === "all" || (state.wb.verdict === "none" ? verdict === "" : verdict !== "")) &&
      (!q || Object.values(r).some((v) => String(v).toLowerCase().includes(q)));
  });
  const done = rev.rows.filter((r) => (edits[r[key]]?.my_verdict ?? r.my_verdict) !== "").length;
  $("wb-progress").style.width = `${rev.rows.length ? (100 * done) / rev.rows.length : 0}%`;
  $("wb-progress-text").textContent = `${fmt(done)} of ${fmt(rev.rows.length)} rows in ${rev.file} have a verdict`;
  const open = rev.rows.filter((r) => r.status !== "no_longer_needs_review").length;
  $("wb-open").textContent = `${fmt(open)} open in this file`;

  const cols = Object.keys(rev.rows[0] || {}).filter((c) => !["my_verdict", "my_note"].includes(c));
  const numeric = new Set(["Liabilities", "StockholdersEquity", "LSE", "check_b_gap", "gap_pct_of_lse"]);
  $("wb-table").innerHTML = `<thead><tr>${cols.map((c) => `<th class="${numeric.has(c) ? "num" : ""}">${esc(c)}</th>`).join("")}<th>my_verdict</th><th>my_note</th></tr></thead>
    <tbody>${rows.map((r) => {
      const e = edits[r[key]] || {};
      const v = e.my_verdict ?? r.my_verdict, n = e.my_note ?? r.my_note;
      return `<tr>${cols.map((c) => {
        const val = r[c];
        if (numeric.has(c) && val !== "") return `<td class="num">${c === "gap_pct_of_lse" ? esc(val) + "%" : money(Number(val))}</td>`;
        return `<td class="${c === key ? "tick" : ""}">${esc(val)}</td>`;
      }).join("")}
      <td><input type="text" data-key="${esc(r[key])}" data-col="my_verdict" value="${esc(v)}" class="${e.my_verdict !== undefined ? "dirty" : ""}"></td>
      <td><input type="text" data-key="${esc(r[key])}" data-col="my_note" value="${esc(n)}" class="${e.my_note !== undefined ? "dirty" : ""}"></td></tr>`;
    }).join("")}</tbody>`;
  $("wb-count").textContent = `Showing ${fmt(rows.length)} of ${fmt(rev.rows.length)} rows`;
  updateDirty();
}

function updateDirty() {
  const n = Object.values(state.edits).reduce((s, f) => s + Object.keys(f).length, 0);
  $("wb-dirty").textContent = n ? `${fmt(n)} unsaved change(s)` : "No unsaved changes";
  $("wb-save").disabled = n === 0;
}

function onWbInput(e) {
  const input = e.target;
  if (!input.dataset.col) return;
  const rev = currentReview();
  const original = rev.rows.find((r) => r[rev.key] === input.dataset.key);
  const fileEdits = (state.edits[rev.file] ||= {});
  const rowEdits = (fileEdits[input.dataset.key] ||= {});
  if (input.value === original[input.dataset.col]) delete rowEdits[input.dataset.col];
  else rowEdits[input.dataset.col] = input.value;
  if (!Object.keys(rowEdits).length) delete fileEdits[input.dataset.key];
  input.classList.toggle("dirty", input.dataset.col in rowEdits);
  updateDirty();
}

async function saveVerdicts() {
  $("wb-save").disabled = true;
  try {
    let saved = 0;
    for (const [file, fileEdits] of Object.entries(state.edits)) {
      const rev = state.review.find((r) => r.file === file);
      const edits = Object.entries(fileEdits).map(([key, e]) => {
        const row = rev.rows.find((r) => r[rev.key] === key);
        return { key, my_verdict: e.my_verdict ?? row.my_verdict, my_note: e.my_note ?? row.my_note };
      });
      if (!edits.length) continue;
      const res = await getJSON("/api/verdicts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ file, edits }) });
      saved += res.saved;
    }
    state.edits = {};
    await reload();
    toast(`Saved ${saved} row(s) to output/`);
  } catch (err) {
    toast(err.message, true);
    updateDirty();
  }
}

// ---------- navigation highlight ----------
function trackSections() {
  const links = [...document.querySelectorAll(".nav a")];
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((en) => {
      if (en.isIntersecting) links.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === "#" + en.target.id));
    });
  }, { rootMargin: "-80px 0px -60% 0px" });
  document.querySelectorAll("main section[id]").forEach((s) => observer.observe(s));
}

// ---------- load ----------
async function reload() {
  const [d, review] = await Promise.all([getJSON("/api/dashboard"), getJSON("/api/review")]);
  state.d = d;
  state.review = review;
  renderChrome(d); renderKpis(d); renderQuality(d); renderIndustry(d); renderFlagged();
  renderLineage(d); renderRules(d);
  if (review.length) { renderWbFilters(); renderWb(); }
}

async function init() {
  try {
    await reload();
  } catch (err) {
    const noServer = err.message === "NO_SERVER" || err instanceof TypeError;
    document.querySelector(".content").innerHTML = noServer
      ? `<div class="card loading"><h2>The dashboard server is not running here</h2>
         <p>This page gets its numbers from <code>app/server.py</code>. In a terminal in the project folder run
         <code>python app/server.py</code>, then open <a href="http://localhost:8000">http://localhost:8000</a>.</p>
         <p class="sub">VS Code Live Server and double-clicking the file cannot load the data.</p></div>`
      : `<div class="card loading">Could not load data: ${esc(err.message)}</div>`;
    return;
  }
  // events
  $("exchange-chips").addEventListener("click", (e) => { const b = e.target.closest("[data-ex]"); if (b) { state.flagExchange = b.dataset.ex; renderFlagged(); } });
  $("flag-table").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-key]");
    if (th) { const k = th.dataset.key; state.flagSort = { key: k, dir: state.flagSort.key === k ? -state.flagSort.dir : -1 }; renderFlagged(); return; }
    const tr = e.target.closest("tr[data-adsh]");
    if (tr) openDrawer(tr.dataset.adsh);
  });
  $("flag-toggle").addEventListener("click", () => { state.flagShowAll = !state.flagShowAll; renderFlagged(); });
  $("drawer-close").addEventListener("click", closeDrawer);
  $("drawer-backdrop").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  $("wb-table").addEventListener("input", onWbInput);
  $("wb-save").addEventListener("click", saveVerdicts);
  window.addEventListener("beforeunload", (e) => { if (Object.keys(state.edits).length) e.preventDefault(); });
  $("global-search").addEventListener("input", (e) => {
    state.recon.q = e.target.value;
    if ($("rf-q")) $("rf-q").value = e.target.value;
    renderRecon();
  });
  $("global-search").addEventListener("keydown", (e) => { if (e.key === "Enter") $("reconciliation").scrollIntoView(); });
  trackSections();

  // tickers are larger: load after the page is drawn
  $("recon-table").innerHTML = '<tbody><tr><td class="loading">Loading tickers…</td></tr></tbody>';
  state.tickers = await getJSON("/api/tickers");
  renderReconFilters();
  renderRecon();
}

init();
