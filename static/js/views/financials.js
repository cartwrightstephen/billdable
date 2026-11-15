// Money > Financials (Stephen, Nov 13): the company's picture as a bank asks for it, from
// /api/financials (financials.py), with a CSV and a printable copy. And the money strip, the one
// line of the company's main figures at the top of every page. Both need "See company financials".
import { escDeep, money, fmtDate } from "../format.js";
import { readJson } from "../net.js";
import { state, currentView, onSessionEnd } from "../state.js";
import { render, can } from "../app-core.js";
import { pathFor } from "../routes.js";

let finData = null;     // the escaped answer from /api/financials
let finFor = null;      // the page load it was asked for: a new one (a save, a payment) asks again
let finAsking = false;
let finFailed = false;
onSessionEnd(() => { finData = null; finFor = null; finFailed = false; });

export async function loadFinancials() {
  if (finAsking || (finFor === state && (finData || finFailed))) return;
  finAsking = true;
  const asked = state;
  try {
    const res = await fetch("/api/financials");
    const data = await readJson(res);
    if (!res.ok) throw new Error(data.error || "Couldn't load the financials.");
    finData = escDeep(data);
    finFailed = false;
  } catch (_) {
    finFailed = true;
  } finally {
    finFor = asked;
    finAsking = false;
  }
  if (currentView === "financials") render();
}

// Whole dollars for the strip and the tiles; the tables keep the cents.
const dollars = (cents) => money(Math.round((Number(cents) || 0) / 100) * 100).replace(/\.00$/, "");

// ---------- the money strip ----------
export function moneyStripHtml() {
  // Not on the Overview, whose tiles show the same figures just below.
  if (!state || !can("financials") || currentView === "dashboard") return "";
  if (!(state.jobs || []).length && !state.closed_jobs_total) return "";
  const t = state.totals || {};
  const item = (label, cents, view, cls = "") =>
    `<a class="strip-item${cls}" href="${pathFor(view)}" data-action="nav" data-view="${view}"><span class="strip-k">${label}</span> <span class="strip-v">${dollars(cents)}</span></a>`;
  return `<nav class="money-strip" aria-label="Company money">
    ${item("Owed to you", t.outstanding_cents, "collections")}
    ${item("Done, not billed", t.unbilled_cents, "jobs", t.unbilled_cents > 0 ? " strip-hot" : "")}
    ${item("Retainage held", t.retainage_cents, "retainage")}
    ${item("Backlog", t.backlog_cents, "financials")}
  </nav>`;
}

// ---------- the Financials page ----------
const pct = (v) => v === null || v === undefined ? "—" : `${v}%`;
const days = (v) => v === null || v === undefined ? "—" : `${v} day${v === 1 ? "" : "s"}`;
const monthName = (key) => {
  const [y, m] = String(key).split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: "short", year: "2-digit" });
};

function tile(label, cents, sub = "", cls = "") {
  return `<div class="metric${cls}"><div class="k">${label}</div><div class="v">${dollars(cents)}</div>${sub ? `<div class="sub row-sub">${sub}</div>` : ""}</div>`;
}

// Billed and collected by month, as bars drawn with SVG attributes (the CSP allows no inline styles).
function monthlyChart(rows) {
  const top = Math.max(1, ...rows.map(r => Math.max(r.billed_cents, r.collected_cents)));
  const W = 720, H = 180, pad = 22, slot = (W - 8) / rows.length, bw = Math.max(4, slot / 2 - 5);
  const h = (c) => Math.round((H - pad - 8) * c / top);
  const bars = rows.map((r, i) => {
    const x = 4 + i * slot;
    return `<g><title>${monthName(r.month)}: billed ${money(r.billed_cents)}, collected ${money(r.collected_cents)}</title>
      <rect class="bar-billed" x="${x + 2}" y="${H - pad - h(r.billed_cents)}" width="${bw}" height="${h(r.billed_cents)}" rx="2"></rect>
      <rect class="bar-collected" x="${x + 4 + bw}" y="${H - pad - h(r.collected_cents)}" width="${bw}" height="${h(r.collected_cents)}" rx="2"></rect>
      <text class="bar-label" x="${x + slot / 2}" y="${H - 6}" text-anchor="middle">${monthName(r.month)}</text></g>`;
  }).join("");
  return `<svg class="fin-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Billed and collected by month, last 12 months">
      <line class="bar-axis" x1="0" x2="${W}" y1="${H - pad}" y2="${H - pad}"></line>${bars}</svg>
    <p class="fin-legend"><span class="key key-billed"></span> Billed <span class="key key-collected"></span> Collected</p>`;
}

function reportHtml(f) {
  const agingTotal = f.receivable_cents || 0;
  const share = (c) => agingTotal ? `${Math.round(100 * c / agingTotal)}%` : "—";
  const over60 = f.aging.filter(a => a.key === "61_90" || a.key === "over_90").reduce((s, a) => s + a.cents, 0);
  return `
    <p class="sub">${f.company} · as of ${fmtDate(f.as_of)}</p>
    <div class="metrics fin-tiles">
      ${tile("Owed by clients", f.receivable_cents, over60 ? `${dollars(over60)} over 60 days` : "none over 60 days")}
      ${tile("Retainage held", f.retainage_cents, "held back, still owed")}
      ${tile("Backlog", f.backlog_cents, `signed work not done yet · ${f.open_jobs} open job${f.open_jobs === 1 ? "" : "s"}`)}
      ${tile("Done, not billed", f.unbilled_cents, "work done, no pay app yet", f.unbilled_cents > 0 ? " highlight" : "")}
    </div>
    <div class="metrics fin-tiles">
      ${tile("Billed, last 12 months", f.billed_12m_cents, "before retainage")}
      ${tile("Collected, last 12 months", f.collected_12m_cents)}
      <div class="metric"><div class="k">Days to get paid</div><div class="v">${f.paid_median_days ?? "—"}</div><div class="sub row-sub">median, sent to paid · ${f.paid_count} paid in 12 months</div></div>
      <div class="metric"><div class="k">Largest client's share</div><div class="v">${pct(f.top_client_share)}</div><div class="sub row-sub">of what is owed, retainage included</div></div>
    </div>

    <section class="card">
      <h2>Billed and collected by month</h2>
      ${monthlyChart(f.monthly)}
    </section>

    <section class="card">
      <h2>Receivables aging</h2>
      <div class="table-wrap"><table>
        <tr><th>Age</th><th class="num">Owed</th><th class="num">Share</th></tr>
        ${f.aging.map(a => `<tr><td>${a.label}</td><td class="num">${money(a.cents)}</td><td class="num">${share(a.cents)}</td></tr>`).join("")}
        <tr class="total"><td>Total</td><td class="num">${money(agingTotal)}</td><td class="num"></td></tr>
      </table></div>
      ${f.aging_rows.length ? `<details class="fin-more"><summary>By pay app</summary><div class="table-wrap"><table>
        <tr><th>Job</th><th>Client</th><th>Pay app</th><th class="num">Days since sent</th><th class="num">Owed</th></tr>
        ${f.aging_rows.map(r => `<tr><td><a href="${pathFor("job", r.job_id)}" data-action="open-job" data-id="${r.job_id}">${r.job}</a></td><td>${r.client}</td><td>${r.pay_app}</td><td class="num">${r.days}</td><td class="num">${money(r.cents)}</td></tr>`).join("")}
      </table></div></details>` : ""}
    </section>

    <section class="card">
      <h2>Clients</h2>
      ${f.clients.length ? `<div class="table-wrap"><table>
        <tr><th>Client</th><th class="num">Owed</th><th class="num">Retainage held</th><th class="num">Share of owed</th><th class="num">Billed, 12 months</th><th class="num">Share of billed</th></tr>
        ${f.clients.map(r => `<tr><td>${r.client}</td><td class="num">${money(r.owed_cents)}</td><td class="num">${money(r.retainage_cents)}</td><td class="num">${pct(r.owed_share)}</td><td class="num">${money(r.billed_12m_cents)}</td><td class="num">${pct(r.billed_share)}</td></tr>`).join("")}
      </table></div>` : `<p class="sub">No billing yet.</p>`}
    </section>

    <section class="card">
      <h2>Backlog by job</h2>
      ${f.backlog_jobs.length ? `<div class="table-wrap"><table>
        <tr><th>Job</th><th>Client</th><th class="num">Contract</th><th class="num">Billed</th><th class="num">Done, not billed</th><th class="num">Not done yet</th></tr>
        ${f.backlog_jobs.map(r => `<tr><td><a href="${pathFor("job", r.job_id)}" data-action="open-job" data-id="${r.job_id}">${r.job}</a></td><td>${r.client}</td><td class="num">${money(r.contract_cents)}</td><td class="num">${money(r.billed_cents)}</td><td class="num">${money(r.unbilled_cents)}</td><td class="num">${money(r.left_cents)}</td></tr>`).join("")}
        <tr class="total"><td>Total</td><td></td>${["contract_cents", "billed_cents", "unbilled_cents", "left_cents"].map(k => `<td class="num">${money(f.backlog_jobs.reduce((t, r) => t + r[k], 0))}</td>`).join("")}</tr>
      </table></div>` : `<p class="sub">No signed work waiting.</p>`}
      <dl class="fin-facts">
        <dt>Contract value of open jobs</dt><dd>${money(f.open_contract_cents)}</dd>
        <dt>Change orders waiting for signature</dt><dd>${money(f.pending_co_cents)}</dd>
        <dt>Billed ahead of the contract</dt><dd>${money(f.overbilled_cents)}</dd>
        <dt>Jobs completed, last 12 months</dt><dd>${f.completed_jobs_12m}</dd>
      </dl>
    </section>`;
}

export const views = {
  financials() {
    if (!can("financials")) {
      return `<h1>Financials</h1><p class="notice">Ask an owner for "See company financials" to open this page.</p>`;
    }
    const f = finData;   // the last figures stay up while new ones load
    return `
      <div class="page-head"><h1>Financials</h1>
        <div class="head-actions">
          <a class="btn secondary small" href="/api/financials.csv" download>Download CSV</a>
          <button class="btn secondary small" data-action="fin-print"${f ? "" : " disabled"}>Print or save as PDF</button>
        </div>
      </div>
      ${f ? `<div id="fin-report">${reportHtml(f)}</div>` : finFailed ? `<p class="notice">Couldn't load the financials. <button class="btn small secondary" data-action="fin-retry">Try again</button></p>` : `<p class="sub">Loading…</p>`}`;
  },
};

export const actions = {
  // On paper: the report alone, on the print sheet the Outbox uses (styles.css @media print).
  "fin-print": () => {
    const report = document.getElementById("fin-report");
    if (!finData || !report) return;
    let sheet = document.getElementById("print-sheet");
    if (!sheet) {
      sheet = document.createElement("div");
      sheet.id = "print-sheet";
      sheet.className = "print-sheet";
      document.body.appendChild(sheet);
    }
    sheet.innerHTML = `<h1>${finData.company}: financial summary</h1>${report.innerHTML}`;
    sheet.querySelectorAll("details").forEach(d => { d.open = true; });
    document.body.classList.add("printing");
    window.addEventListener("afterprint", () => document.body.classList.remove("printing"), { once: true });
    if (typeof window.print === "function") window.print();
  },
  "fin-retry": () => { finFailed = false; finFor = null; loadFinancials(); render(); },
};
