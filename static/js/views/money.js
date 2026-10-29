// Money: Pay apps (with their paperwork), Change orders, Unpaid, Payments (with QuickBooks),
// Closeout (lien waivers and retainage) and Outbox, and the detail pages they open.
import { esc, unesc, escDeep, money, centsFrom, toDate, fmtDate, fmtDateTime, words, INVOICE_WORDS, invoicePill, emailStatusHtml } from "../format.js";
import { RECORD_KINDS, isBlocked, coBlockers, statusPill, coSignature, WAIVER_KINDS, waiverKindLabel, coStatusWords, waiverHint, waiverTypeText, waiverSendForm, followUpDue, retainageAskWaits, defaultJobId, payAppList, shortPayLabel, shortPayAttrs, canPullBack } from "../jobs.js";
import { continuationHtml } from "../sov.js";
import { pathFor } from "../routes.js";
import { readJson, setNetBanner, postJson } from "../net.js";
import { moreButton, askConfirm } from "../dom.js";
import { field, moneyField, formErrorLine, refuseForm, formValues, draftValue, clearForm } from "../forms.js";
import { canRecord, needsPick, splitTooMuch, picksFrom, afterRecord, refreshOwed } from "../payimport.js";
import { termText } from "../terms.js";
import { companyGapsHtml } from "../setup.js";
import { state, me, currentView, currentJobId, currentDetailId, formOpen, mailReady, onSessionEnd } from "../state.js";
import { may, render, DEMO_COMPANY_ID, toast, post, backButton, emptyState, nav, listPage, setState, EMAIL_PATTERN } from "../app-core.js";
import { coFormFields, coSignControls, shortPayCardHtml, owedText, emailFailed, shortPayButtons, sendsTo, addChangeOrder } from "./job.js";

export let paymentImport = null;  // {rows, warnings} proposed from a QuickBooks CSV or the QuickBooks connection
export let qboStatus = null;      // /api/qbo/status: configured, connected, environment (escaped)
let qboReconcile = null;   // /api/qbo/reconcile: {rows, invoices}
// A sent pay app downloads as a QuickBooks invoice-import file (R4-11).
const QBO_READY = new Set(["sent", "approved", "partial", "paid"]);
const qboCsvLink = (jobId, invId) => `<a class="btn secondary small qbo-csv" href="/api/jobs/${jobId}/payapp/${invId}.csv" download>Download for QuickBooks</a>`;
const RECON_WORDS = { missing: "Not in QuickBooks", amount: "Amount differs", paid_in_qbo: "Paid in QuickBooks", open_in_qbo: "Open in QuickBooks" };
let payappFilter = "all";
let outboxJobFilter = "all";
export function setOutboxJobFilter(value) { outboxJobFilter = value; }
let coPreselectJob = null;

// Signed out: the QuickBooks connection, the payments being imported and a job picked in a
// filter are that company's.
onSessionEnd(() => { paymentImport = null; qboStatus = null; qboReconcile = null; outboxJobFilter = "all"; coPreselectJob = null; });

// The job-record kind for a file taken for a paperwork item: the item's own kind when it is one
// (waiver, ticket, contract, photo...), else Other.
function recordKindFor(item) {
  return RECORD_KINDS[unesc(item.kind || "")] && !["note", "daily"].includes(item.kind) ? item.kind : "other";
}

// Ask for retainage from the Retainage page (R4-07): an amount box, blank for all of it once
// the job is done; before that, the part to ask for now.
function retainageAskHtml(j) {
  const key = `retask-${j.id}`, done = j.work_status === "complete";
  if (!may("retainage")) return `<span class="sub">An owner asks for it.</span>`;
  // chase_retainage waits the customer's days between contacts after the last request.
  const asked = retainageAskWaits(j);
  if (asked) return `<span class="sub">Asked ${fmtDate(asked.last)}. Ask again from ${fmtDate(asked.due)}.</span>`;
  return `<div class="retainage-ask" data-form-key="${key}">${formErrorLine(key)}<div class="form-inline">
    ${moneyField({ form: key, name: "amount_cents", id: `${key}-amt`, label: done ? "Amount ($), blank for all" : "Part to ask for now ($)" })}
    <button class="btn small" data-action="retainage" data-id="${j.id}">Ask for retainage</button></div></div>`;
}

// The email subject the pay app goes out with (domain.pay_app_subject, R4-05).
function subjectPreview(j, inv) {
  const company = (state.company || {}).name || "";
  return [`${inv.final ? "Final pay application" : "Pay application"} ${inv.number}`, j.name, money(inv.amount_cents), company,
    inv.period_to ? `period to ${inv.period_to}` : ""].filter(Boolean).join(" — ");
}

// Pull back a sent pay app (R4-03): a button, then the reason and the button that does it.
function pullBackHtml(j, i) {
  const key = `voidsent-${i.id}`;
  if (!formOpen[key]) return `<button class="btn small secondary danger-text" data-action="toggle-form" data-form="${key}" aria-expanded="false">${termText("Pull back this pay app…")}</button>`;
  return `<div class="pull-back" data-form-key="${key}"><h3>Pull back ${i.number}</h3>
    <p class="sub">${termText("For a pay app that went out with a mistake.")} ${j.payer.name || "The customer"} is emailed that it is withdrawn; you can then bill again.</p>
    ${formErrorLine(key)}<div class="form-row">
      ${field({ form: key, name: "reason", id: `voidsent-reason-${i.id}`, label: "Why pull it back", required: true, placeholder: "Wrong quantity on line 3", wide: true })}
      <button class="btn small danger" data-action="void-sent" data-id="${j.id}" data-inv="${i.id}">Pull back ${i.number}</button>
      <button class="btn small secondary" data-action="toggle-form" data-form="${key}">Cancel</button>
    </div></div>`;
}

const OWNER_SENDS = `<p class="sub owner-only">Only an owner can send this. Tell an owner it is ready.</p>`;

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

// On a server that sends mail, nothing goes out until the signed-in email is confirmed: the
// server refuses (R1-07), so the send buttons are off and say why, with the link to send again.
function emailUnconfirmed() {
  return !!(mailReady && me && !me.email_verified && me.company_id !== DEMO_COMPANY_ID);
}

function confirmFirstHtml(what) {
  return `<p class="notice confirm-first">Confirm your email first so ${what} goes out from you.
    <button class="btn small secondary" data-action="resend-verify">Send the link again</button></p>`;
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
    <p class="sub">Each sent pay app also has <strong>Download for QuickBooks</strong>: a file QuickBooks imports as an invoice, with retainage as its own line.</p></div>`;
}

// Pay apps' Paperwork column (R6-03, was its own tab): what is missing, linked to the list.
function paperworkCell(a) {
  const pkt = a.packet || a.jobPacket || [];
  if (!pkt.length && !a.packet) return "—";
  const missing = pkt.filter(p => p.required && !p.present).length;
  return `<a href="${pathFor("packet-detail", a.jobId, a.id)}" data-action="open-packet" data-job="${a.jobId}" data-inv="${a.id}">${missing ? `<span class="pill red">${missing} missing</span>` : `<span class="pill green">Complete</span>`}</a>`;
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  packetDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return backButton("payapps", termText("Pay apps")) + emptyState("Paperwork not found", "The job may have been deleted.", `<button class="btn" data-action="nav" data-view="payapps">${termText("Go to Pay apps")}</button>`);
    const isCloseout = currentDetailId === "closeout";
    const inv = isCloseout ? null : (j.invoices.find(i => i.id === currentDetailId) || [...j.invoices].reverse().find(i => i.packet) || j.invoices[j.invoices.length - 1]);
    const packet = isCloseout ? (j.closeout_packet || []) : ((inv && inv.packet) || j.packet || []);
    const jobRecords = j.records || [];
    if (!inv && !packet.length) return backButton("job", j.name, j.id) + emptyState("No paperwork yet", "Draft a pay app. Its paperwork list comes with it, from this customer's settings. Until then, add notes and files to the job record.", `<button class="btn" data-action="open-job" data-id="${j.id}">Back to ${j.name}</button>`);
    const missing = packet.filter(p => p.required && !p.present).length;
    // Unsigned change orders on the bill block the same send; say so here with a way to them (R3-05).
    const cosBlocking = !isCloseout && isBlocked(j) ? coBlockers(j) : [];
    // What went with a pay app stays as it was sent: Remove is for a draft's paperwork, the job's and closeout's.
    const canRemove = !j.closed && !(inv && !["draft", "awaiting_approval"].includes(inv.status));
    return `
      ${backButton("job", j.name, j.id)}
      <h1>${isCloseout ? "Closeout paperwork" : (inv ? "Paperwork for " + inv.number : "Paperwork")}</h1>
      <p class="sub">${j.name}${inv ? " · " + money(inv.amount_cents) + " · " + (INVOICE_WORDS[inv.status] || words(inv.status)) : ""}${isCloseout ? " · needed before you ask for the money held back" : ""} · ${missing ? `<span class="blocker">${missing} missing</span>` : "complete"}</p>
      <div class="card">
        <h2>Required items</h2>
        ${packet.length ? packet.map(p => {
          const skipped = !p.required && p.skipped_reason;
          return `
          <div class="list-item packet-item ${p.present || skipped ? "present" : "absent"}">
            <div><span class="mark" aria-hidden="true">${p.present ? "✓" : skipped ? "–" : "✗"}</span> <strong>${p.label}</strong> <span class="visually-hidden">${p.present ? "in" : skipped ? "not needed" : "missing"}</span>
              ${!p.present && !skipped ? `<span class="field-error">${p.blocker}</span>` : ""}
              ${skipped ? `<div class="sub">Not needed for this customer${p.skipped_reason !== "Not needed for this customer" ? ": " + p.skipped_reason : ""}</div>` : ""}
              ${p.present && p.present_reason === "Produced by Duracash" ? `<div class="sub">Produced by Duracash with the pay app</div>` : ""}
              ${p.present && p.present_reason && p.present_reason !== "Produced by Duracash" ? `<div class="sub">Marked as in hand: ${p.present_reason}</div>` : ""}
              ${p.present && !p.present_reason && !(p.attachments || []).length ? `<div class="sub">Marked as in hand.</div>` : ""}
              ${(p.attachments || []).map(a => `<div class="sub">${a.auto ? "Attached from the job record" : "Attached"}: ${a.name}</div>`).join("")}
              ${p.present && p.present_reason !== "Produced by Duracash" && canRemove ? `<button class="btn small secondary" data-action="packet-detach" data-id="${j.id}" data-item="${p.id}">Remove</button>` : ""}
            </div>
            ${!p.present && !skipped ? `<div class="form-inline packet-add">
              <button class="btn small" data-action="pick-files" data-job="${j.id}" data-attach="${p.id}" data-kind="${recordKindFor(p)}" data-capture="1">Take a photo</button>
              <button class="btn small secondary" data-action="pick-files" data-job="${j.id}" data-attach="${p.id}" data-kind="${recordKindFor(p)}">Choose file</button>
            </div>
            <div class="form-inline">
              ${jobRecords.length ? `<select id="attach-rec-${p.id}" aria-label="Job record to attach"><option value="">Choose a record…</option>${jobRecords.map(r => `<option value="${r.id}">${r.label}</option>`).join("")}</select><button class="btn small secondary" data-action="attach-record" data-id="${j.id}" data-item="${p.id}">Attach</button>` : ""}
              <button class="btn small secondary" data-action="toggle-form" data-form="resolve-${p.id}" aria-expanded="${!!formOpen["resolve-" + p.id]}">I have this…</button>
              ${isCloseout ? "" : `<button class="btn small secondary" data-action="packet-skip" data-id="${j.id}" data-item="${p.id}">Not needed for this customer</button>`}
            </div>
            ${formOpen["resolve-" + p.id] ? `<div class="form-inline"><input id="resolve-reason-${p.id}" placeholder="Where is it? (optional)" aria-label="Where is it? (optional)" class="grow"><button class="btn small" data-action="resolve" data-id="${j.id}" data-item="${p.id}">Save</button></div>` : ""}` : ""}
          </div>`;
        }).join("") : `<p class="sub">${termText("This pay app needs no paperwork.")}</p>`}
      </div>
      ${!isCloseout && cosBlocking.length ? `<div class="card also-blocking" role="note">
        <h2>Also blocking send</h2>
        ${cosBlocking.map(b => `<div class="list-item">${b.text} <a href="${pathFor("co-detail", j.id, b.co_id)}" data-action="open-co" data-job="${j.id}" data-co="${b.co_id}">Open CO ${b.number}</a></div>`).join("")}
      </div>` : ""}
    `;
  },
  coDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return backButton("cos", "Change orders") + emptyState("Change order not found", "The job may have been deleted.", `<button class="btn" data-action="nav" data-view="cos">Go to Change orders</button>`);
    const co = j.change_orders.find(c => c.id === currentDetailId) || j.change_orders[j.change_orders.length - 1];
    if (!co) {
      return `
        ${backButton("cos", "Change orders")}
        <h1>New change order</h1>
        <div data-form-key="newco">${formErrorLine("newco")}<div class="form-row">
          ${coFormFields()}
          <button class="btn" data-action="add-co" data-id="${j.id}">Create</button>
        </div></div>`;
    }
    return `
      ${backButton("cos", "Change orders")}
      <h1>CO ${co.number}: ${co.title}</h1>
      <p class="sub"><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a> · ${money(co.amount_cents)} · <span class="pill ${co.status === "approved" ? "green" : "amber"}">${coStatusWords(co.status)}</span></p>
      <div class="card">
        <h2>Summary</h2>
        ${co.description ? `<p>${co.description}</p>` : ""}
        <div class="money-row"><span class="label">Waiting on</span><span>${co.waiting_on || (co.status === "pending" ? "Customer signature" : "Nothing")}</span></div>
        <div class="money-row"><span class="label">${termText("On a pay app")}</span><span>${co.included_in_billing ? "Yes" : "No"}</span></div>
        ${coSignature(co)}
        ${coSignControls(j, co)}
      </div>
    `;
  },
  payappDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    const inv = j && (j.invoices.find(i => i.id === currentDetailId) || j.invoices[j.invoices.length - 1]);
    if (!j || !inv) return backButton("payapps", "Pay apps") + emptyState("Pay app not found", "The job may have been deleted.", `<button class="btn" data-action="nav" data-view="payapps">Go to Pay apps</button>`);
    const pays = j.payments.filter(p => p.invoice_id === inv.id);
    const pkt = inv.packet || j.packet || [];
    const missing = pkt.filter(p => p.required && !p.present).length;
    const draft = ["draft", "awaiting_approval"].includes(inv.status);
    const copies = (j.payer.copy && unesc(j.payer.copy) !== "None") ? j.payer.copy : "";
    const firstSend = draft && !j.payer.sent_before && !state.jobs.some(x => x.payer_id === j.payer_id && x.invoices.some(i => i.sent_at));
    const noEmail = !j.payer.ap_email;
    // The PDF's From block prints the company's address, phone and licence number (R2-05, Oct 23
    // note 1): a blank one is said here before it goes out, as a warning only.
    const locked = emailUnconfirmed();
    // The customer's payment terms set the due date from the day it goes out (R4-04).
    const terms = Number(unesc(String(j.payer.payment_terms_days || ""))) || 0;
    const dueIso = terms ? new Date(Date.now() + terms * 86400000).toISOString().slice(0, 10) : "";
    return `
      ${backButton("job", j.name, j.id)}
      <h1>${inv.number}</h1>
      <p class="sub">${invoicePill(inv)} <a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a> · ${money(inv.amount_cents)}</p>
      ${inv.status === "void" && inv.voided_reason ? `<p class="notice pulled-back">Pulled back ${fmtDate(inv.voided_at)}${inv.voided_by ? " by " + inv.voided_by : ""}: ${inv.voided_reason}. The customer was told nothing is due on it.</p>` : ""}
      <p><a class="btn secondary small" href="/api/jobs/${j.id}/payapp/${inv.id}.pdf" target="_blank" rel="noopener">Open the PDF</a> <span class="sub">${termText("The same PDF goes with the email when the pay app is sent.")}</span></p>
      ${QBO_READY.has(inv.status) ? `<p>${qboCsvLink(j.id, inv.id)} <span class="sub">A file to import in QuickBooks: Sales, Invoices, Import invoices.</span></p>` : ""}
      ${draft ? `<div class="card send-check">
        <h2>Before you send</h2>
        <div class="money-row"><span class="label">To</span><span>${j.payer.approver ? j.payer.approver + " · " : ""}${j.payer.ap_email || '<span class="blocker">no email yet</span>'}</span></div>
        ${noEmail ? `<div class="send-email" data-form-key="sendemail">${formErrorLine("sendemail")}
          <div class="form-grid">${field({ form: "sendemail", name: "ap_email", id: "send-email", label: "Email for pay apps", helpKey: "ap_email", type: "email", autocomplete: "off", placeholder: "name@example.com" })}</div>
          <button class="btn small secondary" data-action="save-send-email" data-id="${j.payer_id}">Save email</button>
        </div>` : ""}
        <div class="money-row"><span class="label" data-help="copy">Also email (CC)</span><span>${copies || "Nobody"}</span></div>
        <div class="money-row"><span class="label">Amount</span><span>${money(inv.amount_cents)}</span></div>
        <div class="money-row"><span class="label">Period to</span><span>${inv.period_to ? fmtDate(inv.period_to) : "—"}</span></div>
        ${terms ? `<div class="money-row"><span class="label">Payment due</span><span>${fmtDate(dueIso)}, ${terms} days after it goes out</span></div>` : ""}
        <div class="money-row"><span class="label">Subject</span><span>${subjectPreview(j, inv)}</span></div>
        ${inv.final ? `<p class="sub final-note">${termText("The final pay app: the PDF is marked FINAL APPLICATION.")}</p>` : ""}
        <div class="money-row"><span class="label">Attached</span><span>Pay app PDF${inv.sov_sheet && inv.sov_sheet.length ? " with continuation sheet" : ""}, paperwork checklist, ${pkt.filter(p => p.present).length} paperwork item${pkt.filter(p => p.present).length === 1 ? "" : "s"}</span></div>
        ${companyGapsHtml(state.company)}
        ${firstSend && !noEmail ? `<p class="notice">First pay app to ${j.payer.name}. Check the email above; a typo sends it to a stranger. Change it in <button class="linkish" data-action="nav" data-view="playbook">Customer settings</button>.</p>` : ""}
        ${missing ? `<p class="blocker">${missing} paperwork item${missing === 1 ? " is" : "s are"} missing. Add ${missing === 1 ? "it" : "them"}, then send.</p>` : ""}
        ${noEmail ? `<p class="blocker">${termText("Add the email pay apps go to, then send.")}</p>` : ""}
        ${locked ? confirmFirstHtml(termText("the pay app")) : ""}
        ${may("send") ? `<button class="btn" data-action="send" data-id="${j.id}" ${missing || (j.blockers || []).length || noEmail || locked ? "disabled" : ""}>Approve & send to ${j.payer.ap_email || "the customer"}</button>` : OWNER_SENDS}
      </div>` : ""}
      <div class="card">
        <h2>Lines</h2>
        ${(inv.lines || []).map(l => `<div class="money-row"><span>${l.label}</span><span>${money(l.amount_cents)}</span></div>`).join("")}
        ${inv.progress_notes ? `<p class="payapp-notes"><span class="label">Notes</span> ${inv.progress_notes}</p>` : ""}
      </div>
      ${continuationHtml(inv, j)}
      <div class="card">
        <h2>Paperwork</h2>
        ${pkt.length ? pkt.map(p => {
          const skipped = !p.required && !p.present;
          return `<div class="list-item"><span aria-hidden="true">${p.present ? "✓" : skipped ? "–" : "✗"}</span> ${p.label}${skipped ? ` <span class="sub">not needed</span>` : `<span class="visually-hidden">${p.present ? " in" : " missing"}</span>`}</div>`;
        }).join("") : `<p class="sub">${termText("This pay app needs no paperwork.")}</p>`}
        <button class="btn secondary small" data-action="open-packet" data-job="${j.id}" data-inv="${inv.id}">Open paperwork</button>
      </div>
      <div class="card">
        <h2>Payments received</h2>
        ${pays.length ? pays.map(p => `<div class="list-item">${money(p.amount_cents)} · ${p.reference} · ${fmtDate(p.matched_at)}</div>`).join("") : `<p class="sub">None yet.</p>`}
        ${inv.short_pay_cents ? `<p class="sub">Short pay accepted ${fmtDate(inv.short_pay_at)}: ${money(inv.short_pay_cents)} not paid. ${inv.short_pay_reason || ""}</p>` : ""}
        ${inv.status === "partial" && inv.remaining_cents > 0 && !formOpen[`shortpay-${inv.id}`] && may("accept-short-pay") ? `<p class="sub short-pay-row">Paid short, and the rest won't come? <button class="btn small secondary" ${shortPayAttrs(j, inv)}>${shortPayLabel(inv)}</button></p>` : ""}
        ${inv.status === "partial" ? shortPayCardHtml(j, inv) : ""}
      </div>
      <div class="card">
        <h2>Approval</h2>
        <div class="money-row"><span class="label">Sent by</span><span>${inv.approved_by || "—"}</span></div>
        <div class="money-row"><span class="label">Sent</span><span>${fmtDateTime(inv.sent_at)}</span></div>
        ${inv.period_to ? `<div class="money-row"><span class="label">Period to</span><span>${fmtDate(inv.period_to)}</span></div>` : ""}
        ${inv.due_on ? `<div class="money-row"><span class="label">Payment due</span><span>${fmtDate(inv.due_on)}</span></div>` : ""}
        <div class="money-row"><span class="label">Received by the customer</span><span>${inv.received_at ? `${inv.received_by || "the customer"}, ${fmtDateTime(inv.received_at)}` : inv.sent_at ? "Not confirmed yet" : "—"}</span></div>
        ${inv.approved_at ? `<div class="money-row"><span class="label">Approved by the customer</span><span>${inv.approved_by_payer || "the customer"}, ${fmtDateTime(inv.approved_at)}</span></div>
        <div class="money-row"><span class="label">Payment expected by</span><span>${fmtDate(inv.expected_payment_on)}</span></div>` : ""}
        ${inv.customer_page ? `<p class="sub">The customer's page for this pay app (it went in the email): <a href="/api/jobs/${j.id}/payapp/${inv.id}/page" target="_blank" rel="noopener">open it</a>. They can see the pay app and its paperwork, mark it received, approve it for payment with a date, or send you a question.</p>` : ""}
        ${canPullBack(j, inv) && may("void-sent") ? pullBackHtml(j, inv) : ""}
      </div>
    `;
  },
  waiverDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return backButton("waivers", "Closeout") + emptyState("Waiver not found", "The job may have been deleted.", `<button class="btn" data-action="nav" data-view="waivers">Go to Closeout</button>`);
    const due = j.invoices.filter(i => i.waiver_due && !i.waiver_sent);
    const sent = j.invoices.filter(i => i.waiver_sent);
    const locked = emailUnconfirmed();
    const owed = due.length || (j.waiver_due && !j.waiver_sent);
    // Sending attaches the newest lien waiver on the job record; say so when there is none (R1-06).
    const hasWaiver = (j.records || []).some(r => r.kind === "waiver");
    return `
      ${backButton("waivers", "Closeout")}
      <h1>Waivers: ${j.name}</h1>
      <p class="sub">Duracash never writes a waiver: state law sets the forms. Drop your signed waiver on the job record; sending attaches the newest one.</p>
      <div class="card">
        <h2>What the customer needs</h2>
        <p>${j.waiver_standard || j.payer.packet || "See Customer settings."}</p>
      </div>
      ${owed && !j.summary && !hasWaiver ? `<div class="card no-waiver">
        <h2>No signed waiver on the job yet</h2>
        <p class="sub">Sending without one tells ${j.payer.name} the signed waiver follows separately.</p>
        <div class="form-inline">
          <button class="btn small" data-action="pick-files" data-job="${j.id}" data-kind="waiver" data-capture="1">Take a photo</button>
          <button class="btn small secondary" data-action="pick-files" data-job="${j.id}" data-kind="waiver">Choose file</button>
        </div>
      </div>` : ""}
      <div class="card send-check">
        <h2>${termText("By pay app")}</h2>
        ${owed && j.payer.ap_email ? `<div class="money-row"><span class="label">To</span><span>${j.payer.approver ? j.payer.approver + " · " : ""}${j.payer.ap_email}</span></div>` : ""}
        ${owed && !j.payer.ap_email ? `<p class="blocker">Add the email pay apps go to in <button class="linkish" data-action="nav" data-view="playbook">Customer settings</button>, then send.</p>` : ""}
        ${owed && locked ? confirmFirstHtml("the waiver") : ""}
        ${due.concat(sent).length ? due.concat(sent).map(i => `<div class="list-item"><strong>${i.number}</strong> · ${money(i.amount_cents)} · ${i.waiver_sent ? `sent ${fmtDate(i.waiver_sent_at)}${i.waiver_kind ? " · " + waiverKindLabel(i.waiver_kind) : ""}` : `<span class="pill amber">Due</span>`}
          ${!i.waiver_sent ? (may("waiver") ? waiverSendForm(j, i, locked || !j.payer.ap_email) : OWNER_SENDS) : ""}</div>`).join("")
          : j.waiver_due && !j.waiver_sent ? (may("waiver") ? waiverSendForm(j, null, locked || !j.payer.ap_email) : OWNER_SENDS) : `<p class="sub">${j.waiver_sent ? "Sent " + fmtDate(j.waiver_sent_at) : termText("No waiver is due yet. One is due after each paid pay app.")}</p>`}
      </div>
    `;
  },
  "packet-detail": function() { return views.packetDetail(); },
  "co-detail": function() { return views.coDetail(); },
  "payapp-detail": function() { return views.payappDetail(); },
  "waiver-detail": function() { return views.waiverDetail(); },
  payapps() {
    let apps = [];
    state.jobs.forEach(j => j.invoices.forEach(inv => apps.push({ ...inv, job: j.name, jobId: j.id, jobPacket: j.packet, blockers: ["draft", "awaiting_approval"].includes(inv.status) ? j.blockers : [] })));
    // "Unpaid" (the sent filter) holds every pay app out for payment, part-paid ones too (R3-02).
    const shown = { draft: ["draft", "awaiting_approval"], sent: ["sent", "approved", "partial"] };
    if (payappFilter !== "all") apps = apps.filter(a => (shown[payappFilter] || [payappFilter]).includes(a.status));
    const f = (key, label) => `<button class="${payappFilter === key ? "btn" : "btn secondary"} small" data-action="payapp-filter" data-filter="${key}" aria-pressed="${payappFilter === key}">${label}</button>`;
    return `
      <h1>${termText("Pay apps")}</h1>
      <div class="filter-bar" role="group" aria-label="Show">${f("all", "All")}${f("draft", "Ready to send")}${f("sent", "Unpaid")}${f("paid", "Paid")}</div>
      ${apps.length ? `<div class="table-wrap">
        <table>
          <tr><th>Number</th><th>Job</th><th>Amount</th><th>Status</th><th>Paperwork</th><th>Sent</th><th>Before it can go</th></tr>
          ${apps.slice().reverse().map(a => `<tr>
            <td><a href="${pathFor("payapp-detail", a.jobId, a.id)}" data-action="open-payapp" data-job="${a.jobId}" data-inv="${a.id}">${a.number}</a></td>
            <td><a href="${pathFor("job", a.jobId)}" data-action="open-job" data-id="${a.jobId}">${a.job}</a></td>
            <td class="num">${a.status === "partial" ? owedText(a) : money(a.amount_cents)}</td>
            <td>${invoicePill(a)}</td>
            <td>${paperworkCell(a)}</td>
            <td>${a.sent_at ? fmtDate(a.sent_at) : "—"}${emailFailed(a.id) ? " · email failed" : ""}</td>
            <td>${a.blockers.join("; ") || "—"}</td>
          </tr>`).join("")}
        </table>
      </div>` : emptyState(payappFilter === "all" ? termText("No pay apps yet") : "None in this view", payappFilter === "all" ? "Draft one from a job. Its paperwork list comes with it." : "Pick another filter above.")}
    `;
  },
  // Paperwork is a column of Pay apps now (R6-03); /money/packets still opens it.
  packets() { return views.payapps(); },
  cos() {
    const cos = [];
    state.jobs.forEach(j => j.change_orders.forEach(co => cos.push({ ...co, job: j.name, jobId: j.id })));
    const openJobs = state.jobs.filter(j => !j.closed);
    return `
      <h1>Change orders</h1>
      <p class="sub">Extra or changed work. Not billable until the customer signs.</p>
      ${openJobs.length ? `<div class="card" data-form-key="newco">
        <h2>New change order</h2>
        ${formErrorLine("newco")}
        <div class="form-grid">
          ${field({ form: "newco", name: "job", id: "new-co-job", label: "Job", value: unesc(defaultJobId(openJobs, coPreselectJob || "")), options: [["", "Pick a job…"], ...openJobs.map(j => [unesc(j.id), unesc(j.name)])] })}
          ${coFormFields()}
        </div>
        <button class="btn" data-action="add-co-from-list">Create change order</button>
      </div>` : ""}
      ${cos.length ? `<div class="table-wrap">
        <table>
          <tr><th>Change order</th><th>Job</th><th>Amount</th><th>Status</th><th>Waiting on</th><th>${termText("On a pay app")}</th></tr>
          ${cos.slice().reverse().map(c => `<tr>
            <td><a href="${pathFor("co-detail", c.jobId, c.id)}" data-action="open-co" data-job="${c.jobId}" data-co="${c.id}">CO ${c.number}: ${c.title}</a>${c.description ? `<div class="sub row-sub">${c.description.slice(0, 80)}</div>` : ""}</td>
            <td>${c.job}</td>
            <td class="num">${money(c.amount_cents)}</td>
            <td><span class="pill ${c.status === "approved" ? "green" : c.status === "withdrawn" ? "" : "amber"}">${coStatusWords(c.status)}</span></td>
            <td>${c.waiting_on || (c.status === "pending" ? "Signature" : "—")}</td>
            <td>${c.included_in_billing ? "Yes" : "No"}</td>
          </tr>`).join("")}
        </table>
      </div>` : emptyState("No change orders", openJobs.length ? "Create one above when the scope changes." : "Create a job first.")}
    `;
  },
  collections() {
    const open = state.jobs.filter(j => j.money.outstanding_cents > 0);
    const today = new Date();
    return `
      <h1>Unpaid</h1>
      <p class="sub">What is unpaid, who to contact, and a one-tap follow-up.</p>
      ${open.length ? `<div class="table-wrap">
        <table>
          <tr><th>Job</th><th>Unpaid</th><th>Days since sent</th><th>Due</th><th>Expected</th><th>Contact</th><th>Follow up</th></tr>
          ${open.map(j => {
            const sent = j.invoices.find(i => ["sent", "partial", "approved"].includes(i.status) && i.sent_at);
            const days = sent ? Math.floor((today - toDate(sent.sent_at)) / 86400000) : "—";
            const unpaid = j.invoices.filter(i => ["sent", "partial", "approved"].includes(i.status)).map(i => i.number + " " + owedText(i)).join(", ") || money(j.money.outstanding_cents);
            // Inside the customer's days between follow-ups the button waits, and says until when (R3-03).
            const follow = followUpDue(j);
            const early = follow && today < follow.due;
            // From the customer's payment terms, on the oldest unpaid pay app that has them (R4-04).
            const dueInv = j.invoices.find(i => ["sent", "partial", "approved"].includes(i.status) && i.due_on);
            const late = dueInv && toDate(dueInv.due_on) < new Date(today.toDateString());
            // When the money should come: the date given with the customer's approval (R4-08), or
            // the one noted after a call (R4-06).
            const approved = j.invoices.find(i => i.status === "approved" && i.expected_payment_on);
            const expected = approved ? `${fmtDate(approved.expected_payment_on)}<div class="sub row-sub">${approved.number} approved by ${approved.approved_by_payer || "the customer"}</div>`
              : j.expected_payment_on ? `${fmtDate(j.expected_payment_on)}<div class="sub row-sub">they said on the phone</div>` : "—";
            return `<tr>
              <td><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a><div class="sub row-sub">${statusPill(j)}</div></td>
              <td>${unpaid}</td>
              <td class="num">${days}</td>
              <td>${dueInv ? (late ? `<span class="pill red">Overdue</span><div class="sub row-sub">due ${fmtDate(dueInv.due_on)}</div>` : fmtDate(dueInv.due_on)) : "—"}</td>
              <td>${expected}</td>
              <td>${j.payer.approver}<div class="sub row-sub">${j.payer.ap_email}${j.payer.phone ? "<br>" + j.payer.phone : ""}</div></td>
              <td class="actions-cell">
                ${early ? `<button class="btn small" disabled>Next follow-up ${fmtDate(follow.day)}</button>` : `<button class="btn small" data-action="follow-up" data-id="${j.id}">Send follow-up</button>`}
                ${shortPayButtons(j)}
                <a class="btn small secondary" href="mailto:${j.payer.ap_email}">Email</a>
                ${j.payer.phone ? `<a class="btn small secondary" href="tel:${(j.payer.phone || "").replace(/[^0-9+]/g, "")}">Call</a>` : ""}
              </td>
            </tr>`;
          }).join("")}
        </table>
      </div>` : emptyState("Nothing unpaid", termText("Unpaid pay apps show up here after one is sent."))}
    `;
  },
  payments() {
    const pays = [];
    state.jobs.forEach(j => j.payments.forEach(p => pays.push({ ...p, job: j.name, jobId: j.id })));
    const imp = paymentImport;
    const importCard = `<details class="card"${imp || !pays.length ? " open" : ""}><summary><strong>Import payments from QuickBooks (CSV)</strong></summary>
      <p class="sub">In QuickBooks, export a report to CSV and paste it here. Any QuickBooks report with payments in it works; invoices and refunds are skipped. Duracash proposes a pay app for each payment and records nothing until you tick and apply. A check already recorded from the same customer is skipped, so importing the same file twice is safe.</p>
      ${imp ? `${imp.warnings.length ? `<div class="sov-warn">${imp.warnings.map(w => `<div>${w}</div>`).join("")}</div>` : ""}
        <div class="table-wrap"><table>
          <tr><th>Record</th><th>Date</th><th>Customer</th><th>Number</th><th>Amount</th><th>Pay app</th><th>Why</th></tr>
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
      </div>` : emptyState("No payments yet", "Paste your QuickBooks payments export above, or record one from a job page with Record a payment.")}
    `;
  },
  // Closeout (R6-01): the lien waivers due and the money held back, one tab. /money/retainage
  // opens the same page at the held-back part.
  waivers() {
    const due = state.jobs.filter(j => j.waiver_due && !j.waiver_sent);
    const sent = state.jobs.filter(j => j.waiver_sent);
    const rows = due.concat(sent);
    const open = state.jobs.filter(j => j.money.retainage_open_cents > 0);
    return `
      <h1>Closeout</h1>
      <p class="sub">${termText("Lien waivers for each paid pay app, and the money held back until the job is closed out.")}</p>
      <section class="card closeout-waivers" aria-labelledby="closeout-waivers-h">
        <h2 id="closeout-waivers-h">Lien waivers</h2>
        <p class="sub">${termText("One lien waiver per paid pay app. Duracash never writes the waiver; it sends the signed one on the job record.")}</p>
        <details class="waiver-hints"><summary>Which type?</summary>
          <ul>${Object.entries(WAIVER_KINDS).map(([k, v]) => `<li><strong>${v}.</strong> ${waiverHint(k)}</li>`).join("")}</ul>
        </details>
        ${rows.length ? `<div class="table-wrap">
          <table>
            <tr><th>Job</th><th>Type</th><th>Amount</th><th>Status</th><th>Action</th></tr>
            ${rows.map(j => {
              const paid = j.invoices.find(i => i.waiver_due && !i.waiver_sent) || [...j.invoices].reverse().find(i => i.status === "paid");
              return `<tr>
                <td><a href="${pathFor("waiver-detail", j.id)}" data-action="open-waiver" data-id="${j.id}">${j.name}</a></td>
                <td>${waiverTypeText(j, paid)}</td>
                <td class="num">${paid ? money(paid.amount_cents) : "—"}</td>
                <td>${j.waiver_sent ? `<span class="pill green">Sent</span>` : `<span class="pill amber">Due</span>`}</td>
                <td>${!j.waiver_sent && j.waiver_due ? `<button class="btn small" data-action="open-waiver" data-id="${j.id}">Choose type and send</button>` : "—"}</td>
              </tr>`;
            }).join("")}
          </table>
        </div>` : emptyState("No waivers due", "A waiver is due once a payment is recorded.")}
      </section>
      <section class="card closeout-retainage" id="closeout-retainage" aria-labelledby="closeout-retainage-h">
        <h2 id="closeout-retainage-h">Held back, still owed</h2>
        <p class="sub">Retainage: what is holding each release, and how to get it. Before a job is done you can ask for part of it, for example at substantial completion.</p>
        ${open.length ? `<div class="table-wrap">
          <table>
            <tr><th>Job</th><th>Held back</th><th>Holding it up</th><th>Next</th><th>Contact</th></tr>
            ${open.map(j => {
              const missing = j.closeout_missing || [];   // labels (domain.closeout_missing)
              return `<tr>
                <td><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a></td>
                <td class="num">${money(j.money.retainage_open_cents)}</td>
                <td>${missing.length ? missing.join(", ") : (j.work_status === "complete" ? "Closeout paperwork complete" : "Job still in progress")}</td>
                <td>${j.work_status === "complete" && missing.length ? `<button class="btn small" data-action="open-packet" data-job="${j.id}" ${j.closeout_packet && j.closeout_packet.length ? `data-inv="closeout"` : ""}>Open closeout paperwork</button>` : retainageAskHtml(j)}</td>
                <td>${j.payer.approver}<div class="sub row-sub"><a href="mailto:${j.payer.ap_email}">${j.payer.ap_email}</a></div></td>
              </tr>`;
            }).join("")}
          </table>
        </div>` : emptyState("Nothing held back", "Money held back (retainage) shows here once a job holds it. Set a retainage percent when creating the job.")}
      </section>
    `;
  },
  retainage() { return views.waivers(); },
  outbox() {
    const page = listPage("outbox", outboxJobFilter);
    const emails = page ? page.entries : [];
    return `
      <h1>Outbox</h1>
      <p class="sub">Every approved send and follow-up, with what happened to it.</p>
      <p class="sub" id="outbox-mode"></p>
      <div class="filter-bar">
        <label for="outbox-filter" class="inline-label">Job</label>
        <select id="outbox-filter" data-filter-for="outbox">
          <option value="all">All jobs</option>
          ${state.jobs.map(j => `<option value="${j.id}" ${outboxJobFilter === j.id ? "selected" : ""}>${j.name}</option>`).join("")}
        </select>
      </div>
      ${emails.length ? `<div class="table-wrap">
        <table>
          <tr><th>Subject</th><th>When</th><th>Kind</th><th>To</th><th>Status</th></tr>
          ${emails.map(e => `<tr><td><a href="${pathFor("job", e.job_id)}" data-action="open-job" data-id="${e.job_id}">${e.subject}</a><details class="email-details"><summary>Show message</summary><div class="sub email-body">${e.body || ""}</div></details>${e.payapp_invoice_id ? `<div class="sub">Pay app PDF attached</div>` : ""}${(e.attachments || []).length ? `<div class="sub">${e.attachments.length} attachment${e.attachments.length === 1 ? "" : "s"}${(e.not_attached || []).length ? "; not attached: " + e.not_attached.join("; ") : ""}</div>` : ""}</td><td>${fmtDateTime(e.at)}</td><td>${words(e.kind)}</td><td>${e.to}</td><td>${emailStatusHtml(e)}</td></tr>`).join("")}
        </table>
      </div>${moreButton("outbox", page)}` : !page ? `<p class="sub">Loading…</p>` : emptyState("Outbox is empty", "Approved sends and follow-ups are listed here.")}
    `;
  },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in app.js.
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
  "email-retry": (el) => post(`/api/outbox/${el.dataset.email}/retry`),
  "send": (el, id) => post(`/api/jobs/${id}/send`),
  // "Before you send" with no email yet (R1-05): saved to the customer's settings, then send unlocks.
  "save-send-email": async (el, id) => {
    const v = formValues("sendemail");
    if (refuseForm("sendemail", EMAIL_PATTERN.test(v.ap_email) ? {} : { ap_email: "Enter the email pay apps go to, like ap@builder.com." })) return;
    await post(`/api/payers/${id}/update`, { ap_email: v.ap_email }, { formKey: "sendemail" });
  },
  "resolve": async (el, id) => {
    const box = document.getElementById(`resolve-reason-${el.dataset.item}`);
    const reason = (box?.value || "").trim();   // optional (R3-05)
    const r = await post(`/api/jobs/${id}/resolve/${el.dataset.item}`, { reason });
    if (r && r.ok) { formOpen[`resolve-${el.dataset.item}`] = false; render(); }
  },
  "waiver": async (el, id) => {
    const kind = document.getElementById(`waiver-kind-${el.dataset.inv || "job"}`)?.value;
    await post(`/api/jobs/${id}/waiver`, { ...(el.dataset.inv ? { invoice_id: el.dataset.inv } : {}), ...(kind ? { kind } : {}) });
  },
  // All of the retainage, or the part typed on the Retainage page (R4-07): before the job is
  // done only a part can be asked for.
  "retainage": async (el, id) => {
    const j = state.jobs.find(x => x.id === id);
    const key = `retask-${id}`, form = document.querySelector(`[data-form-key="${key}"]`);
    const open = j ? j.money.retainage_open_cents : 0;
    let cents = null;
    if (form) {
      const typed = String(formValues(key).amount_cents || "").trim();
      const a = centsFrom(typed);
      if (!typed && j && j.work_status !== "complete") { refuseForm(key, { amount_cents: "Enter the part to ask for now. All of it waits until the job is done." }); return; }
      if (typed && (a.error || a.cents <= 0)) { refuseForm(key, { amount_cents: "Enter the retainage to ask for in dollars, like 5,000." }); return; }
      if (typed && a.cents > open) { refuseForm(key, { amount_cents: `Enter no more than the ${money(open)} held.` }); return; }
      if (typed && a.cents >= open && j && j.work_status !== "complete") { refuseForm(key, { amount_cents: `Enter less than the ${money(open)} held. All of it waits until the job is done.` }); return; }
      if (typed) cents = a.cents;
    }
    const part = cents != null && cents < open;
    if (j && !(await askConfirm(`Email a request for ${part ? `${money(cents)} of the ${money(open)}` : `the ${money(open)}`} retainage to ${sendsTo(j)}?`, "Send the request"))) return;
    await post(`/api/jobs/${id}/retainage`, cents != null ? { amount_cents: cents } : {}, form ? { formKey: key } : {});
  },
  "withdraw-co": async (el, id) => {
    if (!(await askConfirm("Withdraw this change order? Use this when the customer will not sign it.", "Withdraw", true))) return;
    await post(`/api/jobs/${id}/withdraw-co/${el.dataset.co}`);
  },
  // Short pay (R4-01): the card opens where the button was, or on the pay app's page.
  "short-pay-open": (el) => {
    const { job, inv } = el.dataset;
    const here = (currentView === "job" && currentJobId === job) || (currentView === "payapp-detail" && currentJobId === job && currentDetailId === inv);
    if (!here) nav("payapp-detail", job, inv);
    formOpen[`shortpay-${inv}`] = true;
    render();
    document.getElementById(`shortpay-reason-${inv}`)?.focus();
  },
  "accept-short-pay": async (el, id) => {
    const inv = el.dataset.inv, key = `shortpay-${inv}`;
    const j = state.jobs.find(x => x.id === id);
    const i = j && j.invoices.find(x => x.id === inv);
    const v = formValues(key);
    if (refuseForm(key, v.reason ? {} : { reason: "Enter why it was paid short, like a backcharge for cleanup." })) return;
    if (i && !(await askConfirm(`Accept ${money(i.amount_cents - i.remaining_cents)} on ${unesc(i.number)} as settled? The ${money(i.remaining_cents)} left is no longer owed, and its lien waiver comes due.`, "Accept short pay"))) return;
    await post(`/api/jobs/${id}/accept-short-pay/${inv}`, { reason: v.reason }, { formKey: key, close: key });
  },
  // Pull back a sent pay app (R4-03): a reason, then a confirm, then the customer is told.
  "void-sent": async (el, id) => {
    const inv = el.dataset.inv, key = `voidsent-${inv}`;
    const j = state.jobs.find(x => x.id === id);
    const i = j && j.invoices.find(x => x.id === inv);
    const v = formValues(key);
    if (refuseForm(key, v.reason ? {} : { reason: "Enter why you are pulling it back, like a wrong quantity on line 3." })) return;
    if (j && !(await askConfirm(`Pull back ${unesc(i ? i.number : termText("this pay app"))}? An email to ${sendsTo(j)} says it is withdrawn and nothing is due on it.`, "Pull back", true))) return;
    await post(`/api/jobs/${id}/void-sent/${inv}`, { reason: v.reason }, { formKey: key, close: key });
  },
  "follow-up": async (el, id) => {
    const j = state.jobs.find(x => x.id === id);
    const follow = j && followUpDue(j);
    // "Follow up now" on the job page goes before the customer's days between follow-ups are up (R3-04).
    const early = !!el.dataset.early && follow && follow.due > new Date();
    if (j && !(await askConfirm(`Email a follow-up on ${unesc(follow ? payAppList(follow.invoices) : "the pay app")} to ${sendsTo(j)}?${early ? ` It's before the follow-up date, ${fmtDate(follow.day)}.` : ""}`, "Send follow-up"))) return;
    await post(`/api/jobs/${id}/follow-up`, early ? { early: true } : {});
  },
  "add-co": async (el, id) => {
    const r = await addChangeOrder(id);
    if (r && r.ok) nav("co-detail", id);
  },
  "goto-co": (el, id) => { coPreselectJob = id; nav("cos"); },
  "add-co-from-list": async () => {
    const jobId = formValues("newco").job;
    if (!jobId) { refuseForm("newco", { job: "Pick a job." }); return; }
    const r = await addChangeOrder(jobId);
    if (r && r.ok) toast(`Change order created on ${unesc((state.jobs.find(j => j.id === jobId) || {}).name || "the job")}. It stays unbilled until signed.`);
  },
  "goto-packet": (el, id) => nav("packet-detail", id),
  "open-packet": (el) => nav("packet-detail", el.dataset.job, el.dataset.inv),
  "open-co": (el) => nav("co-detail", el.dataset.job, el.dataset.co),
  "open-payapp": (el) => {
    nav("payapp-detail", el.dataset.job, el.dataset.inv);
    if (el.dataset.focus) requestAnimationFrame(() => document.querySelector(el.dataset.focus)?.scrollIntoView({ block: "start" }));
  },
  "open-waiver": (el, id) => nav("waiver-detail", id),
  "attach": async (el, id) => {
    const name = document.querySelector("#attach-name")?.value || "attachment.pdf";
    await post(`/api/jobs/${id}/attach/${el.dataset.item}`, { name });
  },
  "approve-co": async (el, id) => {
    const recordId = document.getElementById(`cosign-rec-${el.dataset.co}`)?.value || "";
    if (!recordId) { toast("Choose the record that holds the signed copy, or take a photo of it."); return; }
    const r = await post(`/api/jobs/${id}/approve-co/${el.dataset.co}`, { record_id: recordId });
    if (r && r.ok) { formOpen[`cosign-${el.dataset.co}`] = false; render(); }
  },
  "request-co-signature": (el, id) => post(`/api/jobs/${id}/request-co-signature/${el.dataset.co}`),
  "packet-detach": (el, id) => post(`/api/jobs/${id}/detach/${el.dataset.item}`),
  "packet-skip": (el, id) => post(`/api/jobs/${id}/packet-skip/${el.dataset.item}`, {}),
  "attach-record": async (el, id) => {
    const recordId = document.querySelector(`#attach-rec-${el.dataset.item}`)?.value;
    if (!recordId) { toast("Choose a record first."); document.querySelector(`#attach-rec-${el.dataset.item}`)?.focus(); return; }
    await post(`/api/jobs/${id}/attach-record/${el.dataset.item}`, { record_id: recordId });
  },
  "payapp-filter": (el) => { payappFilter = el.dataset.filter; render(); },
};
