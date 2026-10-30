// Money, Pay apps: the list and each pay app's page (Before you send, lines, payments, approval).
// A pay app is drafted on its job's page; New pay app here asks for the job and opens that job's
// step for it, the progress form or Draft pay app (Stephen's Oct 29 review, G10).
import { unesc, money, fmtDate, fmtDateTime, invoicePill } from "../format.js";
import { shortPayLabel, shortPayAttrs, canPullBack, openJobs, draftFromDailyOn, payAppStarts, defaultJobId } from "../jobs.js";
import { pathFor } from "../routes.js";
import { askConfirm } from "../dom.js";
import { field, formErrorLine, refuseForm, formValues } from "../forms.js";
import { termText } from "../terms.js";
import { state, currentView, currentJobId, currentDetailId, formOpen, onSessionEnd } from "../state.js";
import { may, perms, render, post, backButton, emptyState, nav, EMAIL_PATTERN } from "../app-core.js";
import { continuationHtml } from "../sov.js";
import { companyGapsHtml } from "../setup.js";
import { QBO_READY, qboCsvLink, OWNER_SENDS, emailUnconfirmed, confirmFirstHtml } from "./money.js";
import { shortPayCardHtml, owedText, emailFailed, sendsTo } from "./job.js";
import { beforeYouSendHtml } from "./paperwork.js";

let payappFilter = "all";
let payPreselectJob = null;
// Signed out: a job picked for a new pay app is that company's.
onSessionEnd(() => { payPreselectJob = null; });

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
    <p class="sub">${termText("For a pay app that went out with a mistake.")} ${j.payer.name || "The client"} is emailed that it is withdrawn; you can then bill again.</p>
    ${formErrorLine(key)}<div class="form-row">
      ${field({ form: key, name: "reason", id: `voidsent-reason-${i.id}`, label: "Why pull it back", required: true, placeholder: "Wrong quantity on line 3", wide: true })}
      <button class="btn small danger" data-action="void-sent" data-id="${j.id}" data-inv="${i.id}">Pull back ${i.number}</button>
      <button class="btn small secondary" data-action="toggle-form" data-form="${key}">Cancel</button>
    </div></div>`;
}

// Pay apps' Paperwork column (R6-03, was its own tab): what is missing, linked to the list.
function paperworkCell(a) {
  const pkt = a.packet || a.jobPacket || [];
  if (!pkt.length && !a.packet) return "—";
  const missing = pkt.filter(p => p.required && !p.present).length;
  return `<a href="${pathFor("packet-detail", a.jobId, a.id)}" data-action="open-packet" data-job="${a.jobId}" data-inv="${a.id}">${missing ? `<span class="pill red">${missing} missing</span>` : `<span class="pill green">Complete</span>`}</a>`;
}

// The pay app's summary, lines 1-9 as its PDF prints them (Stephen's Oct 29 review, M2), from
// the server's figures (domain.payapp_figures), and the payments received on earlier pay apps.
export function payappSummaryHtml(inv) {
  const f = inv.figures;
  if (!f) return "";
  const row = (label, cents, cls = "") => `<div class="money-row${cls}"><span class="label">${label}</span><span>${money(cents)}</span></div>`;
  return `<div class="card payapp-summary">
    <h2>${termText("Pay app summary")}</h2>
    ${row("Contract sum", f.original)}
    ${row("Approved change orders", f.net_co)}
    ${f.unsigned_co ? row("Unsigned change orders billed here", f.unsigned_co) : ""}
    ${row("Contract sum to date", f.contract_to_date, " strong")}
    ${row("Completed and stored to date", f.completed)}
    ${row("Retainage held", f.retainage)}
    ${row("Earned less retainage", f.earned)}
    ${row(termText("Less previous pay apps"), f.previous)}
    ${row("Current amount due", f.due, " strong payapp-due")}
    ${row("Balance to finish, including retainage", f.balance)}
    <p class="sub payapp-received">Prior payments received: ${money(f.received_before || 0)}${f.released ? ` · Retainage released: ${money(f.released)}` : ""}</p>
  </div>`;
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  payappDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    const inv = j && (j.invoices.find(i => i.id === currentDetailId) || j.invoices[j.invoices.length - 1]);
    if (!j || !inv) return backButton("payapps", "Pay apps") + emptyState("Pay app not found", "The job may have been deleted.", `<button class="btn" data-action="nav" data-view="payapps">Go to Pay apps</button>`);
    const pays = j.payments.filter(p => p.invoice_id === inv.id);
    const pkt = inv.packet || j.packet || [];
    const missing = pkt.filter(p => p.required && !p.present).length;
    const draft = ["draft", "awaiting_approval"].includes(inv.status);
    const fromDaily = draftFromDailyOn(j, inv);
    const copies = (j.payer.copy && unesc(j.payer.copy) !== "None") ? j.payer.copy : "";
    const firstSend = draft && !j.payer.sent_before && !state.jobs.some(x => x.payer_id === j.payer_id && x.invoices.some(i => i.sent_at));
    const noEmail = !j.payer.ap_email;
    // The PDF's From block prints the company's address, phone and licence number (R2-05, Oct 23
    // note 1): a blank one is said here before it goes out, as a warning only.
    const locked = emailUnconfirmed();
    // The client's payment terms set the due date from the day it goes out (R4-04).
    const terms = Number(unesc(String(j.payer.payment_terms_days || ""))) || 0;
    const dueIso = terms ? new Date(Date.now() + terms * 86400000).toISOString().slice(0, 10) : "";
    return `
      ${backButton("job", j.name, j.id)}
      <h1>${inv.number}</h1>
      <p class="sub">${invoicePill(inv)} <a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a> · ${money(inv.amount_cents)}</p>
      ${fromDaily ? `<p class="sov-daily payapp-daily">Updated from daily records on ${fmtDate(fromDaily)}</p>` : ""}
      ${inv.status === "void" && inv.voided_reason ? `<p class="notice pulled-back">Pulled back ${fmtDate(inv.voided_at)}${inv.voided_by ? " by " + inv.voided_by : ""}: ${inv.voided_reason}. The client was told nothing is due on it.</p>` : ""}
      <p><a class="btn secondary small" href="/api/jobs/${j.id}/payapp/${inv.id}.pdf" target="_blank" rel="noopener">Open the PDF</a></p>
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
        ${beforeYouSendHtml(j, inv)}
        ${companyGapsHtml(state.company)}
        ${firstSend && !noEmail ? `<p class="notice">First pay app to ${j.payer.name}. Check the email above; a typo sends it to a stranger. Change it in <button class="linkish" data-action="nav" data-view="playbook">Client playbook</button>.</p>` : ""}
        ${missing ? `<p class="blocker">${missing} paperwork item${missing === 1 ? " is" : "s are"} missing. Add ${missing === 1 ? "it" : "them"}, then send.</p>` : ""}
        ${noEmail ? `<p class="blocker">${termText("Add the email pay apps go to, then send.")}</p>` : ""}
        ${locked ? confirmFirstHtml(termText("the pay app")) : ""}
        ${may("send") ? `<button class="btn" data-action="send" data-id="${j.id}" ${missing || (j.blockers || []).length || noEmail || locked ? "disabled" : ""}>Approve & send to ${j.payer.ap_email || "the client"}</button>` : OWNER_SENDS}
      </div>` : ""}
      ${payappSummaryHtml(inv)}
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
        <div class="money-row"><span class="label">Received by the client</span><span>${inv.received_at ? `${inv.received_by || "the client"}, ${fmtDateTime(inv.received_at)}` : inv.sent_at ? "Not confirmed yet" : "—"}</span></div>
        ${inv.approved_at ? `<div class="money-row"><span class="label">Approved by the client</span><span>${inv.approved_by_payer || "the client"}, ${fmtDateTime(inv.approved_at)}</span></div>
        <div class="money-row"><span class="label">Payment expected by</span><span>${fmtDate(inv.expected_payment_on)}</span></div>` : ""}
        ${inv.customer_page ? `<p class="sub">The client's page for this pay app: <a href="/api/jobs/${j.id}/payapp/${inv.id}/page" target="_blank" rel="noopener">open it</a>.</p>` : ""}
        ${canPullBack(j, inv) && may("void-sent") ? pullBackHtml(j, inv) : ""}
      </div>
    `;
  },
  "payapp-detail": function() { return views.payappDetail(); },
  payapps() {
    let apps = [];
    // A finished job's pay apps are in its job file (J9), not on this working list.
    openJobs(state.jobs).forEach(j => j.invoices.forEach(inv => apps.push({ ...inv, job: j.name, jobId: j.id, jobPacket: j.packet, blockers: ["draft", "awaiting_approval"].includes(inv.status) ? j.blockers : [] })));
    // "Unpaid" (the sent filter) holds every pay app out for payment, part-paid ones too (R3-02).
    const shown = { draft: ["draft", "awaiting_approval"], sent: ["sent", "approved", "partial"] };
    if (payappFilter !== "all") apps = apps.filter(a => (shown[payappFilter] || [payappFilter]).includes(a.status));
    const f = (key, label) => `<button class="${payappFilter === key ? "btn" : "btn secondary"} small" data-action="payapp-filter" data-filter="${key}" aria-pressed="${payappFilter === key}">${label}</button>`;
    const starts = payAppStarts(state.jobs, perms());
    const picking = formOpen.papick && starts.length > 1;
    return `
      <div class="job-head"><h1>${termText("Pay apps")}</h1>
        ${starts.length ? `<button class="btn small" data-action="payapp-new" aria-expanded="${!!picking}">${termText("New pay app")}</button>` : ""}
      </div>
      ${picking ? `<div class="card" data-form-key="papick">
        ${formErrorLine("papick")}
        <div class="form-row">
          ${field({ form: "papick", name: "job", id: "new-payapp-job", label: "Which job?", value: unesc(defaultJobId(starts.map(s => s.job), payPreselectJob || "")),
            options: [["", "Pick a job…"], ...starts.map(s => [unesc(s.job.id), unesc(s.job.name)])] })}
          <button class="btn small" data-action="payapp-new-open">Continue</button>
          <button class="btn small secondary" data-action="toggle-form" data-form="papick">Cancel</button>
        </div>
      </div>` : ""}
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
      </div>` : emptyState(payappFilter === "all" ? termText("No pay apps yet") : "None in this view")}
    `;
  },
  // Paperwork is a column of Pay apps now (R6-03); /money/packets still opens it.
  packets() { return views.payapps(); },
};

// Start a pay app on a job the way its own page does (G10): the Progress pay app form while the
// work goes on, or Draft pay app for a job marked done. step comes from payAppStarts.
export async function startPayApp(jobId, step) {
  nav("job", jobId);
  if (step === "draft") return post(`/api/jobs/${jobId}/draft`);
  formOpen.progress = true;
  render();
  requestAnimationFrame(() => document.querySelector('[data-form-key="progress"] input, [data-form-key="sovprogress"] input:not([disabled])')?.focus());
  return null;
}

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  // New pay app on the list (G10): with one job to bill its step opens; else pick the job first.
  "payapp-new": () => {
    const starts = payAppStarts(state.jobs, perms());
    if (starts.length === 1) return startPayApp(starts[0].job.id, starts[0].step);
    formOpen.papick = !formOpen.papick;
    render();
    requestAnimationFrame(() => document.getElementById("new-payapp-job")?.focus());
    return null;
  },
  "payapp-new-open": () => {
    const jobId = formValues("papick").job;
    const start = payAppStarts(state.jobs, perms()).find(s => unesc(s.job.id) === jobId);
    if (refuseForm("papick", start ? {} : { job: "Pick a job." })) return null;
    payPreselectJob = start.job.id;
    formOpen.papick = false;
    return startPayApp(start.job.id, start.step);
  },
  "send": (el, id) => post(`/api/jobs/${id}/send`),
  // "Before you send" with no email yet (R1-05): saved to the client's settings, then send unlocks.
  "save-send-email": async (el, id) => {
    const v = formValues("sendemail");
    if (refuseForm("sendemail", EMAIL_PATTERN.test(v.ap_email) ? {} : { ap_email: "Enter the email pay apps go to, like ap@builder.com." })) return;
    await post(`/api/payers/${id}/update`, { ap_email: v.ap_email }, { formKey: "sendemail" });
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
  // Pull back a sent pay app (R4-03): a reason, then a confirm, then the client is told.
  "void-sent": async (el, id) => {
    const inv = el.dataset.inv, key = `voidsent-${inv}`;
    const j = state.jobs.find(x => x.id === id);
    const i = j && j.invoices.find(x => x.id === inv);
    const v = formValues(key);
    if (refuseForm(key, v.reason ? {} : { reason: "Enter why you are pulling it back, like a wrong quantity on line 3." })) return;
    if (j && !(await askConfirm(`Pull back ${unesc(i ? i.number : termText("this pay app"))}? An email to ${sendsTo(j)} says it is withdrawn and nothing is due on it.`, "Pull back", true))) return;
    await post(`/api/jobs/${id}/void-sent/${inv}`, { reason: v.reason }, { formKey: key, close: key });
  },
  "open-payapp": (el) => {
    nav("payapp-detail", el.dataset.job, el.dataset.inv);
    if (el.dataset.focus) requestAnimationFrame(() => document.querySelector(el.dataset.focus)?.scrollIntoView({ block: "start" }));
  },
  "payapp-filter": (el) => { payappFilter = el.dataset.filter; render(); },
};
