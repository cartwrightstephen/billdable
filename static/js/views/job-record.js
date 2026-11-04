// The job page's Job records card: New job record, the drop zone, a link to Import, and the
// records in groups by kind (J5) with a filter, each record with Edit.
import { esc, unesc, fmtDate } from "../format.js";
import { RECORD_KINDS, groupRecords, recordKindWord } from "../jobs.js";
import { draftValue, clearForm } from "../forms.js";
import { pathFor } from "../routes.js";
import { termText } from "../terms.js";
import { state, onSessionEnd } from "../state.js";
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
  return `<div class="list-item"><span class="pill">${termText(recordKindWord(r))}</span> ${r.label}${r.note ? ` <span class="sub">— ${r.note.length > 240 ? r.note.slice(0, 240) + "…" : r.note}</span>` : ""}${j.closed ? "" : ` <button class="linkish" data-action="record-edit" data-item="${r.id}" aria-label="Edit ${r.label}">Edit</button>`}${recordFiles(r) ? `<div class="sub">${recordFiles(r)}</div>` : ""}${r.source || r.added_at ? `<div class="sub">${r.source ? r.source + " · " : ""}${fmtDate(r.on_date || r.added_at)}</div>` : ""}</div>`;
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

// The Job records card on the job page.
export function recordCardHtml(j) {
  return `<div class="card job-records">
        <div class="top"><h2>Job records</h2>${j.closed ? "" : `<button class="btn small" data-action="daily-for-job" data-id="${j.id}">New job record</button>`}</div>
        ${j.closed ? "" : `<div class="dropzone small" data-job="${j.id}">
          <div class="capture-buttons">
            <button class="btn small" data-action="pick-files" data-job="${j.id}" data-capture="1">Take a photo</button>
            <button class="btn small secondary" data-action="pick-files" data-job="${j.id}">Add a file</button>
          </div>
          <span class="sub pointer-only">Or drop files here.</span>
        </div>
        <p class="sub">Emails, text threads and notes go in on <a href="${pathFor("import")}" data-action="import-for-job" data-id="${j.id}">Import</a>.</p>`}
        ${recordGroupsHtml(j)}
      </div>
      `;
}

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  "add-evidence": async (el, id) => {
    const label = document.querySelector("#ev-label")?.value;
    if (label) await post(`/api/jobs/${id}/add-evidence`, { label, kind: "note" });
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
