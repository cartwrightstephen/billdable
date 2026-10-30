// Money, Change orders: the list, and each change order's page. A change order is made on its
// job's page; New change order here asks for the job and opens that form (Stephen's Oct 29
// review, G10).
import { unesc, money, fmtDate, fmtDateTime } from "../format.js";
import { coSignature, coStatusWords, defaultJobId, RECORD_KINDS } from "../jobs.js";
import { pathFor } from "../routes.js";
import { askConfirm } from "../dom.js";
import { field, formErrorLine, refuseForm, formValues } from "../forms.js";
import { termText } from "../terms.js";
import { state, currentJobId, currentDetailId, formOpen, onSessionEnd } from "../state.js";
import { render, toast, post, backButton, emptyState, nav } from "../app-core.js";
import { coFormFields, coSignControls, addChangeOrder, workStartedPill, CO_REASONS, CO_PRICING } from "./job-cos.js";
import { recordFiles } from "./job-record.js";

let coPreselectJob = null;
// Signed out: a job picked for a new change order is that company's.
onSessionEnd(() => { coPreselectJob = null; });

const days = (n) => `${n > 0 ? "+" : ""}${n} day${Math.abs(n) === 1 ? "" : "s"}`;

// What each history row says (domain.co_event, M10).
const HISTORY_WORDS = {
  created: "Created", included: termText("Put on the next pay app"), off_draft: "Taken off the deleted draft",
  signature_requested: "Signature requested", signed: "Signed", withdrawn: "Withdrawn",
  work_started: "Work started", client_logged: "Client logged it on their end", client_logged_undone: "Client logged it: taken off",
};

// Who did what and when, oldest first. A change order from before its own history has only what
// was stamped on it.
export function coHistoryHtml(co) {
  let rows = co.history || [];
  if (!rows.length) {
    rows = [["created", co.created_at, co.created_by], ["included", co.included_at, co.included_by],
      ["signed", co.approved_at, co.approved_by], ["withdrawn", co.withdrawn_at, co.withdrawn_by]]
      .filter(r => r[1]).map(([what, at, by]) => ({ what, at, by }));
  }
  if (!rows.length) return "";
  return `<div class="card co-history"><h2>History</h2>
    ${rows.map(r => `<div class="money-row"><span class="label">${HISTORY_WORDS[unesc(r.what)] || r.what}${r.note ? ` · ${unesc(r.what) === "work_started" ? fmtDate(r.note) : r.note}` : ""}</span><span>${r.by ? r.by + ", " : ""}${fmtDateTime(r.at)}</span></div>`).join("")}
  </div>`;
}

// The contract sum with this change order (M8): what was signed before it, this one, and the
// revised sum. An unsigned one reads "if signed".
export function coContractHtml(j, co) {
  const original = j.money ? j.money.contract_cents : 0;
  const before = j.change_orders.filter(c => c.status === "approved" && c.id !== co.id && c.number < co.number).reduce((s, c) => s + c.amount_cents, 0);
  const counts = co.status !== "withdrawn";
  const revised = original + before + (counts ? co.amount_cents : 0);
  const when = co.status === "pending" ? " if signed" : "";
  return `<div class="card co-contract"><h2>Contract sum</h2>
    <div class="money-row"><span class="label">Original contract</span><span>${money(original)}</span></div>
    <div class="money-row"><span class="label">Signed change orders before this one</span><span>${money(before)}</span></div>
    <div class="money-row"><span class="label">This change order${co.status === "withdrawn" ? " (withdrawn, not counted)" : ""}</span><span>${money(counts ? co.amount_cents : 0)}</span></div>
    <div class="money-row strong"><span class="label">Revised contract sum${when}</span><span>${money(revised)}</span></div>
  </div>`;
}

// What the change order is waiting on: the signature, then the client logging it (M8).
function waitingOn(co) {
  if (co.waiting_on) return co.waiting_on;
  if (co.status === "pending") return "Client signature";
  if (co.status === "approved" && !co.client_logged_at) return "Client to log it on their end";
  return "Nothing";
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  coDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return backButton("cos", "Change orders") + emptyState("Change order not found", "The job may have been deleted.", `<button class="btn" data-action="nav" data-view="cos">Go to Change orders</button>`);
    const co = j.change_orders.find(c => c.id === currentDetailId) || j.change_orders[j.change_orders.length - 1];
    if (!co) {
      return `
        ${backButton("cos", "Change orders")}
        <h1>New change order</h1>
        <div class="card" data-form-key="newco">${formErrorLine("newco")}
          ${coFormFields(j)}
          <button class="btn" data-action="add-co" data-id="${j.id}">Create</button>
        </div>`;
    }
    const items = co.items || [];
    const records = j.records || [];
    const backup = (co.support_record_ids || []).map(id => records.find(r => r.id === id)).filter(Boolean);
    const startKey = `costart-${co.id}`;
    return `
      ${backButton("cos", "Change orders")}
      <h1>CO ${co.number}: ${co.title}</h1>
      <p class="sub"><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a> · ${money(co.amount_cents)} · <span class="pill ${co.status === "approved" ? "green" : co.status === "withdrawn" ? "" : "amber"}">${coStatusWords(co.status)}</span>${workStartedPill(co)}</p>
      <div class="card">
        <h2>Summary</h2>
        ${co.reason ? `<div class="money-row"><span class="label">Reason</span><span>${CO_REASONS[unesc(co.reason)] || co.reason}</span></div>` : ""}
        ${co.description ? `<p class="co-desc">${co.description}</p>` : ""}
        ${co.contract_ref ? `<div class="money-row"><span class="label">Changes</span><span>${co.contract_ref}</span></div>` : ""}
        <div class="money-row"><span class="label">Pricing</span><span>${CO_PRICING[unesc(co.pricing || "lump_sum")] || co.pricing}</span></div>
        ${items.length ? `<div class="co-cost-lines">${items.map(i => `<div class="money-row"><span>${i.label}</span><span>${money(i.amount_cents)}</span></div>`).join("")}
          <div class="money-row strong"><span>Total</span><span>${money(co.amount_cents)}</span></div></div>` : ""}
        <div class="money-row"><span class="label">Schedule</span><span>${co.schedule_days ? days(co.schedule_days) : "No change"}</span></div>
        <div class="money-row"><span class="label">Work started</span><span>${co.work_started_on ? fmtDate(co.work_started_on) : "Not recorded"}</span></div>
        <div class="money-row"><span class="label">Waiting on</span><span>${waitingOn(co)}</span></div>
        <div class="money-row"><span class="label">${termText("On a pay app")}</span><span>${co.included_in_billing ? "Yes" : "No"}</span></div>
        ${coSignature(co)}
        ${co.status === "approved" && !j.closed ? `<div class="form-inline co-logged">${co.client_logged_at
          ? `<span>Client logged it on their end (${co.client_logged_by || ""}, ${fmtDate(co.client_logged_at)})</span> <button class="btn small secondary" data-action="co-logged" data-id="${j.id}" data-co="${co.id}" data-logged="0">Undo</button>`
          : `<button class="btn small secondary" data-action="co-logged" data-id="${j.id}" data-co="${co.id}" data-logged="1">Client logged it on their end</button>`}</div>` : ""}
        ${co.status !== "withdrawn" && !j.closed ? (formOpen[startKey] ? `<div class="co-start" data-form-key="${startKey}">${formErrorLine(startKey)}<div class="form-inline">
            ${field({ form: startKey, name: "work_started_on", id: `co-start-${co.id}`, label: "Work started on", type: "date", attrs: `max="${new Date().toISOString().slice(0, 10)}"` })}
            <button class="btn small" data-action="co-work-started" data-id="${j.id}" data-co="${co.id}">Save</button>
            <button class="btn small secondary" data-action="toggle-form" data-form="${startKey}">Cancel</button></div></div>`
          : `<div class="form-inline"><button class="btn small secondary" data-action="toggle-form" data-form="${startKey}" aria-expanded="false">${co.work_started_on ? "Change the day work started…" : "Work started…"}</button></div>`) : ""}
        ${coSignControls(j, co, "", { flag: false })}
      </div>
      ${backup.length ? `<div class="card co-backup-list"><h2>Backup</h2>
        ${backup.map(r => `<div class="list-item"><span class="pill">${termText(RECORD_KINDS[unesc(r.kind)] || r.kind)}</span> ${r.label}${recordFiles(r) ? `<div class="sub">${recordFiles(r)}</div>` : ""}</div>`).join("")}
      </div>` : ""}
      ${coContractHtml(j, co)}
      ${coHistoryHtml(co)}
    `;
  },
  "co-detail": function() { return views.coDetail(); },
  cos() {
    const cos = [];
    state.jobs.forEach(j => j.change_orders.forEach(co => cos.push({ ...co, job: j.name, jobId: j.id })));
    const openJobs = state.jobs.filter(j => !j.closed);
    const picking = formOpen.copick && openJobs.length > 1;
    return `
      <div class="job-head"><h1>Change orders</h1>
        ${openJobs.length ? `<button class="btn small" data-action="co-new" aria-expanded="${!!picking}">New change order</button>` : ""}
      </div>
      ${picking ? `<div class="card" data-form-key="copick">
        ${formErrorLine("copick")}
        <div class="form-row">
          ${field({ form: "copick", name: "job", id: "new-co-job", label: "Which job?", value: unesc(defaultJobId(openJobs, coPreselectJob || "")), options: [["", "Pick a job…"], ...openJobs.map(j => [unesc(j.id), unesc(j.name)])] })}
          <button class="btn small" data-action="co-new-open">Open the form</button>
          <button class="btn small secondary" data-action="toggle-form" data-form="copick">Cancel</button>
        </div>
      </div>` : ""}
      ${cos.length ? `<div class="table-wrap">
        <table>
          <tr><th>Change order</th><th>Job</th><th>Amount</th><th>Status</th><th>Waiting on</th><th>${termText("On a pay app")}</th></tr>
          ${cos.slice().reverse().map(c => `<tr>
            <td><a href="${pathFor("co-detail", c.jobId, c.id)}" data-action="open-co" data-job="${c.jobId}" data-co="${c.id}">CO ${c.number}: ${c.title}</a>${c.reason || c.description ? `<div class="sub row-sub">${[c.reason ? CO_REASONS[unesc(c.reason)] || c.reason : "", c.description ? c.description.slice(0, 80) : ""].filter(Boolean).join(" · ")}</div>` : ""}</td>
            <td>${c.job}</td>
            <td class="num">${money(c.amount_cents)}</td>
            <td><span class="pill ${c.status === "approved" ? "green" : c.status === "withdrawn" ? "" : "amber"}">${coStatusWords(c.status)}</span>${workStartedPill(c)}</td>
            <td>${c.status === "pending" && !c.waiting_on ? "Signature" : c.status === "withdrawn" ? "—" : waitingOn(c)}</td>
            <td>${c.included_in_billing ? "Yes" : "No"}</td>
          </tr>`).join("")}
        </table>
      </div>` : emptyState("No change orders", "", openJobs.length ? "" : `<button class="btn" data-action="onb-newjob">New job</button>`)}
    `;
  },
};

// Open a job's page with its New change order form showing.
function openCoForm(jobId) {
  nav("job", jobId);
  formOpen.newco = true;
  render();
  requestAnimationFrame(() => document.getElementById("co-title")?.focus());
}

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  "withdraw-co": async (el, id) => {
    if (!(await askConfirm("Withdraw this change order? Use this when the client will not sign it.", "Withdraw", true))) return;
    await post(`/api/jobs/${id}/withdraw-co/${el.dataset.co}`);
  },
  "add-co": async (el, id) => {
    const r = await addChangeOrder(id);
    if (r && r.ok) nav("co-detail", id);
  },
  "goto-co": (el, id) => { coPreselectJob = id; nav("cos"); },
  // New change order on the list (G10): with one open job its form opens; else pick the job first.
  "co-new": () => {
    const open = state.jobs.filter(j => !j.closed);
    if (open.length === 1) { openCoForm(open[0].id); return; }
    formOpen.copick = !formOpen.copick;
    render();
    requestAnimationFrame(() => document.getElementById("new-co-job")?.focus());
  },
  "co-new-open": () => {
    const jobId = formValues("copick").job;
    if (refuseForm("copick", jobId ? {} : { job: "Pick a job." })) return;
    coPreselectJob = jobId;
    openCoForm(jobId);
  },
  "open-co": (el) => nav("co-detail", el.dataset.job, el.dataset.co),
  "approve-co": async (el, id) => {
    const key = `cosign-${el.dataset.co}`;
    const v = formValues(key);
    const recordId = document.getElementById(`cosign-rec-${el.dataset.co}`)?.value || "";
    const today = new Date().toISOString().slice(0, 10);
    const errors = {};
    if ((v.signer_name || "").length < 2) errors.signer_name = "Enter the name of the person who signed it.";
    if (!/^\d{4}-\d{2}-\d{2}$/.test(v.signed_on || "")) errors.signed_on = "Enter the date it was signed, like 2026-10-27.";
    else if (v.signed_on > today) errors.signed_on = "Enter the date it was signed that has already happened.";
    if (refuseForm(key, errors)) return;
    if (!recordId) { toast("Choose the record that holds the signed copy, or take a photo of it."); return; }
    const r = await post(`/api/jobs/${id}/approve-co/${el.dataset.co}`, { record_id: recordId, signer_name: v.signer_name, signed_on: v.signed_on }, { formKey: key });
    if (r && r.ok) { formOpen[key] = false; render(); }
  },
  "request-co-signature": (el, id) => post(`/api/jobs/${id}/request-co-signature/${el.dataset.co}`),
  "co-logged": (el, id) => post(`/api/jobs/${id}/co-logged/${el.dataset.co}`, { logged: el.dataset.logged === "1" }),
  "co-work-started": async (el, id) => {
    const key = `costart-${el.dataset.co}`;
    const v = formValues(key);
    if (refuseForm(key, /^\d{4}-\d{2}-\d{2}$/.test(v.work_started_on || "") ? {} : { work_started_on: "Enter the day work started, like 2026-10-27." })) return;
    await post(`/api/jobs/${id}/co-work-started/${el.dataset.co}`, { work_started_on: v.work_started_on }, { formKey: key, close: key });
  },
};
