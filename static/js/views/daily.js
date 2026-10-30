// Jobs, Daily record: what happened on site, saved on the job record. The Diary, crew, weather,
// materials and deliveries (T12); boxes for typical items, each opening its own fields, and files
// (T11); quantities put in place today, which add to the schedule of values and the draft pay
// app (M1). The server checks every field (domain/daily.py).
import { esc, unesc, fmtDate } from "../format.js";
import { defaultJobId } from "../jobs.js";
import { fmtQty } from "../sov.js";
import { pathFor } from "../routes.js";
import { readJson, setNetBanner } from "../net.js";
import { draftValue, clearForm, field, formValues, formErrors } from "../forms.js";
import { state, onSessionEnd, currentView } from "../state.js";
import { post, toast, render, emptyState, nav, setState } from "../app-core.js";

// The Daily record starts on the job last used on this device (R2-07).
const DAILY_JOB_KEY = "duracash-daily-job";
function lastDailyJob() {
  try { return localStorage.getItem(DAILY_JOB_KEY) || ""; } catch (_) { return ""; }
}
export function rememberDailyJob(id) {
  try { localStorage.setItem(DAILY_JOB_KEY, unesc(id)); } catch (_) { /* private window: the only open job still comes first */ }
}

export const WEATHER = ["Clear", "Cloudy", "Rain", "Snow", "Wind", "Heat", "Cold"];   // domain.daily.WEATHER
export const SECTIONS = [["photos", "Progress photos"], ["quantities", "Quantities"], ["co_work", "Change order work"],
  ["inspections", "Inspections or major discussions"], ["punch", "New punchlist items"]];   // domain.daily.DAILY_SECTIONS
const MAX_UPLOAD_MB = 15;

// What the form holds beyond its typed boxes (those are kept by forms.js): the ticked boxes, how
// many change order and inspection rows, and the files picked. A file picker can't be refilled
// after a redraw, so the files are kept here and listed; once uploaded, a file is a record on the
// job and only its record id is kept, so a second Save doesn't upload it again.
export const daily = { checked: new Set(), rows: { co: 1, insp: 1 }, files: { photos: [], files: [] }, uploaded: [] };
function resetDaily() {
  daily.checked = new Set();
  daily.rows = { co: 1, insp: 1 };
  daily.files = { photos: [], files: [] };
  daily.uploaded = [];
}
onSessionEnd(resetDaily);

const typed = (name, fallback = "") => esc(draftValue("daily", name, fallback));
const errorOf = (name) => esc((formErrors.daily || {})[name] || "");
const errSpan = (id, name) => `<span class="field-error" id="${id}-err">${errorOf(name)}</span>`;
const lumpSum = (l) => l.lump_sum || (String(l.unit).toUpperCase() === "LS" && l.qty_milli === 1000);

// The chosen files of one picker, each with a Remove button.
function fileList(kind) {
  const list = daily.files[kind];
  const done = daily.uploaded.filter(u => u.kind === kind);
  if (!list.length && !done.length) return "";
  return `<ul class="daily-files">${done.map(u => `<li>✓ ${esc(u.name)}</li>`).join("")}${list.map((f, n) => `<li>${esc(f.name)}
    <button class="linkish" data-action="daily-file-remove" data-kind="${kind}" data-n="${n}" aria-label="Remove ${esc(f.name)}">Remove</button></li>`).join("")}</ul>`;
}

function sectionHtml(key, j) {
  if (key === "photos") return `<div class="daily-section"><h3>Progress photos</h3>
      <input type="file" id="daily-photos" accept="image/*" capture="environment" multiple aria-label="Add progress photos">
      ${fileList("photos")}
      <div class="form-grid">${field({ form: "daily", name: "photos_note", id: "daily-photos-note", label: "What they show (optional)", wide: true })}</div>
    </div>`;
  if (key === "quantities") {
    const lines = ((j.sov_view && j.sov_view.lines) || []).filter(l => l.done_milli < l.qty_milli);
    return `<div class="daily-section"><h3>Quantities put in place today</h3>
      <p class="sub">They add to each line's quantity done and update this period's draft pay app.</p>
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
  if (key === "inspections") {
    const rows = Array.from({ length: daily.rows.insp }, (_, n) => `<div class="form-grid">
        ${field({ form: "daily", name: `insp_type_${n}`, id: `daily-insp-type-${n}`, label: "Kind", options: [["inspection", "Inspection"], ["discussion", "Discussion"]] })}
        ${field({ form: "daily", name: `insp_who_${n}`, id: `daily-insp-who-${n}`, label: "With whom", placeholder: "City electrical inspector" })}
        ${field({ form: "daily", name: `insp_outcome_${n}`, id: `daily-insp-outcome-${n}`, label: "Outcome", wide: true, placeholder: "Passed rough-in" })}
      </div>`).join("");
    return `<div class="daily-section"><h3>Inspections or major discussions</h3>${rows}
      <button class="btn small secondary" data-action="daily-more" data-kind="insp">Add another</button></div>`;
  }
  if (key === "punch") return `<div class="daily-section"><h3>New punchlist items</h3>
      <div class="form-grid">${field({ form: "daily", name: "punch", id: "daily-punch", label: "One item per line", multiline: true, wide: true })}</div></div>`;
  return "";
}

// A saved record's extra lines in Recent records.
function recordMeta(r) {
  const out = [];
  if (r.crew_count !== undefined || (r.crew_names || []).length) out.push(`Crew: ${r.crew_count ?? ""}${(r.crew_names || []).length ? ` (${r.crew_names.join(", ")})` : ""}`);
  if (r.weather || r.weather_note) out.push(`Weather: ${[r.weather, r.weather_note].filter(Boolean).join(", ")}`);
  if (r.materials) out.push(`Materials: ${r.materials}`);
  if (r.deliveries) out.push(`Deliveries: ${r.deliveries}`);
  const s = r.sections || {};
  for (const [key, label] of SECTIONS) if (s[key] !== undefined) out.push(label + (key === "punch" ? `: ${s.punch.join("; ")}` : ""));
  if ((r.attachments || []).length) out.push(`${r.attachments.length} file${r.attachments.length === 1 ? "" : "s"} attached`);
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
    const box = (key, label) => key === "quantities" && !hasSov ? ""
      : `<label><input type="checkbox" data-daily-section="${key}"${daily.checked.has(key) ? " checked" : ""}> ${label}</label>`;
    return `
      <h1>Daily record</h1>
      <div class="card" data-form-key="daily">
        <h2>New record</h2>
        ${openJobs.length ? `<div class="form-grid">
          <div><label>Job</label><select id="daily-job"><option value="">Pick a job…</option>${openJobs.map(o => `<option value="${o.id}"${o.id === pick ? " selected" : ""}>${o.name}</option>`).join("")}</select></div>
          ${field({ form: "daily", name: "crew_count", id: "daily-crew", label: "Crew members", type: "number", inputmode: "numeric", attrs: 'min="0" max="500"' })}
          ${field({ form: "daily", name: "crew_names", id: "daily-crew-names", label: "Names (optional)", placeholder: "Ana, Ben, Cruz" })}
          ${field({ form: "daily", name: "weather", id: "daily-weather", label: "Weather", options: [["", "Not noted"], ...WEATHER.map(w => [w, w])] })}
          ${field({ form: "daily", name: "weather_note", id: "daily-weather-note", label: "Weather note (optional)", placeholder: "Rain until 10" })}
          ${field({ form: "daily", name: "materials", id: "daily-materials", label: "Materials used or installed", multiline: true, wide: true })}
          ${field({ form: "daily", name: "deliveries", id: "daily-deliveries", label: "Deliveries: what arrived, from whom", multiline: true, wide: true })}
          <div class="span-2"><label>Diary</label><textarea id="daily-notes" data-field="notes" rows="3" placeholder="Framing done on the east wall, inspector booked for Thursday">${esc(draftValue("daily", "notes"))}</textarea><span class="field-error" id="daily-notes-err">${errorOf("notes")}</span></div>
        </div>
        <div class="daily-checks" role="group" aria-label="Also on this record">${SECTIONS.map(([k, label]) => box(k, label)).join("")}</div>
        ${j ? SECTIONS.filter(([k]) => daily.checked.has(k) && (k !== "quantities" || hasSov)).map(([k]) => sectionHtml(k, j)).join("") : ""}
        <div class="daily-section"><h3>Attach files</h3>
          <input type="file" id="daily-files" multiple aria-label="Attach files: photos, PDFs, documents">
          ${fileList("files")}
        </div>
        <button class="btn" data-action="daily-save">Save to job record</button>` : emptyState("No open jobs", "", `<button class="btn" data-action="onb-newjob">New job</button>`)}
      </div>
      <div class="card">
        <h2>Recent records</h2>
        ${records.length ? records.slice().reverse().map(r => {
          const rj = state.jobs.find(x => x.id === r.job_id);
          return `<div class="list-item"><strong>${fmtDate(r.at)}</strong> · ${rj ? `<a href="${pathFor("job", rj.id)}" data-action="open-job" data-id="${rj.id}">${rj.name}</a>` : "Deleted job"}<div>${r.notes}</div>${recordMeta(r)}</div>`;
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
  if (checked.has("photos")) sections.photos = { note: v.photos_note || "" };
  if (checked.has("quantities") && j && j.sov_view && j.sov_view.present) {
    sections.quantities = (j.sov_view.lines || []).filter(l => (v[`qty_${l.id}`] || "").trim()).map(l => ({ line_id: unesc(l.id), qty: v[`qty_${l.id}`].trim() }));
  }
  if (checked.has("co_work")) sections.co_work = Object.keys(v).filter(k => /^co_desc_\d+$/.test(k)).map(k => k.slice(8))
    .map(n => ({ co_id: v[`co_${n}`] || "", description: v[`co_desc_${n}`] || "", hours: v[`co_hours_${n}`] || "" }));
  if (checked.has("inspections")) sections.inspections = Object.keys(v).filter(k => /^insp_who_\d+$/.test(k)).map(k => k.slice(9))
    .map(n => ({ type: v[`insp_type_${n}`] || "inspection", who: v[`insp_who_${n}`] || "", outcome: v[`insp_outcome_${n}`] || "" }));
  if (checked.has("punch")) sections.punch = v.punch || "";
  if (Object.keys(sections).length) body.sections = sections;
  return body;
}

// Upload the picked files to the job record one at a time (the importer's ?job=&file=1&kind=),
// keeping each new record's id. Returns false if one didn't go up (the rest stay picked).
async function uploadDailyFiles(jobId) {
  for (const kind of ["photos", "files"]) {
    while (daily.files[kind].length) {
      const f = daily.files[kind][0];
      if (f.size > MAX_UPLOAD_MB * 1024 * 1024) { toast(`${f.name}: larger than ${MAX_UPLOAD_MB} MB.`); return false; }
      const before = new Set(((state.jobs.find(x => x.id === jobId) || {}).records || []).map(r => unesc(r.id)));
      let res, data;
      try {
        res = await fetch(`/api/import/file?job=${encodeURIComponent(unesc(jobId))}&file=1${kind === "photos" ? "&kind=photo" : ""}`, {
          method: "POST", headers: { "Content-Type": f.type || "application/octet-stream", "X-Filename": encodeURIComponent(f.name || "upload") }, body: f,
        });
        data = await readJson(res);
      } catch (_) { setNetBanner(true); toast(`${f.name}: can't reach Duracash.`); return false; }
      if (!res.ok) { toast(data.error || `${f.name}: upload failed.`); return false; }
      const job = (data.jobs || []).find(x => x.id === unesc(jobId));
      const added = ((job && job.records) || []).filter(r => !before.has(r.id) && (r.file_ids || []).length).map(r => r.id);
      added.forEach(id => daily.uploaded.push({ kind, name: f.name, record_id: id }));
      daily.files[kind].shift();
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
  } else if (el.id === "daily-photos" || el.id === "daily-files") {
    daily.files[el.id === "daily-photos" ? "photos" : "files"].push(...(el.files || []));
    render();
  } else if (el.id === "daily-job" && currentView === "daily") {
    rememberDailyJob(el.value);
    render();
  }
});

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  // "New daily record" on a job page (T9): the Daily record with that job picked.
  "daily-for-job": (el, id) => { rememberDailyJob(id); nav("daily"); },
  "daily-more": (el) => { daily.rows[el.dataset.kind] = Math.min(10, (daily.rows[el.dataset.kind] || 1) + 1); render(); },
  "daily-file-remove": (el) => { daily.files[el.dataset.kind].splice(+el.dataset.n, 1); render(); },
  "daily-save": async () => {
    const notes = document.querySelector("#daily-notes")?.value || "";
    const job = document.querySelector("#daily-job")?.value;
    if (!job) { toast("Pick a job."); document.querySelector("#daily-job")?.focus(); return; }
    if (!notes.trim()) { toast("Write the diary entry."); document.querySelector("#daily-notes")?.focus(); return; }
    // Remembered before saving: the save redraws the page, which picks the job last used.
    rememberDailyJob(job);
    if (!(await uploadDailyFiles(job))) { render(); return; }
    const j = state.jobs.find(x => x.id === job);
    const body = dailyBody({ ...formValues("daily"), notes }, j, daily.checked);
    if (daily.uploaded.length) body.attachments = daily.uploaded.map(u => u.record_id);
    const r = await post(`/api/jobs/${job}/daily-record`, body, { formKey: "daily" });
    if (r && r.ok) { resetDaily(); clearForm("daily"); render(); }   // saved: the form is empty for the next one
  },
};
