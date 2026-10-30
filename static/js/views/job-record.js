// The job page's job record: the drop zone, the paste box, the records in groups by kind (J5)
// with a filter, each record with Edit, and Add a note.
import { esc, unesc, fmtDate } from "../format.js";
import { RECORD_KINDS, groupRecords } from "../jobs.js";
import { field, formErrorLine, refuseForm, formValues, formErrors, draftValue, clearForm } from "../forms.js";
import { termText } from "../terms.js";
import { state, onSessionEnd } from "../state.js";
import { pasteBoxHtml } from "./inbox.js";
import { post, toast, render, nav } from "../app-core.js";


// A record on the job page, with Edit for its label and kind (R5-07). One open at a time.
let editingRecord = "";
// The group the record list shows (J5): "" for all, else a RECORD_GROUPS key, for one job.
let recordFilter = { job: "", group: "" };
// Signed out: a record being edited, and the filter, are that company's.
onSessionEnd(() => { editingRecord = ""; recordFilter = { job: "", group: "" }; });
function recordRow(j, r) {
  if (editingRecord === r.id && !j.closed) {
    // What was typed stays through a redraw until saved or cancelled (a form: watchForms keeps it).
    const key = `record-${r.id}`, kind = draftValue(key, "kind", unesc(r.kind));
    return `<div class="list-item record-edit" data-form-key="${key}">
      <label class="inline-label" for="rec-edit-label-${r.id}">What it is</label>
      <input id="rec-edit-label-${r.id}" data-field="label" value="${esc(draftValue(key, "label", unesc(r.label)))}" maxlength="120">
      <label class="inline-label" for="rec-edit-kind-${r.id}">Kind</label>
      <select id="rec-edit-kind-${r.id}" data-field="kind">${Object.entries(RECORD_KINDS).map(([k, v]) => `<option value="${k}"${kind === k ? " selected" : ""}>${termText(v)}</option>`).join("")}</select>
      <button class="btn small" data-action="record-save" data-id="${j.id}" data-item="${r.id}">Save</button>
      <button class="btn small secondary" data-action="record-edit" data-item="">Cancel</button>
    </div>`;
  }
  return `<div class="list-item"><span class="pill">${termText(RECORD_KINDS[r.kind] || r.kind)}</span> ${r.label}${r.note ? ` <span class="sub">— ${r.note.length > 240 ? r.note.slice(0, 240) + "…" : r.note}</span>` : ""}${j.closed ? "" : ` <button class="linkish" data-action="record-edit" data-item="${r.id}" aria-label="Edit ${r.label}">Edit</button>`}${recordFiles(r) ? `<div class="sub">${recordFiles(r)}</div>` : ""}${r.source || r.added_at ? `<div class="sub">${r.source ? r.source + " · " : ""}${fmtDate(r.added_at)}</div>` : ""}</div>`;
}


export function recordFiles(r) {
  return (r.file_ids || []).map(fid => {
    const f = (state.files || []).find(x => x.id === fid);
    return f ? `<a href="/api/files/${f.id}" target="_blank" rel="noopener">${f.name}</a>` : "";
  }).filter(Boolean).join(" · ");
}

// The records in labelled groups with their counts, each folding on its own, under a filter row:
// All, then each group that has records.
export function recordGroupsHtml(j) {
  const groups = groupRecords(j.records);
  if (!groups.length) return "";
  const on = recordFilter.job === j.id && groups.some(g => g.key === recordFilter.group) ? recordFilter.group : "";
  const total = groups.reduce((n, g) => n + g.records.length, 0);
  const button = (key, label, n) => `<button class="${on === key ? "btn" : "btn secondary"} small" data-action="record-filter" data-id="${j.id}" data-group="${key}" aria-pressed="${on === key}">${label} (${n})</button>`;
  return `<div class="filter-bar record-filter" role="group" aria-label="Show">${button("", "All", total)}${groups.map(g => button(g.key, g.label, g.records.length)).join("")}</div>
    ${groups.filter(g => !on || g.key === on).map(g => `<details class="record-group" data-group="${g.key}" open>
      <summary><span class="record-group-name">${g.label}</span> <span class="pill">${g.records.length}</span></summary>
      ${g.records.map(r => recordRow(j, r)).join("")}
    </details>`).join("")}`;
}

// The Job record card on the job page.
export function recordCardHtml(j) {
  return `<div class="card">
        <h2>Job record</h2>
        ${j.closed ? "" : `<div class="dropzone small" data-job="${j.id}">
          <div class="capture-buttons">
            <button class="btn small" data-action="pick-files" data-job="${j.id}" data-capture="1">Take a photo</button>
            <button class="btn small secondary" data-action="pick-files" data-job="${j.id}">Add a file</button>
          </div>
          <span class="sub pointer-only">Or drop files here.</span>
        </div>
        <details class="paste-job"><summary>Paste an email, a text thread, or notes</summary>${pasteBoxHtml(j.id)}</details>`}
        ${recordGroupsHtml(j)}
        <details class="add-record"${(j.records || []).length && !formErrors.record ? "" : " open"}><summary>Add a note to the record</summary>
          <div data-form-key="record">${formErrorLine("record")}<div class="form-row">
            ${field({ form: "record", name: "label", id: "rec-label", label: "What it is", placeholder: "Rough-in inspection passed" })}
            ${field({ form: "record", name: "note", id: "rec-note", label: "Note (optional)" })}
            ${field({ form: "record", name: "kind", id: "rec-kind", label: "Kind", options: Object.entries(RECORD_KINDS).map(([k, v]) => [k, termText(v)]) })}
            <button class="btn small" data-action="add-record" data-id="${j.id}">Add to record</button>
          </div></div>
        </details>
      </div>
      `;
}

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  "add-evidence": async (el, id) => {
    const label = document.querySelector("#ev-label")?.value;
    if (label) await post(`/api/jobs/${id}/add-evidence`, { label, kind: "note" });
  },
  "add-record": async (el, id) => {
    const v = formValues("record");
    if (refuseForm("record", v.label ? {} : { label: "Enter what this is, like \"Rough-in inspection passed\"." })) return;
    await post(`/api/jobs/${id}/add-record`, { label: v.label, note: v.note, kind: v.kind || "note" }, { formKey: "record" });
  },
  "record-filter": (el, id) => { recordFilter = { job: id, group: el.dataset.group || "" }; render(); },
  "record-edit": (el) => {
    if (editingRecord) clearForm(`record-${editingRecord}`);   // Cancel, or another record's Edit, drops what was typed
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
    if (r && r.ok) { clearForm(`record-${rid}`); editingRecord = ""; render(); }
  },
};
