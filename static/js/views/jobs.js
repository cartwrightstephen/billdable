// Jobs: the open jobs with New job, and Job history. The job's own fields are shared with Edit job.
import { esc, unesc, money, centsFrom, percentFrom, fmtDate } from "../format.js";
import { nextStep, statusPill } from "../jobs.js";
import { pathFor } from "../routes.js";
import { moreButton } from "../dom.js";
import { field, moneyField, formErrorLine, refuseForm, formValues, formDraft, draftValue } from "../forms.js";
import { state, formOpen } from "../state.js";
import { nav, moreOpen, emptyState, listPage, EMAIL_PATTERN, post, moreDetails, toast } from "../app-core.js";

// The job's own fields, the same on New job and Edit job (R2-01). prefix is the form's id
// prefix (nj, ej); j holds the saved values (escaped, as in state), if any.
// Who pays you: the two kinds of client, as the forms show them.
export const ROLE_OPTIONS = [["Owner", "The homeowner or property owner"], ["GC", "A general contractor (you are the sub)"]];
export function roleLabel(role) { return unesc(role || "") === "GC" ? "General contractor" : "Homeowner or owner"; }

export function jobFields(form, prefix, j = {}) {
  const f = (o) => field({ form, ...o });
  return {
    name: f({ name: "name", id: `${prefix}-name`, label: "Job name", helpKey: "job_name", required: true, autocomplete: "off", value: unesc(j.name || "") }),
    address: f({ name: "address", id: `${prefix}-address`, label: "Address", helpKey: "job_address", required: true, autocomplete: "street-address", value: unesc(j.address || "") }),
    contract_number: f({ name: "contract_number", id: `${prefix}-contract`, label: "Contract number", helpKey: "contract_number", value: unesc(j.contract_number || "") }),
    contract_cents: moneyField({ form, name: "contract_cents", id: `${prefix}-amount`, label: "Contract amount ($)", helpKey: "contract_amount", required: true, placeholder: "48,500", cents: j.contract_cents }),
    retainage_pct: f({ name: "retainage_pct", id: `${prefix}-retainage`, label: "Retainage %", helpKey: "retainage_pct", value: String(j.retainage_pct || 0), inputmode: "decimal", autocomplete: "off" }),
  };
}

// New job (R1-05): a client already saved keeps who signs off and where pay apps go (changed
// in the Client playbook, never by a new job), so the form shows them instead of asking again.
// Matched like the server does: the same name, ignoring case.
function knownPayer(name) {
  const key = String(name || "").trim().toLowerCase();
  return key ? (state.payers || []).find(p => unesc(p.name).trim().toLowerCase() === key) || null : null;
}

function knownPayerText(p) {
  return p ? `Pay apps go to ${p.approver || p.name} · ${p.ap_email || "no email yet"}` : "";
}

// "Same as ABC Builders?" (Oct 23 note 5): a new client name that nearly matches a saved one
// ("ABC Builders Inc", "abc builders, LLC") is refused by the server with the saved client in
// same_customer, and the form asks. Nothing is merged without the person saying so: one button
// uses the saved client, the other keeps the typed name as a new client.
// keptNames[form] is the name the person chose to keep, sent again as new_customer: true.
export const keptNames = {};

// The place under the name box where the question's two buttons go.
export function sameCustomerBox(formKey) {
  return `<div class="span-2 same-customer form-inline" data-same-customer="${formKey}" hidden></div>`;
}

// After a refusal: show the two buttons when it carries same_customer. prefix names the form's
// actions, `${prefix}-same-use` and `${prefix}-same-keep`. Returns whether it showed them.
export function showSameCustomer(formKey, prefix, data) {
  const box = document.querySelector(`[data-same-customer="${formKey}"]`);
  const same = data && data.same_customer;
  if (!box || !same || !same.name) return false;
  box.innerHTML = `<button type="button" class="btn small" data-action="${prefix}-same-use" data-name="${esc(same.name)}" data-id="${esc(same.id || "")}">Use ${esc(same.name)}</button>`
    + `<button type="button" class="btn small secondary" data-action="${prefix}-same-keep" data-name="${esc(same.typed || "")}">Keep “${esc(same.typed || "")}” as a new client</button>`;
  box.hidden = false;
  return true;
}

export function hideSameCustomer(formKey) {
  const box = document.querySelector(`[data-same-customer="${formKey}"]`);
  if (box) { box.hidden = true; box.innerHTML = ""; }
}

// How many jobs use a client, finished ones included. The server's job_count counts them all;
// state.jobs holds only the open and recent ones, so it is the fallback for an older page.
// The card's "N jobs", the Merge question and Delete client all count this one way.
export function payerJobCount(p, jobs = []) {
  return Number.isInteger(p.job_count) ? p.job_count : jobs.filter(j => j.payer_id === p.id).length;
}

// Delete client, on Client playbook (Oct 23 note 47): owners only, and only for a client
// no job uses, finished jobs included. The server refuses the rest with the reason. canMerge:
// another client exists, so the Merge box is there to point at. p is escaped, as in state.
export function deletePayerHtml(p, { owner = false, jobs = [], canMerge = false } = {}) {
  if (!owner) return "";
  const n = payerJobCount(p, jobs);
  if (n > 0) return `<p class="sub delete-payer">A client with jobs can't be deleted, finished jobs included. ${canMerge ? `To remove ${p.name}, merge it into another client.` : "Its jobs need it."}</p>`;
  return `<div class="form-inline delete-payer"><button type="button" class="btn small danger" data-action="delete-payer" data-id="${p.id}" data-name="${p.name}">Delete client</button><span class="sub">No job uses ${p.name}.</span></div>`;
}

// As the Client box is typed in: swap the two contact boxes for the saved contact, or back.
export function syncNewJobCustomer() {
  const form = document.querySelector('[data-form-key="newjob"]');
  if (!form) return;
  hideSameCustomer("newjob");
  const p = knownPayer(form.querySelector('[data-field="customer"]')?.value);
  const note = form.querySelector("[data-known-payer]");
  if (note) { note.hidden = !p; note.querySelector("[data-known-payer-text]").innerHTML = knownPayerText(p); }
  const approver = form.querySelector('[data-payer-slot="approver"]'), email = form.querySelector('[data-payer-slot="ap_email"]');
  if (approver) approver.hidden = !!p;
  if (email) email.hidden = !!(p && p.ap_email);
}

// Leaving the Client box fills "Who signs off" with the client when it is still empty: a
// homeowner signs off on their own bills. A saved client sets "Who pays you?" to theirs, and
// a general contractor's retainage to what their last job held (R2-01), unless already typed.
export function prefillApprover() {
  const form = document.querySelector('[data-form-key="newjob"]');
  const customer = form?.querySelector('[data-field="customer"]')?.value.trim();
  if (!customer) return;
  const known = knownPayer(customer);
  const set = (name, value) => {
    const box = form.querySelector(`[data-field="${name}"]`);
    if (!box) return;
    box.value = value;
    (formDraft.newjob ||= {})[name] = value;
  };
  if (!known) {
    const box = form.querySelector('[data-field="approver"]');
    if (box && !box.value.trim()) set("approver", customer);
    return;
  }
  if (known.role === "Owner" || known.role === "GC") set("role", known.role);
  prefillRetainage();
}

// A general contractor usually holds the same retainage job after job: start from their last one.
export function prefillRetainage() {
  const form = document.querySelector('[data-form-key="newjob"]');
  const known = knownPayer(form?.querySelector('[data-field="customer"]')?.value);
  const role = form?.querySelector('[data-field="role"]')?.value;
  if (!known || role !== "GC" || (formDraft.newjob || {}).retainage_touched) return;
  const last = [...state.jobs].reverse().find(j => (j.payer_id || (j.payer && j.payer.id)) === known.id);
  const box = form.querySelector('[data-field="retainage_pct"]');
  if (!last || !box) return;
  box.value = String(last.retainage_pct || 0);
  (formDraft.newjob ||= {}).retainage_pct = box.value;
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  jobs() {
    const open = state.jobs.filter(j => !j.closed);
    const closed = state.closed_jobs_total || 0;
    const f = (o) => field({ form: "newjob", ...o });
    const known = knownPayer(draftValue("newjob", "customer"));
    const jf = jobFields("newjob", "nj", { retainage_pct: 0 });
    return `
      <div class="top">
        <h1>Jobs</h1>
        <button class="btn" data-action="toggle-form" data-form="newjob">${formOpen.newjob ? "Cancel" : "New job"}</button>
      </div>
      ${formOpen.newjob ? `
        <div class="card" data-form-key="newjob">
          <h2>New job</h2>
          <p class="sub">Fields marked <span class="req">*</span> are required.</p>
          ${formErrorLine("newjob")}
          <div class="form-grid">
            ${jf.name}
            ${jf.address}
            ${f({ name: "customer", id: "nj-customer", label: "Client", required: true, autocomplete: "off", attrs: 'list="nj-customers"' })}
            <datalist id="nj-customers">${(state.payers || []).map(p => `<option value="${p.name}"></option>`).join("")}</datalist>
            ${sameCustomerBox("newjob")}
            ${f({ name: "role", id: "nj-role", label: "Who pays you?", helpKey: "role", help: "Sets the paperwork they usually need and when to follow up. You can change both later.", options: ROLE_OPTIONS })}
            ${jf.contract_cents}
            <p class="span-2 sub known-payer" data-known-payer${known ? "" : " hidden"}><span data-known-payer-text>${knownPayerText(known)}</span>.
              <button class="linkish" data-action="nav" data-view="playbook">Change in the Client playbook</button></p>
          </div>
          <details class="more-details" data-more="newjob"${moreOpen("newjob") ? " open" : ""}>
            <summary>More details (optional)</summary>
            <div class="form-grid">
              ${jf.contract_number}
              ${jf.retainage_pct}
              ${f({ name: "phone", id: "nj-phone", label: "Phone", helpKey: "customer_phone", type: "tel", autocomplete: "off" })}
              <div data-payer-slot="approver"${known ? " hidden" : ""}>${f({ name: "approver", id: "nj-approver", label: "Who signs off on your bills", helpKey: "approver", placeholder: "The client, unless you say otherwise" })}</div>
              <div data-payer-slot="ap_email"${known && known.ap_email ? " hidden" : ""}>${f({ name: "ap_email", id: "nj-email", label: "Email for pay apps (can add later)", helpKey: "ap_email", type: "email", autocomplete: "off", placeholder: "name@example.com" })}</div>
              ${f({ name: "note", id: "nj-note", label: "Notes (kept on the job record)", helpKey: "job_notes", multiline: true, wide: true, placeholder: "Gate code, who to call, contract notes" })}
            </div>
          </details>
          <button class="btn" data-action="create-job">Save job</button>
        </div>` : ""}
      ${open.length ? `<div class="table-wrap">
        <table>
          <tr><th>Job</th><th>Code</th><th>Client</th><th>Status</th><th>Outstanding</th><th>Unbilled</th><th>Held back</th><th>Next</th></tr>
          ${open.map(j => `
            <tr>
              <td><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a><div class="sub row-sub">${j.status_label}</div></td>
              <td>${j.code}</td>
              <td>${j.payer ? j.payer.name : "—"}</td>
              <td>${statusPill(j)}</td>
              <td class="num">${money(j.money.outstanding_cents)}</td>
              <td class="num">${money(j.money.unbilled_completed_cents)}</td>
              <td class="num">${money(j.money.retainage_open_cents)}</td>
              <td class="next-action">${nextStep(j)}</td>
            </tr>`).join("")}
        </table>
      </div>` : formOpen.newjob ? "" : emptyState(state.jobs.length || state.closed_jobs_total ? "No open jobs" : "No jobs yet", state.jobs.length || state.closed_jobs_total ? "Every job is closed. Closed jobs are under Job history." : "Create a job. Its record holds notes and files until a pay app needs them.", `<button class="btn" data-action="toggle-form" data-form="newjob">New job</button>`)}
      ${closed ? `<p class="sub">${closed} closed job${closed === 1 ? " is" : "s are"} under <button class="linkish" data-action="nav" data-view="history">Job history</button>.</p>` : ""}
    `;
  },
  history() {
    const page = listPage("history");
    const closed = page ? page.entries : [];
    const totalCollected = state.totals.closed_collected_cents || 0;
    return `
      <h1>Job history</h1>
      <div class="summary-row">
        <div class="metric"><div class="k">Finished jobs</div><div class="v">${state.closed_jobs_total || 0}</div></div>
        <div class="metric"><div class="k">Received on finished jobs</div><div class="v">${money(totalCollected)}</div></div>
      </div>
      ${closed.length ? `<div class="table-wrap">
        <table>
          <tr><th>Job</th><th>Client</th><th>Contract total</th><th data-help="collected">Received</th><th>Finished</th><th>Notes</th></tr>
          ${closed.map(j => `
            <tr>
              <td><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a></td>
              <td>${j.payer.name}</td>
              <td class="num">${money(j.money.contract_total_cents)}</td>
              <td class="num">${money(j.money.collected_cents)}</td>
              <td>${fmtDate(j.closed_at || j.completed_at)}</td>
              <td>${j.done_notes || j.status_label}</td>
            </tr>`).join("")}
        </table>
      </div>${moreButton("history", page)}` : !page ? `<p class="sub">Loading…</p>` : emptyState("No finished jobs yet")}
    `;
  },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in app.js.
export const actions = {
  "create-job": async () => {
    const v = formValues("newjob");
    const errors = {};
    if (!v.name) errors.name = "Enter the job name.";
    if (!v.address) errors.address = "Enter the job address.";
    if (!v.customer) errors.customer = "Enter the client: who pays for this job.";
    // A saved client keeps its contact (shown instead of the two boxes); a new one may leave
    // both blank: who signs off defaults to the client, the email is asked for before sending.
    const known = knownPayer(v.customer);
    const approver = known ? "" : v.approver;
    const apEmail = known && known.ap_email ? "" : v.ap_email;
    if (apEmail && !EMAIL_PATTERN.test(apEmail)) errors.ap_email = "Enter the email pay apps go to, like ap@builder.com, or leave it blank for now.";
    const amount = centsFrom(v.contract_cents);
    if (amount.error || amount.cents <= 0) errors.contract_cents = v.contract_cents && amount.error ? amount.error : "Enter the contract amount, like 48,500.";
    const ret = v.retainage_pct ? percentFrom(v.retainage_pct) : { value: 0, error: "" };
    if (ret.error || !Number.isInteger(ret.value) || ret.value > 20) errors.retainage_pct = "Enter a whole percent from 0 to 20.";
    if (refuseForm("newjob", errors)) return;
    const r = await post("/api/jobs", {
      name: v.name, address: v.address, customer: v.customer, approver,
      ap_email: apEmail, phone: v.phone, role: v.role || "Owner",
      contract_number: v.contract_number, note: v.note,
      contract_cents: amount.cents, retainage_pct: ret.value,
      new_customer: !known && keptNames.newjob === v.customer,
    }, { formKey: "newjob", close: "newjob" });
    if (r && !r.ok) showSameCustomer("newjob", "nj", r.data);
    if (!r || !r.ok) return;
    delete keptNames.newjob;
    delete moreDetails.newjob;
    toast("Job saved");
    const made = state.jobs[state.jobs.length - 1];
    if (made) nav("job", made.id);
  },
  // "Same as ABC Builders?": put the saved name in the Client box and save the job with it.
  "nj-same-use": (el) => {
    const box = document.querySelector('[data-form-key="newjob"] [data-field="customer"]');
    if (!box) return;
    box.value = el.dataset.name || "";
    (formDraft.newjob ||= {}).customer = box.value;
    syncNewJobCustomer();
    prefillApprover();
    return actions["create-job"]();
  },
  // ...or keep the name as typed: a new client, on purpose.
  "nj-same-keep": (el) => {
    keptNames.newjob = el.dataset.name || "";
    hideSameCustomer("newjob");
    return actions["create-job"]();
  },
};
