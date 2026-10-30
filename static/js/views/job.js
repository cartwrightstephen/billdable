// The job page: where it stands, its next step and forms (progress, mark done, record a payment),
// the pay apps and change orders, and its activity; a finished job shows as its job file (J9). Its other cards have their own modules: job-money.js,
// job-record.js, job-sov.js and job-cos.js (change orders).
import { esc, unesc, money, centsFrom, percentFrom, dollarsText, fmtDate, fmtDateTime, words, INVOICE_WORDS, invoicePill, emailStatusHtml } from "../format.js";
import { isBlocked, nextStep, jobActions as jobActionsFor, ACTION_GROUPS, closeBlockerActions, waitingText, retainageNote, closeoutMissing as closeoutMissingCount, statusPill, coStatusWords, billedWork, progressFigures, shortPayable, shortPayLabel, shortPayAttrs, clientTypeLabel, waiverKindLabel } from "../jobs.js";
import { sovProgressHtml } from "../sov.js";
import { pathFor } from "../routes.js";
import { moreButton, askConfirm } from "../dom.js";
import { field, moneyField, formErrorLine, refuseForm, formValues, formDraft, draftValue, clearForm } from "../forms.js";
import { termText, actionWords } from "../terms.js";
import { state, me, currentJobId, formOpen } from "../state.js";
import { may, listPage, DEMO_COMPANY_ID, perms, post, toast, render, jobDetailLoading, jobDetailFailed, backButton, emptyState, nav, moreOpen } from "../app-core.js";
import { jobFields, jobFieldErrors, assigneeField } from "./jobs.js";
import { coSignControls, newCoCardHtml } from "./job-cos.js";
import { sovCardHtml } from "./job-sov.js";
import { recordCardHtml } from "./job-record.js";
import { moneyCardHtml } from "./job-money.js";

// Notes typed with progress or Mark done go on that pay app and its PDF (R4-12).
const NOTES_HELP = "Printed under the lines on this pay app and its PDF, so the client sees it.";
let progressMode = "period";   // the progress form takes this draw $ (default), the total done so far $, or % complete (R3-01)

// Who a client email goes to, for a confirmation: "Sam Jones · ap@jones.example" (plain text).
export function sendsTo(j) {
  const p = j.payer || {};
  return unesc(`${p.approver || p.name || "the client"}${p.ap_email ? " · " + p.ap_email : ""}`);
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

// "Spoke to them…" (R4-06): what the client said, and when to check again.
function contactFormHtml(j) {
  return `<div class="card form-card" data-form-key="contact"><h2>Spoke to ${j.payer.approver || j.payer.name}</h2>${formErrorLine("contact")}<div class="form-row">
    ${field({ form: "contact", name: "text", id: "contact-text", label: "What did they say?", required: true, placeholder: "Check goes out Friday", wide: true })}
    ${field({ form: "contact", name: "next_on", id: "contact-next", label: "Check again on (optional)", type: "date", attrs: `min="${todayIso()}"` })}
    <button class="btn small" data-action="note-contact" data-id="${j.id}">Save</button>
    <button class="btn small secondary" data-action="toggle-form" data-form="contact">Cancel</button></div></div>`;
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
// Called Activity (J7) so it isn't taken for Where this job stands.
function jobHistoryHtml(j) {
  const page = listPage("timeline", j.id);
  const rows = page ? page.entries : [];
  return `<div class="card job-history"><h2>Activity</h2>
    ${rows.length ? `<ol class="timeline">${rows.map(e => `<li>
        <span class="pill${e.kind === "email" ? " copper" : ""}">${e.kind === "email" ? "Email" : actionWords(e.action)}</span> ${e.text}
        <div class="sub">${fmtDateTime(e.at)}${e.who ? " · " + e.who : ""}${e.kind === "email" ? ` · to ${e.to} · ${emailStatusHtml(e)}` : ""}</div>
      </li>`).join("")}</ol>${moreButton("timeline", page)}`
      : !page ? `<p class="sub">Loading…</p>` : `<p class="sub">Nothing has happened on this job yet.</p>`}
  </div>`;
}

// Where this job stands (J7): its stage on one line, then the dates that matter this month. Both
// come from the server (domain.job_stage, job_key_dates).
export function standingCardHtml(j) {
  const stage = j.stage;
  const dates = j.key_dates || [];
  if (!stage && !dates.length) return "";
  return `<section class="card job-standing" aria-labelledby="standing-h">
      <h2 id="standing-h">Where this job stands</h2>
      ${stage ? `<ol class="stage-line">${stage.steps.map(s => `<li class="stage-step ${s.state}"${s.state === "current" ? ' aria-current="step"' : ""}>${termText(s.label)}${s.key === "in_progress" && s.state === "current" && stage.pct_billed ? `, ${stage.pct_billed}% billed` : ""}</li>`).join("")}</ol>` : ""}
      ${dates.length ? `<dl class="key-dates">${dates.map(d => `<div><dt>${termText(d.label)}</dt><dd>${fmtDate(d.date)}</dd></div>`).join("")}</dl>` : ""}
    </section>`;
}

// A finished job's file (J9): what it came to, then every pay app with its PDF, the payments,
// change orders and waivers sent; the job record and the activity follow on the page.
function archiveHtml(j) {
  const m = j.money;
  const live = (j.invoices || []).filter(i => !i.replaced && !(i.status === "void" && !i.sent_at));
  const waivers = (j.invoices || []).filter(i => i.waiver_sent_at);
  const row = (k, v) => `<div class="money-row"><span class="label">${k}</span><span>${v}</span></div>`;
  return `<div class="detail-grid job-archive">
      <div class="card">
        <h2>Job file</h2>
        ${row("Contract", money(m.contract_cents))}
        ${row("Signed change orders", money(m.approved_co_cents))}
        ${row("Contract total", money(m.contract_total_cents))}
        ${row("Billed", money(m.billed_cents))}
        ${row("Received", money(m.collected_cents))}
        ${row("Retainage released", money(m.retainage_released_cents))}
        ${row("Started", `${j.started_on ? fmtDate(j.started_on) : "—"} <button class="linkish" data-action="toggle-form" data-form="editstart" aria-expanded="${!!formOpen.editstart}">${formOpen.editstart ? "Cancel" : "Change"}</button>`)}
        ${formOpen.editstart ? `<div class="form-row" data-form-key="editstart">${formErrorLine("editstart")}${jobFields("editstart", "es", j).started_on}
          <button class="btn small" data-action="save-start" data-id="${j.id}">Save start date</button></div>` : ""}
        ${row("Finished", fmtDate(j.closed_at || j.completed_at))}
        <div class="form-inline"><a class="btn secondary small" href="/api/jobs/${j.id}/archive/job-file.zip" download>Download job file</a></div>
      </div>
      <div class="card">
        <h2>${termText("Pay apps")}</h2>
        ${live.length ? live.map(i => `<div class="list-item"><strong>${i.number}</strong> ${invoicePill(i)} <span class="num">${money(i.amount_cents)}</span>
          <div class="sub row-sub">${i.sent_at ? "sent " + fmtDate(i.sent_at) : "not sent"}${i.received_at ? " · received " + fmtDate(i.received_at) : ""}${i.sent_at && i.status !== "void" ? ` · <a href="/api/jobs/${j.id}/payapp/${i.id}.pdf" target="_blank" rel="noopener">PDF</a>` : ""}</div></div>`).join("") : `<p class="sub">None.</p>`}
        <h2 class="h2-gap">Payments received</h2>
        ${(j.payments || []).length ? j.payments.map(p => `<div class="list-item">${fmtDate(p.matched_at)} · ${money(p.amount_cents)}${p.reference ? ` · ${p.reference}` : ""}</div>`).join("") : `<p class="sub">None.</p>`}
        <h2 class="h2-gap">Change orders</h2>
        ${(j.change_orders || []).length ? j.change_orders.map(co => `<div class="list-item">CO ${co.number} · ${co.title} · ${money(co.amount_cents)} <span class="pill">${coStatusWords(co.status)}</span></div>`).join("") : `<p class="sub">None.</p>`}
        <h2 class="h2-gap">Waivers sent</h2>
        ${waivers.length ? waivers.map(i => `<div class="list-item">${i.number} · ${waiverKindLabel(i.waiver_kind)} · ${fmtDate(i.waiver_sent_at)}</div>`).join("")
          : j.waiver_sent_at ? `<div class="list-item">${j.waiver_kind || "Waiver"} · ${fmtDate(j.waiver_sent_at)}</div>` : `<p class="sub">None.</p>`}
      </div>
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

// This module's pages, by the name their address opens (routes.js).
export const views = {
  job() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j && jobDetailLoading === currentJobId) return backButton("jobs", "Jobs") + `<p class="sub">Loading the job…</p>`;
    if (!j && jobDetailFailed === currentJobId) return backButton("jobs", "Jobs") + emptyState("Couldn't load this job", "Check your connection, then try again.", `<button class="btn" data-action="nav" data-view="job" data-id="${esc(currentJobId)}">Try again</button>`);
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
    const head = `
      ${backButton("jobs", "Jobs")}
      <div class="job-head">
        <div>
          <h1>${j.name}</h1>
          <p class="sub">${statusPill(j)} ${j.code} · ${j.address} · ${j.payer.name} <span class="client-type">${clientTypeLabel(j.payer)}</span></p>
        </div>
        ${j.closed ? (may("reopen") && !formOpen.reopen ? `<button class="btn secondary small" data-action="toggle-form" data-form="reopen" aria-expanded="false">Add more work</button>` : "")
          : `<div class="form-inline"><button class="btn secondary small" data-action="daily-for-job" data-id="${j.id}">New daily record</button>
          <button class="btn secondary small" data-action="toggle-form" data-form="editjob" aria-expanded="${!!formOpen.editjob}">${formOpen.editjob ? "Cancel" : "Edit job"}</button></div>`}
      </div>`;
    // A finished job is its job file (J9): what it came to, its papers and its activity. Add more
    // work opens the same job again, so its record stays in one place (Finish and reopen jobs, with a reason).
    if (j.closed) return `${head}
      ${formOpen.reopen && may("reopen") ? `<div class="card reopen-card" data-form-key="reopen"><h2>Add more work</h2>${formErrorLine("reopen")}<div class="form-row">
          ${field({ form: "reopen", name: "reason", id: "reopen-reason", label: "What is the new work?", required: true, placeholder: "The owner added a backsplash", wide: true })}
          <button class="btn small" data-action="reopen" data-id="${j.id}">Add more work</button>
          <button class="btn small secondary" data-action="toggle-form" data-form="reopen">Cancel</button></div></div>` : ""}
      ${standingCardHtml(j)}
      ${archiveHtml(j)}
      ${recordCardHtml(j)}
      ${sovCardHtml(j)}
      ${jobHistoryHtml(j)}
    `;
    const nextLine = waiting && (!primary || primary.key === "follow") ? waiting : nextStep(j, new Date(), { listed: blocked });
    return `
      ${head}
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
            ${ejf.started_on}
            ${assigneeField("editjob", "ej", j.assignee_id)}
          </div>
          <p class="sub">${hasPayApps ? "The contract amount is fixed once a pay app exists; use a change order. A new retainage applies from the next pay app; those sent keep theirs. " : ""}The client's type and where pay apps go: <button class="linkish" data-action="nav" data-view="playbook">Client playbook</button>.</p>
          <div class="form-inline">
            <button class="btn" data-action="save-job" data-id="${j.id}">Save changes</button>
            ${j.can_delete && may("delete") ? `<button class="btn secondary danger-text" data-action="delete-job" data-id="${j.id}" data-name="${j.name}"${j.invoices.length ? ` data-drafts="${j.invoices.length}"` : ""}>Delete job</button>` : ""}
          </div>
        </div>` : ""}
      ${standingCardHtml(j)}
      <section class="next-card${blocked ? " is-blocked" : ""}" aria-label="Next step">
        <div class="next-text"><span class="eyebrow">Next step</span><strong>${nextLine}</strong>
          ${blocked ? `<ul class="blocker-list">${j.blockers.map(b => `<li>${b}</li>`).join("")}</ul>` : ""}
          ${primary && primary.why ? `<span class="sub">${primary.why}</span>` : ""}</div>
        ${primary || more.length ? `<div class="next-actions">
          ${primary ? `<button class="btn primary-action" ${primary.attrs}>${primary.label}</button>` : ""}
          ${more.slice(0, 2).map(actionButton).join("")}
        </div>` : ""}
        ${blocked ? `<details class="sent-back"><summary>${termText("Client sent a pay app back? Note why.")}</summary>
          <div class="form-inline">
            <input id="learn-reason" placeholder="For example: needs a revised schedule of values" class="grow" aria-label="Why they sent it back">
            <button class="btn small secondary" data-action="learn" data-id="${j.id}">Save to the client playbook</button>
          </div>
        </details>` : ""}
      </section>
      ${formOpen.progress && j.sov_view && j.sov_view.present ? sovProgressHtml(j, periodField("sovprogress", j), formErrorLine("sovprogress")) : ""}
      ${formOpen.progress && !(j.sov_view && j.sov_view.present) ? progressFormHtml(j, openDraft) : ""}
      ${formOpen.pencilchanges ? `<div class="card form-card" data-form-key="pencilchanges"><h2>Client wants changes</h2><div class="form-inline"><input id="pencil-note" data-field="note" value="${esc(draftValue("pencilchanges", "note"))}" placeholder="What to change: quantities, change orders, missing items" class="grow"><button class="btn small" data-action="pencil-changes" data-id="${j.id}">Save</button></div></div>` : ""}
      ${formOpen.done ? `<div class="card form-card" data-form-key="done"><h2>Mark done</h2>${formErrorLine("done")}<div class="form-row">
        ${field({ form: "done", name: "notes", id: "done-notes", label: "Anything to note? (optional)", wide: true, help: NOTES_HELP })}
        ${periodField("done", j)}
        ${openDraft && openDraft.pencil_status !== "sent" ? `<p class="sub">${openDraft.number} becomes the final pay app. Its number and paperwork stay.</p>` : ""}
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
      ${formOpen.newco ? newCoCardHtml(j) : ""}
      ${rest.length ? `
        <details class="more-actions-rest" data-more="jobactions"${moreOpen("jobactions") ? " open" : ""}><summary>More (${rest.length})</summary>
          ${ACTION_GROUPS.map(([g, title]) => {
            const list = rest.filter(a => a.group === g);
            return list.length ? `<div class="more-group"><h3>${title}</h3><div class="more-actions" role="group" aria-label="${title}">${list.map(actionButton).join("")}</div></div>` : "";
          }).join("")}
        </details>` : ""}
      ${j.work_status === "complete" && (j.close_blocker_items || []).length ? `
        <section class="card close-blockers" aria-labelledby="close-blockers-h">
          <h2 id="close-blockers-h">Before you can finish this job</h2>
          <ul class="close-blocker-list">${j.close_blocker_items.map(b => `<li data-blocker="${b.kind}"><span>${termText(b.text)}</span><span class="form-inline">${closeBlockerActions(j, b, perms()).map((a, n) => `<button class="btn small${n ? " secondary" : ""}" ${a.attrs}>${a.label}</button>`).join("")}</span></li>`).join("")}</ul>
        </section>` : ""}
      <div class="detail-grid">
        ${moneyCardHtml(j)}
        <div class="card">
          <h2>${termText("Pay apps")}</h2>
          ${j.invoices.length ? j.invoices.filter(i => !i.replaced).map(i => {  // a draft replaced by a progress correction stays in the history only
            const pkt = i.packet || j.packet || [];
            const missing = pkt.filter(p => p.required && !p.present).length;
            const pencil = i.pencil_status ? ` · draft ${i.pencil_status === "sent" ? "with client" : words(i.pencil_status)}${i.pencil_note ? ": " + i.pencil_note : ""}` : "";
            return `<div class="list-item row-link">
              <div><a href="${pathFor("payapp-detail", j.id, i.id)}" data-action="open-payapp" data-job="${j.id}" data-inv="${i.id}"><strong>${i.number}</strong></a> ${invoicePill(i)} <span class="num">${money(i.amount_cents)}</span>
              <div class="sub row-sub">${i.status === "void" ? (i.voided_reason ? "Pulled back: " + i.voided_reason : "Deleted") : missing ? `${missing} paperwork item${missing === 1 ? "" : "s"} missing` : "Paperwork complete"}${i.sent_at ? " · sent " + fmtDate(i.sent_at) : ""}${emailFailed(i.id) ? " · email failed" : ""}${i.received_at ? " · received " + fmtDate(i.received_at) : ""}${i.approved_by && i.sent_at ? " · sent by " + i.approved_by : ""}${i.due_on && ["sent", "approved", "partial"].includes(i.status) ? " · due " + fmtDate(i.due_on) : ""}${i.short_pay_cents ? " · short pay " + money(i.short_pay_cents) : ""}${pencil}</div></div>
            </div>`;
          }).join("") : `<p class="sub">${termText("No pay apps yet.")} ${j.work_status === "complete" ? "Draft one from the work not billed yet." : "Record progress or mark the job done to draft one."}</p>`}
          ${closeout.length ? `<div class="list-item row-link"><div><a href="${pathFor("packet-detail", j.id, "closeout")}" data-action="open-packet" data-job="${j.id}" data-inv="closeout"><strong>Closeout paperwork</strong></a> <span class="sub">${m.retainage_open_cents > 0 ? "needed before held-back money is paid · " : ""}${closeoutMissing ? closeoutMissing + " missing" : "complete"}</span></div></div>` : ""}
          <h2 class="h2-gap">Change orders</h2>
          ${j.change_orders.length ? j.change_orders.map(co => `
            <div class="list-item">CO ${co.number} · ${co.title} · ${money(co.amount_cents)} <span class="pill ${co.status === "approved" ? "green" : co.status === "withdrawn" ? "" : "amber"}">${coStatusWords(co.status)}</span>${co.included_in_billing ? ` <span class="sub">${termText("on a pay app")}</span>` : ""}
              ${coSignControls(j, co, !co.included_in_billing && !(openDraft && openDraft.pencil_status === "sent") && co.amount_cents > 0 ? `<button class="btn small secondary" data-action="include-co" data-id="${j.id}" data-co="${co.id}">Bill now (send waits for signature)</button>` : "")}
            </div>`).join("") : `<p class="sub">None.</p>`}
        </div>
      </div>
      ${recordCardHtml(j)}
      ${sovCardHtml(j)}
      ${jobHistoryHtml(j)}
    `;
  },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in app.js.
export const actions = {
  "draft": (el, id) => post(`/api/jobs/${id}/draft`),
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
  "pencil": async (el, id) => {
    const j = state.jobs.find(x => x.id === id);
    const draft = j && j.invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
    if (j && !(await askConfirm(`Email ${unesc(draft ? draft.number : "the draft")} to ${sendsTo(j)} as a draft for their review?`, "Send the draft"))) return;
    await post(`/api/jobs/${id}/pencil`);
  },
  "pencil-agreed": (el, id) => post(`/api/jobs/${id}/pencil-response`, { agreed: true }),
  "pencil-changes": async (el, id) => {
    const note = (document.getElementById("pencil-note")?.value || "").trim();
    if (!note) { toast("Say what the client wants changed."); document.getElementById("pencil-note")?.focus(); return; }
    formOpen.pencilchanges = false;
    const r = await post(`/api/jobs/${id}/pencil-response`, { agreed: false, note });
    if (r && r.ok) clearForm("pencilchanges");
  },
  // Add more work (J9): the finished job opens again, with a reason in its activity.
  "reopen": async (el, id) => {
    const v = formValues("reopen");
    if (refuseForm("reopen", v.reason ? {} : { reason: "Enter the new work, like the owner added a backsplash." })) return;
    await post(`/api/jobs/${id}/reopen`, { reason: v.reason }, { formKey: "reopen", close: "reopen" });
  },
  // A finished job's start date, corrected on its job file (J8).
  "save-start": async (el, id) => {
    const v = formValues("editstart");
    if (refuseForm("editstart", v.started_on ? {} : { started_on: "Enter the start date." })) return;
    await post(`/api/jobs/${id}/edit`, { started_on: v.started_on }, { formKey: "editstart", close: "editstart" });
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
  "import-qbo": (el, id) => post(`/api/jobs/${id}/import-qbo`),
  "close": async (el, id) => {
    const j = state.jobs.find(x => x.id === id);
    if (!(await askConfirm(`Finish ${unesc(j ? j.name : "the job")}? It moves to Job history and no more payments can be recorded.`, "Finish job"))) return;
    await post(`/api/jobs/${id}/close`);
  },
  "learn": async (el, id) => {
    const reason = document.querySelector("#learn-reason")?.value || "";
    await post(`/api/jobs/${id}/learn-rejection`, { reason });
  },
  "open-job": (el, id) => {
    nav("job", id);
    if (el.dataset.focus) requestAnimationFrame(() => document.querySelector(el.dataset.focus)?.scrollIntoView({ block: "start" }));
  },
  "save-job": async (el, id) => {
    const v = formValues("editjob");
    // The address goes as its four boxes (J1); the server builds the one line from them.
    const body = { name: v.name, street: v.street, city: v.city, state: v.state, zip: v.zip, contract_number: v.contract_number };
    if (v.started_on) body.started_on = v.started_on;
    if ("assignee_id" in v) body.assignee_id = v.assignee_id;
    const errors = jobFieldErrors(v);
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
