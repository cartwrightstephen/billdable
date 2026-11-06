// Jobs, New job record: one form for what happened on site and the day's documents. The Diary,
// crew, weather and materials (T12); ticked boxes for quantities put in place today, which add to
// the job's totals for the period (M1), change order work and punchlist items (T11);
// then a section each for delivery tickets, progress photos, time sheets, invoices, inspections
// and other documents, each making job records of its own kind. The server checks every field
// (domain/daily.py). The stored kind, the route and the ids keep the word "daily".
import { esc, unesc, fmtDate } from "../format.js";
import { defaultJobId, RECORD_KINDS } from "../jobs.js";
import { fmtQty } from "../sov.js";
import { pathFor } from "../routes.js";
import { localDay } from "../tasks.js";
import { recordFiles } from "./job-record.js";
import { conversationFormHtml } from "./job.js";
import { readJson, setNetBanner } from "../net.js";
import { draftValue, clearForm, field, moneyField, formValues, formErrors, formDraft, refuseForm } from "../forms.js";
import { termText } from "../terms.js";
import { state, onSessionEnd, currentView, formOpen } from "../state.js";
import { post, toast, render, emptyState, nav, setState } from "../app-core.js";
import { focusAfterDraw } from "../dom.js";

// The New job record form starts on the job last used on this device (R2-07).
const DAILY_JOB_KEY = "duracash-daily-job";
function lastDailyJob() {
  try { return localStorage.getItem(DAILY_JOB_KEY) || ""; } catch (_) { return ""; }
}
export function rememberDailyJob(id) {
  try { localStorage.setItem(DAILY_JOB_KEY, unesc(id)); } catch (_) { /* private window: the only open job still comes first */ }
}

export const WEATHER = ["Clear", "Cloudy", "Rain", "Snow", "Wind", "Heat", "Cold"];   // domain.daily.WEATHER
export const SECTIONS = [["quantities", "Quantities"], ["co_work", "Change order work"], ["punch", "New punchlist items"]];   // in domain.daily.DAILY_SECTIONS
const MAX_UPLOAD_MB = 15;

// The document sections, as domain.daily.RECORD_SECTIONS has them, each opened by its chip
// ("+ Delivery ticket", Round 1 item 15): the record kind
// each makes (other's comes from its row) and only the boxes it needs. A section with more has
// Add another. Every row has its own files; each file is one record.
const OTHER_KINDS = () => Object.entries(RECORD_KINDS).filter(([k]) => k !== "daily").map(([k, v]) => [k, termText(v)]);
export const RECORD_SECTIONS = [
  { key: "delivery", title: "Delivery tickets", chip: "Delivery ticket", kind: "ticket", one: "delivery ticket", many: "delivery tickets", more: true, fields: [
    { name: "supplier", label: "Supplier" }, { name: "number", label: "Ticket number" },
    { name: "what", label: "What arrived", wide: true }] },
  { key: "photos", title: "Progress photos", chip: "Photos", kind: "photo", one: "progress photo", many: "progress photos", imagesOnly: true, fields: [
    { name: "note", label: "What they show", wide: true }] },
  { key: "time_sheets", title: "Time sheets", chip: "Time sheet", kind: "ticket", one: "time sheet", many: "time sheets", more: true, fields: [
    { name: "who", label: "Who or which crew" }, { name: "hours", label: "Hours", inputmode: "decimal" }] },
  { key: "invoices", title: "Invoices", chip: "Invoice", kind: "supplier_invoice", one: "invoice", many: "invoices", more: true, fields: [
    { name: "supplier", label: "Supplier" }, { name: "number", label: "Invoice number" }, { name: "amount", label: "Amount ($)", money: true }] },
  { key: "inspections", title: "Inspections", chip: "Inspection", kind: "inspection", one: "inspection", many: "inspections", more: true, fields: [
    { name: "who", label: "Who inspected", placeholder: "City electrical inspector" }, { name: "outcome", label: "Result", placeholder: "Passed rough-in" }] },
  { key: "other", title: "Other documents and notes", chip: "Other", one: "other document or note", many: "other documents and notes", more: true, fields: [
    { name: "kind", label: "Kind", options: OTHER_KINDS }, { name: "label", label: "What it is", placeholder: "COI Brightwater 2026-27" },
    { name: "note", label: "Note", wide: true }] },
];

// What the form holds beyond its typed boxes (those are kept by forms.js): the ticked boxes, how
// many rows each section has, and the files picked, by row ("delivery_0"). A file picker can't be
// refilled after a redraw, so the files are kept here and listed; once uploaded, a file is a
// record on the job and only its record id is kept, so a second Save doesn't upload it again.
// saved says what the last Save did, until the next one.
// open: the document sections whose chip was tapped; photosDone: photo rows the person said Done to.
export const daily = { checked: new Set(), rows: { co: 1 }, files: {}, uploaded: [], saved: null, open: new Set(), photosDone: new Set() };
function resetDaily() {
  daily.checked = new Set();
  daily.open = new Set();
  daily.photosDone = new Set();
  daily.rows = { co: 1 };
  daily.files = {};
  daily.uploaded = [];
  daily.saved = null;
}
onSessionEnd(resetDaily);

const typed = (name, fallback = "") => esc(draftValue("daily", name, fallback));
const errorOf = (name) => esc((formErrors.daily || {})[name] || "");
const errSpan = (id, name) => `<span class="field-error" id="${id}-err">${errorOf(name)}</span>`;
const lumpSum = (l) => l.lump_sum || (String(l.unit).toUpperCase() === "LS" && l.qty_milli === 1000);

// The chosen files of one row, each with a Remove button.
function fileList(key) {
  const list = daily.files[key] || [];
  const done = daily.uploaded.filter(u => u.key === key);
  if (!list.length && !done.length) return "";
  return `<ul class="daily-files">${done.map(u => `<li>✓ ${esc(u.name)}</li>`).join("")}${list.map((f, n) => `<li>${esc(f.name)}
    <button class="linkish" data-action="daily-file-remove" data-kind="${key}" data-n="${n}" aria-label="Remove ${esc(f.name)}">Remove</button></li>`).join("")}</ul>`;
}

// Take a photo opens the phone's rear camera, and asks for more than one where the phone allows;
// once a photo is in, it reads Take another photo, until Done. Choose files offers every source.
function filePickers(s, key) {
  const what = s.title.toLowerCase();
  const taken = (daily.files[key] || []).length + daily.uploaded.filter(u => u.key === key).length;
  if (s.imagesOnly && daily.photosDone.has(key) && taken) {
    return `<p class="record-files-done">${taken} photo${taken === 1 ? "" : "s"} <button class="linkish" data-action="daily-photos-more" data-kind="${key}">Add more</button></p>${fileList(key)}`;
  }
  return `<div class="record-files">
      <label class="btn small secondary file-pick">${taken ? "Take another photo" : "Take a photo"}<input type="file" class="visually-hidden" id="daily-${key}-camera" data-record-files="${key}" accept="image/*" capture="environment" multiple aria-label="Take a photo: ${what}"></label>
      <label class="btn small secondary file-pick">Choose files<input type="file" class="visually-hidden" id="daily-${key}-files" data-record-files="${key}" multiple${s.imagesOnly ? ' accept="image/*"' : ""} aria-label="Choose files: ${what}"></label>
      ${s.imagesOnly && taken ? `<button class="btn small" data-action="daily-photos-done" data-kind="${key}">Done</button>` : ""}
    </div>${fileList(key)}`;
}

function recordSectionHtml(s) {
  const rows = Array.from({ length: daily.rows[s.key] || 1 }, (_, n) => {
    const boxes = s.fields.map(f => {
      const opts = { form: "daily", name: `${s.key}_${f.name}_${n}`, id: `daily-${s.key}-${f.name}-${n}`, label: f.label, wide: f.wide, placeholder: f.placeholder || "", inputmode: f.inputmode || "" };
      if (f.money) return moneyField(opts);
      return field(f.options ? { ...opts, options: f.options() } : opts);
    }).join("");
    return `<div class="record-row"><div class="form-grid">${boxes}</div>${filePickers(s, `${s.key}_${n}`)}</div>`;
  }).join("");
  return `<section class="daily-section" data-record-section="${s.key}"><h3>${s.title}</h3>${rows}</section>`;
}

// A section is shown once its chip is tapped, and whenever something is in it: a typed box or a file.
function sectionOpen(s) {
  if (daily.open.has(s.key)) return true;
  const at = `${s.key}_`;
  return Object.keys(daily.files).some(k => k.startsWith(at) && daily.files[k].length) || daily.uploaded.some(u => u.key.startsWith(at))
    // Other's Kind alone is nothing in it (recordEntries reads it the same way).
    || Object.entries(formDraft.daily || {}).some(([k, v]) => String(v || "").trim() && s.fields.some(f => f.name !== "kind" && k.startsWith(`${at}${f.name}_`)));
}

// The chips that open the document sections; tapped again, a section that takes more adds a row.
function recordChipsHtml() {
  return `<div class="record-chips" role="group" aria-label="Add to this record">${RECORD_SECTIONS.map(s =>
    `<button type="button" class="btn small secondary" data-action="daily-chip" data-kind="${s.key}"${daily.open.has(s.key) ? ' aria-pressed="true"' : ""}>+ ${s.chip}</button>`).join("")}</div>`;
}

function sectionHtml(key, j) {
  if (key === "quantities") {
    const lines = ((j.sov_view && j.sov_view.lines) || []).filter(l => l.done_milli < l.qty_milli);
    return `<div class="daily-section"><h3>Quantities put in place today</h3>
      <p class="sub">They add to the job's totals for the period.</p>
      ${lines.length ? `<div class="table-wrap sov-wrap"><table class="sov-table">
        <tr><th>Item</th><th>Description</th><th>Done so far</th><th>Today</th></tr>
        ${lines.map(l => `<tr><td>${l.item}</td><td class="sov-desc-cell">${l.description}</td>
          <td class="num">${lumpSum(l) ? `${+(l.done_milli / 10).toFixed(1)}%` : `${fmtQty(l.done_milli)} of ${fmtQty(l.qty_milli)} ${l.unit}`}</td>
          <td data-label="${lumpSum(l) ? "% done today" : `Today (${l.unit})`}"><input class="sov-in sov-num" id="daily-qty-${l.id}" data-field="qty_${l.id}" value="${typed(`qty_${l.id}`)}" inputmode="decimal"
            aria-label="${l.description}: ${lumpSum(l) ? "% done today" : `${l.unit} put in place today`}"> ${lumpSum(l) ? "%" : l.unit}${errSpan(`daily-qty-${l.id}`, `qty_${l.id}`)}</td></tr>`).join("")}
      </table></div>` : `<p class="sub">Every line is done.</p>`}
    </div>`;
  }
  if (key === "co_work") {
    const cos = (j.change_orders || []).filter(c => c.status !== "withdrawn");
    const options = [["", "New work, not priced yet"], ...cos.map(c => [c.id, `CO ${unesc(String(c.number))} ${unesc(c.title || "")}`.trim()])];
    const rows = Array.from({ length: daily.rows.co }, (_, n) => `<div class="form-grid">
        ${field({ form: "daily", name: `co_${n}`, id: `daily-co-${n}`, label: "Change order", options })}
        ${field({ form: "daily", name: `co_hours_${n}`, id: `daily-co-hours-${n}`, label: "Hours", inputmode: "decimal" })}
        ${field({ form: "daily", name: `co_desc_${n}`, id: `daily-co-desc-${n}`, label: "Work done", wide: true })}
      </div>`).join("");
    return `<div class="daily-section"><h3>Change order work</h3>${rows}
      <button class="btn small secondary" data-action="daily-more" data-kind="co">Add another</button></div>`;
  }
  if (key === "punch") return `<div class="daily-section"><h3>New punchlist items</h3>
      <div class="form-grid">${field({ form: "daily", name: "punch", id: "daily-punch", label: "One item per line", multiline: true, wide: true })}</div></div>`;
  return "";
}

// A saved record's extra lines in Recent records: crew, weather, materials, deliveries, what
// each ticked section held, and links to the files attached (rj is the record's job).
export function recordMeta(r, rj) {
  const out = [];
  const count = r.crew_count === undefined || r.crew_count === null || r.crew_count === "" ? "" : String(r.crew_count);
  const names = (r.crew_names || []).join(", ");
  if (count || names) out.push(`Crew: ${count && names ? `${count} (${names})` : count || names}`);
  if (r.weather || r.weather_note) out.push(`Weather: ${[r.weather, r.weather_note].filter(Boolean).join(", ")}`);
  if (r.materials) out.push(`Materials: ${r.materials}`);
  if (r.deliveries) out.push(`Deliveries: ${r.deliveries}`);
  const s = r.sections || {};
  const lines = Object.fromEntries((((rj && rj.sov_view) || {}).lines || []).map(l => [l.id, l]));
  const cos = Object.fromEntries(((rj && rj.change_orders) || []).map(c => [c.id, c]));
  if (s.photos) out.push(`Progress photos${s.photos.note ? `: ${s.photos.note}` : ""}`);
  if (s.quantities) out.push(`Quantities: ${s.quantities.map(q => {
    const l = lines[q.line_id];
    if (!l) return fmtQty(q.qty_milli);
    return lumpSum(l) ? `${fmtQty(q.qty_milli * 100)}% ${l.description}` : `${fmtQty(q.qty_milli)} ${l.unit} ${l.description}`;
  }).join(", ")}`);
  // Saved, but the quantities couldn't be added (B5 of the Oct 30 review): say why, under them.
  if (s.quantities && r.quantities_not_applied) out.push(`<span class="blocker">Not added to the schedule of values: ${r.quantities_not_applied}</span>`);
  for (const c of s.co_work || []) {
    const co = cos[c.co_id];
    out.push(`Change order work (${co ? `CO ${co.number}` : "new, not priced yet"}): ${c.description}${c.hours_milli ? `, ${fmtQty(c.hours_milli)} h` : ""}`);
  }
  for (const i of s.inspections || []) out.push(`${i.type === "discussion" ? "Discussion" : "Inspection"}${i.who ? ` with ${i.who}` : ""}${i.outcome ? `: ${i.outcome}` : ""}`);
  if ((s.punch || []).length) out.push(`New punchlist items: ${s.punch.join("; ")}`);
  const attached = (r.attachments || []).map(id => ((rj && rj.records) || []).find(x => x.id === id)).filter(Boolean);
  const links = attached.map(recordFiles).filter(Boolean).join(" · ");
  if (links) out.push(`Files: ${links}`);
  else if ((r.attachments || []).length) out.push(`${r.attachments.length} file${r.attachments.length === 1 ? "" : "s"} attached`);
  return out.length ? `<div class="daily-rec-meta">${out.map(x => `<div>${x}</div>`).join("")}</div>` : "";
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  daily() {
    const openJobs = state.jobs.filter(j => !j.closed);
    const pick = defaultJobId(openJobs, esc(lastDailyJob()));
    const j = openJobs.find(x => x.id === pick);
    const hasSov = !!(j && j.sov_view && j.sov_view.present);
    const records = state.daily_records || [];
    // Quantities are open whenever the job has a schedule (Round 1, item 15); the other two are ticked.
    const box = (key, label) => key === "quantities" ? ""
      : `<label><input type="checkbox" data-daily-section="${key}"${daily.checked.has(key) ? " checked" : ""}> ${label}</label>`;
    return `
      <h1>New job record</h1>
      ${daily.saved ? `<p class="saved-result" role="status">Saved to <a href="${pathFor("job", daily.saved.jobId)}" data-action="open-job" data-id="${daily.saved.jobId}">${daily.saved.jobName}</a>: ${esc(daily.saved.words)}.${esc(daily.saved.added || "")}</p>` : ""}
      <div class="card" data-form-key="daily">
        ${openJobs.length ? `<div class="form-grid">
          <div><label for="daily-job">Job</label><select id="daily-job"><option value="">Pick a job…</option>${openJobs.map(o => `<option value="${o.id}"${o.id === pick ? " selected" : ""}>${o.name}</option>`).join("")}</select></div>
          ${field({ form: "daily", name: "date", id: "daily-date", label: "Date", type: "date", value: localDay(new Date()), attrs: `max="${localDay(new Date())}"` })}
          <div class="span-2"><label for="daily-notes">Diary</label><textarea id="daily-notes" data-field="notes" rows="3" placeholder="Framing done on the east wall, inspector booked for Thursday">${esc(draftValue("daily", "notes"))}</textarea><span class="field-error" id="daily-notes-err">${errorOf("notes")}</span></div>
          ${field({ form: "daily", name: "crew_count", id: "daily-crew", label: "Crew members", type: "number", inputmode: "numeric", attrs: 'min="0" max="500"' })}
          ${field({ form: "daily", name: "crew_names", id: "daily-crew-names", label: "Names (optional)", placeholder: "Ana, Ben, Cruz" })}
          ${field({ form: "daily", name: "weather", id: "daily-weather", label: "Weather", options: [["", "Not noted"], ...WEATHER.map(w => [w, w])] })}
          ${field({ form: "daily", name: "weather_note", id: "daily-weather-note", label: "Weather note (optional)", placeholder: "Rain until 10" })}
          ${field({ form: "daily", name: "materials", id: "daily-materials", label: "Materials used or installed", multiline: true, wide: true })}
        </div>
        <div class="daily-checks" role="group" aria-label="Also on this record">${SECTIONS.map(([k, label]) => box(k, label)).join("")}</div>
        ${j && hasSov ? sectionHtml("quantities", j) : ""}
        ${j ? SECTIONS.filter(([k]) => k !== "quantities" && daily.checked.has(k)).map(([k]) => sectionHtml(k, j)).join("") : ""}
        ${recordChipsHtml()}
        ${RECORD_SECTIONS.filter(sectionOpen).map(recordSectionHtml).join("")}
        <button class="btn" data-action="daily-save">Save job record</button>` : emptyState("No open jobs", "", `<button class="btn" data-action="onb-newjob">New job</button>`)}
      </div>
      ${j ? formOpen.conversation ? conversationFormHtml(j) : `<p class="daily-conversation"><button class="btn secondary" data-action="toggle-form" data-form="conversation">Log a Conversation</button></p>` : ""}
      <div class="card">
        <h2>Recent diary entries</h2>
        ${records.length ? records.slice().reverse().map(r => {
          const rj = state.jobs.find(x => x.id === r.job_id);
          return `<div class="list-item"><strong>${fmtDate(r.at)}</strong> · ${rj ? `<a href="${pathFor("job", rj.id)}" data-action="open-job" data-id="${rj.id}">${rj.name}</a>` : "Deleted job"}<div>${r.notes}</div>${recordMeta(r, rj)}</div>`;
        }).join("") : `<p class="sub">None yet.</p>`}
      </div>
    `;
  },
};

// The request body from what is typed (formValues' names) and the ticked boxes.
export function dailyBody(v, j, checked) {
  const body = { notes: v.notes || "" };
  for (const k of ["crew_count", "crew_names", "weather", "weather_note", "materials", "deliveries"]) if (v[k]) body[k] = v[k];
  const sections = {};
  // Quantities are always on the form for a job with a schedule: sent when one is typed.
  const qty = j && j.sov_view && j.sov_view.present
    ? (j.sov_view.lines || []).filter(l => (v[`qty_${l.id}`] || "").trim()).map(l => ({ line_id: unesc(l.id), qty: v[`qty_${l.id}`].trim() })) : [];
  if (qty.length) sections.quantities = qty;
  if (checked.has("co_work")) sections.co_work = Object.keys(v).filter(k => /^co_desc_\d+$/.test(k)).map(k => k.slice(8))
    .map(n => ({ co_id: v[`co_${n}`] || "", description: v[`co_desc_${n}`] || "", hours: v[`co_hours_${n}`] || "" }));
  if (checked.has("punch")) sections.punch = v.punch || "";
  if (Object.keys(sections).length) body.sections = sections;
  return body;
}

// The document sections' rows that have something in them, for the request: what was typed and
// the records of the files already uploaded. A row's Kind alone is not something in it.
export function recordEntries(v, uploaded = []) {
  const out = [];
  for (const s of RECORD_SECTIONS) {
    const rows = new Set(uploaded.filter(u => u.key.startsWith(`${s.key}_`)).map(u => +u.key.slice(s.key.length + 1)));
    for (const name of Object.keys(v)) {
      const hit = name.startsWith(`${s.key}_`) && /_(\d+)$/.exec(name);
      if (hit && s.fields.some(f => name === `${s.key}_${f.name}_${hit[1]}`)) rows.add(+hit[1]);
    }
    for (const n of [...rows].sort((a, b) => a - b)) {
      const entry = { section: s.key, row: n };
      for (const f of s.fields) if (v[`${s.key}_${f.name}_${n}`]) entry[f.name] = v[`${s.key}_${f.name}_${n}`];
      const attachments = uploaded.filter(u => u.key === `${s.key}_${n}`).map(u => u.record_id);
      if (attachments.length) entry.attachments = attachments;
      if (Object.keys(entry).some(k => !["section", "row", "kind"].includes(k))) out.push(entry);
    }
  }
  return out;
}

// What a Save made, in words: "a diary entry, 2 delivery tickets, 1 invoice".
export function savedWords(notes, entries) {
  const parts = notes ? ["1 diary entry"] : [];
  for (const s of RECORD_SECTIONS) {
    const n = entries.filter(e => e.section === s.key).reduce((sum, e) => sum + ((e.attachments || []).length || 1), 0);
    if (n) parts.push(`${n} ${n === 1 ? s.one : s.many}`);
  }
  return parts.join(", ");
}

// The kind a row's files go up as; the Save puts the row's fields on them.
function uploadKind(key, v) {
  const at = key.lastIndexOf("_");
  const s = RECORD_SECTIONS.find(x => x.key === key.slice(0, at));
  return (s && s.kind) || v[`other_kind_${key.slice(at + 1)}`] || "note";
}

// Upload the picked files to the job one at a time (the importer's ?job=&file=1&for_job_record=1&kind=), keeping
// each new record's id. Returns false if one didn't go up: it and the rest stay picked, what is
// typed stays, and those already up aren't sent again.
async function uploadDailyFiles(jobId, v) {
  for (const key of Object.keys(daily.files)) {
    while (daily.files[key].length) {
      const f = daily.files[key][0];
      if (f.size > MAX_UPLOAD_MB * 1024 * 1024) { toast(`Not saved: ${f.name} is larger than ${MAX_UPLOAD_MB} MB. What you typed is still here. Remove it or pick a smaller one, then save again.`); return false; }
      let res, data;
      try {
        res = await fetch(`/api/import/file?job=${encodeURIComponent(unesc(jobId))}&file=1&for_job_record=1&kind=${encodeURIComponent(uploadKind(key, v))}`, {
          method: "POST", headers: { "Content-Type": f.type || "application/octet-stream", "X-Filename": encodeURIComponent(f.name || "upload") }, body: f,
        });
        data = await readJson(res);
      } catch (_) { setNetBanner(true); toast(`Not saved: can't reach Duracash. What you typed and your files are still here. Save again when you're back online.`); return false; }
      if (!res.ok) { toast(`Not saved: ${data.error || `${f.name} didn't upload.`} What you typed and your files are still here. Save again to retry.`); return false; }
      // The answer names the record this upload made: someone else's upload is never taken for it.
      (data.uploaded_record_ids || []).forEach(id => daily.uploaded.push({ key, name: f.name, record_id: id }));
      daily.files[key].shift();
      setNetBanner(false);
      if (Array.isArray(data.jobs)) setState(data);
    }
  }
  return true;
}

// The ticked boxes and the file pickers redraw the form; what is typed stays (forms.js).
document.addEventListener("change", (e) => {
  const el = e.target;
  if (!el) return;
  if (el.dataset && el.dataset.dailySection) {
    if (el.checked) daily.checked.add(el.dataset.dailySection); else daily.checked.delete(el.dataset.dailySection);
    render();
  } else if (el.dataset && el.dataset.recordFiles) {
    (daily.files[el.dataset.recordFiles] ||= []).push(...(el.files || []));
    render();
  } else if (el.id === "daily-job" && currentView === "daily") {
    // Files already up are on the job they went to; they can't come along to another.
    if (daily.uploaded.length) { daily.uploaded = []; toast("The files already uploaded stay on the job they went to."); }
    rememberDailyJob(el.value);
    render();
  }
});

// A second tap while a save is going makes nothing more.
let saving = false;

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  // "New job record" on a job page or a row of Open jobs (T9): the form with that job picked.
  "daily-for-job": (el, id) => { if (id) rememberDailyJob(id); nav("daily"); },
  // "+ Delivery ticket": opens its section; once open, adds another row where the section takes more.
  "daily-chip": (el) => {
    const key = el.dataset.kind;
    const s = RECORD_SECTIONS.find(x => x.key === key);
    if (!s) return;
    if (!daily.open.has(key)) daily.open.add(key);
    else if (s.more) daily.rows[key] = Math.min(10, (daily.rows[key] || 1) + 1);
    render();
    const n = (daily.rows[key] || 1) - 1;
    focusAfterDraw(() => document.querySelector(`[data-record-section="${key}"] .record-row:nth-of-type(${n + 1}) input, [data-record-section="${key}"] .record-row:nth-of-type(${n + 1}) select`));
  },
  "daily-photos-done": (el) => { daily.photosDone.add(el.dataset.kind); render(); },
  "daily-photos-more": (el) => { daily.photosDone.delete(el.dataset.kind); render(); },
  "daily-more": (el) => { daily.rows[el.dataset.kind] = Math.min(10, (daily.rows[el.dataset.kind] || 1) + 1); render(); },
  "daily-file-remove": (el) => { daily.files[el.dataset.kind].splice(+el.dataset.n, 1); render(); },
  "daily-save": async () => {
    if (saving) return;
    saving = true;
    try { await saveRecord(); } finally { saving = false; }
  },
};

async function saveRecord() {
    const notes = document.querySelector("#daily-notes")?.value || "";
    const job = document.querySelector("#daily-job")?.value;
    if (!job) { toast("Pick a job."); document.querySelector("#daily-job")?.focus(); return; }
    const v = { ...formValues("daily"), notes };
    const picked = daily.uploaded.length || Object.values(daily.files).some(list => list.length);
    if (!notes.trim() && !picked && !recordEntries(v).length) { toast("Write the diary entry, or add a document."); refuseForm("daily", { notes: "Write the diary entry, or add a document." }); return; }
    // Remembered before saving: the save redraws the page, which picks the job last used.
    rememberDailyJob(job);
    if (!(await uploadDailyFiles(job, v))) { render(); return; }
    const j = state.jobs.find(x => x.id === job);
    const body = dailyBody(v, j, daily.checked);
    const entries = recordEntries(v, daily.uploaded);
    if (entries.length) body.entries = entries;
    if (v.date) body.date = v.date;
    body.today = localDay(new Date());   // the record's day is the person's own date, not the server's UTC one
    const r = await post(`/api/jobs/${job}/daily-record`, body, { formKey: "daily", quiet: true });
    // A file that can't be filed comes off the form, so the next save doesn't meet it again.
    if (r && r.data && r.data.stale_record_ids) { daily.uploaded = daily.uploaded.filter(u => !r.data.stale_record_ids.includes(u.record_id)); render(); }
    if (r && r.ok) {
      resetDaily(); clearForm("daily");   // saved: the form is empty for the next one, on the same job
      const words = savedWords(notes.trim(), entries);
      const name = j ? j.name : "";
      const last = (state.daily_records || []).filter(x => x.job_id === job).slice(-1)[0];
      const blocked = notes.trim() && last && last.quantities_not_applied;
      const added = (body.sections || {}).quantities && !blocked ? " Quantities added to the job's totals for the period." : "";
      daily.saved = { jobId: job, jobName: name, words, added };
      render();
      // The record is kept even when its quantities couldn't be added (B5): say so plainly.
      const stayed = (r.data.not_added || []).length;
      if (blocked) toast(`Saved. The quantities were not added to the schedule of values: ${unesc(last.quantities_not_applied)}`);
      else if (stayed) toast(`Job record saved to ${unesc(name)}. ${stayed === 1 ? "One file" : `${stayed} files`} stayed on the job records as they were: someone else's, or filed already.`);
      else toast(`Job record saved to ${unesc(name)}.${added}`);
    }
}
