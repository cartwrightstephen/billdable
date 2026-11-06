// The job page's Job records card: New job record, the drop zone, a link to Import, and the
// records in groups by kind (J5) with a filter, each record with Edit.
import { esc, unesc, fmtDate } from "../format.js";
import { RECORD_KINDS, groupRecords, recordFace, recordKindWord, shortDay } from "../jobs.js";
import { draftValue, clearForm } from "../forms.js";
import { pathFor } from "../routes.js";
import { askConfirm } from "../dom.js";
import { termText } from "../terms.js";
import { state, onSessionEnd } from "../state.js";
import { post, toast, render, nav } from "../app-core.js";


// A record on the job page, with Edit for its label, kind and what a conversation or note says
// (R5-07). One open at a time.
let editingRecord = "";
// The group the record list shows (J5): "" for all, else a RECORD_GROUPS key, for one job.
let recordFilter = { job: "", group: "" };
// Signed out: a record being edited, and the filter, are that company's.
onSessionEnd(() => { editingRecord = ""; recordFilter = { job: "", group: "" }; });
const NOTE_KINDS = ["conversation", "note", "other"];
function recordRow(j, stored) {
  const r = recordFace(stored);
  if (editingRecord === r.id && !j.closed) {
    // What was typed stays through a redraw until saved or cancelled (a form: watchForms keeps it).
    const key = `record-${r.id}`, kind = draftValue(key, "kind", unesc(r.kind));
    const said = NOTE_KINDS.includes(unesc(r.kind));
    return `<div class="list-item record-edit" data-form-key="${key}">
      <label class="inline-label" for="rec-edit-label-${r.id}">What it is</label>
      <input id="rec-edit-label-${r.id}" data-field="label" value="${esc(draftValue(key, "label", unesc(r.label)))}" maxlength="120">
      <label class="inline-label" for="rec-edit-kind-${r.id}">Kind</label>
      <select id="rec-edit-kind-${r.id}" data-field="kind">${Object.entries(RECORD_KINDS).map(([k, v]) => `<option value="${k}"${kind === k ? " selected" : ""}>${termText(v)}</option>`).join("")}</select>
      ${said ? `<label class="inline-label" for="rec-edit-note-${r.id}">${kind === "conversation" ? "What they said" : "Note"}</label>
      <textarea id="rec-edit-note-${r.id}" data-field="note" rows="3" maxlength="5000">${esc(draftValue(key, "note", unesc(r.note || "")))}</textarea>` : ""}
      <button class="btn small" data-action="record-save" data-id="${j.id}" data-item="${r.id}">Save</button>
      <button class="btn small secondary" data-action="record-edit" data-item="">Cancel</button>
    </div>`;
  }
  // One row per file (Round 1, item 13): a record with one file is its link; with several, the
  // links follow on the same line; where it came from and the date close the line.
  const files = recordFileList(r);
  const label = files.length === 1 ? `<a href="/api/files/${files[0].id}" target="_blank" rel="noopener">${r.label}</a>` : r.label;
  const when = r.source || r.added_at || r.on_date ? ` <span class="sub">${r.source ? r.source + " · " : ""}${fmtDate(r.on_date || r.added_at)}</span>` : "";
  return `<div class="list-item record-line"><span class="pill">${termText(recordKindWord(r))}</span> ${label}${files.length > 1 ? ` <span class="sub">${recordFiles(r)}</span>` : ""}${r.note ? ` <span class="sub">— ${r.note.length > 240 ? r.note.slice(0, 240) + "…" : r.note}</span>` : ""}${when}${j.closed ? "" : ` <button class="linkish tall" data-action="record-edit" data-item="${r.id}" aria-label="Edit ${r.label}">Edit</button>`}${(r.quantities || []).length && !j.closed && j.work_status !== "complete" ? ` <button class="linkish" data-action="quantities-undo" data-id="${j.id}" data-item="${r.id}" aria-label="Undo import: ${r.label}">Undo import</button>` : ""}${r.quantities_undone ? `<div class="sub">Quantities taken off by ${r.quantities_undone.by}, ${fmtDate(r.quantities_undone.at)}</div>` : ""}</div>`;
}

// A record's files that are still there.
function recordFileList(r) {
  return (r.file_ids || []).map(fid => (state.files || []).find(x => x.id === fid)).filter(Boolean);
}

// "5 files · last Nov 5": the folded card's summary.
export function recordSummary(j) {
  const records = j.records || [];
  if (!records.length) return "Nothing yet";
  const files = records.reduce((n, r) => n + (r.file_ids || []).length, 0);
  const last = records.map(r => String(r.on_date || r.added_at || "")).filter(Boolean).sort().pop();
  const count = files ? `${files} file${files === 1 ? "" : "s"}` : `${records.length} record${records.length === 1 ? "" : "s"}`;
  return `${count}${last ? ` · last ${shortDay(last)}` : ""}`;
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

// The Job records card on the job page: a fold (a phone starts it folded, Round 1 item 13).
export function recordCardHtml(j, open = true) {
  return `<details class="card job-records" data-more="job-records"${open || editingRecord ? " open" : ""}>
        <summary><h2>Job records</h2><span class="sub">${recordSummary(j)}</span></summary>
        ${j.closed ? "" : `<div class="form-inline"><button class="btn small" data-action="daily-for-job" data-id="${j.id}">New job record</button></div>`}
        ${j.closed ? "" : `<div class="dropzone small" data-job="${j.id}">
          <div class="capture-buttons">
            <button class="btn small" data-action="pick-files" data-job="${j.id}" data-capture="1">Take a photo</button>
            <button class="btn small secondary" data-action="pick-files" data-job="${j.id}">Add a file</button>
          </div>
          <span class="sub pointer-only">Or drop files here.</span>
        </div>
        <p class="sub">Emails, text threads and notes go in on <a href="${pathFor("import")}" data-action="import-for-job" data-id="${j.id}">Import</a>.</p>`}
        ${recordGroupsHtml(j)}
      </details>
      `;
}

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  "add-evidence": async (el, id) => {
    const label = document.querySelector("#ev-label")?.value;
    if (label) await post(`/api/jobs/${id}/add-evidence`, { label, kind: "note" });
  },
  "record-filter": (el, id) => { recordFilter = { job: id, group: el.dataset.group || "" }; render(); },
  // Takes off what the imported file added. Going below what sent pay apps billed is said first.
  "quantities-undo": async (el, id) => {
    const j = state.jobs.find(x => x.id === id);
    const rec = (j?.records || []).find(r => r.id === el.dataset.item);
    if (!rec) return;
    const lines = j.sov_view?.lines || [];
    const below = (rec.quantities || []).some(q => { const l = lines.find(x => x.id === q.line_id); return l && l.done_milli - q.qty_milli < (l.sent_qty_milli || 0); });
    const said = `Take the quantities from ${unesc(rec.label)} off the field totals?` + (below ? " Some of it is already billed. Pay apps sent stay as they are, and nothing more is suggested on those lines until the field total passes what they billed." : "");
    if (await askConfirm(said, "Undo import", below)) await post(`/api/jobs/${id}/quantities-undo/${rec.id}`, { below_billed: below });
  },
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
    const note = document.getElementById(`rec-edit-note-${rid}`);
    const r = await post(`/api/jobs/${id}/edit-record/${rid}`, { label, kind, ...(note ? { note: note.value } : {}) });
    if (r && r.ok) { clearForm(`record-${rid}`); editingRecord = ""; render(); }
  },
};
