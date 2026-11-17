// Jobs: the open jobs with New job and New job record, and Job history. The job's own fields
// are shared with Edit job; Import contract fills New job and Add client.
import { esc, unesc, escDeep, money, centsFrom, percentFrom, dollarsText, fmtDate } from "../format.js";
import { nextStep, statusPill, CLIENT_TYPES, clientTypeLabel, openJobs, startDateLimits, newJobRetainage } from "../jobs.js";
import { pathFor } from "../routes.js";
import { moreButton } from "../dom.js";
import { field, moneyField, formErrorLine, refuseForm, formValues, formDraft, formErrors, draftValue } from "../forms.js";
import { checkFields, US_STATES } from "../validate.js";
import { readJson, sendFile } from "../net.js";
import { localDay } from "../tasks.js";
import { state, me, formOpen, teamData, setTeamData, onSessionEnd } from "../state.js";
import { nav, can, emptyState, listPage, post, toast, render, load, clearJobDetails, moreDetails } from "../app-core.js";

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
    contract_cents: moneyField({ form, name: "contract_cents", id: `${prefix}-amount`, label: "Contract amount ($)", helpKey: "contract_amount", required: true, cents: j.contract_cents, ...mark("contract_cents") }),
    retainage_pct: f({ name: "retainage_pct", id: `${prefix}-retainage`, label: "Retainage", suffix: "%", helpKey: "retainage_pct", value: String(j.retainage_pct ?? 0), inputmode: "decimal", autocomplete: "off", ...mark("retainage_pct") }),
    // J8: the day the job started; New job starts at today, an older job with none stays blank.
    started_on: f({ name: "started_on", id: `${prefix}-start`, label: "Start date", type: "date", value: unesc(j.started_on || (j.id ? "" : limits.today)),
      ...mark("started_on"), attrs: `min="${limits.min}" max="${limits.max}"${contractMark(form, "started_on").attrs ? " data-from-contract" : ""}` }),
    // Blank until it is known; never before the start date (domain.parse_end_date).
    anticipated_end_on: f({ name: "anticipated_end_on", id: `${prefix}-end`, label: "Anticipated end date", type: "date", value: unesc(j.anticipated_end_on || ""), ...mark("anticipated_end_on") }),
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
  if (v.anticipated_end_on && v.started_on && v.anticipated_end_on < v.started_on) errors.anticipated_end_on = `Enter an anticipated end date on or after the start date, ${v.started_on}.`;
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

// What a saved client still has to answer here (domain.playbook_missing): a blank box, a type
// never picked, or a signer that only defaulted to the client's name.
function payerMissing(p) {
  return (p.playbook_missing || ["approver", "ap_email", "phone"].filter(k => !p[k])).map(unesc);
}

function knownPayerText(p) {
  if (!p) return "";
  return `${clientTypeLabel(p)}. Pay apps go to ${p.approver || p.name} · ${p.ap_email || "no email yet"} · ${p.phone || "no phone yet"}${payerMissing(p).length ? ". Fill in or confirm the boxes below; they are saved to this client's playbook" : ""}`;
}

// New job asks which client it is for: a saved one or a new one (Decisions 3 and 4). With no
// clients saved yet there is nothing to pick. The choice is kept with what was typed.
function clientMode() {
  if (!(state.payers || []).length) return "new";
  return draftValue("newjob", "client_mode", "");
}

// Whether a client box stays out of the way: none before the choice; a saved client's boxes only
// for what it is missing, since what it has is changed in the Client playbook, never by a new job.
function payerSlotHidden(slot, known, mode) {
  if (known) return !payerMissing(known).includes(slot);
  return mode !== "new";
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

// "You already have a job called Peachtree" (Round 1, item 20): an open job of the same name is
// refused by the server with same_job, and the form offers that job or keeping this one as new.
let keptJobName = "";
onSessionEnd(() => { keptJobName = ""; });
export function showSameJob(data) {
  const box = document.querySelector('[data-same-job="newjob"]');
  const same = data && data.same_job;
  if (!box || !same || !same.id) return false;
  // The question is said here, beside its two answers, not under the name box: that line clears
  // when the box is left, and the buttons would move under the tap.
  if (formErrors.newjob) delete formErrors.newjob.name;
  const err = document.getElementById("nj-name-err");
  if (err) err.textContent = "";
  document.getElementById("nj-name")?.removeAttribute?.("aria-invalid");
  box.innerHTML = `<p class="same-job-q">You already have a job called ${esc(same.name)}.</p>`
    + `<button type="button" class="btn small" data-action="open-job" data-id="${esc(same.id)}">Open ${esc(same.name)}</button>`
    + `<button type="button" class="btn small secondary" data-action="nj-same-job-keep">Keep as a new job</button>`;
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

// As the Client box is typed in or picked: a saved client's type and contact are shown instead
// of asked, with boxes only for what it is missing.
export function syncNewJobCustomer() {
  const form = document.querySelector('[data-form-key="newjob"]');
  if (!form) return;
  hideSameCustomer("newjob");
  const p = knownPayer(form.querySelector('[data-field="customer"]')?.value);
  const note = form.querySelector("[data-known-payer]");
  if (note) { note.hidden = !p; note.querySelector("[data-known-payer-text]").innerHTML = knownPayerText(p); }
  form.querySelectorAll("[data-payer-slot]").forEach(box => { box.hidden = payerSlotHidden(box.dataset.payerSlot, p, clientMode()); });
}

// Who signs off and the client type are the person's to enter (Decision 4): nothing fills them,
// and a signer that only defaulted to the client's name is asked for again, not shown to save
// unchanged. Retainage follows the client picked (prefillRetainage), unless already typed.
export function prefillApprover() {
  const form = document.querySelector('[data-form-key="newjob"]');
  const customer = form?.querySelector('[data-field="customer"]')?.value.trim();
  if (customer && knownPayer(customer)) prefillRetainage();
}

// Retainage % follows the client and client type picked (Round 1, item 1): the client's Usual
// retainage %, or its type's. A box the person typed in, a 0 too, is theirs and stays.
export function newJobRetainageValue(known, clientType) {
  const pct = newJobRetainage(known, clientType);
  return pct === null ? "" : String(pct);
}

export function prefillRetainage() {
  const form = document.querySelector('[data-form-key="newjob"]');
  const box = form?.querySelector('[data-field="retainage_pct"]');
  if (!box || (formDraft.newjob || {}).retainage_touched) return;
  box.value = newJobRetainageValue(knownPayer(form.querySelector('[data-field="customer"]')?.value), form.querySelector('[data-field="client_type"]')?.value || "");
}

// ---------- Import contract (G4, S12) ----------

// What the last contract read for a form found: { form, name, file, text, found, notes, sov, error }.
// The file (or pasted text) is filed on the new job's record as its contract once the job is saved.
let contractRead = null;
// New job as it stood when the person went to the Client playbook, back when they return to Jobs.
let parkedNewJob = null;
onSessionEnd(() => { contractRead = null; parkedNewJob = null; });

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
function fillFromContract(form, read) {
  const d = (formDraft[form] ||= {});
  // The contract's completion date is the job's anticipated end date.
  const { completion_on: end, ...fields } = read;
  if (end) fields.anticipated_end_on = end;
  const put = (box, value) => { if (value !== undefined && value !== null && value !== "") d[box] = String(value); };
  if (form === "newjob") {
    for (const k of ["name", "street", "city", "state", "zip", "customer", "approver", "contract_number", "started_on", "anticipated_end_on", "phone", "ap_email",
      "payapp_due_day", "payment_terms_days"]) put(k, fields[k]);
    // The client it names: a saved one is picked, any other is a new one. A saved client keeps its
    // own type (and so its usual retainage); the contract's guess is for a new one.
    const saved = fields.customer ? knownPayer(fields.customer) : null;
    if (fields.customer) {
      d.client_mode = saved ? "existing" : "new";
      if (saved) d.customer = unesc(saved.name);
    }
    if (!(saved && saved.client_type)) put("client_type", fields.client_type);
    if (Number.isInteger(fields.contract_cents)) put("contract_cents", dollarsText(fields.contract_cents));
    if (Number.isInteger(fields.retainage_pct)) { put("retainage_pct", fields.retainage_pct); d.retainage_touched = true; }
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
    if (!sent.ok) toast(`The job is saved, but the contract file wasn't added to its records${sent.data && sent.data.error ? `: ${String(sent.data.error).replace(/[.\s]+$/, "")}` : ""}. On the job page, use Add a file`);
  } else if (r.text) {
    await post(`/api/jobs/${jobId}/add-record`, { label: "Contract (pasted)", kind: "contract", note: r.text.slice(0, 5000) }, { quiet: true });
  }
  // Its schedule of values went with the job itself (create-job).
  if (contractRead === r) contractRead = null;
  clearJobDetails();
  await load();
}

// ---------- New job: the schedule of values ----------
// Asked here (Nov 3 Decision 1). Left blank, the server gives the job one lump-sum line for the
// contract amount (domain.create_job). Lines that don't add up to the contract are refused with
// nothing saved (field "sov"). A contract read that found a schedule has its own box above.
let njSovRows = 3;
onSessionEnd(() => { njSovRows = 3; });

function newJobSovHtml() {
  const r = contractRead && contractRead.form === "newjob" ? contractRead : null;
  if (r && r.sov && r.useSov !== false) return "";
  const box = (i, name, label, cls, extra = "") => `<td data-label="${label}"><input id="nj-sov-${name}-${i}" class="sov-in ${cls}" data-field="sov_${name}_${i}" value="${esc(draftValue("newjob", `sov_${name}_${i}`))}" aria-label="Line ${i + 1}: ${label}"${extra}></td>`;
  const rows = Array.from({ length: njSovRows }, (_, i) => `<tr>
      <td class="sov-desc-cell"><input id="nj-sov-desc-${i}" class="sov-in sov-desc" data-field="sov_desc_${i}" value="${esc(draftValue("newjob", `sov_desc_${i}`))}" placeholder="Conduit" aria-label="Line ${i + 1}: description"></td>
      ${box(i, "qty", "Qty", "sov-num", ' inputmode="decimal"')}${box(i, "unit", "Unit", "sov-unit", ' placeholder="LS"')}${box(i, "rate", "Unit rate $", "sov-num", ' inputmode="decimal"')}
    </tr>`).join("");
  // Folded (Round 1, item 16): most jobs start on the one lump-sum line. Open once lines are typed.
  const typedLines = newJobSovLines(formDraft.newjob || {}).length;
  const open = "nj-sov" in moreDetails ? moreDetails["nj-sov"] : typedLines > 0;
  return `<details class="nj-sov" data-more="nj-sov"${open ? " open" : ""}>
    <summary>Schedule of values${typedLines ? ` <span class="sub">${typedLines} line${typedLines === 1 ? "" : "s"}</span>` : ""}</summary>
    <p class="sub">Leave the lines blank for one lump-sum line. You can change the schedule on the job page until the first pay app is sent.</p>
    <p class="form-error" id="nj-sov-err" role="alert" hidden></p>
    <div class="table-wrap sov-wrap"><table class="sov-table sov-edit"><tr class="head-row"><th>Description</th><th>Qty</th><th>Unit</th><th>Unit rate $</th></tr>${rows}</table></div>
    <button class="btn small secondary" data-action="nj-sov-add">Add line</button></details>`;
}

// The lines typed on the form, as POST /api/jobs takes them.
function newJobSovLines(v) {
  const lines = [];
  for (let i = 0; i < njSovRows; i++) {
    const line = { description: v[`sov_desc_${i}`] || "", quantity: v[`sov_qty_${i}`] || "", unit: v[`sov_unit_${i}`] || "", rate: v[`sov_rate_${i}`] || "" };
    if (line.description || line.quantity || line.rate) lines.push(line);
  }
  return lines;
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  jobs() {
    if (parkedNewJob) {
      formDraft.newjob = parkedNewJob.draft;
      formOpen.newjob = true;
      contractRead = parkedNewJob.read;
      parkedNewJob = null;
    }
    forgetClosedContract();
    const open = openJobs(state.jobs);
    const closed = state.closed_jobs_total || 0;
    const f = (o) => field({ form: "newjob", ...o });
    const known = knownPayer(draftValue("newjob", "customer"));
    const mode = clientMode();
    // Until typed in, Retainage % is what the client picked usually holds (prefillRetainage).
    if (formDraft.newjob && !formDraft.newjob.retainage_touched) delete formDraft.newjob.retainage_pct;
    const jf = jobFields("newjob", "nj", { retainage_pct: newJobRetainageValue(known, draftValue("newjob", "client_type")) });
    const mark = (name) => contractMark("newjob", name);
    const slot = (name, html) => `<div data-payer-slot="${name}"${payerSlotHidden(name, known, mode) ? " hidden" : ""}>${html}</div>`;
    return `
      <div class="top">
        <h1>Open jobs</h1>
        <div class="form-inline">
          ${open.length ? `<button class="btn secondary" data-action="daily-for-job">New job record</button>` : ""}
          ${/* with no open jobs the empty state's New job is the one (Round 1 acceptance, item 27) */ ""}
          ${open.length || formOpen.newjob ? `<button class="btn" data-action="toggle-form" data-form="newjob">${formOpen.newjob ? "Cancel" : "New job"}</button>` : ""}
        </div>
      </div>
      ${formOpen.newjob ? `
        <div class="card" data-form-key="newjob">
          <h2>New job</h2>
          ${(() => {
            // Import contract, folded with what it does (Round 1, item 16); open while a read is shown.
            const read = contractRead && contractRead.form === "newjob";
            const open = "nj-import" in moreDetails ? moreDetails["nj-import"] : read;
            return `<details class="contract-fold" data-more="nj-import"${open ? " open" : ""}>
            <summary>Import contract <span class="sub">${read && !contractRead.error ? `Filled from ${esc(contractRead.name)}` : "Fill the boxes from a Word, PDF or text file"}</span></summary>
            ${contractImportHtml("newjob")}</details>`;
          })()}
          <p class="sub">Fields marked <span class="req">*</span> are required.</p>
          ${formErrorLine("newjob")}
          <div class="form-grid">
            ${jf.name}
            <div class="span-2 same-job form-inline" data-same-job="newjob" hidden></div>
            ${jf.address}
            ${jf.contract_cents}
            ${jf.retainage_pct}
            ${(state.payers || []).length ? `<div class="span-2 form-inline client-choice" role="group" aria-label="Client">
              ${[["existing", "Existing client"], ["new", "New client"]].map(([key, label]) => `<button type="button" class="${mode === key ? "btn" : "btn secondary"} small" data-action="nj-client-mode" data-mode="${key}" aria-pressed="${mode === key}">${label}</button>`).join("")}
            </div>` : ""}
            ${mode === "existing" ? f({ name: "customer", id: "nj-client", label: "Client", required: true, ...mark("customer"), options: [["", "Pick a client…"], ...(state.payers || []).map(p => [unesc(p.name), unesc(p.name)])] }) : ""}
            ${mode === "new" ? f({ name: "customer", id: "nj-customer", label: "Client name", helpKey: "customer_name", required: true, autocomplete: "off", rule: "name", ...mark("customer") }) : ""}
            ${sameCustomerBox("newjob")}
            ${slot("client_type", f({ name: "client_type", id: "nj-client-type", label: "Client type", helpKey: "role", options: [["", "Choose…"], ...CLIENT_TYPES], required: true, ...mark("client_type") }))}
            ${slot("approver", f({ name: "approver", id: "nj-approver", label: "Who signs off on your bills", helpKey: "approver", required: true, placeholder: "Sam Jones", ...mark("approver") }))}
            ${slot("ap_email", f({ name: "ap_email", id: "nj-email", label: "Email for pay apps", helpKey: "ap_email", type: "email", required: true, autocomplete: "off", rule: "email", placeholder: "name@example.com", ...mark("ap_email") }))}
            ${slot("phone", f({ name: "phone", id: "nj-phone", label: "Phone", helpKey: "customer_phone", type: "tel", required: true, autocomplete: "off", rule: "phone", ...mark("phone") }))}
            <p class="span-2 sub known-payer" data-known-payer${known ? "" : " hidden"}><span data-known-payer-text>${knownPayerText(known)}</span>.
              <button type="button" class="linkish" data-action="nj-to-playbook">${can("company") ? "Change in the Client playbook" : "Open Client playbook"}</button></p>
          </div>
          <div class="form-grid">
            ${jf.started_on}
            ${jf.anticipated_end_on}
            ${slot("payapp_due_day", f({ name: "payapp_due_day", id: "nj-due-day", label: "Pay apps due by (day of the month)", helpKey: "payapp_due_day", inputmode: "numeric", autocomplete: "off", ...mark("payapp_due_day") }))}
            ${slot("payment_terms_days", f({ name: "payment_terms_days", id: "nj-terms", label: "Payment terms, days", helpKey: "payment_terms_days", inputmode: "numeric", autocomplete: "off", ...mark("payment_terms_days") }))}
            ${jf.contract_number}
            ${assigneeField("newjob", "nj")}
            ${f({ name: "note", id: "nj-note", label: "Notes (kept in the job records)", helpKey: "job_notes", multiline: true, wide: true, placeholder: "Gate code, who to call, contract notes" })}
          </div>
          ${newJobSovHtml()}
          <button class="btn" data-action="create-job">Save job</button>
        </div>` : ""}
      ${open.length ? `<div class="table-wrap">
        <table>
          <tr><th>Job</th><th>Code</th><th>Client</th><th>Status</th><th>Outstanding</th><th>Unbilled</th><th>Held back</th><th>Next</th></tr>
          ${open.map(j => `
            <tr>
              <td><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a><div class="sub row-sub">${j.status_label}</div>
                <button class="linkish row-record" data-action="daily-for-job" data-id="${j.id}" aria-label="New job record for ${j.name}">New job record</button></td>
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
      ${closed ? `<p class="sub">${closed} closed job${closed === 1 ? " is" : "s are"} under <button class="linkish tall" data-action="nav" data-view="history">Job history</button>.</p>` : ""}
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
      </div>${moreButton("history", page)}` : !page ? `<p class="sub">Loading…</p>` : emptyState("No finished jobs yet", "", openJobs(state.jobs).length ? `<button class="btn" data-action="nav" data-view="jobs">Open jobs</button>` : `<button class="btn" data-action="onb-newjob">New job</button>`)}
    `;
  },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in app.js.
export const actions = {
  "nj-sov-add": () => { njSovRows = Math.min(njSovRows + 1, 50); render(); document.getElementById(`nj-sov-desc-${njSovRows - 1}`)?.focus(); },
  "create-job": async () => {
    const v = formValues("newjob");
    const errors = jobFieldErrors(v);
    // A saved client keeps what it has (shown instead of the boxes) and takes only what it is
    // missing from here; a new one needs all of it, as the server does (domain.create_job).
    const mode = clientMode();
    const customer = v.customer || "";
    const known = knownPayer(customer);
    const asked = (k) => !known || payerMissing(known).includes(k);
    const approver = asked("approver") ? v.approver : "";
    const apEmail = asked("ap_email") ? v.ap_email : "";
    const phone = asked("phone") ? v.phone : "";
    Object.assign(errors, checkFields({ customer, phone }, { customer: "name", phone: "phone" }));
    if (!mode) errors._form = "Pick an existing client, or tap New client.";
    else if (!customer) errors.customer = mode === "existing" ? "Pick the client." : "Enter the client: who pays for this job.";
    if (mode) {
      if (asked("client_type") && !v.client_type) errors.client_type = "Pick the client type.";
      if (asked("approver") && !approver) errors.approver = "Enter who signs off on your bills.";
      if (asked("phone") && !phone) errors.phone = "Enter the client's phone number.";
      if (asked("ap_email") && (!apEmail || checkFields({ ap_email: apEmail }, { ap_email: "email" }).ap_email)) errors.ap_email = "Enter the email pay apps go to, like ap@builder.com.";
    }
    const amount = centsFrom(v.contract_cents);
    // The contract read this job is saved with: the save closes the form, which forgets it.
    const read = contractRead && contractRead.form === "newjob" ? contractRead : null;
    const useSov = document.getElementById("nj-use-sov");
    if (read && useSov) read.useSov = useSov.checked;
    const useTotal = document.getElementById("nj-sov-total");
    if (read) read.useSovTotal = !!(useTotal && useTotal.checked);
    // The contract's schedule is saved with the job, in the same request, so whoever may create the
    // job sets its contract (Round 1): the schedule total, when ticked, is the contract amount.
    const sovRead = read && read.sov && read.useSov !== false ? read.sov : null;
    const byTotal = !!(sovRead && read.useSovTotal);
    if (byTotal) Object.assign(amount, { cents: sovRead.total_cents, error: "" });
    if (amount.error || amount.cents <= 0) errors.contract_cents = v.contract_cents && amount.error ? amount.error : "Enter the contract amount, like 48,500.";
    // Blank: the server takes what the client usually holds. A typed 0 is sent as 0.
    const ret = v.retainage_pct ? percentFrom(v.retainage_pct) : { value: "", error: "" };
    if (ret.error || (ret.value !== "" && (!Number.isInteger(ret.value) || ret.value > 20))) errors.retainage_pct = "Enter a whole percent from 0 to 20.";
    // A new client's pay app day and terms (from the boxes, which an imported contract fills).
    const days = {};
    if (!known) {
      if (v.payapp_due_day && !(/^\d+$/.test(v.payapp_due_day) && +v.payapp_due_day >= 1 && +v.payapp_due_day <= 31)) errors.payapp_due_day = "Enter a day of the month from 1 to 31 for when pay apps are due, or leave it blank.";
      else if (v.payapp_due_day) days.payapp_due_day = Number(v.payapp_due_day);
      if (v.payment_terms_days && !(/^\d+$/.test(v.payment_terms_days) && +v.payment_terms_days >= 1 && +v.payment_terms_days <= 365)) errors.payment_terms_days = "Enter the days they have to pay as a whole number from 1 to 365.";
      else if (v.payment_terms_days) days.payment_terms_days = Number(v.payment_terms_days);
    }
    // A schedule that doesn't add up to the amount is refused with nothing saved (C6).
    if (sovRead && !byTotal && !errors.contract_cents && amount.cents !== sovRead.total_cents) {
      errors.contract_cents = `The schedule of values from the contract adds up to ${money(read.sov.total_cents)}. Change the amount, tick Use the schedule total as the contract amount, or untick the schedule.`;
    }
    if (refuseForm("newjob", errors)) return;
    const before = new Set(state.jobs.map(j => j.id));
    const r = await post("/api/jobs", {
      sov_lines: sovRead ? sovRead.lines : newJobSovLines(v),
      name: v.name, street: v.street, city: v.city, state: v.state, zip: v.zip, customer, approver,
      ...(approver ? { approver_confirmed: true } : {}),   // typed into its box here: a person's answer, even the client's own name
      ap_email: apEmail, phone, client_type: asked("client_type") ? v.client_type : "",
      contract_number: v.contract_number, note: v.note, started_on: v.started_on || "", anticipated_end_on: v.anticipated_end_on || "", today: localDay(new Date()),
      ...(v.assignee_id ? { assignee_id: v.assignee_id } : {}),
      contract_cents: amount.cents, retainage_pct: ret.value, ...days,
      new_customer: !known && keptNames.newjob === customer,
      new_job: !!keptJobName && keptJobName === v.name,
    }, { formKey: "newjob", close: "newjob" });
    if (r && !r.ok && !showSameJob(r.data)) showSameCustomer("newjob", "nj", r.data);
    const sovError = r && !r.ok && r.data && r.data.fields && r.data.fields.sov;
    const sovLine = document.getElementById("nj-sov-err");
    if (sovLine) { sovLine.textContent = sovError || ""; sovLine.hidden = !sovError; if (sovError) sovLine.scrollIntoView?.({ block: "center" }); }
    if (!r || !r.ok) return;
    delete keptNames.newjob;
    keptJobName = "";
    njSovRows = 3;
    toast(!sovRead ? "Job saved" : byTotal ? "Job saved with the schedule of values from the contract and its total as the contract amount"
      : "Job saved with the schedule of values from the contract");
    const made = state.jobs.find(j => !before.has(j.id));
    if (made) {
      await fileContract(made.id, read);
      nav("job", made.id);
    }
  },
  // ...or keep it as a new job of the same name, on purpose.
  "nj-same-job-keep": () => {
    keptJobName = formValues("newjob").name || "";
    const box = document.querySelector('[data-same-job="newjob"]');
    if (box) { box.hidden = true; box.innerHTML = ""; }
    return actions["create-job"]();
  },
  // To the Client playbook and back: nothing typed on New job is lost.
  "nj-to-playbook": () => {
    parkedNewJob = { draft: { ...formDraft.newjob }, read: contractRead };
    nav("playbook");
  },
  // Which client: a saved one or a new one. Changing it starts the Client box over.
  "nj-client-mode": (el) => {
    const d = (formDraft.newjob ||= {});
    if (d.client_mode !== el.dataset.mode) d.customer = "";
    d.client_mode = el.dataset.mode;
    delete keptNames.newjob;
    render();
    document.getElementById(el.dataset.mode === "existing" ? "nj-client" : "nj-customer")?.focus();
  },
  // "Same as ABC Builders?": pick the saved client and save the job with it.
  "nj-same-use": (el) => {
    const d = (formDraft.newjob ||= {});
    d.customer = el.dataset.name || "";
    d.client_mode = "existing";
    render();
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
};

// The SOV choice is kept across a redraw of the form.
if (typeof document !== "undefined" && document.addEventListener) {
  document.addEventListener("change", (e) => {
    if (e.target && e.target.id === "nj-use-sov" && contractRead) { contractRead.useSov = e.target.checked; render(); }
    if (e.target && e.target.id === "nj-sov-total" && contractRead) contractRead.useSovTotal = e.target.checked;
    // A changed contract amount redraws the schedule's difference line (C6), in place: a whole
    // redraw on leaving the box would replace Save job under the click that left it.
    if (e.target && e.target.id === "nj-amount" && contractRead && contractRead.sov) updateSovChoice();
  });
}
