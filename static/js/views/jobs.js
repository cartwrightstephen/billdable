// Jobs: the open jobs with New job and Add to a job record, and Job history. The job's own fields
// are shared with Edit job; Import contract fills New job and Add client.
import { esc, unesc, escDeep, money, centsFrom, percentFrom, dollarsText, fmtDate } from "../format.js";
import { nextStep, statusPill, CLIENT_TYPES, clientTypeLabel, openJobs, RECORD_KINDS, startDateLimits } from "../jobs.js";
import { pathFor } from "../routes.js";
import { moreButton } from "../dom.js";
import { field, moneyField, formErrorLine, refuseForm, formValues, formDraft, formErrors, draftValue, clearForm } from "../forms.js";
import { checkFields, US_STATES } from "../validate.js";
import { readJson, sendFile } from "../net.js";
import { pasteGuess } from "../inbox.js";
import { localDay } from "../tasks.js";
import { termText } from "../terms.js";
import { state, me, formOpen, teamData, setTeamData, onSessionEnd } from "../state.js";
import { nav, moreOpen, emptyState, listPage, post, moreDetails, toast, render, load, clearJobDetails } from "../app-core.js";

// Who pays you, as the older pages had it: Owner or GC. Client type (J2) is the finer word.
export const ROLE_OPTIONS = [["Owner", "The homeowner or property owner"], ["GC", "A general contractor (you are the sub)"]];
export function roleLabel(role) { return unesc(role || "") === "GC" ? "General contractor" : "Homeowner or owner"; }
export { clientTypeLabel };

// The job's own fields, the same on New job and Edit job (R2-01). prefix is the form's id
// prefix (nj, ej); j holds the saved values (escaped, as in state), if any. The address is four
// boxes (J1): Street on its own row, then City, State and ZIP. An older job's one-line address
// comes back from the server in address_parts (all of it as the street when it doesn't split).
export function jobFields(form, prefix, j = {}) {
  const f = (o) => field({ form, ...o });
  const parts = j.address_parts || {};
  const part = (k) => unesc(parts[k] || "");
  const limits = startDateLimits();
  const mark = (name) => contractMark(form, name);
  const stateCode = unesc(draftValue(form, "state", part("state")));
  return {
    name: f({ name: "name", id: `${prefix}-name`, label: "Job name", helpKey: "job_name", required: true, autocomplete: "off", rule: "name", value: unesc(j.name || ""), ...mark("name") }),
    address: f({ name: "street", id: `${prefix}-street`, label: "Street address", helpKey: "job_address", required: true, autocomplete: "address-line1", rule: "street", wide: true, value: part("street"), ...mark("street") })
      + `<div class="span-2 address-row">
        ${f({ name: "city", id: `${prefix}-city`, label: "City", required: true, autocomplete: "address-level2", rule: "city", value: part("city"), ...mark("city") })}
        ${f({ name: "state", id: `${prefix}-state`, label: "State", required: true, autocomplete: "address-level1", rule: "state", value: stateCode, ...mark("state"),
          options: [["", "Pick…"], ...(stateCode && !US_STATES.includes(stateCode) ? [[stateCode, stateCode]] : []), ...US_STATES.map(s => [s, s])] })}
        ${f({ name: "zip", id: `${prefix}-zip`, label: "ZIP", required: true, autocomplete: "postal-code", inputmode: "numeric", rule: "zip", value: part("zip"), ...mark("zip") })}
      </div>`,
    contract_number: f({ name: "contract_number", id: `${prefix}-contract`, label: "Contract number", helpKey: "contract_number", rule: "contract_number", value: unesc(j.contract_number || ""), ...mark("contract_number") }),
    contract_cents: moneyField({ form, name: "contract_cents", id: `${prefix}-amount`, label: "Contract amount ($)", helpKey: "contract_amount", required: true, placeholder: "48,500", cents: j.contract_cents, ...mark("contract_cents") }),
    retainage_pct: f({ name: "retainage_pct", id: `${prefix}-retainage`, label: "Retainage %", helpKey: "retainage_pct", value: String(j.retainage_pct || 0), inputmode: "decimal", autocomplete: "off", ...mark("retainage_pct") }),
    // J8: the day the job started; New job starts at today, an older job with none stays blank.
    started_on: f({ name: "started_on", id: `${prefix}-start`, label: "Start date", type: "date", value: unesc(j.started_on || (j.id ? "" : limits.today)),
      ...mark("started_on"), attrs: `min="${limits.min}" max="${limits.max}"${contractMark(form, "started_on").attrs ? " data-from-contract" : ""}` }),
  };
}

// The address boxes' rules and the messages for a blank one, as the server's (domain.check_address).
const ADDRESS_RULES = { street: "street", city: "city", state: "state", zip: "zip" };

// {field: message} for a job form's values: the address, name and contract number, by the rules.
// With `saved` (Edit job: the values the boxes opened with), a box left as it was isn't checked
// (the address's four boxes as one: all checked when any changed),
// so a contract number or address saved before the rules doesn't block an unrelated edit (C4);
// the server does the same.
export function jobFieldErrors(v, saved = null) {
  const errors = checkFields(v, { ...ADDRESS_RULES, name: "name", contract_number: "contract_number" }, ["street", "city", "state", "zip"]);
  if (!v.name) errors.name = "Enter the job name.";
  if (saved) {
    const same = (k) => String(v[k] ?? "").trim() === String(saved[k] ?? "").trim();
    // The address's four boxes go together: checked only when one of them changed.
    const addressSame = Object.keys(ADDRESS_RULES).every(same);
    for (const k of Object.keys(errors)) {
      if (k in ADDRESS_RULES ? addressSame : (k in saved && same(k))) delete errors[k];
    }
  }
  return errors;
}

// What Edit job's boxes opened with (jobFields' values), for jobFieldErrors' `saved`.
export function savedJobFields(j) {
  const parts = (j && j.address_parts) || {};
  const out = { name: unesc((j && j.name) || ""), contract_number: unesc((j && j.contract_number) || "") };
  for (const k of ["street", "city", "state", "zip"]) out[k] = unesc(parts[k] || "");
  return out;
}

// The person a job's tasks go to (assignee_id): a team member, the one who made it unless another
// is picked. The team is asked for when the form opens; with one person there is nothing to pick.
let teamAsked = false;
onSessionEnd(() => { teamAsked = false; });
function askTeam() {
  if (teamData || teamAsked || typeof fetch !== "function") return;
  teamAsked = true;
  fetch("/api/team").then(r => (r.ok ? readJson(r) : null)).then(data => { if (data) { setTeamData(escDeep(data)); render(); } }).catch(() => {});
}

export function assigneeField(form, prefix, current = "") {
  askTeam();
  const users = (teamData && teamData.users) || [];
  if (users.length < 2) return "";
  return field({ form, name: "assignee_id", id: `${prefix}-assignee`, label: "Assigned to", value: unesc(current || (me && me.id) || ""),
    options: users.map(u => [unesc(u.id), unesc(u.name || u.email)]) });
}

// New job (R1-05): a client already saved keeps who signs off and where pay apps go (changed
// in the Client playbook, never by a new job), so the form shows them instead of asking again.
// Matched like the server does: the same name, ignoring case.
function knownPayer(name) {
  const key = String(name || "").trim().toLowerCase();
  return key ? (state.payers || []).find(p => unesc(p.name).trim().toLowerCase() === key) || null : null;
}

function knownPayerText(p) {
  return p ? `${clientTypeLabel(p)}. Pay apps go to ${p.approver || p.name} · ${p.ap_email || "no email yet"}` : "";
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

// Delete client, on Client playbook (Oct 23 note 47): for those who can edit client settings
// (the company permission, as the server route), and only for a client
// no job uses, finished jobs included. The server refuses the rest with the reason. canMerge:
// another client exists, so the Merge box is there to point at. p is escaped, as in state.
export function deletePayerHtml(p, { owner = false, jobs = [], canMerge = false } = {}) {
  if (!owner) return "";
  const n = payerJobCount(p, jobs);
  if (n > 0) return `<p class="sub delete-payer">A client with jobs can't be deleted, finished jobs included. ${canMerge ? `To remove ${p.name}, merge it into another client.` : "Its jobs need it."}</p>`;
  return `<div class="form-inline delete-payer"><button type="button" class="btn small danger" data-action="delete-payer" data-id="${p.id}" data-name="${p.name}">Delete client</button><span class="sub">No job uses ${p.name}.</span></div>`;
}

// As the Client box is typed in: a saved client's type and contact are shown instead of asked.
export function syncNewJobCustomer() {
  const form = document.querySelector('[data-form-key="newjob"]');
  if (!form) return;
  hideSameCustomer("newjob");
  const p = knownPayer(form.querySelector('[data-field="customer"]')?.value);
  const note = form.querySelector("[data-known-payer]");
  if (note) { note.hidden = !p; note.querySelector("[data-known-payer-text]").innerHTML = knownPayerText(p); }
  const type = form.querySelector('[data-payer-slot="client_type"]');
  const approver = form.querySelector('[data-payer-slot="approver"]'), email = form.querySelector('[data-payer-slot="ap_email"]');
  if (type) type.hidden = !!p;
  for (const slot of ["payapp_due_day", "payment_terms_days"]) {
    const box = form.querySelector(`[data-payer-slot="${slot}"]`);
    if (box) box.hidden = !!p;
  }
  if (approver) approver.hidden = !!p;
  if (email) email.hidden = !!(p && p.ap_email);
}

// Leaving the Client box fills "Who signs off" with the client when it is still empty: a
// homeowner signs off on their own bills. A saved general contractor's retainage starts at what
// their last job held (R2-01), unless already typed.
export function prefillApprover() {
  const form = document.querySelector('[data-form-key="newjob"]');
  const customer = form?.querySelector('[data-field="customer"]')?.value.trim();
  if (!customer) return;
  const known = knownPayer(customer);
  if (!known) {
    const box = form.querySelector('[data-field="approver"]');
    if (box && !box.value.trim()) {
      box.value = customer;
      (formDraft.newjob ||= {}).approver = customer;
    }
    return;
  }
  prefillRetainage();
}

// A general contractor usually holds the same retainage job after job: start from their last one.
export function prefillRetainage() {
  const form = document.querySelector('[data-form-key="newjob"]');
  const known = knownPayer(form?.querySelector('[data-field="customer"]')?.value);
  if (!known || unesc(known.role || "") !== "GC" || (formDraft.newjob || {}).retainage_touched) return;
  const last = [...state.jobs].reverse().find(j => (j.payer_id || (j.payer && j.payer.id)) === known.id);
  const box = form.querySelector('[data-field="retainage_pct"]');
  if (!last || !box) return;
  box.value = String(last.retainage_pct || 0);
  (formDraft.newjob ||= {}).retainage_pct = box.value;
}

// ---------- Import contract (G4, S12) ----------

// What the last contract read for a form found: { form, name, file, text, found, notes, sov, error }.
// The file (or pasted text) is filed on the new job's record as its contract once the job is saved.
let contractRead = null;
onSessionEnd(() => { contractRead = null; });

const MORE_FIELDS = ["contract_number", "retainage_pct", "started_on", "phone", "ap_email", "payapp_due_day", "payment_terms_days"];

// A form closed (saved or cancelled) forgets what its contract filled.
export function forgetClosedContract() {
  if (contractRead && !formOpen[contractRead.form]) contractRead = null;
}

// "From the contract" under a box the import filled, and the box marked, until the form is saved.
export function contractMark(form, name) {
  const hit = contractRead && contractRead.form === form && (contractRead.found || []).includes(name);
  return hit ? { help: "From the contract", attrs: "data-from-contract" } : {};
}

// The Import contract button, the paste box and what the last read said, for New job or Add client.
export function contractImportHtml(form) {
  const r = contractRead && contractRead.form === form ? contractRead : null;
  const sovLine = r && r.sov && form === "newjob"
    ? `<label class="check-line"><input type="checkbox" id="nj-use-sov"${r.useSov === false ? "" : " checked"}> Set up the schedule of values from the contract (${r.sov.lines.length} line${r.sov.lines.length === 1 ? "" : "s"}, ${money(r.sov.total_cents)})</label><div id="nj-sov-choice">${sovTotalChoice(r)}</div>` : "";
  return `<div class="contract-import" data-contract-import="${form}">
      <div class="form-inline">
        <button type="button" class="btn small secondary" data-action="contract-pick" data-for="${form}">${form === "newjob" ? "Import contract" : "Import a contract"}</button>
        <span class="sub">Word, PDF or text. You check each box before saving.</span>
      </div>
      <details class="contract-paste"><summary>Paste the contract's text</summary>
        <textarea id="contract-text-${form}" rows="5" aria-label="The contract's text"></textarea>
        <div class="form-inline"><button type="button" class="btn small secondary" data-action="contract-paste" data-for="${form}">Read it</button></div>
      </details>
      ${r ? `<p class="sub contract-status${r.error ? " field-error" : ""}" role="status">${r.error ? esc(r.error) : r.found.length
        ? `Filled ${r.found.length} box${r.found.length === 1 ? "" : "es"} from ${esc(r.name)}. Each says From the contract.`
        : `Nothing to fill was found in ${esc(r.name)}.`}</p>` : `<p class="sub contract-status" role="status"></p>`}
      ${r && (r.notes || []).length ? `<ul class="contract-notes">${r.notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
      ${sovLine}
    </div>`;
}

// The contract amount typed in New job, in cents, or null.
function newJobAmount() {
  const a = centsFrom(draftValue("newjob", "contract_cents", ""));
  return a.error || !(a.cents > 0) ? null : a.cents;
}

// When the imported schedule of values doesn't add up to the contract amount, the server won't
// take it as it is (review C6): say by how much beside the checkbox, and offer to make the
// schedule's total the contract amount (sent as update_contract with the schedule).
export function sovTotalChoice(r) {
  const amount = newJobAmount();
  if (!r || !r.sov || r.useSov === false || amount === null || amount === r.sov.total_cents) return "";
  const diff = r.sov.total_cents - amount;
  return `<p class="sub sov-difference" id="nj-sov-diff">The schedule adds up to ${money(r.sov.total_cents)}; the contract amount is ${money(amount)}, ${money(Math.abs(diff))} ${diff > 0 ? "more" : "less"}.</p>
    <label class="check-line"><input type="checkbox" id="nj-sov-total"${r.useSovTotal ? " checked" : ""}> Use the schedule total as the contract amount</label>`;
}

// The difference line and its checkbox, drawn again where they are (the rest of the form stays).
export function updateSovChoice(r = contractRead) {
  const box = document.getElementById("nj-sov-choice");
  if (box && "innerHTML" in box) box.innerHTML = sovTotalChoice(r);
  else render();
}

// What a read found, put in the form's boxes (as typed text, so the boxes show it).
function fillFromContract(form, fields) {
  const d = (formDraft[form] ||= {});
  const put = (box, value) => { if (value !== undefined && value !== null && value !== "") d[box] = String(value); };
  if (form === "newjob") {
    for (const k of ["name", "street", "city", "state", "zip", "customer", "client_type", "contract_number", "started_on", "phone", "ap_email",
      "payapp_due_day", "payment_terms_days"]) put(k, fields[k]);
    if (Number.isInteger(fields.contract_cents)) put("contract_cents", dollarsText(fields.contract_cents));
    if (Number.isInteger(fields.retainage_pct)) { put("retainage_pct", fields.retainage_pct); d.retainage_touched = true; }
    if (MORE_FIELDS.some(k => k in fields)) moreDetails.newjob = true;
  } else {
    put("name", fields.customer);
    for (const k of ["client_type", "phone", "ap_email", "payapp_due_day", "payment_terms_days"]) put(k, fields[k]);
  }
  // The names of the boxes it filled, as the form calls them.
  const boxes = Object.keys(fields).map(k => (form !== "newjob" && k === "customer" ? "name" : k)).filter(k => k in d);
  // A box it filled drops the note an earlier value left under it.
  const errs = formErrors[form];
  if (errs) for (const box of boxes) delete errs[box];
  return boxes;
}

async function readContract(form, body, name, keep) {
  const status = document.querySelector(`[data-contract-import="${form}"] .contract-status`);
  if (status) status.textContent = "Reading…";
  const r = await sendFile("/api/contract/read", body, name, name.endsWith(".txt") ? "text/plain; charset=utf-8" : "");
  if (r.status === 401) { load(); return; }
  // What the status line calls it: the file's name, or "the pasted text".
  const shown = keep.text ? "the pasted text" : name;
  if (!r.ok || !r.data.ok) {
    contractRead = { form, name: shown, error: (r.data && r.data.error) || "Couldn't read that file.", found: [] };
  } else {
    formOpen[form] = true;
    const found = fillFromContract(form, r.data.fields || {});
    contractRead = { form, name: shown, ...keep, found, notes: r.data.notes || [], sov: r.data.sov || null };
  }
  render();
}

// The contract's file or text on the new job's record, and its schedule of values if asked for.
// r is the read the job was saved with, taken before the save closed the form.
async function fileContract(jobId, r) {
  if (!r || r.error) return;
  if (r.file) {
    const sent = await sendFile(`/api/import/file?job=${encodeURIComponent(jobId)}&file=1&kind=contract`, r.file, r.file.name);
    if (!sent.ok) toast(`The job is saved, but the contract file wasn't added to its record: ${(sent.data && sent.data.error) || "try Add a file on the job page"}.`);
  } else if (r.text) {
    await post(`/api/jobs/${jobId}/add-record`, { label: "Contract (pasted)", kind: "contract", note: r.text.slice(0, 5000) }, { quiet: true });
  }
  if (r.sov && r.useSov === false) {
    // The person unticked it: nothing to set up.
  } else if (r.sov) {
    const res = await post(`/api/jobs/${jobId}/sov`, { lines: r.sov.lines, op_pct: 0, update_contract: !!r.useSovTotal }, { quiet: true });
    if (res && res.ok) toast(r.useSovTotal ? "Job saved with the schedule of values from the contract and its total as the contract amount"
      : "Job saved with the schedule of values from the contract");
    else toast(`The job is saved, but the schedule of values wasn't set up: ${(res && res.data && res.data.error) || "set it up on the job page"}.`);
  }
  if (contractRead === r) contractRead = null;
  clearJobDetails();
  await load();
}

// ---------- Add to a job record (J4) ----------

function addRecordCardHtml() {
  const jobs = openJobs(state.jobs);
  // A job picked before it was finished (or deleted) is no longer offered.
  const wanted = draftValue("jobrecord", "job", "");
  const picked = jobs.some(j => unesc(j.id) === wanted) ? wanted : "";
  const kinds = Object.entries(RECORD_KINDS).map(([k, v]) => [k, termText(v)]);
  return `<div class="card add-to-record" data-form-key="jobrecord">
      <h2>Add to a job record</h2>
      ${formErrorLine("jobrecord")}
      <div class="form-grid">
        ${field({ form: "jobrecord", name: "job", id: "jr-job", label: "Job", required: true, options: [["", "Pick a job…"], ...jobs.map(j => [unesc(j.id), `${unesc(j.name)} (${unesc(j.code)})`])], value: picked })}
      </div>
      <div class="dropzone small">
        <div class="capture-buttons">
          <button class="btn small" data-action="${picked ? "pick-files" : "jr-pick"}" data-job="${esc(picked)}" data-capture="1">Take a photo</button>
          <button class="btn small secondary" data-action="${picked ? "pick-files" : "jr-pick"}" data-job="${esc(picked)}">Add a file</button>
        </div>
      </div>
      <details class="paste-job"><summary>Paste an email, a text thread, or notes</summary>
        <textarea id="jr-paste" data-field="text" rows="5" aria-label="What to add">${esc(draftValue("jobrecord", "text"))}</textarea>
        <div class="form-inline"><button class="btn small" data-action="jr-paste">Add to job</button></div>
      </details>
      <details class="add-record"${formOpen.jobrecordnote ? " open" : ""}><summary>Add a note to the record</summary>
        <div class="form-row">
          ${field({ form: "jobrecord", name: "label", id: "jr-label", label: "What it is", placeholder: "Rough-in inspection passed" })}
          ${field({ form: "jobrecord", name: "note", id: "jr-note", label: "Note (optional)" })}
          ${field({ form: "jobrecord", name: "kind", id: "jr-kind", label: "Kind", options: kinds })}
          <button class="btn small" data-action="jr-add-note">Add to record</button>
        </div>
      </details>
    </div>`;
}

// The job picked on the card, or a refusal under the picker.
function pickedJob() {
  const id = document.getElementById("jr-job")?.value || draftValue("jobrecord", "job", "");
  if (!id) { refuseForm("jobrecord", { job: "Pick the job to add to." }); return ""; }
  return id;
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  jobs() {
    forgetClosedContract();
    const open = openJobs(state.jobs);
    const closed = state.closed_jobs_total || 0;
    const f = (o) => field({ form: "newjob", ...o });
    const known = knownPayer(draftValue("newjob", "customer"));
    const jf = jobFields("newjob", "nj", { retainage_pct: 0 });
    const mark = (name) => contractMark("newjob", name);
    return `
      <div class="top">
        <h1>Open jobs</h1>
        <div class="form-inline">
          ${open.length ? `<button class="btn secondary" data-action="toggle-form" data-form="addrecord">${formOpen.addrecord ? "Cancel" : "Add to a job record"}</button>` : ""}
          <button class="btn" data-action="toggle-form" data-form="newjob">${formOpen.newjob ? "Cancel" : "New job"}</button>
        </div>
      </div>
      ${formOpen.addrecord && open.length ? addRecordCardHtml() : ""}
      ${formOpen.newjob ? `
        <div class="card" data-form-key="newjob">
          <h2>New job</h2>
          ${contractImportHtml("newjob")}
          <p class="sub">Fields marked <span class="req">*</span> are required.</p>
          ${formErrorLine("newjob")}
          <div class="form-grid">
            ${jf.name}
            ${jf.address}
            ${f({ name: "customer", id: "nj-customer", label: "Client", required: true, autocomplete: "off", rule: "name", ...mark("customer"), attrs: `list="nj-customers"${mark("customer").attrs ? " data-from-contract" : ""}` })}
            <datalist id="nj-customers">${(state.payers || []).map(p => `<option value="${p.name}"></option>`).join("")}</datalist>
            ${sameCustomerBox("newjob")}
            <div data-payer-slot="client_type"${known ? " hidden" : ""}>${f({ name: "client_type", id: "nj-client-type", label: "Client type", helpKey: "role", options: CLIENT_TYPES, value: "gc", ...mark("client_type") })}</div>
            ${jf.contract_cents}
            <p class="span-2 sub known-payer" data-known-payer${known ? "" : " hidden"}><span data-known-payer-text>${knownPayerText(known)}</span>.
              <button class="linkish" data-action="nav" data-view="playbook">Change in the Client playbook</button></p>
          </div>
          <details class="more-details" data-more="newjob"${moreOpen("newjob") ? " open" : ""}>
            <summary>More details (optional)</summary>
            <div class="form-grid">
              ${jf.contract_number}
              ${jf.retainage_pct}
              ${jf.started_on}
              ${assigneeField("newjob", "nj")}
              ${f({ name: "phone", id: "nj-phone", label: "Phone", helpKey: "customer_phone", type: "tel", autocomplete: "off", rule: "phone", ...mark("phone") })}
              <div data-payer-slot="approver"${known ? " hidden" : ""}>${f({ name: "approver", id: "nj-approver", label: "Who signs off on your bills", helpKey: "approver", placeholder: "The client, unless you say otherwise" })}</div>
              <div data-payer-slot="payapp_due_day"${known ? " hidden" : ""}>${f({ name: "payapp_due_day", id: "nj-due-day", label: "Pay apps due by (day of the month)", helpKey: "payapp_due_day", inputmode: "numeric", autocomplete: "off", placeholder: "e.g. 25", ...mark("payapp_due_day") })}</div>
              <div data-payer-slot="payment_terms_days"${known ? " hidden" : ""}>${f({ name: "payment_terms_days", id: "nj-terms", label: "Payment terms, days", helpKey: "payment_terms_days", inputmode: "numeric", autocomplete: "off", placeholder: "30", ...mark("payment_terms_days") })}</div>
              <div data-payer-slot="ap_email"${known && known.ap_email ? " hidden" : ""}>${f({ name: "ap_email", id: "nj-email", label: "Email for pay apps (can add later)", helpKey: "ap_email", type: "email", autocomplete: "off", placeholder: "name@example.com", ...mark("ap_email") })}</div>
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
              <td><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a><div class="sub row-sub">${j.status_label}</div>
                <button class="linkish row-record" data-action="jr-open" data-id="${j.id}" aria-label="Add to the record of ${j.name}">Add to record</button></td>
              <td>${j.code}</td>
              <td>${j.payer ? j.payer.name : (((state.payers || []).find(p => p.id === j.payer_id) || {}).name || "—")}</td>
              <td>${statusPill(j)}</td>
              <td class="num">${money(j.money.outstanding_cents)}</td>
              <td class="num">${money(j.money.unbilled_completed_cents)}</td>
              <td class="num">${money(j.money.retainage_open_cents)}</td>
              <td class="next-action">${nextStep(j)}</td>
            </tr>`).join("")}
        </table>
      </div>` : formOpen.newjob ? "" : emptyState(state.jobs.length || state.closed_jobs_total ? "No open jobs" : "No jobs yet", "", `<button class="btn" data-action="toggle-form" data-form="newjob">New job</button>`)}
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
          <tr><th>Job</th><th>Client</th><th>Contract total</th><th data-help="collected">Received</th><th>Started</th><th>Finished</th><th>Notes</th></tr>
          ${closed.map(j => `
            <tr>
              <td><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a></td>
              <td>${j.payer ? j.payer.name : (((state.payers || []).find(p => p.id === j.payer_id) || {}).name || "—")}</td>
              <td class="num">${money(j.money.contract_total_cents)}</td>
              <td class="num">${money(j.money.collected_cents)}</td>
              <td>${j.started_on ? fmtDate(j.started_on) : "—"}</td>
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
    const errors = jobFieldErrors(v);
    // A saved client keeps its type and contact (shown instead of the boxes); a new one may leave
    // both contact boxes blank: who signs off defaults to the client, the email is asked for before sending.
    const known = knownPayer(v.customer);
    const approver = known ? "" : v.approver;
    const apEmail = known && known.ap_email ? "" : v.ap_email;
    Object.assign(errors, checkFields({ customer: v.customer, phone: v.phone }, { customer: "name", phone: "phone" }));
    if (!v.customer) errors.customer = "Enter the client: who pays for this job.";
    if (apEmail && checkFields({ ap_email: apEmail }, { ap_email: "email" }).ap_email) errors.ap_email = "Enter the email pay apps go to, like ap@builder.com, or leave it blank for now.";
    const amount = centsFrom(v.contract_cents);
    if (amount.error || amount.cents <= 0) errors.contract_cents = v.contract_cents && amount.error ? amount.error : "Enter the contract amount, like 48,500.";
    const ret = v.retainage_pct ? percentFrom(v.retainage_pct) : { value: 0, error: "" };
    if (ret.error || !Number.isInteger(ret.value) || ret.value > 20) errors.retainage_pct = "Enter a whole percent from 0 to 20.";
    // A new client's pay app day and terms (from the boxes, which an imported contract fills).
    const days = {};
    if (!known) {
      if (v.payapp_due_day && !(/^\d+$/.test(v.payapp_due_day) && +v.payapp_due_day >= 1 && +v.payapp_due_day <= 31)) errors.payapp_due_day = "Enter a day of the month from 1 to 31 for when pay apps are due, or leave it blank.";
      else if (v.payapp_due_day) days.payapp_due_day = Number(v.payapp_due_day);
      if (v.payment_terms_days && !(/^\d+$/.test(v.payment_terms_days) && +v.payment_terms_days >= 1 && +v.payment_terms_days <= 365)) errors.payment_terms_days = "Enter the days they have to pay as a whole number from 1 to 365.";
      else if (v.payment_terms_days) days.payment_terms_days = Number(v.payment_terms_days);
    }
    // The contract read this job is saved with: the save closes the form, which forgets it.
    const read = contractRead && contractRead.form === "newjob" ? contractRead : null;
    const useSov = document.getElementById("nj-use-sov");
    if (read && useSov) read.useSov = useSov.checked;
    const useTotal = document.getElementById("nj-sov-total");
    if (read) read.useSovTotal = !!(useTotal && useTotal.checked);
    // A schedule that doesn't add up to the amount would be refused after the job is saved (C6).
    if (read && read.sov && read.useSov !== false && !read.useSovTotal && !errors.contract_cents && amount.cents !== read.sov.total_cents) {
      errors.contract_cents = `The schedule of values from the contract adds up to ${money(read.sov.total_cents)}. Change the amount, tick Use the schedule total as the contract amount, or untick the schedule.`;
    }
    if (refuseForm("newjob", errors)) return;
    const before = new Set(state.jobs.map(j => j.id));
    const r = await post("/api/jobs", {
      name: v.name, street: v.street, city: v.city, state: v.state, zip: v.zip, customer: v.customer, approver,
      ap_email: apEmail, phone: v.phone, client_type: known ? "" : (v.client_type || "gc"),
      contract_number: v.contract_number, note: v.note, started_on: v.started_on || "", today: localDay(new Date()),
      ...(v.assignee_id ? { assignee_id: v.assignee_id } : {}),
      contract_cents: amount.cents, retainage_pct: ret.value, ...days,
      new_customer: !known && keptNames.newjob === v.customer,
    }, { formKey: "newjob", close: "newjob" });
    if (r && !r.ok) showSameCustomer("newjob", "nj", r.data);
    if (!r || !r.ok) return;
    delete keptNames.newjob;
    delete moreDetails.newjob;
    toast("Job saved");
    const made = state.jobs.find(j => !before.has(j.id));
    if (made) {
      await fileContract(made.id, read);
      nav("job", made.id);
    }
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
  // Import contract: a file picked here is read on the server; nothing is saved until Save.
  "contract-pick": (el) => {
    const form = el.dataset.for;
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".docx,.pdf,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain";
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      if (file) readContract(form, file, file.name, { file });
    });
    input.click();
  },
  "contract-paste": (el) => {
    const form = el.dataset.for;
    const text = (document.getElementById(`contract-text-${form}`)?.value || "").trim();
    if (!text) { toast("Paste the contract's text first."); return; }
    return readContract(form, text, "pasted text.txt", { text });
  },
  // Add to a job record from the Jobs page (J4): the card opens with the row's job picked.
  "jr-open": (el, id) => {
    formOpen.addrecord = true;
    (formDraft.jobrecord ||= {}).job = unesc(id || "");
    render();
    requestAnimationFrame(() => document.querySelector(".add-to-record")?.scrollIntoView({ block: "start", behavior: "smooth" }));
  },
  // Take a photo or Add a file before a job is picked: the picker says so. With a job picked,
  // the buttons are the job page's own (pick-files, with the job).
  "jr-pick": () => { pickedJob(); },
  "jr-paste": async () => {
    const job = pickedJob();
    const text = (document.getElementById("jr-paste")?.value || "").trim();
    if (!job) return;
    if (!text) { toast("Paste what to add first."); document.getElementById("jr-paste")?.focus(); return; }
    const r = await post("/api/import/text?file=1", { text, source: pasteGuess(text).source, job_id: job }, { quiet: true });
    if (r && r.ok) { clearForm("jobrecord"); (formDraft.jobrecord ||= {}).job = job; toast("Added to the job record."); render(); }
  },
  "jr-add-note": async () => {
    const job = pickedJob();
    if (!job) return;
    const v = formValues("jobrecord");
    if (refuseForm("jobrecord", v.label ? {} : { label: "Enter what this is, like \"Rough-in inspection passed\"." })) return;
    formOpen.jobrecordnote = true;
    const r = await post(`/api/jobs/${job}/add-record`, { label: v.label, note: v.note, kind: v.kind || "note" }, { formKey: "jobrecord", quiet: true });
    if (r && r.ok) { (formDraft.jobrecord ||= {}).job = job; toast("Added to the job record."); render(); }
  },
};

// The SOV choice is kept across a redraw of the form.
if (typeof document !== "undefined" && document.addEventListener) {
  document.addEventListener("change", (e) => {
    if (e.target && e.target.id === "nj-use-sov" && contractRead) { contractRead.useSov = e.target.checked; render(); }
    if (e.target && e.target.id === "nj-sov-total" && contractRead) contractRead.useSovTotal = e.target.checked;
    // A changed contract amount redraws the schedule's difference line (C6), in place: a whole
    // redraw on leaving the box would replace Save job under the click that left it.
    if (e.target && e.target.id === "nj-amount" && contractRead && contractRead.sov) updateSovChoice();
    // The job picked on Add to a job record: its buttons now add to that job.
    if (e.target && e.target.id === "jr-job" && state) { (formDraft.jobrecord ||= {}).job = e.target.value; render(); }
  });
}
