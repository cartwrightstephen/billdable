// The job page: its next step and forms (progress, mark done, record a payment, change orders),
// the money, pay apps and change orders, the job record, the schedule of values and the history.
import { esc, unesc, money, centsFrom, percentFrom, dollarsText, fmtDate, fmtDateTime, words, INVOICE_WORDS, invoicePill, emailStatusHtml } from "../format.js";
import { RECORD_KINDS, isBlocked, nextStep, jobActions as jobActionsFor, ACTION_GROUPS, closeBlockerActions, mayTake, waitingText, retainageNote, closeoutMissing as closeoutMissingCount, statusPill, coSignature, coStatusWords, billedWork, progressFigures, shortPayable, shortPayLabel, shortPayAttrs } from "../jobs.js";
import { sovNum, sovMilli, sovCents, sovExtend, sovMarkup, fmtQty, blankSovLine, sovLineProblem, sovDraftLockHtml, sovProgressHtml } from "../sov.js";
import { pathFor } from "../routes.js";
import { readJson, setNetBanner } from "../net.js";
import { moreButton, askConfirm } from "../dom.js";
import { field, moneyField, formErrorLine, refuseForm, formValues, formErrors, formDraft, draftValue, clearForm } from "../forms.js";
import { termText, actionWords } from "../terms.js";
import { state, me, currentJobId, formOpen } from "../state.js";
import { may, listPage, DEMO_COMPANY_ID, perms, post, toast, render, jobDetailLoading, backButton, emptyState, nav, moreOpen, toggleForm } from "../app-core.js";
import { jobFields } from "./jobs.js";
import { pasteBoxHtml } from "./inbox.js";

// Notes typed with progress or Mark done go on that pay app and its PDF (R4-12).
const NOTES_HELP = "Printed under the lines on this pay app and its PDF, so the customer sees it.";
let progressMode = "period";   // the progress form takes this draw $ (default), the total done so far $, or % complete (R3-01)
// The change order just made from the job page's New change order card, shown there with
// "Request signature" in place of the form (R3-06): {job, co}.
let newCoMade = null;
export let sovDraft = null; // schedule-of-values editor: { jobId, lines: [{id,item,description,quantity,unit,rate}], op_pct, warnings } (raw text, not escaped)

// Change orders are approved by a signature: online through a link the payer opens,
// or a signed copy on the job record (P2-3). There is no bare "approve" any more.
export function coSignControls(j, co, extra = "") {
  if (co.status !== "pending" || j.closed) return "";
  const key = `cosign-${co.id}`;
  const records = j.records || [];
  // The signed copy is most likely the newest change-order record (R3-06).
  const newestCo = records.filter(r => r.kind === "change_order").pop();
  return `<div class="form-inline">
      <button class="btn small" data-action="request-co-signature" data-id="${j.id}" data-co="${co.id}">${co.signing_page ? "Send the signing link again" : "Request signature"}</button>
      <button class="btn small secondary" data-action="toggle-form" data-form="${key}" aria-expanded="${!!formOpen[key]}">Signed on paper…</button>
      <button class="btn small secondary" data-action="withdraw-co" data-id="${j.id}" data-co="${co.id}">Withdraw</button>
      ${extra}
    </div>
    ${co.signing_page ? `<p class="sub">Signing page: <a href="/api/jobs/${j.id}/co/${co.id}/page" target="_blank" rel="noopener">open it</a>${co.signature_requested_at ? ` · requested ${fmtDate(co.signature_requested_at)}` : ""}</p>` : ""}
    ${formOpen[key] ? `<div class="form-inline">
        <button class="btn small" data-action="pick-files" data-job="${j.id}" data-co="${co.id}" data-kind="change_order" data-capture="1">Take a photo of the signed copy</button>
        ${records.length ? `<label for="cosign-rec-${co.id}" class="inline-label">or pick it from the job record</label><select id="cosign-rec-${co.id}"><option value="">Choose a record…</option>${records.map(r => `<option value="${r.id}"${newestCo && r.id === newestCo.id ? " selected" : ""}>${r.label}</option>`).join("")}</select><button class="btn small secondary" data-action="approve-co" data-id="${j.id}" data-co="${co.id}">Save as signed</button>` : ""}
      </div>` : ""}`;
}

// Who a customer email goes to, for a confirmation: "Sam Jones · ap@jones.example" (plain text).
export function sendsTo(j) {
  const p = j.payer || {};
  return unesc(`${p.approver || p.name || "the customer"}${p.ap_email ? " · " + p.ap_email : ""}`);
}

// What is owed on a pay app: "$13,725.00", or "$2,000.00 of $5,000.00" once part is paid (R3-02).
export function owedText(i) {
  const left = i.remaining_cents ?? i.amount_cents;
  return left < i.amount_cents ? `${money(left)} of ${money(i.amount_cents)}` : money(i.amount_cents);
}

// Accept short pay (R4-01): why it was short, then Accept. On the job page and the pay app's page.
export function shortPayCardHtml(j, i) {
  const key = `shortpay-${i.id}`;
  if (!formOpen[key]) return "";
  return `<div class="card form-card short-pay" data-form-key="${key}"><h2>Accept short pay on ${i.number}</h2>
    <p class="sub">${i.number} was ${money(i.amount_cents)} and ${money(i.amount_cents - i.remaining_cents)} came in. Accepting marks it paid: the ${money(i.remaining_cents)} left is no longer owed, and its lien waiver comes due.</p>
    ${formErrorLine(key)}<div class="form-row">
      ${field({ form: key, name: "reason", id: `shortpay-reason-${i.id}`, label: "Why it was paid short", required: true, placeholder: "Backcharge for cleanup", wide: true })}
      <button class="btn small" data-action="accept-short-pay" data-id="${j.id}" data-inv="${i.id}">Accept short pay</button>
      <button class="btn small secondary" data-action="toggle-form" data-form="${key}">Cancel</button>
    </div></div>`;
}

// The short-pay buttons for a job's part-paid pay apps, naming the pay app when there are several.
export function shortPayButtons(j) {
  const list = may("accept-short-pay") ? shortPayable(j) : [];
  return list.map(i => `<button class="btn small secondary" ${shortPayAttrs(j, i)}>${list.length > 1 ? i.number + ": " : ""}${shortPayLabel(i)}</button>`).join(" ");
}

// "Spoke to them…" (R4-06): what the customer said, and when to check again.
function contactFormHtml(j) {
  return `<div class="card form-card" data-form-key="contact"><h2>Spoke to ${j.payer.approver || j.payer.name}</h2>${formErrorLine("contact")}<div class="form-row">
    ${field({ form: "contact", name: "text", id: "contact-text", label: "What did they say?", required: true, placeholder: "Check goes out Friday", wide: true })}
    ${field({ form: "contact", name: "next_on", id: "contact-next", label: "Check again on (optional)", type: "date", attrs: `min="${todayIso()}"` })}
    <button class="btn small" data-action="note-contact" data-id="${j.id}">Save</button>
    <button class="btn small secondary" data-action="toggle-form" data-form="contact">Cancel</button></div>
    <p class="sub">Kept on the job record. It counts as a follow-up; with a date, Today waits until then.</p></div>`;
}

// Today on this device, as a date box wants it (YYYY-MM-DD).
function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// The date a new pay app runs to (R4-04): today, or the last sent pay app's when that is later
// (domain.period_to), which is also the earliest the box takes.
function lastPeriod(j) {
  return (j.invoices || []).filter(i => i.period_to && !["void", "draft", "awaiting_approval"].includes(i.status)).map(i => unesc(i.period_to)).sort().pop() || "";
}
function periodField(form, j) {
  const last = lastPeriod(j), today = todayIso();
  return field({ form, name: "period_to", id: `${form}-period`, label: "Period to", helpKey: "period_to", type: "date",
    value: last > today ? last : today, attrs: last ? `min="${last}"` : "" });
}

// The box the progress form shows for its mode, by request key.
const progressInput = () => progressMode === "percent" ? "percent" : progressMode === "total" ? "completed_cents" : "period_cents";

// The progress form's "This pay app: ..." line, from what is typed (R3-01).
function progressPreviewText(j, typed = draftValue("progress", progressInput(), "")) {
  const head = termText("This pay app");
  if (!String(typed).trim()) return `${head}: enter an amount above.`;
  const f = progressFigures(j, progressMode, typed);
  if (f.error) return `${head}: ${f.error}`;
  return f.retained ? `${head}: ${money(f.gross)} less retainage ${money(f.retained)} = ${money(f.net)}.` : `${head}: ${money(f.net)}.`;
}

// The progress card for a job billed in dollars: where the job stands, three ways to say how
// far it got, what the pay app comes to, and "All of it" for the last one.
function progressFormHtml(j, openDraft) {
  const m = j.money;
  const base = billedWork(j);
  const pctBilled = m.contract_total_cents ? Math.round(base * 100 / m.contract_total_cents) : 0;
  const mode = (key, label) => `<button class="${progressMode === key ? "btn" : "btn secondary"} small" data-action="progress-mode" data-mode="${key}" aria-pressed="${progressMode === key}">${label}</button>`;
  const input = progressMode === "percent"
    ? field({ form: "progress", name: "percent", id: "progress-pct", label: "% complete", inputmode: "decimal", placeholder: "50", help: "How much of the whole contract is done." })
    : progressMode === "total"
      ? moneyField({ form: "progress", name: "completed_cents", id: "progress-amt", label: "Total done so far ($)", help: "All the work done to date, not just this draw." })
      : moneyField({ form: "progress", name: "period_cents", id: "progress-draw", label: "This draw ($)", help: "The work done since the last pay app, before retainage." });
  return `<div class="card form-card" data-form-key="progress"><h2>${termText("Progress pay app")}</h2>
    <p class="sub progress-context">Billed so far ${money(base)} of ${money(m.contract_total_cents)} contract (${pctBilled}%).</p>
    ${openDraft && openDraft.pencil_status !== "sent" ? `<p class="sub">This replaces ${openDraft.number} (${money(openDraft.amount_cents)}). Its number and paperwork stay.</p>` : ""}
    <div class="filter-bar progress-modes" role="group" aria-label="Enter">${mode("period", "This draw $")}${mode("total", "Total done so far $")}${mode("percent", "% complete")}</div>
    ${formErrorLine("progress")}<div class="form-row">
      ${input}
      ${periodField("progress", j)}
      ${field({ form: "progress", name: "notes", id: "progress-notes", label: "What was completed (optional)", wide: true, help: NOTES_HELP })}
    </div>
    <p class="progress-preview" id="progress-preview" aria-live="polite">${progressPreviewText(j)}</p>
    <div class="form-inline">
      <button class="btn small" data-action="progress" data-id="${j.id}">${termText("Draft progress pay app")}</button>
      <button class="linkish" data-action="progress-all">All of it: mark the job done</button>
    </div></div>`;
}

// The pay app's latest email didn't go out (light_snapshot's email_failures).
export function emailFailed(invoiceId) {
  return (state.email_failures || []).some(e => e.payapp_invoice_id === invoiceId);
}

// What happened on the job, newest first (UI/UX Option B): actions from the audit trail and
// emails with what became of them, 50 at a time.
function jobHistoryHtml(j) {
  const page = listPage("timeline", j.id);
  const rows = page ? page.entries : [];
  return `<div class="card job-history"><h2>History</h2>
    ${rows.length ? `<ol class="timeline">${rows.map(e => `<li>
        <span class="pill${e.kind === "email" ? " copper" : ""}">${e.kind === "email" ? "Email" : actionWords(e.action)}</span> ${e.text}
        <div class="sub">${fmtDateTime(e.at)}${e.who ? " · " + e.who : ""}${e.kind === "email" ? ` · to ${e.to} · ${emailStatusHtml(e)}` : ""}</div>
      </li>`).join("")}</ol>${moreButton("timeline", page)}`
      : !page ? `<p class="sub">Loading…</p>` : `<p class="sub">Nothing has happened on this job yet.</p>`}
  </div>`;
}

// The job page's buttons come from jobs.js (R3-04); the sample company also gets the QuickBooks sample.
function jobActions(j) {
  return jobActionsFor(j, { demo: !!(me && me.company_id === DEMO_COMPANY_ID), perms: perms() });
}

// One job-page button: the label, and why on a second line (no tooltip, so a phone shows it too).
function actionButton(a) {
  return `<button class="btn secondary two-line" ${a.attrs}><span>${a.label}</span><span class="sub">${a.why || ""}</span></button>`;
}

// The progress form's preview follows what is typed, without a redraw.
export function updateProgressPreview() {
  const j = state.jobs.find(x => x.id === currentJobId);
  const line = document.getElementById("progress-preview");
  const box = document.querySelector(`[data-form-key="progress"] [data-field="${progressInput()}"]`);
  if (j && line && box) line.textContent = unesc(progressPreviewText(j, box.value));
}

// Picking another pay app in Record a payment fills in what is still owed on it.
export function prefillPaymentAmount(invoiceId) {
  const j = state.jobs.find(x => x.id === currentJobId);
  const inv = j && j.invoices.find(i => unesc(i.id) === invoiceId);
  const box = document.getElementById("match-amt");
  if (!inv || !box) return;
  box.value = dollarsText(inv.remaining_cents ?? inv.amount_cents);
  (formDraft.match ||= {}).amount_cents = box.value;
}

// The job page's New change order card (R3-06). Once saved, it shows the change order with the
// ways to get it signed in place of the form.
function newCoCardHtml(j) {
  const made = newCoMade && newCoMade.job === j.id && j.change_orders.find(c => c.id === newCoMade.co);
  if (made) return `<div class="card form-card new-co-card"><h2>CO ${made.number}: ${made.title}</h2>
    <p class="sub">${made.amount_cents < 0 ? `A credit of ${money(-made.amount_cents)}` : money(made.amount_cents)}${made.schedule_days ? ` · ${made.schedule_days > 0 ? "+" : ""}${made.schedule_days} days on the schedule` : ""}. ${made.status === "pending" ? "Saved. Now get it signed." : "Signed."}</p>
    ${made.status === "pending" ? coSignControls(j, made) : coSignature(made)}
    <div class="form-inline"><button class="btn small secondary" data-action="new-co-done">Done</button></div></div>`;
  return `<div class="card form-card" data-form-key="newco"><h2>New change order</h2>${formErrorLine("newco")}<div class="form-row">
      ${coFormFields()}
      <button class="btn small" data-action="add-co-here" data-id="${j.id}">Create change order</button>
      <button class="btn small secondary" data-action="new-co-done">Cancel</button>
    </div></div>`;
}

// The New change order boxes (job page, Change orders tab, a job with none yet). A negative amount
// is a credit (R3-06).
export function coFormFields() {
  return `${field({ form: "newco", name: "title", id: "co-title", label: "Title", placeholder: "Add two outlets in the kitchen" })}
    ${moneyField({ form: "newco", name: "amount_cents", id: "co-amt", label: "Amount ($), negative for a credit", inputmode: "text" })}
    ${field({ form: "newco", name: "description", id: "co-desc", label: "What changed (optional)", wide: true })}
    ${field({ form: "newco", name: "schedule_days", id: "co-days", label: "Schedule change, days (optional)", inputmode: "numeric", placeholder: "3" })}`;
}

// The New change order form: check it here, then save.
export async function addChangeOrder(jobId) {
  const v = formValues("newco");
  const amount = centsFrom(v.amount_cents);
  const days = String(v.schedule_days || "").trim();
  const errors = {};
  if (!v.title) errors.title = "Enter a title for the change order.";
  if (amount.error || amount.cents === 0) errors.amount_cents = "Enter the change order amount in dollars, like 1,250, or -1,250 for a credit.";
  if (days && !/^[-+]?\d{1,3}$/.test(days)) errors.schedule_days = "Enter the schedule change in whole days, like 3, or leave it blank.";
  if (refuseForm("newco", errors)) return null;
  return post(`/api/jobs/${jobId}/add-co`, { title: v.title, amount_cents: amount.cents, description: v.description, schedule_days: days ? Number(days) : 0 }, { formKey: "newco" });
}

function sovStartEdit(j) {
  const v = j.sov_view || {};
  const base = (v.lines || []).filter(l => !l.co_id);
  sovDraft = {
    jobId: j.id,
    lines: base.length
      ? base.map(l => ({ id: l.id, item: unesc(l.item), description: unesc(l.description), quantity: String(l.qty_milli / 1000), unit: unesc(l.unit), rate: (l.rate_cents / 100).toFixed(2) }))
      : [blankSovLine(), blankSovLine(), blankSovLine()],
    op_pct: v.op_bp ? String(v.op_bp / 100) : "",
    warnings: [],
  };
}

function sovFilledLines() {
  return sovDraft.lines.filter(l => [l.description, l.quantity, l.rate].some(x => String(x || "").trim()));
}

function sovDraftTotals() {
  let base = 0, bad = 0, noPrice = 0;
  const amounts = sovDraft.lines.map(l => {
    const filled = [l.description, l.quantity, l.rate].some(x => String(x || "").trim());
    if (!filled) return null;
    const problem = sovLineProblem(l);
    if (problem === "price") { bad++; noPrice++; return "price"; }
    if (problem) { bad++; return NaN; }
    const q = String(l.quantity || "").trim() ? sovMilli(l.quantity) : 1000;
    const r = sovCents(l.rate);
    const a = sovExtend(q, r);
    base += a;
    return a;
  });
  const bpRaw = String(sovDraft.op_pct || "").trim();
  const bp = bpRaw ? Math.round(sovNum(bpRaw) * 100) : 0;
  const op = isNaN(bp) ? 0 : sovMarkup(base, bp);
  return { amounts, base, bp, op, total: base + op, bad, noPrice, badBp: isNaN(bp) || bp < 0 || bp > 5000 };
}

export function updateSovTotals() {
  if (!sovDraft) return;
  const j = state.jobs.find(x => x.id === sovDraft.jobId);
  if (!j) return;
  const t = sovDraftTotals();
  t.amounts.forEach((a, i) => {
    const el = document.getElementById(`sov-amt-${i}`);
    if (el) el.textContent = a === null ? "" : a === "price" ? "needs a price" : isNaN(a) ? "check" : money(a);
  });
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set("sov-sum-lines", money(t.base));
  set("sov-sum-op", t.badBp ? "check the percent" : money(t.op));
  set("sov-sum-total", money(t.total));
  const diff = t.total - j.contract_cents;
  set("sov-sum-diff", diff === 0 ? "matches the contract" : `${diff > 0 ? "+" : "−"}${money(Math.abs(diff))} vs the contract`);
  const d = document.getElementById("sov-sum-diff");
  if (d) d.className = diff === 0 ? "ok" : "blocker";
  const alt = document.querySelector('[data-action="sov-save-contract"]');
  if (alt) {
    alt.hidden = diff === 0 || t.base === 0;
    alt.textContent = `Save and change the contract to ${money(t.total)}`;
  }
}

function sovEditorHtml(j) {
  const rows = sovDraft.lines.map((l, i) => `
    <tr>
      <td class="sov-item-cell"><input class="sov-in sov-item" data-sov-row="${i}" data-sov-field="item" value="${esc(l.item)}" placeholder="${i + 1}" aria-label="Line ${i + 1}: item"></td>
      <td class="sov-desc-cell"><input class="sov-in sov-desc" data-sov-row="${i}" data-sov-field="description" value="${esc(l.description)}" placeholder="Rough-in wiring" aria-label="Line ${i + 1}: description"></td>
      <td><input class="sov-in sov-num" data-sov-row="${i}" data-sov-field="quantity" value="${esc(l.quantity)}" placeholder="1" inputmode="decimal" aria-label="Line ${i + 1}: quantity"></td>
      <td><input class="sov-in sov-unit" data-sov-row="${i}" data-sov-field="unit" value="${esc(l.unit)}" placeholder="LS" aria-label="Line ${i + 1}: unit"></td>
      <td><input class="sov-in sov-num${l.needsPrice ? " sov-need-price" : ""}" data-sov-row="${i}" data-sov-field="rate" value="${esc(l.rate)}" placeholder="${l.needsPrice ? "Price?" : "0.00"}" inputmode="decimal" aria-label="Line ${i + 1}: unit rate, dollars"></td>
      <td class="num sov-amt" id="sov-amt-${i}"></td>
      <td class="sov-remove-cell"><button class="btn small secondary" data-action="sov-remove" data-row="${i}" aria-label="Remove line ${i + 1}">×</button></td>
    </tr>`).join("");
  return `
    <p class="sub">Each line is a quantity at a unit rate; a lump sum is quantity 1 (unit LS) at its amount. Overhead and profit goes on top of the base lines. Approved change orders add their own lines.</p>
    <div class="sov-paste">
      <textarea id="sov-paste" rows="3" placeholder="Paste rows from Excel, Google Sheets, a CSV or a PDF: description, quantity, unit, unit rate, or a lump-sum amount. A header row is optional; total rows are skipped."></textarea>
      <button class="btn small" data-action="sov-read">Read pasted rows</button>
    </div>
    ${sovDraft.warnings.length ? `<div class="sov-warn">${sovDraft.warnings.map(w => `<div>${esc(w)}</div>`).join("")}</div>` : ""}
    <div class="table-wrap sov-wrap"><table class="sov-table sov-edit">
      <tr><th>Item</th><th>Description</th><th>Qty</th><th>Unit</th><th>Unit rate $</th><th>Scheduled value</th><th></th></tr>
      ${rows}
    </table></div>
    <div class="form-inline">
      <button class="btn small secondary" data-action="sov-add">Add line</button>
      <label for="sov-op" class="sov-op-label">Overhead and profit</label>
      <input id="sov-op" class="sov-in sov-num" data-sov-field="op_pct" value="${esc(sovDraft.op_pct)}" placeholder="0" inputmode="decimal"> %
    </div>
    <div class="sov-sums">
      <div class="money-row"><span class="label">Schedule lines</span><span id="sov-sum-lines"></span></div>
      <div class="money-row"><span class="label">Overhead and profit</span><span id="sov-sum-op"></span></div>
      <div class="money-row"><span class="label">Schedule total</span><span><strong id="sov-sum-total"></strong> · <span id="sov-sum-diff"></span></span></div>
      <div class="money-row"><span class="label">Contract</span><span>${money(j.contract_cents)}</span></div>
    </div>
    <div class="form-inline">
      <button class="btn" data-action="sov-save" data-id="${j.id}">Save schedule</button>
      <button class="btn secondary" data-action="sov-save-contract" data-id="${j.id}" hidden>Save and change the contract</button>
      <button class="btn secondary" data-action="sov-cancel">Cancel</button>
    </div>`;
}

function sovCardHtml(j) {
  const v = j.sov_view || {};
  if (sovDraft && sovDraft.jobId === j.id) {
    return `<div class="card" id="sov-card"><h2>Schedule of values</h2>${sovEditorHtml(j)}</div>`;
  }
  if (!v.present) {
    // No schedule and a pay app already exists. While that pay app is only a draft, one quiet line
    // says how to set a schedule up (Oct 23 note 8); otherwise the card stays hidden.
    if (!v.editable) {
      const note = sovDraftLockHtml(j, mayTake("void-draft", perms()));
      return note ? `<div class="card" id="sov-card"><h2>Schedule of values</h2>${note}</div>` : "";
    }
    return `<div class="card" id="sov-card">
      <h2>Schedule of values</h2>
      <p class="sub">Bill line by line, the way subs bill: quantity × unit rate plus overhead and profit. ${termText("Each pay app then shows what was done on each line this period. Optional; without it, progress is entered in dollars. Set it before the first pay app.")}</p>
      <button class="btn secondary" data-action="sov-start" data-id="${j.id}">Set up schedule of values</button>
    </div>`;
  }
  // Fixed only because of an unsent draft: say so and offer Delete draft (Oct 23 note 8).
  const lockNote = v.editable ? "" : sovDraftLockHtml(j, mayTake("void-draft", perms()));
  const t = v.totals;
  const rows = v.lines.map(l => `
    <tr>
      <td>${l.item}</td>
      <td class="sov-desc-cell">${l.description}${l.co_id ? ' <span class="pill green">CO</span>' : ""}</td>
      <td class="num">${l.lump_sum ? "" : fmtQty(l.qty_milli) + " " + l.unit}</td>
      <td class="num">${l.lump_sum ? "LS" : money(l.rate_cents)}</td>
      <td class="num">${money(l.scheduled_cents)}</td>
      <td class="num">${l.lump_sum ? l.pct + "%" : fmtQty(l.done_milli)}${l.stored_cents ? `<div class="sub">+ ${money(l.stored_cents)} stored</div>` : ""}</td>
      <td class="num">${money(l.to_date_cents)}</td>
      <td class="num">${money(l.billed_cents)}</td>
      <td class="num">${money(l.scheduled_cents - l.to_date_cents)}</td>
    </tr>`).join("");
  return `<div class="card" id="sov-card">
    <h2>Schedule of values</h2>
    <div class="table-wrap sov-wrap"><table class="sov-table">
      <tr><th>Item</th><th>Description</th><th>Qty</th><th>Unit rate</th><th>Scheduled value</th><th>Done to date</th><th>Earned to date</th><th>Billed</th><th>Balance to finish</th></tr>
      ${rows}
      ${v.op_bp ? `<tr><td></td><td class="sov-desc-cell">Overhead and profit ${v.op_bp / 100}%</td><td></td><td></td><td class="num">${money(t.op_scheduled_cents)}</td><td></td><td class="num">${money(t.op_to_date_cents)}</td><td class="num">${money(v.op_billed_cents)}</td><td class="num">${money(t.op_scheduled_cents - t.op_to_date_cents)}</td></tr>` : ""}
      <tr class="sov-total"><td></td><td class="sov-desc-cell">Total</td><td></td><td></td><td class="num">${money(t.scheduled_cents)}</td><td></td><td class="num">${money(t.to_date_cents)}</td><td class="num">${money(v.lines.reduce((a, l) => a + l.billed_cents, 0) + v.op_billed_cents)}</td><td class="num">${money(t.scheduled_cents - t.to_date_cents)}</td></tr>
    </table></div>
    ${v.editable ? `<button class="btn small secondary" data-action="sov-start" data-id="${j.id}">Edit schedule</button>`
      : lockNote ? lockNote
      : `<p class="sub">Fixed once the first pay app exists. Scope changes go through change orders, which add their own lines when approved.</p>`}
  </div>`;
}

async function sovReadPaste() {
  const box = document.getElementById("sov-paste");
  if (!box || !sovDraft) return;
  const text = box.value;
  if (!text.trim()) { toast("Paste rows into the box first."); return; }
  let res;
  try {
    res = await fetch("/api/sov/parse", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
  } catch (_) { setNetBanner(true); toast("Can't reach Duracash."); return; }
  const data = await readJson(res);
  if (!res.ok) { toast(data.error || "Couldn't read the rows."); return; }
  const kept = sovFilledLines();
  sovDraft.lines = kept.concat((data.lines || []).map(l => ({ id: "", item: l.item, description: l.description, quantity: l.quantity, unit: l.unit, rate: l.rate, needsPrice: !!l.needs_price })));
  if (!sovDraft.lines.length) sovDraft.lines = [blankSovLine()];
  if (data.op_pct && !String(sovDraft.op_pct || "").trim()) sovDraft.op_pct = data.op_pct;
  sovDraft.warnings = [`Read ${(data.lines || []).length} line${(data.lines || []).length === 1 ? "" : "s"}. Check them below, then save.`].concat(data.warnings || []);
  render();
}

async function sovSave(jobId, updateContract) {
  const t = sovDraftTotals();
  if (t.noPrice && t.noPrice === t.bad) { toast(t.noPrice === 1 ? "One line needs a price. Enter its unit rate, then save." : `${t.noPrice} lines need a price. Enter their unit rates, then save.`); return; }
  if (t.bad) { toast("Some lines need a description, a quantity above zero and a rate above zero."); return; }
  const j = state.jobs.find(x => x.id === jobId);
  if (updateContract && !(await askConfirm(`Change the contract amount from ${money(j.contract_cents)} to ${money(t.total)} to match the schedule?`, "Change the contract"))) return;
  const lines = sovFilledLines().map(l => ({ id: l.id || undefined, item: l.item, description: l.description, quantity: l.quantity, unit: l.unit, rate: l.rate }));
  const r = await post(`/api/jobs/${jobId}/sov`, { lines, op_pct: sovDraft.op_pct, update_contract: !!updateContract });
  if (r && r.ok) { sovDraft = null; render(); }
}

async function sovProgress(jobId) {
  const done = {};
  document.querySelectorAll("[data-sovp]").forEach(el => {
    if (el.disabled) return;
    const raw = el.value.trim();
    if (!raw) return;
    if (el.dataset.lump === "1") {
      const pct = sovNum(raw);
      done[el.dataset.sovp] = isNaN(pct) ? raw : (Math.round(pct * 10) / 1000).toFixed(3);
    } else done[el.dataset.sovp] = raw;
  });
  const stored = {};
  document.querySelectorAll("[data-sovs]").forEach(el => {
    if (el.disabled) return;
    const raw = el.value.trim();
    const line = (state.jobs.find(j => j.id === jobId)?.sov_view?.lines || []).find(l => l.id === el.dataset.sovs);
    const before = line && line.stored_cents ? (line.stored_cents / 100).toFixed(2) : "";
    if (raw !== before) stored[el.dataset.sovs] = raw || "0";
  });
  const notes = document.querySelector("#progress-notes")?.value || "";
  const r = await post(`/api/jobs/${jobId}/sov-progress`, { done, stored, notes, period_to: formValues("sovprogress").period_to || "" }, { formKey: "sovprogress" });
  if (r && r.ok) { formOpen.progress = false; render(); }
}

document.addEventListener("input", (e) => {
  const el = e.target;
  if (!sovDraft || !el || !el.dataset || !el.dataset.sovField) return;
  if (el.dataset.sovField === "op_pct") sovDraft.op_pct = el.value;
  else if (sovDraft.lines[+el.dataset.sovRow]) sovDraft.lines[+el.dataset.sovRow][el.dataset.sovField] = el.value;
  updateSovTotals();
});
document.addEventListener("paste", (e) => {
  if (e.target && e.target.id === "sov-paste") setTimeout(sovReadPaste, 0);
});

// A record on the job page, with Edit for its label and kind (R5-07). One open at a time.
let editingRecord = "";
function recordRow(j, r) {
  if (editingRecord === r.id && !j.closed) {
    return `<div class="list-item record-edit">
      <label class="inline-label" for="rec-edit-label-${r.id}">What it is</label>
      <input id="rec-edit-label-${r.id}" value="${r.label}" maxlength="120">
      <label class="inline-label" for="rec-edit-kind-${r.id}">Kind</label>
      <select id="rec-edit-kind-${r.id}">${Object.entries(RECORD_KINDS).map(([k, v]) => `<option value="${k}"${r.kind === k ? " selected" : ""}>${termText(v)}</option>`).join("")}</select>
      <button class="btn small" data-action="record-save" data-id="${j.id}" data-item="${r.id}">Save</button>
      <button class="btn small secondary" data-action="record-edit" data-item="">Cancel</button>
    </div>`;
  }
  return `<div class="list-item"><span class="pill">${termText(RECORD_KINDS[r.kind] || r.kind)}</span> ${r.label}${r.note ? ` <span class="sub">— ${r.note.length > 240 ? r.note.slice(0, 240) + "…" : r.note}</span>` : ""}${j.closed ? "" : ` <button class="linkish" data-action="record-edit" data-item="${r.id}" aria-label="Edit ${r.label}">Edit</button>`}${recordFiles(r) ? `<div class="sub">${recordFiles(r)}</div>` : ""}${r.source || r.added_at ? `<div class="sub">${r.source ? r.source + " · " : ""}${fmtDate(r.added_at)}</div>` : ""}</div>`;
}

function recordFiles(r) {
  return (r.file_ids || []).map(fid => {
    const f = (state.files || []).find(x => x.id === fid);
    return f ? `<a href="/api/files/${f.id}" target="_blank" rel="noopener">${f.name}</a>` : "";
  }).filter(Boolean).join(" · ");
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  job() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j && jobDetailLoading === currentJobId) return backButton("jobs", "Jobs") + `<p class="sub">Loading the job…</p>`;
    if (!j) return backButton("jobs", "Jobs") + emptyState("Job not found", "It may have been deleted, or the link belongs to another company.", `<button class="btn" data-action="nav" data-view="jobs">Go to Jobs</button>`);
    const m = j.money;
    const blocked = isBlocked(j);
    const openDraft = j.invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
    const openForPay = j.invoices.filter(i => ["sent", "approved", "partial"].includes(i.status));
    const closeout = j.closeout_packet || [];
    const closeoutMissing = closeoutMissingCount(j);
    const { primary, more } = jobActions(j);
    const rest = more.slice(2);
    const waiting = waitingText(j);
    const retNote = retainageNote(j);
    const hasPayApps = j.invoices.some(i => i.status !== "void");
    const ejf = jobFields("editjob", "ej", j);
    return `
      ${backButton("jobs", "Jobs")}
      <div class="job-head">
        <div>
          <h1>${j.name}</h1>
          <p class="sub">${statusPill(j)} ${j.code} · ${j.address} · ${j.payer.name}</p>
        </div>
        ${j.closed ? "" : `<button class="btn secondary small" data-action="toggle-form" data-form="editjob" aria-expanded="${!!formOpen.editjob}">${formOpen.editjob ? "Cancel" : "Edit job"}</button>`}
      </div>
      ${formOpen.editjob ? `
        <div class="card" data-form-key="editjob">
          <h2>Edit job</h2>
          ${formErrorLine("editjob")}
          <div class="form-grid">
            ${ejf.name}
            ${ejf.address}
            ${ejf.contract_number}
            ${hasPayApps || (j.sov_view && j.sov_view.present) ? "" : ejf.contract_cents}
            ${ejf.retainage_pct}
          </div>
          <p class="sub">${hasPayApps ? "The contract amount is fixed once a pay app exists; use a change order. A new retainage applies from the next pay app; those sent keep theirs. " : ""}The job code ${j.code} stays the same, because emails and texts are filed by it. Who pays you and where pay apps go are in <button class="linkish" data-action="nav" data-view="playbook">Customer settings</button>.</p>
          <div class="form-inline">
            <button class="btn" data-action="save-job" data-id="${j.id}">Save changes</button>
            ${j.can_delete && may("delete") ? `<button class="btn secondary danger-text" data-action="delete-job" data-id="${j.id}" data-name="${j.name}"${j.invoices.length ? ` data-drafts="${j.invoices.length}"` : ""}>Delete job</button>` : ""}
          </div>
        </div>` : ""}
      <section class="next-card" aria-label="Next step">
        <div class="next-text"><span class="eyebrow">Next step</span><strong>${waiting && (!primary || primary.key === "follow") ? waiting : nextStep(j)}</strong>${primary && primary.why ? `<span class="sub">${primary.why}</span>` : ""}</div>
        ${primary || more.length ? `<div class="next-actions">
          ${primary ? `<button class="btn primary-action" ${primary.attrs}>${primary.label}</button>` : ""}
          ${more.slice(0, 2).map(actionButton).join("")}
        </div>` : ""}
      </section>
      ${blocked ? `<div class="card blockers-card" role="note">
        <strong class="blocker">Blocking send:</strong>
        <ul class="blocker-list">${j.blockers.map(b => `<li>${b}</li>`).join("")}</ul>
        <details><summary>${termText("Customer sent a pay app back? Note why.")}</summary>
          <div class="form-inline">
            <input id="learn-reason" placeholder="For example: needs a revised schedule of values" class="grow">
            <button class="btn small secondary" data-action="learn" data-id="${j.id}">Save to customer settings</button>
          </div>
          <p class="sub">The reason is kept in this customer's settings and shown before the next send.</p>
        </details>
      </div>` : ""}
      ${formOpen.progress && j.sov_view && j.sov_view.present ? sovProgressHtml(j, periodField("sovprogress", j), formErrorLine("sovprogress")) : ""}
      ${formOpen.progress && !(j.sov_view && j.sov_view.present) ? progressFormHtml(j, openDraft) : ""}
      ${formOpen.pencilchanges ? `<div class="card form-card"><h2>Customer wants changes</h2><div class="form-inline"><input id="pencil-note" placeholder="What to change: quantities, change orders, missing items" class="grow"><button class="btn small" data-action="pencil-changes" data-id="${j.id}">Save</button></div></div>` : ""}
      ${formOpen.done ? `<div class="card form-card" data-form-key="done"><h2>Mark done</h2>${formErrorLine("done")}<div class="form-row">
        ${field({ form: "done", name: "notes", id: "done-notes", label: "Anything to note? (optional)", wide: true, help: NOTES_HELP })}
        ${periodField("done", j)}
        <button class="btn small" data-action="done" data-id="${j.id}">Confirm done</button></div></div>` : ""}
      ${formOpen.contact ? contactFormHtml(j) : ""}
      ${formOpen.match ? `<div class="card form-card" data-form-key="match"><h2>Record a payment</h2>${openForPay.length ? `${formErrorLine("match")}<div class="form-row">
        ${field({ form: "match", name: "invoice_id", id: "match-inv", label: "Pay app", options: openForPay.map(i => [unesc(i.id), `${unesc(i.number)} · ${owedText(i)} · ${INVOICE_WORDS[unesc(i.status)] || unesc(i.status)}`]) })}
        ${moneyField({ form: "match", name: "amount_cents", id: "match-amt", label: "Amount received ($)", cents: (openForPay.find(i => i.id === draftValue("match", "invoice_id", "")) || openForPay[0]).remaining_cents })}
        ${field({ form: "match", name: "received_on", id: "match-date", label: "Date received", type: "date", value: todayIso(), attrs: `max="${todayIso()}"` })}
        ${field({ form: "match", name: "reference", id: "match-ref", label: "Check or reference no. (optional)", placeholder: "CHK 2207" })}
        <button class="btn small" data-action="match" data-id="${j.id}">Record</button></div>
        ${shortPayable(j).length && may("accept-short-pay") ? `<p class="sub short-pay-row">Paid short, and the rest won't come? ${shortPayButtons(j)}</p>` : ""}` : `<p class="sub">No sent pay app is waiting for payment.</p>`}</div>` : ""}
      ${shortPayable(j).map(i => shortPayCardHtml(j, i)).join("")}
      ${formOpen.retpay ? `<div class="card form-card" data-form-key="retpay"><h2>Record retainage paid</h2>${formErrorLine("retpay")}<div class="form-row">
        ${moneyField({ form: "retpay", name: "amount_cents", id: "retpay-amt", label: "Retainage received ($)" })}
        ${field({ form: "retpay", name: "reference", id: "retpay-ref", label: "Reference", placeholder: "Check no. or QuickBooks id" })}
        <button class="btn small" data-action="retainage-paid" data-id="${j.id}">Record</button></div></div>` : ""}
      ${formOpen.newco && !j.closed ? newCoCardHtml(j) : ""}
      ${j.closed ? `<div class="card reopen-card" data-form-key="reopen"><p class="sub">This job is finished. Its money record is read-only; you can still add notes to the job record.</p>
        ${me && me.role === "owner" ? formOpen.reopen ? `${formErrorLine("reopen")}<div class="form-row">
          ${field({ form: "reopen", name: "reason", id: "reopen-reason", label: "Why reopen it?", required: true, placeholder: "A late payment came in", wide: true })}
          <button class="btn small" data-action="reopen" data-id="${j.id}">Reopen job</button>
          <button class="btn small secondary" data-action="toggle-form" data-form="reopen">Cancel</button></div>`
          : `<button class="btn small secondary" data-action="toggle-form" data-form="reopen" aria-expanded="false">Reopen job…</button>` : ""}</div>` : rest.length ? `
        <details class="more-actions-rest" data-more="jobactions"${moreOpen("jobactions") ? " open" : ""}><summary>More (${rest.length})</summary>
          ${ACTION_GROUPS.map(([g, title]) => {
            const list = rest.filter(a => a.group === g);
            return list.length ? `<div class="more-group"><h3>${title}</h3><div class="more-actions" role="group" aria-label="${title}">${list.map(actionButton).join("")}</div></div>` : "";
          }).join("")}
        </details>` : ""}
      ${!j.closed && j.work_status === "complete" && (j.close_blocker_items || []).length ? `
        <section class="card close-blockers" aria-labelledby="close-blockers-h">
          <h2 id="close-blockers-h">Before you can finish this job</h2>
          <ul class="close-blocker-list">${j.close_blocker_items.map(b => `<li data-blocker="${b.kind}"><span>${termText(b.text)}</span><span class="form-inline">${closeBlockerActions(j, b, perms()).map((a, n) => `<button class="btn small${n ? " secondary" : ""}" ${a.attrs}>${a.label}</button>`).join("")}</span></li>`).join("")}</ul>
        </section>` : ""}
      <div class="detail-grid">
        <details class="card money-file"${window.innerWidth >= 800 ? " open" : ""}>
          <summary><h2>Money</h2><span class="sub">${money(m.contract_total_cents)} contract · ${money(m.billed_cents)} billed · ${money(m.collected_cents)} received · ${money(m.outstanding_cents)} owed</span></summary>
          <div class="money-row"><span class="label">Contract</span><span>${money(m.contract_cents)}</span></div>
          <div class="money-row"><span class="label">Signed change orders</span><span>${money(m.approved_co_cents)}</span></div>
          <div class="money-row"><span class="label">Unsigned change orders</span><span>${money(m.pending_co_cents)}</span></div>
          <div class="money-row strong"><span class="label">Contract total</span><span>${money(m.contract_total_cents)}</span></div>
          <div class="money-row"><span class="label">Billed</span><span>${money(m.billed_cents)}</span></div>
          <div class="money-row"><span class="label" data-help="collected">Received</span><span>${money(m.collected_cents)}</span></div>
          ${m.short_paid_cents ? `<div class="money-row"><span class="label">Short pay accepted</span><span>${money(m.short_paid_cents)}</span></div>` : ""}
          <div class="money-row strong"><span class="label">Outstanding</span><span>${money(m.outstanding_cents)}</span></div>
          <div class="money-row"><span class="label">Done, not billed</span><span>${money(m.unbilled_completed_cents)}</span></div>
          <div class="money-row"><span class="label">Retainage held</span><span>${money(m.retainage_held_cents)}${j.retainage_pct ? " · " + j.retainage_pct + "%" : ""}</span></div>
          <div class="money-row"><span class="label">Retainage released</span><span>${money(m.retainage_released_cents)}</span></div>
          <div class="money-row"><span class="label">Held back, still owed</span><span>${money(m.retainage_open_cents)}</span></div>
          ${retNote ? `<p class="sub retainage-note">${closeoutMissing && j.work_status === "complete" ? `<a href="${pathFor("packet-detail", j.id, "closeout")}" data-action="open-packet" data-job="${j.id}" data-inv="closeout">${retNote}</a>` : `${retNote} <button class="linkish" data-action="nav" data-view="retainage">Ask for part of it</button>`}</p>` : ""}
        </details>
        <div class="card">
          <h2>${termText("Pay apps")}</h2>
          ${j.invoices.length ? j.invoices.filter(i => !i.replaced).map(i => {  // a draft replaced by a progress correction stays in the history only
            const pkt = i.packet || j.packet || [];
            const missing = pkt.filter(p => p.required && !p.present).length;
            const pencil = i.pencil_status ? ` · draft ${i.pencil_status === "sent" ? "with customer" : words(i.pencil_status)}${i.pencil_note ? ": " + i.pencil_note : ""}` : "";
            return `<div class="list-item row-link">
              <div><a href="${pathFor("payapp-detail", j.id, i.id)}" data-action="open-payapp" data-job="${j.id}" data-inv="${i.id}"><strong>${i.number}</strong></a> ${invoicePill(i)} <span class="num">${money(i.amount_cents)}</span>
              <div class="sub row-sub">${i.status === "void" ? (i.voided_reason ? "Pulled back: " + i.voided_reason : "Deleted") : missing ? `${missing} paperwork item${missing === 1 ? "" : "s"} missing` : "Paperwork complete"}${i.sent_at ? " · sent " + fmtDate(i.sent_at) : ""}${emailFailed(i.id) ? " · email failed" : ""}${i.received_at ? " · received " + fmtDate(i.received_at) : ""}${i.approved_by && i.sent_at ? " · sent by " + i.approved_by : ""}${i.due_on && ["sent", "approved", "partial"].includes(i.status) ? " · due " + fmtDate(i.due_on) : ""}${i.short_pay_cents ? " · short pay " + money(i.short_pay_cents) : ""}${pencil}</div></div>
            </div>`;
          }).join("") : `<p class="sub">${termText("No pay apps yet.")} ${j.work_status === "complete" ? "Draft one from the work not billed yet." : "Record progress or mark the job done to draft one."}</p>`}
          ${closeout.length ? `<div class="list-item row-link"><div><a href="${pathFor("packet-detail", j.id, "closeout")}" data-action="open-packet" data-job="${j.id}" data-inv="closeout"><strong>Closeout paperwork</strong></a> <span class="sub">needed before held-back money is paid · ${closeoutMissing ? closeoutMissing + " missing" : "complete"}</span></div></div>` : ""}
          <h2 class="h2-gap">Change orders</h2>
          ${j.change_orders.length ? j.change_orders.map(co => `
            <div class="list-item">CO ${co.number} · ${co.title} · ${money(co.amount_cents)} <span class="pill ${co.status === "approved" ? "green" : co.status === "withdrawn" ? "" : "amber"}">${coStatusWords(co.status)}</span>${co.included_in_billing ? ` <span class="sub">${termText("on a pay app")}</span>` : ""}
              ${coSignControls(j, co, !co.included_in_billing && !openDraft && co.amount_cents > 0 ? `<button class="btn small secondary" data-action="include-co" data-id="${j.id}" data-co="${co.id}">Bill now (send waits for signature)</button>` : "")}
            </div>`).join("") : `<p class="sub">No change orders. Add one if the scope changed.</p>`}
        </div>
      </div>
      <div class="card">
        <h2>Job record</h2>
        ${j.closed ? "" : `<div class="dropzone small" data-job="${j.id}">
          <strong>${(j.records || []).length ? "Add photos, PDFs, saved emails or spreadsheets" : "Nothing in the record yet. Add the contract, photos and tickets"}</strong>
          <div class="capture-buttons">
            <button class="btn small" data-action="pick-files" data-job="${j.id}" data-capture="1">Take a photo</button>
            <button class="btn small secondary" data-action="pick-files" data-job="${j.id}">Add a file</button>
          </div>
          <span class="sub pointer-only">Or drop files here or paste a screenshot.</span>
          <span class="sub">Forwarded emails and texts come in through the <button class="linkish" data-action="nav" data-view="inbox">Inbox</button>.</span>
        </div>
        <details class="paste-job"><summary>Paste an email, a text thread, or notes</summary>${pasteBoxHtml(j.id)}</details>`}
        ${(j.records || []).slice().reverse().map(r => recordRow(j, r)).join("")}
        <details class="add-record"${(j.records || []).length && !formErrors.record ? "" : " open"}><summary>Add a note to the record</summary>
          <div data-form-key="record">${formErrorLine("record")}<div class="form-row">
            ${field({ form: "record", name: "label", id: "rec-label", label: "What it is", placeholder: "Rough-in inspection passed" })}
            ${field({ form: "record", name: "note", id: "rec-note", label: "Note (optional)" })}
            ${field({ form: "record", name: "kind", id: "rec-kind", label: "Kind", options: Object.entries(RECORD_KINDS).map(([k, v]) => [k, termText(v)]) })}
            <button class="btn small" data-action="add-record" data-id="${j.id}">Add to record</button>
          </div></div>
        </details>
      </div>
      ${sovCardHtml(j)}
      ${jobHistoryHtml(j)}
    `;
  },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in app.js.
export const actions = {
  "draft": (el, id) => post(`/api/jobs/${id}/draft`),
  "sov-start": (el, id) => { sovStartEdit(state.jobs.find(x => x.id === id)); render(); },
  "sov-add": () => { sovDraft.lines.push(blankSovLine()); render(); document.querySelectorAll(".sov-desc")[sovDraft.lines.length - 1]?.focus(); },
  "sov-remove": (el) => { sovDraft.lines.splice(+el.dataset.row, 1); if (!sovDraft.lines.length) sovDraft.lines.push(blankSovLine()); render(); },
  "sov-read": () => sovReadPaste(),
  "sov-cancel": () => { sovDraft = null; render(); },
  "sov-save": (el, id) => sovSave(id, false),
  "sov-save-contract": (el, id) => sovSave(id, true),
  "sov-progress": (el, id) => sovProgress(id),
  // This draw goes to the server as period_cents, which it adds to what is billed; the total and
  // the percent go as the total done so far (R3-01).
  "progress": async (el, id) => {
    const j = state.jobs.find(x => x.id === id);
    const v = formValues("progress");
    const input = progressInput();
    const f = progressFigures(j, progressMode, v[input]);
    if (refuseForm("progress", f.error ? { [input]: f.error } : {})) return;
    await post(`/api/jobs/${id}/progress`, { [f.field]: f.field === "period_cents" ? f.period : f.completed, notes: v.notes, period_to: v.period_to || "" },
      { formKey: "progress", close: "progress" });
  },
  "progress-mode": (el) => { progressMode = el.dataset.mode; render(); document.querySelector('[data-form-key="progress"] [data-money], #progress-pct')?.focus(); },
  // "All of it": the Mark done form instead, which drafts the final pay app for the rest.
  "progress-all": () => { formOpen.progress = false; clearForm("progress"); formOpen.done = true; render(); document.getElementById("done-notes")?.focus(); },
  "done": async (el, id) => {
    const j = state.jobs.find(x => x.id === id);
    if (!(await askConfirm(`Mark ${unesc(j ? j.name : "the job")} done? ${termText("This bills the rest of the contract on a final pay app.")}`, "Mark done"))) return;
    const v = formValues("done");
    await post(`/api/jobs/${id}/done`, { notes: v.notes || "", period_to: v.period_to || "" }, { formKey: "done", close: "done" });
  },
  // "Spoke to them" (R4-06): a call counts as a follow-up; with a date, Today waits until then.
  "note-contact": async (el, id) => {
    const v = formValues("contact");
    if (refuseForm("contact", v.text ? {} : { text: "Enter what they said, like \"Check goes out Friday\"." })) return;
    await post(`/api/jobs/${id}/note-contact`, { text: v.text, next_on: v.next_on || "" }, { formKey: "contact", close: "contact" });
  },
  "spoke": (el, id) => {
    nav("job", id);
    formOpen.contact = true;
    render();
    requestAnimationFrame(() => document.getElementById("contact-text")?.focus());
  },
  "retainage-paid": async (el, id) => {
    const v = formValues("retpay");
    const amount = centsFrom(v.amount_cents);
    if (refuseForm("retpay", amount.error || amount.cents <= 0 ? { amount_cents: "Enter the retainage received in dollars, like 2,425." } : {})) return;
    await post(`/api/jobs/${id}/retainage-paid`, { amount_cents: amount.cents, reference: v.reference }, { formKey: "retpay", close: "retpay" });
  },
  "include-co": (el, id) => post(`/api/jobs/${id}/include-co/${el.dataset.co}`),
  "pencil": async (el, id) => {
    const j = state.jobs.find(x => x.id === id);
    const draft = j && j.invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
    if (j && !(await askConfirm(`Email ${unesc(draft ? draft.number : "the draft")} to ${sendsTo(j)} as a draft for their review?`, "Send the draft"))) return;
    await post(`/api/jobs/${id}/pencil`);
  },
  "pencil-agreed": (el, id) => post(`/api/jobs/${id}/pencil-response`, { agreed: true }),
  "pencil-changes": async (el, id) => {
    const note = (document.getElementById("pencil-note")?.value || "").trim();
    if (!note) { toast("Say what the customer wants changed."); document.getElementById("pencil-note")?.focus(); return; }
    formOpen.pencilchanges = false;
    await post(`/api/jobs/${id}/pencil-response`, { agreed: false, note });
  },
  "reopen": async (el, id) => {
    const v = formValues("reopen");
    if (refuseForm("reopen", v.reason ? {} : { reason: "Enter why you are reopening it, like a late payment came in." })) return;
    await post(`/api/jobs/${id}/reopen`, { reason: v.reason }, { formKey: "reopen", close: "reopen" });
  },
  "undo-done": async (el, id) => {
    const j = state.jobs.find(x => x.id === id);
    const draft = j && j.invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
    if (!(await askConfirm(`Undo done on ${unesc(j ? j.name : "the job")}? ${draft ? `The final draft ${unesc(draft.number)} is deleted and the` : "The"} job goes back to in progress at what is billed.`, "Undo done", true))) return;
    await post(`/api/jobs/${id}/undo-done`);
  },
  "void-draft": async (el, id) => {
    if (!(await askConfirm(termText("Delete the open draft pay app? You can draft it again from the job page."), "Delete draft", true))) return;
    await post(`/api/jobs/${id}/void-draft`);
  },
  "match": async (el, id) => {
    const v = formValues("match");
    const amount = centsFrom(v.amount_cents);
    const errors = {};
    if (amount.error || amount.cents <= 0) errors.amount_cents = "Enter the payment amount in dollars, like 9,500.";
    if (!v.received_on) errors.received_on = "Enter the date received.";
    if (refuseForm("match", errors)) return;
    await post(`/api/jobs/${id}/match-payment`, { amount_cents: amount.cents, reference: v.reference, invoice_id: v.invoice_id || "", received_on: v.received_on },
      { formKey: "match", close: "match" });
  },
  "new-co": () => { newCoMade = null; toggleForm("newco"); requestAnimationFrame(() => document.getElementById("co-title")?.focus()); },
  "new-co-done": () => { newCoMade = null; formOpen.newco = false; clearForm("newco"); render(); },
  "add-co-here": async (el, id) => {
    const r = await addChangeOrder(id);
    if (!(r && r.ok)) return;
    const j = state.jobs.find(x => x.id === id);
    const co = j && j.change_orders.reduce((a, c) => (!a || c.number > a.number ? c : a), null);
    if (co) { newCoMade = { job: id, co: co.id }; render(); }
  },
  "import-qbo": (el, id) => post(`/api/jobs/${id}/import-qbo`),
  "close": async (el, id) => {
    const j = state.jobs.find(x => x.id === id);
    if (!(await askConfirm(`Finish ${unesc(j ? j.name : "the job")}? It moves to Job history and no more payments can be recorded.`, "Finish job"))) return;
    await post(`/api/jobs/${id}/close`);
  },
  "add-evidence": async (el, id) => {
    const label = document.querySelector("#ev-label")?.value;
    if (label) await post(`/api/jobs/${id}/add-evidence`, { label, kind: "note" });
  },
  "learn": async (el, id) => {
    const reason = document.querySelector("#learn-reason")?.value || "";
    await post(`/api/jobs/${id}/learn-rejection`, { reason });
  },
  "add-record": async (el, id) => {
    const v = formValues("record");
    if (refuseForm("record", v.label ? {} : { label: "Enter what this is, like \"Rough-in inspection passed\"." })) return;
    await post(`/api/jobs/${id}/add-record`, { label: v.label, note: v.note, kind: v.kind || "note" }, { formKey: "record" });
  },
  "record-edit": (el) => {
    editingRecord = el.dataset.item || "";
    render();
    if (editingRecord) document.getElementById(`rec-edit-label-${editingRecord}`)?.focus();
  },
  "record-save": async (el, id) => {
    const rid = el.dataset.item;
    const label = document.getElementById(`rec-edit-label-${rid}`)?.value || "";
    const kind = document.getElementById(`rec-edit-kind-${rid}`)?.value || "";
    if (!label.trim()) { toast("Enter what this is."); document.getElementById(`rec-edit-label-${rid}`)?.focus(); return; }
    const r = await post(`/api/jobs/${id}/edit-record/${rid}`, { label, kind });
    if (r && r.ok) { editingRecord = ""; render(); }
  },
  "open-job": (el, id) => {
    nav("job", id);
    if (el.dataset.focus) requestAnimationFrame(() => document.querySelector(el.dataset.focus)?.scrollIntoView({ block: "start" }));
  },
  "save-job": async (el, id) => {
    const v = formValues("editjob");
    const body = { name: v.name, address: v.address, contract_number: v.contract_number };
    const errors = {};
    if (!body.name) errors.name = "Enter the job name.";
    if (!body.address) errors.address = "Enter the job address.";
    if ("contract_cents" in v) {
      const amount = centsFrom(v.contract_cents);
      if (amount.error || amount.cents <= 0) errors.contract_cents = v.contract_cents && amount.error ? amount.error : "Enter the contract amount, like 48,500.";
      body.contract_cents = amount.cents;
    }
    if ("retainage_pct" in v) {
      const ret = v.retainage_pct ? percentFrom(v.retainage_pct) : { value: 0, error: "" };
      if (ret.error || !Number.isInteger(ret.value) || ret.value > 20) errors.retainage_pct = "Enter a whole percent from 0 to 20.";
      body.retainage_pct = ret.value;
    }
    if (refuseForm("editjob", errors)) return;
    await post(`/api/jobs/${id}/edit`, body, { formKey: "editjob", close: "editjob" });
  },
  "delete-job": async (el, id) => {
    const billed = el.dataset.drafts ? "Nothing was billed: its pay apps are deleted drafts." : "It has no pay apps, so nothing billed is lost.";
    if (!(await askConfirm(`Delete ${unesc(el.dataset.name || "this job")}? ${billed} Its record and notes go too.`, "Delete the job", true))) return;
    const r = await post(`/api/jobs/${id}/delete`);
    if (r && r.ok) nav("jobs");
  },
};
