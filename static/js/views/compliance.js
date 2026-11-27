// Jobs, Compliance (Siteline comparison, items 7 and 8): the company's documents (certificates of
// insurance, W-9s, licenses, bonds, DBE certifications, certified payroll) with their expiry
// dates and files, what each client needs on file, and each open job's lien deadlines for its
// state (Georgia only so far). The server works it all out (compliance.py, liens.py) and answers
// GET /api/compliance; reminders before a document expires and before a lien deadline come round
// on the Dashboard's To do (reminders.py, kind "compliance").
// complianceSendWarningHtml is the soft gate the pay app page shows above Approve & send: the
// client needs a certificate of insurance and the one on file has expired. It never blocks.
import { unesc, escDeep, fmtDate } from "../format.js";
import { askConfirm } from "../dom.js";
import { field, formErrorLine, formValues, clearForm } from "../forms.js";
import { readJson, sendFile } from "../net.js";
import { state, me, currentView, onSessionEnd } from "../state.js";
import { post, render, toast, can, moreDetails } from "../app-core.js";

let data = null;        // GET /api/compliance, escaped (escDeep)
let loadedFor = null;   // the state it was asked for: a change answers with a new state, so it is asked again
let asking = false;

onSessionEnd(() => { data = null; loadedFor = null; });

export async function loadCompliance(force = false) {
  if (asking || !me || (!force && loadedFor === state)) return;
  asking = true;
  const asked = state;
  try {
    const res = await fetch("/api/compliance");
    if (res.ok) {
      data = escDeep(await readJson(res));
      loadedFor = asked;
      if (currentView === "compliance" || currentView === "payapp-detail") render();
    }
  } catch (_) { /* the page keeps its loading line; the banner covers outages */ }
  finally { asking = false; }
}

// A change's answer carries the page's data (server.py ctx.reply["compliance"]).
function took(reply) {
  if (reply && reply.compliance) { data = escDeep(reply.compliance); loadedFor = state; }
}

// The soft gate on the pay app page: warnings only, from the last answer (asked for again when stale).
export function complianceSendWarningHtml(job) {
  if (loadedFor !== state) loadCompliance();
  const warnings = data && data.warnings ? data.warnings[job.id] || [] : [];
  if (!warnings.length) return "";
  return `<div class="notice compliance-warning" role="note"><strong>Insurance certificate</strong>
    ${warnings.map(w => `<div>${w}</div>`).join("")}
    <div class="sub">You can still send. Upload the current certificate on <button class="linkish tall" data-action="nav" data-view="compliance">Jobs, Compliance</button>.</div></div>`;
}

const DOC_PILL = {
  current: ["green", "Current"], expiring: ["amber", "Expires soon"], expired: ["red", "Expired"],
  no_expiry: ["", "No expiry"], replaced: ["", "Replaced"],
};
const LIEN_PILL = {
  done: ["green", "Done"], skipped: ["", "Not doing"], not_needed: ["", "Not needed"], waiting: ["", "Needs a date"],
  passed: ["red", "Passed"], due_soon: ["amber", "Due soon"], upcoming: ["", "Upcoming"],
};
const REQ_PILL = { met: ["green", "On file"], expired: ["red", "Expired"], missing: ["red", "Missing"] };
const pill = (map, key, extra = "") => { const [tone, words] = map[key] || ["", key]; return `<span class="pill ${tone}">${words}${extra}</span>`; };

function payerOptions(blank) {
  return [["", blank]].concat((state.payers || []).map(p => [unesc(p.id), unesc(p.name)]));
}
function jobOptions(blank) {
  return [["", blank]].concat(state.jobs.filter(j => !j.closed).map(j => [unesc(j.id), unesc(j.name)]));
}

// ---------- documents ----------

function docRow(d, edit) {
  const days = d.days_left == null ? "" : d.days_left < 0 ? "" : ` (${d.days_left} day${d.days_left === 1 ? "" : "s"})`;
  const who = [d.holder, d.issuer].filter(Boolean).join(" · ");
  const fileLink = d.file_id ? `<a href="/api/compliance/docs/${d.id}/file" target="_blank" rel="noopener">Open file</a>` : `<span class="sub">No file yet</span>`;
  return `<tr>
    <td><strong>${d.type_label}</strong>${d.sensitive ? ` <span class="pill">Private</span>` : ""}${d.payer ? `<div class="sub">For ${d.payer}${d.additional_insured ? ", additional insured" : ""}</div>` : ""}${who ? `<div class="sub">${who}</div>` : ""}${d.number ? `<div class="sub">No. ${d.number}</div>` : ""}</td>
    <td>${d.effective_on ? fmtDate(d.effective_on) : "—"}</td>
    <td>${d.expires_on ? fmtDate(d.expires_on) : "—"}</td>
    <td>${pill(DOC_PILL, d.status, d.status === "expiring" ? days : "")}</td>
    <td>${fileLink}${edit ? `<div class="compliance-upload"><input type="file" id="cdoc-file-${d.id}" aria-label="File for ${d.type_label}" accept=".pdf,.png,.jpg,.jpeg,.heic,.doc,.docx">
      <button class="btn small secondary" data-action="compliance-upload" data-id="${d.id}">${d.file_id ? "Replace file" : "Upload file"}</button>
      <button class="btn small secondary" data-action="compliance-delete-doc" data-id="${d.id}">Delete</button></div>` : ""}</td>
  </tr>`;
}

function addDocForm() {
  const f = "cdoc-new";
  const types = (data.types || []).map(t => [unesc(t.key), unesc(t.label)]);
  return `<details class="compliance-add" data-more="cdoc-add"${moreDetails["cdoc-add"] ? " open" : ""}><summary>Add a document</summary>
    <div data-form-key="${f}">${formErrorLine(f)}
    <div class="form-grid">
      ${field({ form: f, name: "type", id: "cdoc-type", label: "Kind", options: types, required: true })}
      ${field({ form: f, name: "holder", id: "cdoc-holder", label: "Holder", help: "Who it is for: your company, or the insured named on it." })}
      ${field({ form: f, name: "issuer", id: "cdoc-issuer", label: "Issued by", help: "The insurer, surety, state board or agency." })}
      ${field({ form: f, name: "number", id: "cdoc-number", label: "Policy, license or bond number", help: "Not for a W-9 or certified payroll: upload the file. Never a Social Security or tax ID number." })}
      ${field({ form: f, name: "effective_on", id: "cdoc-effective", label: "Effective", type: "date" })}
      ${field({ form: f, name: "expires_on", id: "cdoc-expires", label: "Expires", type: "date" })}
      ${field({ form: f, name: "payer_id", id: "cdoc-payer", label: "For a client", options: payerOptions("Every client (the company's own)") })}
      ${field({ form: f, name: "job_id", id: "cdoc-job", label: "For a job", options: jobOptions("Every job") })}
      <div class="field"><label class="terms-check"><input type="checkbox" id="cdoc-ai" data-field="additional_insured" value="1"> Names the client as additional insured (a COI)</label></div>
      <div class="field"><label for="cdoc-file">File</label><input type="file" id="cdoc-file" accept=".pdf,.png,.jpg,.jpeg,.heic,.doc,.docx"><span class="sub field-help">A W-9 or certified payroll opens only for people who can edit company details.</span></div>
    </div>
    <button class="btn" data-action="compliance-add-doc">Save document</button></div></details>`;
}

function docsCard(edit) {
  const docs = data.docs || [];
  return `<div class="card" id="compliance-docs"><h2>Company documents</h2>
    <p class="sub">Certificates of insurance, W-9s, licenses, bonds and certifications. Reminders come ${(data.remind_days || []).join(", ")} days before one expires, on the Dashboard's To do.</p>
    ${docs.length ? `<div class="table-wrap"><table class="compliance-table"><tr><th>Document</th><th>Effective</th><th>Expires</th><th>Status</th><th>File</th></tr>
      ${docs.map(d => docRow(d, edit)).join("")}</table></div>` : `<p class="sub">No documents yet.</p>`}
    ${edit ? addDocForm() : ""}
  </div>`;
}

// ---------- what clients need ----------

function reqsCard(edit) {
  const f = "creq-new";
  const reqs = data.requirements || [];
  const types = (data.types || []).map(t => [unesc(t.key), unesc(t.label)]);
  return `<div class="card" id="compliance-requirements"><h2>What clients need on file</h2>
    <p class="sub">When a client needs a certificate of insurance and the one on file has expired, its pay apps show a warning before they go. Sending isn't stopped.</p>
    ${reqs.length ? `<div class="table-wrap"><table class="compliance-table"><tr><th>Client</th><th>Needs</th><th>Status</th><th></th></tr>
      ${reqs.map(r => `<tr><td>${r.payer}${r.job_id ? `<div class="sub">${(state.jobs.find(j => j.id === r.job_id) || {}).name || ""}</div>` : ""}</td>
        <td>${r.type_label}${r.additional_insured ? ", naming them as additional insured" : ""}${r.note ? `<div class="sub">${r.note}</div>` : ""}</td>
        <td>${pill(REQ_PILL, r.status)}</td>
        <td>${edit ? `<button class="btn small secondary" data-action="compliance-delete-req" data-id="${r.id}">Remove</button>` : ""}</td></tr>`).join("")}
      </table></div>` : `<p class="sub">None noted yet.</p>`}
    ${edit ? `<details data-more="creq-add"${moreDetails["creq-add"] ? " open" : ""}><summary>Add what a client needs</summary>
      <div data-form-key="${f}">${formErrorLine(f)}<div class="form-grid">
        ${field({ form: f, name: "payer_id", id: "creq-payer", label: "Client", options: payerOptions("Pick a client"), required: true })}
        ${field({ form: f, name: "type", id: "creq-type", label: "Needs", options: types, required: true })}
        ${field({ form: f, name: "job_id", id: "creq-job", label: "Only for a job", options: jobOptions("Every job for this client") })}
        ${field({ form: f, name: "note", id: "creq-note", label: "Note", placeholder: "Limits, wording on the certificate…" })}
        <div class="field"><label class="terms-check"><input type="checkbox" id="creq-ai" data-field="additional_insured" value="1"> Must name them as additional insured</label></div>
      </div><button class="btn" data-action="compliance-add-req">Save</button></div></details>` : ""}
  </div>`;
}

function settingsCard() {
  const f = "csettings";
  return `<details class="card" data-more="csettings"${moreDetails.csettings ? " open" : ""}><summary><strong>When reminders come</strong></summary>
    <div data-form-key="${f}">${formErrorLine(f)}
    ${field({ form: f, name: "remind_days", id: "cset-days", label: "Days before a document expires or a lien deadline", value: unesc((data.remind_days || []).join(", ")), help: "Up to five numbers, like 30, 14, 3." })}
    <button class="btn small" data-action="compliance-settings">Save</button></div></details>`;
}

// ---------- lien deadlines ----------

function deadlineRow(j, r, edit) {
  const extra = r.status === "done" && r.done_on ? ` ${fmtDate(r.done_on)}` : "";
  const when = r.file_by ? `<strong>${fmtDate(r.file_by)}</strong>${r.moved ? `<div class="sub">The statute's last day is ${fmtDate(r.last_day)}: ${fmtDate(r.counted)} falls on a weekend or holiday.</div>` : ""}` : "—";
  // A step that waits on an earlier one (the copy to the owner waits for the filed lien) has nothing to mark yet.
  const doneable = ["due_soon", "upcoming", "passed"].includes(r.status) || (r.status === "waiting" && r.note);
  return `<tr>
    <td><strong>${r.label}</strong><div class="sub">${r.cite}</div>${r.help ? `<div class="sub">${r.help}</div>` : ""}${r.note ? `<div class="sub">${r.note}</div>` : ""}</td>
    <td>${when}${r.from_on ? `<div class="sub">From ${r.from}, ${fmtDate(r.from_on)}</div>` : ""}</td>
    <td>${pill(LIEN_PILL, r.status, extra)}</td>
    <td>${edit ? (r.status === "done" || r.status === "skipped"
      ? `<button class="btn small secondary" data-action="compliance-lien-undo" data-job="${j.job_id}" data-key="${r.key}">Undo</button>`
      : doneable ? `<div class="compliance-done"><input type="date" id="lien-done-${j.job_id}-${r.key}" aria-label="Day ${r.label} was done">
          <button class="btn small secondary" data-action="compliance-lien-done" data-job="${j.job_id}" data-key="${r.key}">Done on</button>
          <button class="btn small secondary" data-action="compliance-lien-skip" data-job="${j.job_id}" data-key="${r.key}">Not doing</button></div>` : "") : ""}</td>
  </tr>`;
}

function jobLienCard(j, edit) {
  const f = `lien-${j.job_id}`;
  const i = j.inputs || {};
  const saved = j.saved || {};
  const dueSoon = (j.deadlines || []).filter(r => r.status === "due_soon").length;
  const lastWords = { saved: "typed in", job_record: "the latest job record", work_done: "the day the work was marked done" }[j.last_from] || "";
  const form = edit ? `<div data-form-key="${f}">${formErrorLine(f)}<div class="form-grid">
      ${field({ form: f, name: "state", id: `${f}-state`, label: "State", value: unesc(saved.state || ""), placeholder: unesc(j.state || "GA"), help: j.state ? `From the job's address: ${unesc(j.state)}. Type another to change it.` : "The job's address names no state.", rule: "state" })}
      ${field({ form: f, name: "tier", id: `${f}-tier`, label: "Who hired you", value: unesc(saved.tier || ""), options: [["", `From the client's type (${unesc(((data.tiers || []).find(t => t[0] === j.tier) || [, j.tier])[1])})`]].concat((data.tiers || []).map(t => [unesc(t[0]), unesc(t[1])])) })}
      ${field({ form: f, name: "first_furnished_on", id: `${f}-first`, label: "First work or materials", type: "date", value: unesc(saved.first_furnished_on || ""), help: i.first_furnished_on && !saved.first_furnished_on ? `Blank uses the job's start, ${unesc(fmtDate(i.first_furnished_on))}.` : "" })}
      ${field({ form: f, name: "last_furnished_on", id: `${f}-last`, label: "Last work or materials", type: "date", value: unesc(saved.last_furnished_on || ""), help: i.last_furnished_on && !saved.last_furnished_on ? `Blank uses ${lastWords}, ${unesc(fmtDate(i.last_furnished_on))}.` : "Blank uses the latest job record." })}
      ${field({ form: f, name: "noc_filed_on", id: `${f}-noc`, label: "Notice of Commencement filed", type: "date", value: unesc(saved.noc_filed_on || ""), help: "If the owner or contractor filed one. Ask the contractor for a copy." })}
      ${field({ form: f, name: "contest_received_on", id: `${f}-contest`, label: "Notice of contest received", type: "date", value: unesc(saved.contest_received_on || "") })}
      ${field({ form: f, name: "waiver_signed_on", id: `${f}-waiver`, label: "Waiver signed, not paid", type: "date", value: unesc(saved.waiver_signed_on || ""), help: "Only one signed outside Duracash: the ones Duracash sent are counted already." })}
    </div><button class="btn small" data-action="compliance-lien-save" data-job="${j.job_id}">Save dates</button></div>` : "";
  const clocks = (j.waiver_clocks || []).length ? `<p class="sub">Waivers Duracash sent, still unpaid: ${j.waiver_clocks.map(c => `${c.what}, sent ${fmtDate(c.sent_on)}, takes effect ${fmtDate(c.takes_effect)} unless you file an affidavit of nonpayment before then`).join("; ")}.</p>` : "";
  const body = !j.supported
    ? `<p class="sub">${j.state ? `Lien deadlines for ${j.state} aren't in Duracash yet.` : "Add the job's state to see its lien deadlines."} Only Georgia so far.</p>`
    : `<p class="sub">${j.state_name}, ${j.statute}; days counted under ${j.counting}. ${j.waiver_note}</p>${clocks}
      <div class="table-wrap"><table class="compliance-table"><tr><th>Deadline</th><th>File by</th><th>Status</th><th></th></tr>
      ${(j.deadlines || []).map(r => deadlineRow(j, r, edit)).join("")}</table></div>`;
  return `<details class="card compliance-job" data-more="lien-${j.job_id}"${moreDetails[`lien-${j.job_id}`] ? " open" : ""}>
    <summary><strong>${j.job}</strong> ${j.state ? `<span class="sub">${j.state}</span>` : ""} ${dueSoon ? `<span class="pill amber">${dueSoon} due soon</span>` : ""}</summary>
    ${body}${form}</details>`;
}

function liensSection(edit) {
  const jobs = data.jobs || [];
  return `<h2 class="section-title" id="compliance-liens">Lien deadlines</h2>
    <p class="notice compliance-legal" role="note">${data.not_legal_advice}</p>
    ${jobs.length ? jobs.map(j => jobLienCard(j, edit)).join("") : `<p class="sub">No open jobs.</p>`}`;
}

export const views = {
  compliance() {
    if (loadedFor !== state) loadCompliance();
    const head = `<div class="job-head"><div><h1>Compliance</h1></div></div>`;
    if (!data) return `${head}<div class="card"><p class="sub">Loading…</p></div>`;
    const edit = can("company");
    return `${head}${docsCard(edit)}${reqsCard(edit)}${edit ? settingsCard() : ""}${liensSection(can("manage_jobs"))}`;
  },
};

// ---------- actions ----------

function liensBody(jobId, change) {
  const j = (data.jobs || []).find(x => x.job_id === jobId);
  const saved = (j && j.saved) || {};
  const typed = document.querySelector(`[data-form-key="lien-${jobId}"]`) ? formValues(`lien-${jobId}`) : {};
  const body = { state: "", tier: "", first_furnished_on: "", last_furnished_on: "", noc_filed_on: "", contest_received_on: "", waiver_signed_on: "" };
  for (const k of Object.keys(body)) body[k] = k in typed ? typed[k] : unesc(saved[k] || "");
  body.done = Object.fromEntries(Object.entries(saved.done || {}).map(([k, v]) => [unesc(k), unesc(v)]));
  body.skipped = (saved.skipped || []).map(unesc);
  if (change) change(body);
  return body;
}

async function saveLiens(el, change) {
  const jobId = unesc(el.dataset.job);
  const key = `lien-${jobId}`;
  const r = await post(`/api/compliance/liens/${encodeURIComponent(jobId)}`, liensBody(jobId, change), { formKey: key });
  if (r && r.ok) { took(r.data); render(); }
}

export const actions = {
  "compliance-add-doc": async () => {
    const f = "cdoc-new";
    const body = formValues(f);
    body.additional_insured = !!document.getElementById("cdoc-ai")?.checked;
    const input = document.getElementById("cdoc-file");
    const r = await post("/api/compliance/docs", body, { formKey: f, quiet: true });
    if (!r || !r.ok) return;
    took(r.data);
    if (input && input.files && input.files[0]) {
      const up = await sendFile(`/api/compliance/docs/${encodeURIComponent(r.data.doc_id)}/file`, input.files[0], input.files[0].name);
      if (!up.ok) { toast(`Saved, but the file didn't upload: ${up.data.error || "try again"}`); await loadCompliance(true); return; }
      took(up.data);
    }
    clearForm(f);
    moreDetails["cdoc-add"] = false;
    toast("Saved");
    render();
  },
  "compliance-upload": async (el) => {
    const input = document.getElementById(`cdoc-file-${el.dataset.id}`);
    if (!input || !input.files || !input.files[0]) { toast("Choose the file first."); input?.focus(); return; }
    const r = await sendFile(`/api/compliance/docs/${encodeURIComponent(unesc(el.dataset.id))}/file`, input.files[0], input.files[0].name);
    if (!r.ok) { toast(r.data.error || "The file didn't upload."); return; }
    took(r.data);
    toast("Uploaded");
    render();
  },
  "compliance-delete-doc": async (el) => {
    if (!(await askConfirm("Delete this document and its file? This can't be undone.", "Delete", true))) return;
    const r = await post(`/api/compliance/docs/${encodeURIComponent(unesc(el.dataset.id))}/delete`, {});
    if (r && r.ok) { took(r.data); render(); }
  },
  "compliance-add-req": async () => {
    const f = "creq-new";
    const body = formValues(f);
    body.additional_insured = !!document.getElementById("creq-ai")?.checked;
    const r = await post("/api/compliance/requirements", body, { formKey: f });
    if (r && r.ok) { took(r.data); moreDetails["creq-add"] = false; render(); }
  },
  "compliance-delete-req": async (el) => {
    const r = await post(`/api/compliance/requirements/${encodeURIComponent(unesc(el.dataset.id))}/delete`, {});
    if (r && r.ok) { took(r.data); render(); }
  },
  "compliance-settings": async () => {
    const f = "csettings";
    const days = String(formValues(f).remind_days || "").split(/[,\s]+/).filter(Boolean);
    const r = await post("/api/compliance/settings", { remind_days: days }, { formKey: f });
    if (r && r.ok) { took(r.data); render(); }
  },
  "compliance-lien-save": (el) => saveLiens(el),
  "compliance-lien-done": (el) => {
    const key = unesc(el.dataset.key);
    const day = document.getElementById(`lien-done-${el.dataset.job}-${el.dataset.key}`)?.value || "";
    if (!day) { toast("Enter the day it was done."); return; }
    return saveLiens(el, b => { b.done[key] = day; b.skipped = b.skipped.filter(k => k !== key); });
  },
  "compliance-lien-skip": (el) => {
    const key = unesc(el.dataset.key);
    return saveLiens(el, b => { if (!b.skipped.includes(key)) b.skipped.push(key); });
  },
  "compliance-lien-undo": (el) => {
    const key = unesc(el.dataset.key);
    return saveLiens(el, b => { delete b.done[key]; b.skipped = b.skipped.filter(k => k !== key); });
  },
};
