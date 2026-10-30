// Money, Payments: payments received, the QuickBooks import (CSV or the connection) and the
// QuickBooks card (also on Settings, Company).
import { esc, unesc, escDeep, money, fmtDate, words } from "../format.js";
import { pathFor } from "../routes.js";
import { readJson, setNetBanner, postJson } from "../net.js";
import { askConfirm } from "../dom.js";
import { field, draftValue, clearForm } from "../forms.js";
import { state, me, currentView, onSessionEnd } from "../state.js";
import { render, toast, emptyState, nav, setState } from "../app-core.js";
import { canRecord, needsPick, splitTooMuch, picksFrom, afterRecord, refreshOwed } from "../payimport.js";
import { qboCsvLink } from "./money.js";

export let paymentImport = null;  // {rows, warnings} proposed from a QuickBooks CSV or the QuickBooks connection
export let qboStatus = null;      // /api/qbo/status: configured, connected, environment (escaped)
let qboReconcile = null;   // /api/qbo/reconcile: {rows, invoices}
const RECON_WORDS = { missing: "Not in QuickBooks", amount: "Amount differs", paid_in_qbo: "Paid in QuickBooks", open_in_qbo: "Open in QuickBooks" };

// Signed out: the QuickBooks connection and the payments being imported are that company's.
onSessionEnd(() => { paymentImport = null; qboStatus = null; qboReconcile = null; });

export async function loadQboStatus() {
  qboStatus = { loading: true };
  const asked = me;
  try {
    const res = await fetch("/api/qbo/status");
    const data = res.ok ? await readJson(res) : null;
    if (me !== asked) return;   // signed out while this was asked: the last company's connection
    if (!data) { qboStatus = null; return; }
    qboStatus = escDeep(data);
  } catch (_) { if (me === asked) qboStatus = null; setNetBanner(true); return; }
  if (["payments", "company"].includes(currentView)) render();
}

// A payments export chosen on Payments: a CSV is read here, an Excel file on the server (R5-04).
const MAX_PAYMENTS_XLSX = 700 * 1024;   // base64 of it has to fit in a 1 MB request
async function readPaymentsFile(file) {
  const xlsx = /\.xlsx$/i.test(file.name);
  if (xlsx && file.size > MAX_PAYMENTS_XLSX) { toast("That Excel file is too large. In QuickBooks, export the report to CSV instead."); return; }
  let body;
  if (xlsx) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let bin = "";
    for (let k = 0; k < bytes.length; k += 0x8000) bin += String.fromCharCode(...bytes.subarray(k, k + 0x8000));
    body = { xlsx: btoa(bin) };
  } else {
    body = { csv: await file.text() };
  }
  const r = await postJson("/api/import/payments-csv", body);
  if (!r.ok) { toast(r.data.error || "Couldn't read it."); return; }
  paymentImport = escDeep(r.data); render();
}

// The Payments import's tick box: on for a match, off until a pay app is picked (R5-03).
function qboTick(row, n) {
  const label = `Record ${row.ref || "this payment"}`;
  if (canRecord(row)) return `<input type="checkbox" data-qbo-row="${n}"${row.ticked === false ? "" : " checked"} aria-label="${label}">`;
  return needsPick(row) ? `<input type="checkbox" data-qbo-row="${n}" disabled aria-label="${label}">` : "";
}

// The Payments import's pay-app cell: the matched pay app, a select of open pay apps for a row
// that needs one, or "Record $owed to PA-X" for a payment larger than its pay app (R5-03).
function qboPayAppCell(row, n) {
  const pill = row.status === "not recorded" ? `<span class="pill red">Not recorded</span>` : `<span class="pill">${words(row.status)}</span>`;
  if (needsPick(row)) {
    return `${pill}<select class="qbo-pick" data-qbo-pick="${n}" aria-label="Pay app for ${row.ref || "this payment"}">
      <option value="">Pick a pay app…</option>
      ${row.candidates.map((c, k) => `<option value="${k}"${row.picked === String(k) ? " selected" : ""}>${c.number} · ${c.job} · ${money(c.owed_cents)} owed</option>`).join("")}
    </select>`;
  }
  if (row.status === "too much" && row.split && row.split.owed_cents > 0) {
    return `${pill}<button class="btn small secondary" data-action="qbo-split" data-row="${n}">Record ${money(row.split.owed_cents)} to ${row.split.number}</button>`;
  }
  return row.number ? `${row.number} · ${row.job}` : pill;
}

// The QuickBooks card, on Payments and on Settings > Company (R4-11).
export function qboCardHtml(onPayments) {
  const q = qboStatus;
  const owner = me && me.role === "owner";
  return !q || q.loading ? "" : `<div class="card"><h2>QuickBooks connection</h2>${
      !q.configured ? `<p class="sub">Not set up on this server yet: it needs the keys of Duracash's Intuit app. Until then, use the CSV import ${onPayments ? "below" : "on Payments"}.</p>`
      : !q.connected ? (owner
        ? `<p class="sub">Read-only: Duracash reads payments and invoices from QuickBooks and never changes your books.</p><button class="btn" data-action="qbo-connect">Connect QuickBooks</button>`
        : `<p class="sub">An owner of this company can connect QuickBooks here.</p>`)
      : `<p class="sub"><span class="pill green">Connected</span> ${q.environment === "sandbox" ? "Sandbox company. " : ""}Since ${fmtDate(q.connected_at)}${q.connected_by ? ", by " + q.connected_by : ""}. Read-only.</p>
        <div class="form-inline"><button class="btn" data-action="qbo-sync">Read payments from QuickBooks</button><button class="btn secondary" data-action="qbo-reconcile">Check against QuickBooks</button>${owner ? `<button class="btn secondary" data-action="qbo-disconnect">Disconnect</button>` : ""}</div>
        ${qboReconcile ? (qboReconcile.rows.length
          ? `<h3>Where Duracash and QuickBooks differ</h3>${qboReconcile.rows.map(r => `<div class="list-item"><span class="pill">${RECON_WORDS[r.kind] || words(r.kind)}</span> <a href="${pathFor("job", r.job_id)}" data-action="open-job" data-id="${r.job_id}">${r.job}</a>: ${r.detail}${r.kind === "missing" && r.invoice_id ? " " + qboCsvLink(r.job_id, r.invoice_id) : ""}</div>`).join("")}`
          : `<p class="sub">Duracash and QuickBooks agree on every sent pay app (${qboReconcile.invoices} QuickBooks invoices checked).</p>`) : ""}`}
    </div>`;
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  payments() {
    const pays = [];
    state.jobs.forEach(j => j.payments.forEach(p => pays.push({ ...p, job: j.name, jobId: j.id })));
    const imp = paymentImport;
    const importCard = `<details class="card"${imp || !pays.length ? " open" : ""}><summary><strong>Import payments from QuickBooks (CSV)</strong></summary>
      <p class="sub">Paste a QuickBooks report with payments in it (CSV), or choose the file. Nothing is recorded until you tick and apply.</p>
      ${imp ? `${imp.warnings.length ? `<div class="sov-warn">${imp.warnings.map(w => `<div>${w}</div>`).join("")}</div>` : ""}
        <div class="table-wrap"><table>
          <tr><th>Record</th><th>Date</th><th>Client</th><th>Number</th><th>Amount</th><th>Pay app</th><th>Why</th></tr>
          ${imp.rows.map((row, n) => `<tr><td>${qboTick(row, n)}</td>
            <td>${row.date ? fmtDate(row.date) : "—"}</td><td>${row.customer}</td><td>${row.ref}</td><td class="num">${money(row.amount_cents)}</td>
            <td>${qboPayAppCell(row, n)}</td><td class="sub">${row.reason}</td></tr>`).join("")}
        </table></div>
        <div class="form-inline"><button class="btn" data-action="qbo-apply">Record the ticked payments</button><button class="btn secondary" data-action="qbo-cancel">Cancel</button></div>`
      : `<div data-form-key="qbocsv"><textarea id="qbo-csv" data-field="csv" rows="5" placeholder="Paste the CSV here" class="full-input">${esc(draftValue("qbocsv", "csv"))}</textarea></div>
        <div class="form-inline"><button class="btn small" data-action="qbo-read">Read payments</button><button class="btn small secondary" data-action="qbo-choose-file">Choose the file</button></div>`}
    </details>`;
    const qboCard = qboCardHtml(true);
    return `
      <h1>Payments</h1>
      ${qboCard}
      ${importCard}
      ${pays.length ? `<div class="table-wrap">
        <table>
          <tr><th>Job</th><th>Pay app</th><th>Amount</th><th>Reference</th><th>Received</th></tr>
          ${pays.slice().reverse().map(p => {
            const job = state.jobs.find(j => j.id === p.jobId);
            const inv = job && job.invoices.find(i => i.id === p.invoice_id);
            return `<tr>
              <td><a href="${pathFor("job", p.jobId)}" data-action="open-job" data-id="${p.jobId}">${p.job}</a></td>
              <td>${inv ? `<a href="${pathFor("payapp-detail", p.jobId, inv.id)}" data-action="open-payapp" data-job="${p.jobId}" data-inv="${inv.id}">${inv.number}</a>` : "—"}</td>
              <td class="num">${money(p.amount_cents)}</td>
              <td>${p.reference}</td>
              <td>${fmtDate(p.matched_at)}</td>
            </tr>`;
          }).join("")}
        </table>
      </div>` : emptyState("No payments yet")}
    `;
  },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  "qbo-read": async () => {
    const text = document.getElementById("qbo-csv")?.value || "";
    if (!text.trim()) { toast("Paste the CSV first."); return; }
    const r = await postJson("/api/import/payments-csv", { csv: text });
    if (!r.ok) { toast(r.data.error || "Couldn't read it."); return; }
    clearForm("qbocsv");   // read: Cancel shows an empty box again, as before
    paymentImport = escDeep(r.data); render();
  },
  "qbo-apply": async () => {
    const picks = picksFrom(paymentImport.rows, unesc);
    if (!picks.length) { toast("Tick the payments to record."); return; }
    const r = await postJson("/api/import/payments-apply", { payments: picks });
    if (!r.ok) { toast(r.data.error || "Nothing was recorded."); return; }
    // Recorded rows go; a refused row stays with its reason, and rows still to pick stay (R5-03).
    const after = afterRecord(paymentImport.rows, escDeep(r.data.import_results));
    setState(r.data);
    paymentImport = after.rows ? { ...paymentImport, rows: refreshOwed(after.rows, state.jobs), warnings: [] } : null;
    toast(after.toast);
    render();
  },
  "qbo-split": (el) => {
    paymentImport = { ...paymentImport, rows: splitTooMuch(paymentImport.rows, +el.dataset.row) };
    render();
  },
  "qbo-cancel": () => { paymentImport = null; render(); },
  // A payments export dropped in the Inbox, read on Payments (R5-04).
  "inbox-read-payments": async (el) => {
    const r = await postJson("/api/import/payments-csv", { file_id: el.dataset.file });
    if (!r.ok) { toast(r.data.error || "Couldn't read it."); return; }
    paymentImport = escDeep(r.data);
    nav("payments");
  },
  "qbo-choose-file": () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".csv,.xlsx,.txt";
    input.addEventListener("change", () => { if (input.files[0]) readPaymentsFile(input.files[0]); });
    input.click();
  },
  "qbo-connect": async () => {
    const r = await postJson("/api/qbo/connect", {});
    if (!r.ok) { toast(r.data.error || "Couldn't start the QuickBooks connection."); return; }
    if (typeof r.data.url === "string" && r.data.url.startsWith("https://")) location.assign(r.data.url);
  },
  "qbo-sync": async () => {
    toast("Reading payments from QuickBooks…");
    const r = await postJson("/api/qbo/payments", { days: 90 });
    if (!r.ok) { toast(r.data.error || "Couldn't read QuickBooks."); qboStatus = null; render(); return; }
    paymentImport = escDeep(r.data); render();
  },
  "qbo-reconcile": async () => {
    try {
      const res = await fetch("/api/qbo/reconcile");
      const data = await readJson(res);
      if (!res.ok) { toast(data.error || "Couldn't read QuickBooks."); qboStatus = null; render(); return; }
      qboReconcile = escDeep(data);
    } catch (_) { setNetBanner(true); return; }
    render();
  },
  "qbo-disconnect": async () => {
    if (!(await askConfirm("Disconnect QuickBooks? Payments already recorded stay. An owner can connect it again any time.", "Disconnect"))) return;
    const r = await postJson("/api/qbo/disconnect", {});
    if (r.ok) { qboStatus = escDeep(r.data); qboReconcile = null; }
    toast(r.ok ? "QuickBooks is disconnected." : (r.data.error || "That didn't work."));
    render();
  },
};
