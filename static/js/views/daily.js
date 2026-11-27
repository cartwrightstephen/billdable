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
import { teamUsers } from "./jobs.js";
import { readJson, setNetBanner } from "../net.js";
import { draftValue, clearForm, field, moneyField, formValues, formErrors, formDraft, refuseForm } from "../forms.js";
import { termText } from "../terms.js";
import { state, onSessionEnd, currentView } from "../state.js";
import { post, toast, render, emptyState, nav, setState } from "../app-core.js";
import { focusAfterDraw, askConfirm } from "../dom.js";

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
  // The time sheet is rows of crew member and hours (Nov 26), drawn by timeSheetHtml, always on the form.
  { key: "time_sheets", title: "Time sheet", kind: "ticket", one: "time sheet", many: "time sheets", always: true, fields: [] },
  { key: "invoices", title: "Invoices", chip: "Invoice", kind: "supplier_invoice", one: "invoice", many: "invoices", more: true, fields: [
    { name: "supplier", label: "Supplier" }, { name: "number", label: "Invoice number" }, { name: "amount", label: "Amount ($)", money: true }] },
  { key: "inspections", title: "Inspections", chip: "Inspection", kind: "inspection", one: "inspection", many: "inspections", more: true, fields: [
    { name: "who", label: "Who inspected", placeholder: "City electrical inspector" }, { name: "outcome", label: "Result", placeholder: "Passed rough-in" }] },
  // Conversation / note with client (Nov 26): what Log a conversation did, here with the rest.
  { key: "conversation", title: "Conversation / note with client", chip: "Conversation / note with client", kind: "conversation", one: "conversation", many: "conversations", more: true, noFiles: true, fields: [
    { name: "text", label: "What was said", wide: true, placeholder: "Owner asked to move the vanity 6 inches" },
    { name: "next_on", label: "Check again on (optional)", type: "date", waiting: true }] },
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
// editing: Edit on a saved job record (Nov 26) — {jobId, dailyId, day, recordIds: {"delivery_0": record
// id}, files: {"delivery_0": [names]}}; the same form, saved to that record.
export const daily = { checked: new Set(), rows: { co: 1, crew: 1 }, files: {}, uploaded: [], saved: null, open: new Set(), photosDone: new Set(), editing: null };
function resetDaily() {
  daily.checked = new Set();
  daily.open = new Set();
  daily.photosDone = new Set();
  daily.rows = { co: 1, crew: 1 };
  daily.files = {};
  daily.uploaded = [];
  daily.saved = null;
  daily.editing = null;
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
  const kept = (daily.editing && daily.editing.files[key]) || [];
  if (!list.length && !done.length && !kept.length) return "";
  return `<ul class="daily-files">${kept.map(name => `<li>✓ ${esc(name)}</li>`).join("")}${done.map(u => `<li>✓ ${esc(u.name)}</li>`).join("")}${list.map((f, n) => `<li>${esc(f.name)}
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

// A row can be taken off before the save, and on Edit (Nov 26: items added couldn't be deleted).
function removeButton(kind, n, what) {
  return `<button type="button" class="linkish row-remove" data-action="daily-row-remove" data-kind="${kind}" data-n="${n}" aria-label="Remove ${esc(what)} ${n + 1}">Remove</button>`;
}

function recordSectionHtml(s, j) {
  if (s.key === "time_sheets") return timeSheetHtml(s);
  const waiting = !!(j && (j.invoices || []).some(i => ["sent", "approved", "partial"].includes(i.status)));
  const rows = Array.from({ length: daily.rows[s.key] || 1 }, (_, n) => {
    const boxes = s.fields.filter(f => !f.waiting || waiting).map(f => {
      const opts = { form: "daily", name: `${s.key}_${f.name}_${n}`, id: `daily-${s.key}-${f.name}-${n}`, label: f.label, wide: f.wide, placeholder: f.placeholder || "", inputmode: f.inputmode || "", type: f.type || "text" };
      if (f.money) return moneyField(opts);
      return field(f.options ? { ...opts, options: f.options() } : opts);
    }).join("");
    const kept = daily.editing && daily.editing.files[`${s.key}_${n}`];
    return `<div class="record-row"><div class="form-grid">${boxes}</div>${s.noFiles ? "" : kept && kept.length ? fileList(`${s.key}_${n}`) : filePickers(s, `${s.key}_${n}`)}${removeButton(s.key, n, s.one)}</div>`;
  }).join("");
  return `<section class="daily-section" data-record-section="${s.key}"><h3>${s.title}</h3>${rows}</section>`;
}

// The time sheet (Nov 26): a row per person, picked from the team or typed for someone from outside
// the company, with their hours (numbers only), and the total. The crew count comes from the rows.
export const OTHER_PERSON = "__other";
export function crewRows(v, users = []) {
  const out = [];
  for (let n = 0; n < (daily.rows.crew || 1); n++) {
    const pick = v[`ts_pick_${n}`] || "";
    const user = users.find(u => unesc(u.id) === pick);
    const name = user ? unesc(user.name || user.email || "") : (v[`ts_name_${n}`] || "").trim();
    const hours = (v[`ts_hours_${n}`] || "").trim();
    if (!name && !hours) continue;
    out.push({ name, hours, ...(user ? { member_id: unesc(user.id) } : {}) });
  }
  return out;
}
export function crewTotal(rows) {
  const total = rows.reduce((sum, r) => sum + (Number(String(r.hours).replace(/,/g, "")) || 0), 0);
  return +total.toFixed(2);
}
function timeSheetHtml(s) {
  const users = teamUsers();
  const options = [["", "Pick who worked…"], ...users.map(u => [unesc(u.id), unesc(u.name || u.email || "")]), [OTHER_PERSON, "Someone else (type the name)"]];
  const rows = Array.from({ length: daily.rows.crew || 1 }, (_, n) => {
    const pick = draftValue("daily", `ts_pick_${n}`, "");
    const typedName = draftValue("daily", `ts_name_${n}`, "");
    const other = pick === OTHER_PERSON || (!pick && typedName);
    return `<div class="crew-row form-grid">
      ${field({ form: "daily", name: `ts_pick_${n}`, id: `daily-ts-pick-${n}`, label: "Crew member", options, value: other && !pick ? OTHER_PERSON : "", attrs: 'data-crew-pick="1"' })}
      ${other ? field({ form: "daily", name: `ts_name_${n}`, id: `daily-ts-name-${n}`, label: "Name", placeholder: "Independent contractor, day labor" }) : ""}
      ${field({ form: "daily", name: `ts_hours_${n}`, id: `daily-ts-hours-${n}`, label: "Hours", inputmode: "decimal", attrs: 'data-crew-hours="1" pattern="[0-9]*[.]?[0-9]*"' })}
      ${removeButton("crew", n, "crew member")}
    </div>`;
  }).join("");
  const total = crewTotal(crewRows(formDraft.daily || {}, users));
  const err = errorOf("time_sheets");
  return `<section class="daily-section time-sheet" data-record-section="time_sheets"><h3>${s.title}</h3>
    <p class="sub">Who worked and their hours. Pick someone on your team, or Someone else for a worker from outside the company.</p>
    ${rows}
    <div class="form-inline"><button type="button" class="btn small secondary" data-action="daily-more" data-kind="crew">Add a person</button>
      <span class="crew-total" id="daily-ts-total" aria-live="polite">Total: ${total} h</span></div>
    ${err ? `<span class="field-error">${err}</span>` : ""}
    ${filePickers({ ...s, title: "Time sheet" }, "time_sheets_0")}
  </section>`;
}

// A section is shown once its chip is tapped, and whenever something is in it: a typed box or a file.
function sectionOpen(s) {
  if (s.always || daily.open.has(s.key)) return true;
  const at = `${s.key}_`;
  return Object.keys(daily.files).some(k => k.startsWith(at) && daily.files[k].length) || daily.uploaded.some(u => u.key.startsWith(at))
    // Other's Kind alone is nothing in it (recordEntries reads it the same way).
    || Object.entries(formDraft.daily || {}).some(([k, v]) => String(v || "").trim() && s.fields.some(f => f.name !== "kind" && k.startsWith(`${at}${f.name}_`)));
}

// Files dropped on the form, or on a job's page (which opens this form for that job): photos go to
// Progress photos and anything else to Other documents, each section opened (Yusuf, 10 Nov: the
// drop area is on New job record only).
export function stageRecordFiles(files) {
  const list = [...(files || [])];
  const images = list.filter(f => /^image\//.test(f.type || ""));
  const rest = list.filter(f => !/^image\//.test(f.type || ""));
  if (images.length) { (daily.files.photos_0 ||= []).push(...images); daily.open.add("photos"); daily.photosDone.delete("photos_0"); }
  if (rest.length) { (daily.files.other_0 ||= []).push(...rest); daily.open.add("other"); }
  render();
}

// The chips that open the document sections; tapped again, a section that takes more adds a row.
function recordChipsHtml() {
  return `<div class="record-chips" role="group" aria-label="Add to this record">${RECORD_SECTIONS.filter(s => !s.always).map(s =>
    `<button type="button" class="btn small secondary" data-action="daily-chip" data-kind="${s.key}"${daily.open.has(s.key) ? ' aria-pressed="true"' : ""}>+ ${s.chip}</button>`).join("")}</div>`;
}

// What the quantities do, in words that fit what is typed: a lump sum's % is done to date.
export function quantitiesNote(lines) {
  const lump = lines.filter(lumpSum).length;
  if (lump && lump === lines.length) return "They set the job's % done to date.";
  if (lump) return "A quantity today adds to the job's totals; a % is done to date.";
  return "They add to the job's totals for the period.";
}

function sectionHtml(key, j) {
  if (key === "quantities") {
    // On Edit, a line this record put quantities on stays, even if it is done now.
    const lines = ((j.sov_view && j.sov_view.lines) || []).filter(l => l.done_milli < l.qty_milli || String(draftValue("daily", `qty_${l.id}`, "")).trim());
    // A lump sum asks its % done to date, the figure a foreman knows; the server records the step
    // up from the field total (domain.daily.clean_daily_details). Other lines take today's quantity.
    // A mixed schedule labels each row as it is sent (dailyBody): % done to date, or today's quantity.
    const allLump = lines.length > 0 && lines.every(lumpSum);
    const anyLump = lines.some(lumpSum);
    return `<div class="daily-section"><h3>${allLump ? "Work done to date" : anyLump ? "Work done" : "Quantities put in place today"}</h3>
      <p class="sub">${quantitiesNote(lines)}</p>
      ${lines.length ? `<div class="table-wrap sov-wrap"><table class="sov-table">
        <tr><th>Item</th><th>Description</th><th>Done so far</th><th>${allLump ? "% done to date" : anyLump ? "Today, or % done to date" : "Today"}</th></tr>
        ${lines.map(l => `<tr><td>${l.item}</td><td class="sov-desc-cell">${l.description}</td>
          <td class="num">${lumpSum(l) ? `${+(l.done_milli / 10).toFixed(1)}%` : `${fmtQty(l.done_milli)} of ${fmtQty(l.qty_milli)} ${l.unit}`}</td>
          <td data-label="${lumpSum(l) ? "% done to date" : `Today (${l.unit})`}">${lumpSum(l) ? `<span class="with-suffix inline">` : ""}<input class="sov-in sov-num" id="daily-qty-${l.id}" data-field="qty_${l.id}" value="${typed(`qty_${l.id}`)}" inputmode="decimal"
            aria-label="${l.description}: ${lumpSum(l) ? "% done to date" : `${l.unit} put in place today`}">${lumpSum(l) ? `<span class="suffix" aria-hidden="true">%</span></span>` : " " + l.unit}${errSpan(`daily-qty-${l.id}`, `qty_${l.id}`)}</td></tr>`).join("")}
      </table></div>` : `<p class="sub">Every line is done.</p>`}
    </div>`;
  }
  if (key === "co_work") {
    const cos = (j.change_orders || []).filter(c => c.status !== "withdrawn");
    const options = [["", "New work, not priced yet"], ...cos.map(c => [c.id, `CO ${unesc(String(c.number))} ${unesc(c.title || "")}${c.unpriced ? " (no price yet)" : ""}`.trim()])];
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
    if (lumpSum(l) && q.to_date_milli != null) return `${l.description} ${fmtQty(q.to_date_milli * 100)}% done to date (+${fmtQty(q.qty_milli * 100)}%)`;
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
    // Left for another page and back (which clears forms): the edit is over.
    if (daily.editing && !formDraft.daily) resetDaily();
    const ed = daily.editing && openJobs.some(x => x.id === daily.editing.jobId) ? daily.editing : null;
    const pick = ed ? ed.jobId : defaultJobId(openJobs, esc(lastDailyJob()));
    const j = openJobs.find(x => x.id === pick);
    const hasSov = !!(j && j.sov_view && j.sov_view.present);
    const records = state.daily_records || [];
    // Quantities are open whenever the job has a schedule (Round 1, item 15); the other two are ticked.
    const box = (key, label) => key === "quantities" ? ""
      : `<label><input type="checkbox" data-daily-section="${key}"${daily.checked.has(key) ? " checked" : ""}> ${label}</label>`;
    return `
      <h1>${ed ? "Edit job record" : "New job record"}</h1>
      ${daily.saved ? `<p class="saved-result" role="status">Saved to <a href="${pathFor("job", daily.saved.jobId)}" data-action="open-job" data-id="${daily.saved.jobId}">${daily.saved.jobName}</a>: ${esc(daily.saved.words)}.${esc(daily.saved.added || "")}</p>` : ""}
      ${ed ? `<p class="sub">Changing the job record of ${fmtDate(ed.day)} on ${j ? j.name : "this job"}. Remove takes a row off; what has a file stays on the job records.</p>` : ""}
      <div class="card" data-form-key="daily">
        ${openJobs.length ? `<div class="form-grid">
          <div><label for="daily-job">Job</label><select id="daily-job"${ed ? " disabled" : ""}><option value="">Pick a job…</option>${openJobs.map(o => `<option value="${o.id}"${o.id === pick ? " selected" : ""}>${o.name}</option>`).join("")}</select></div>
          ${field({ form: "daily", name: "date", id: "daily-date", label: "Date", type: "date", value: ed ? ed.day : localDay(new Date()), attrs: `max="${localDay(new Date())}"` })}
          <div class="span-2"><label for="daily-notes">Diary</label><textarea id="daily-notes" data-field="notes" rows="3" placeholder="Framing done on the east wall, inspector booked for Thursday">${esc(draftValue("daily", "notes"))}</textarea><span class="field-error" id="daily-notes-err">${errorOf("notes")}</span></div>
          ${field({ form: "daily", name: "weather", id: "daily-weather", label: "Weather", options: [["", "Not noted"], ...WEATHER.map(w => [w, w])] })}
          ${field({ form: "daily", name: "weather_note", id: "daily-weather-note", label: "Weather note (optional)", placeholder: "Rain until 10" })}
          ${field({ form: "daily", name: "materials", id: "daily-materials", label: "Materials used or installed", multiline: true, wide: true })}
        </div>
        <div class="daily-checks" role="group" aria-label="Also on this record">${SECTIONS.map(([k, label]) => box(k, label)).join("")}</div>
        ${j && hasSov ? sectionHtml("quantities", j) : ""}
        ${j ? SECTIONS.filter(([k]) => k !== "quantities" && daily.checked.has(k)).map(([k]) => sectionHtml(k, j)).join("") : ""}
        ${recordChipsHtml()}
        <div class="dropzone small record-drop pointer-only">Drop files here</div>
        ${RECORD_SECTIONS.filter(sectionOpen).map(s => recordSectionHtml(s, j)).join("")}
        <div class="form-inline"><button class="btn" data-action="daily-save">${ed ? "Save changes" : "Save job record"}</button>
          ${ed ? `<button class="btn secondary" data-action="daily-edit-cancel">Cancel</button>` : ""}</div>` : emptyState("No open jobs", "", `<button class="btn" data-action="onb-newjob">New job</button>`)}
      </div>
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
  for (const k of ["weather", "weather_note", "materials", "deliveries"]) if (v[k]) body[k] = v[k];
  const sections = {};
  // Quantities are always on the form for a job with a schedule: sent when one is typed.
  const qty = j && j.sov_view && j.sov_view.present
    ? (j.sov_view.lines || []).filter(l => (v[`qty_${l.id}`] || "").trim())
      .map(l => ({ line_id: unesc(l.id), qty: v[`qty_${l.id}`].trim(), ...(lumpSum(l) ? { to_date: true } : {}) })) : [];
  if (qty.length) sections.quantities = qty;
  if (checked.has("co_work")) sections.co_work = Object.keys(v).filter(k => /^co_desc_\d+$/.test(k)).map(k => k.slice(8))
    .map(n => ({ co_id: v[`co_${n}`] || "", description: v[`co_desc_${n}`] || "", hours: v[`co_hours_${n}`] || "" }));
  if (checked.has("punch")) sections.punch = v.punch || "";
  if (Object.keys(sections).length) body.sections = sections;
  return body;
}

// The document sections' rows that have something in them, for the request: what was typed and
// the records of the files already uploaded. A row's Kind alone is not something in it. The time
// sheet is one entry of crew rows. On Edit, a row saved before carries its record_id; one emptied
// of what was typed, with no file, is left out, and the server takes it off.
export function recordEntries(v, uploaded = [], editing = null, users = []) {
  const out = [];
  const ids = (editing && editing.recordIds) || {};
  const kept = (key) => !!(editing && (editing.files[key] || []).length);
  for (const s of RECORD_SECTIONS) {
    if (s.key === "time_sheets") {
      const crew = crewRows(v, users);
      const attachments = uploaded.filter(u => u.key === "time_sheets_0").map(u => u.record_id);
      const rid = ids.time_sheets_0;
      if (crew.length || attachments.length || (rid && kept("time_sheets_0"))) {
        out.push({ section: "time_sheets", row: 0, ...(crew.length ? { crew } : {}), ...(attachments.length ? { attachments } : {}), ...(rid ? { record_id: rid } : {}) });
      }
      continue;
    }
    const rows = new Set(uploaded.filter(u => u.key.startsWith(`${s.key}_`)).map(u => +u.key.slice(s.key.length + 1)));
    for (const name of Object.keys(v)) {
      const hit = name.startsWith(`${s.key}_`) && /_(\d+)$/.exec(name);
      if (hit && s.fields.some(f => name === `${s.key}_${f.name}_${hit[1]}`)) rows.add(+hit[1]);
    }
    for (const key of Object.keys(ids)) if (key.startsWith(`${s.key}_`) && kept(key)) rows.add(+key.slice(s.key.length + 1));
    for (const n of [...rows].sort((a, b) => a - b)) {
      const entry = { section: s.key, row: n };
      for (const f of s.fields) if (v[`${s.key}_${f.name}_${n}`]) entry[f.name] = v[`${s.key}_${f.name}_${n}`];
      const attachments = uploaded.filter(u => u.key === `${s.key}_${n}`).map(u => u.record_id);
      if (attachments.length) entry.attachments = attachments;
      const rid = ids[`${s.key}_${n}`];
      if (rid) entry.record_id = rid;
      if (Object.keys(entry).some(k => !["section", "row", "kind", "record_id"].includes(k)) || (rid && kept(`${s.key}_${n}`))) out.push(entry);
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
if (typeof document !== "undefined" && document.addEventListener) document.addEventListener("change", (e) => {
  const el = e.target;
  if (!el) return;
  if (el.dataset && el.dataset.dailySection) {
    if (el.checked) daily.checked.add(el.dataset.dailySection); else daily.checked.delete(el.dataset.dailySection);
    render();
  } else if (el.dataset && el.dataset.recordFiles) {
    (daily.files[el.dataset.recordFiles] ||= []).push(...(el.files || []));
    render();
  } else if (el.dataset && el.dataset.crewPick) {
    // Someone else opens the name box; a team member closes it.
    (formDraft.daily ||= {})[el.dataset.field] = el.value;
    render();
    if (el.value === OTHER_PERSON) focusAfterDraw(() => document.getElementById(el.id.replace("-pick-", "-name-")));
  } else if (el.id === "daily-job" && currentView === "daily") {
    // Files already up are on the job they went to; they can't come along to another.
    if (daily.uploaded.length) { daily.uploaded = []; toast("The files already uploaded stay on the job they went to."); }
    rememberDailyJob(el.value);
    render();
  }
});

// The time sheet's total follows the hours as they are typed, without a redraw. Hours are numbers only.
if (typeof document !== "undefined" && document.addEventListener) document.addEventListener("input", (e) => {
  const el = e.target;
  if (!el || !el.dataset || !el.dataset.crewHours) return;
  const clean = el.value.replace(/[^0-9.]/g, "");
  if (clean !== el.value) el.value = clean;
  const v = formValues("daily");
  const line = document.getElementById("daily-ts-total");
  if (line) line.textContent = `Total: ${crewTotal(crewRows(v, teamUsers()))} h`;
});

// Take row n off a section before saving (Nov 26): what was typed below it moves up a row.
export function dropRow(kind, n) {
  const d = formDraft.daily || {};
  const s = RECORD_SECTIONS.find(x => x.key === kind);
  const bases = kind === "crew" ? ["ts_pick_", "ts_name_", "ts_hours_"] : kind === "co" ? ["co_", "co_hours_", "co_desc_"] : s ? s.fields.map(f => `${kind}_${f.name}_`) : [];
  const count = daily.rows[kind] || 1;
  for (let m = n; m < count; m++) {
    for (const base of bases) {
      if (m + 1 < count && d[`${base}${m + 1}`] !== undefined) d[`${base}${m}`] = d[`${base}${m + 1}`];
      else delete d[`${base}${m}`];
    }
    const here = `${kind}_${m}`, next = `${kind}_${m + 1}`;
    if (m + 1 < count) {
      daily.files[here] = daily.files[next] || [];
      daily.uploaded.forEach(u => { if (u.key === next) u.key = here; });
      if (daily.editing) {
        if (daily.editing.recordIds[next]) daily.editing.recordIds[here] = daily.editing.recordIds[next]; else delete daily.editing.recordIds[here];
        if (daily.editing.files[next]) daily.editing.files[here] = daily.editing.files[next]; else delete daily.editing.files[here];
      }
    } else {
      delete daily.files[here];
      if (daily.editing) { delete daily.editing.recordIds[here]; delete daily.editing.files[here]; }
    }
  }
  // An upload of the row taken off stays on the job records; it isn't filed with this one.
  daily.uploaded = daily.uploaded.filter(u => u.key !== `${kind}_${count - 1}` || n !== count - 1);
  if (count > 1) daily.rows[kind] = count - 1;
  else if (s) daily.open.delete(kind);
  else if (kind === "co") daily.checked.delete("co_work");
}

// Edit on a saved job record (Nov 26): the form filled in from the daily record and the records
// its rows made, so it opens as it was saved instead of as one line of text.
const NO_HEAD = (label, word) => String(label || "").replace(new RegExp(`^${word}: `), "");
function splitNumber(text) {
  const at = String(text).lastIndexOf(" #");
  return at < 0 ? [text, ""] : [text.slice(0, at), text.slice(at + 2)];
}
export function editPrefill(j, dr) {
  const d = {};
  const checked = new Set(), open = new Set();
  const rows = { co: 1, crew: 1 };
  const recordIds = {}, files = {};
  const raw = (x) => unesc(x == null ? "" : String(x));
  d.notes = raw(dr.notes);
  for (const k of ["weather", "weather_note", "materials", "deliveries"]) if (dr[k]) d[k] = raw(dr[k]);
  const day = raw(dr.at).slice(0, 10);
  d.date = day;
  const lines = Object.fromEntries((((j.sov_view || {}).lines) || []).map(l => [unesc(l.id), l]));
  const sec = dr.sections || {};
  for (const q of sec.quantities || []) {
    const l = lines[unesc(q.line_id)];
    if (!l) continue;
    d[`qty_${l.id}`] = lumpSum(l) ? String(+(((q.to_date_milli ?? q.qty_milli) || 0) / 10).toFixed(1)) : String(q.qty_milli / 1000);
  }
  (sec.co_work || []).forEach((c, n) => { d[`co_${n}`] = raw(c.co_id); d[`co_hours_${n}`] = c.hours_milli ? String(c.hours_milli / 1000) : ""; d[`co_desc_${n}`] = raw(c.description); });
  if ((sec.co_work || []).length) { checked.add("co_work"); rows.co = sec.co_work.length; }
  if ((sec.punch || []).length) { checked.add("punch"); d.punch = sec.punch.map(raw).join("\n"); }
  if (sec.photos && !(j.records || []).some(r => r.daily_record_id === dr.id && r.kind === "photo")) { open.add("photos"); d.photos_note_0 = raw(sec.photos.note); }
  const users = teamUsers();
  const count = {};
  const next = (key) => { const n = count[key] || 0; count[key] = n + 1; return n; };
  const fileNames = (r) => (r.file_ids || []).map(fid => (state.files || []).find(f => f.id === fid)).filter(Boolean).map(f => unesc(f.name));
  const saved = (j.records || []).filter(x => x.daily_record_id === dr.id && x.kind !== "daily");
  for (const r of saved) {
    const kind = unesc(r.kind);
    let key;
    if (kind === "ticket" && unesc(r.ticket_type || "") === "time_sheet") {
      key = "time_sheets";
      const crew = (r.crew || []).length ? r.crew : [{ name: NO_HEAD(raw(r.label), "Time sheet"), hours_milli: r.hours_milli || 0 }];
      crew.forEach((p, n) => {
        const user = p.member_id && users.find(u => unesc(u.id) === unesc(p.member_id));
        if (user) d[`ts_pick_${n}`] = unesc(user.id); else { d[`ts_pick_${n}`] = OTHER_PERSON; d[`ts_name_${n}`] = raw(p.name); }
        d[`ts_hours_${n}`] = p.hours_milli ? String(p.hours_milli / 1000) : "";
      });
      rows.crew = Math.max(1, crew.length);
      recordIds.time_sheets_0 = unesc(r.id);
      if ((r.file_ids || []).length) files.time_sheets_0 = fileNames(r);
      continue;
    }
    if (kind === "ticket") {
      key = "delivery";
      const n = next(key);
      const [supplier, number] = splitNumber(NO_HEAD(raw(r.label), "Delivery ticket"));
      Object.assign(d, { [`delivery_supplier_${n}`]: supplier, [`delivery_number_${n}`]: number, [`delivery_what_${n}`]: raw(r.note) });
      recordIds[`${key}_${n}`] = unesc(r.id); if ((r.file_ids || []).length) files[`${key}_${n}`] = fileNames(r);
    } else if (kind === "photo") {
      key = "photos";
      const n = next(key);
      d[`photos_note_${n}`] = NO_HEAD(raw(r.label), "Progress photo").replace(new RegExp(" [(][0-9]+ of [0-9]+[)]$"), "");
      recordIds[`${key}_${n}`] = unesc(r.id); if ((r.file_ids || []).length) files[`${key}_${n}`] = fileNames(r);
    } else if (kind === "supplier_invoice") {
      key = "invoices";
      const n = next(key);
      const [supplier, number] = splitNumber(NO_HEAD(raw(r.label), "Invoice"));
      Object.assign(d, { [`invoices_supplier_${n}`]: supplier, [`invoices_number_${n}`]: number, [`invoices_amount_${n}`]: r.amount_cents != null ? (r.amount_cents / 100).toFixed(2) : "" });
      recordIds[`${key}_${n}`] = unesc(r.id); if ((r.file_ids || []).length) files[`${key}_${n}`] = fileNames(r);
    } else if (kind === "inspection") {
      key = "inspections";
      const n = next(key);
      Object.assign(d, { [`inspections_who_${n}`]: NO_HEAD(raw(r.label), "Inspection"), [`inspections_outcome_${n}`]: raw(r.note) });
      recordIds[`${key}_${n}`] = unesc(r.id); if ((r.file_ids || []).length) files[`${key}_${n}`] = fileNames(r);
    } else if (kind === "conversation") {
      key = "conversation";
      const n = next(key);
      const said = raw(r.note);
      const again = new RegExp("^(.*?)(?: Check again ([0-9]{4}-[0-9]{2}-[0-9]{2})[.])?$").exec(said) || [said, said, ""];
      Object.assign(d, { [`conversation_text_${n}`]: again[1], [`conversation_next_on_${n}`]: again[2] || "" });
      recordIds[`${key}_${n}`] = unesc(r.id);
    } else {
      key = "other";
      const n = next(key);
      Object.assign(d, { [`other_kind_${n}`]: kind, [`other_label_${n}`]: raw(r.label), [`other_note_${n}`]: raw(r.note) });
      recordIds[`${key}_${n}`] = unesc(r.id); if ((r.file_ids || []).length) files[`${key}_${n}`] = fileNames(r);
    }
    open.add(key);
  }
  for (const [key, n] of Object.entries(count)) rows[key] = Math.max(rows[key] || 1, n);
  return { draft: d, checked, open, rows, editing: { jobId: j.id, dailyId: unesc(dr.id), day, recordIds, files } };
}

// A second tap while a save is going makes nothing more.
let saving = false;

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  // "New job record" on a job page or a row of Open jobs (T9): the form with that job picked.
  "daily-for-job": (el, id) => { if (daily.editing) { resetDaily(); clearForm("daily"); } if (id) rememberDailyJob(id); nav("daily"); },
  // Talked to the client (the Dashboard's follow-up row): New job record with its conversation section open.
  "daily-conversation": (el, id) => {
    if (daily.editing) { resetDaily(); clearForm("daily"); }
    if (id) rememberDailyJob(id);
    daily.open.add("conversation");
    nav("daily");
    focusAfterDraw(() => document.getElementById("daily-conversation-text-0"));
  },
  // A signed change order with no work recorded (the Dashboard, Nov 26): its work section, on that job.
  "daily-co-work": (el, id) => {
    if (daily.editing) { resetDaily(); clearForm("daily"); }
    if (id) rememberDailyJob(id);
    nav("daily");
    daily.checked.add("co_work");
    (formDraft.daily ||= {}).co_0 = el.dataset.co || "";
    render();
    focusAfterDraw(() => document.getElementById("daily-co-desc-0"));
  },
  // Edit on a saved job record: the same form, filled in (Nov 26).
  "daily-edit": (el, id) => {
    const j = state.jobs.find(x => x.id === id);
    const dr = (state.daily_records || []).find(x => x.id === el.dataset.item);
    if (!j || !dr) { toast("That job record isn't here any more. Reload the page."); return; }
    resetDaily();
    const p = editPrefill(j, dr);
    nav("daily");   // a page change clears what forms hold: the form is filled after it
    formDraft.daily = p.draft;
    daily.checked = p.checked; daily.open = p.open; daily.rows = p.rows; daily.editing = p.editing;
    render();
    focusAfterDraw(() => document.getElementById("daily-notes"));
  },
  "daily-edit-cancel": () => { const id = daily.editing && daily.editing.jobId; resetDaily(); clearForm("daily"); if (id) nav("job", id); else render(); },
  "daily-row-remove": (el) => { dropRow(el.dataset.kind, +el.dataset.n); render(); },
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
  "daily-more": (el) => { daily.rows[el.dataset.kind] = Math.min(el.dataset.kind === "crew" ? 50 : 10, (daily.rows[el.dataset.kind] || 1) + 1); render(); },
  "daily-file-remove": (el) => { daily.files[el.dataset.kind].splice(+el.dataset.n, 1); render(); },
  "daily-save": async () => {
    if (saving) return;
    saving = true;
    try { await saveRecord(); } finally { saving = false; }
  },
};

// More than a line has left (Nov 26: 2 sinks on a 1-each line went through): the server says which,
// and the person records it anyway or goes back to fix it.
export function overScheduleText(data) {
  const lines = (data && Array.isArray(data.blockers) ? data.blockers : []).map(String);
  return `${lines.join(" ")} Only what is left is added to the schedule of values; the rest is kept on this record. Record it anyway?`;
}

async function saveRecord() {
    const notes = document.querySelector("#daily-notes")?.value || "";
    const ed = daily.editing;
    const job = ed ? ed.jobId : document.querySelector("#daily-job")?.value;
    if (!job) { toast("Pick a job."); document.querySelector("#daily-job")?.focus(); return; }
    const v = { ...formValues("daily"), notes };
    const users = teamUsers();
    const picked = daily.uploaded.length || Object.values(daily.files).some(list => list.length);
    if (ed && !notes.trim()) { refuseForm("daily", { notes: "Enter the diary entry." }); return; }
    if (!notes.trim() && !picked && !recordEntries(v, [], ed, users).length) { toast("Write the diary entry, or add a document."); refuseForm("daily", { notes: "Write the diary entry, or add a document." }); return; }
    // Remembered before saving: the save redraws the page, which picks the job last used.
    rememberDailyJob(job);
    if (!(await uploadDailyFiles(job, v))) { render(); return; }
    const j = state.jobs.find(x => x.id === job);
    const body = dailyBody(v, j, daily.checked);
    const entries = recordEntries(v, daily.uploaded, ed, users);
    if (entries.length || ed) body.entries = entries;
    if (v.date) body.date = v.date;
    body.today = localDay(new Date());   // the record's day is the person's own date, not the server's UTC one
    const path = ed ? `/api/jobs/${job}/daily-record-edit/${encodeURIComponent(ed.dailyId)}` : `/api/jobs/${job}/daily-record`;
    let r = await post(path, body, { formKey: "daily", quiet: true });
    if (r && !r.ok && r.data && r.data.over_schedule) {
      if (!(await askConfirm(overScheduleText(r.data), "Record anyway"))) return;
      r = await post(path, { ...body, over_ok: true }, { formKey: "daily", quiet: true });
    }
    // A file that can't be filed comes off the form, so the next save doesn't meet it again.
    if (r && r.data && r.data.stale_record_ids) { daily.uploaded = daily.uploaded.filter(u => !r.data.stale_record_ids.includes(u.record_id)); render(); }
    if (r && r.ok && ed) {
      resetDaily(); clearForm("daily");
      toast("Job record changed.");
      nav("job", job);
      return;
    }
    if (r && r.ok) {
      resetDaily(); clearForm("daily");   // saved: the form is empty for the next one, on the same job
      const words = savedWords(notes.trim(), entries);
      const name = j ? j.name : "";
      const last = (state.daily_records || []).filter(x => x.job_id === job).slice(-1)[0];
      const blocked = notes.trim() && last && last.quantities_not_applied;
      const sent = (body.sections || {}).quantities || [];
      const added = sent.length && !blocked ? (sent.every(q => q.to_date) ? " The job's % done to date is updated." : " Quantities added to the job's totals for the period.") : "";
      daily.saved = { jobId: job, jobName: name, words, added };
      render();
      // The record is kept even when its quantities couldn't be added (B5): say so plainly.
      const stayed = (r.data.not_added || []).length;
      if (blocked) toast(`Saved. The quantities were not added to the schedule of values: ${unesc(last.quantities_not_applied)}`);
      else if (stayed) toast(`Job record saved to ${unesc(name)}. ${stayed === 1 ? "One file" : `${stayed} files`} stayed on the job records as they were: someone else's, or filed already.`);
      else toast(`Job record saved to ${unesc(name)}.${added}`);
    }
}
