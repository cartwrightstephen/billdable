// Jobs, Daily record: what happened on site, saved on the job record.
import { esc, unesc, fmtDate } from "../format.js";
import { defaultJobId } from "../jobs.js";
import { pathFor } from "../routes.js";
import { termText } from "../terms.js";
import { draftValue, clearForm } from "../forms.js";
import { state } from "../state.js";
import { post, toast, render, emptyState, nav } from "../app-core.js";

// The Daily record starts on the job last used on this device (R2-07).
const DAILY_JOB_KEY = "duracash-daily-job";
function lastDailyJob() {
  try { return localStorage.getItem(DAILY_JOB_KEY) || ""; } catch (_) { return ""; }
}
export function rememberDailyJob(id) {
  try { localStorage.setItem(DAILY_JOB_KEY, unesc(id)); } catch (_) { /* private window: the only open job still comes first */ }
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  daily() {
    const openJobs = state.jobs.filter(j => !j.closed);
    const pick = defaultJobId(openJobs, esc(lastDailyJob()));
    const records = state.daily_records || [];
    return `
      <h1>Daily record</h1>
      <div class="card" data-form-key="daily">
        <h2>New record</h2>
        ${openJobs.length ? `<div class="form-grid">
          <div><label>Job</label><select id="daily-job"><option value="">Pick a job…</option>${openJobs.map(j => `<option value="${j.id}"${j.id === pick ? " selected" : ""}>${j.name}</option>`).join("")}</select></div>
          <div class="span-2"><label>Diary</label><textarea id="daily-notes" data-field="notes" rows="3" placeholder="Framing done on the east wall, 4 crew, inspector booked for Thursday">${esc(draftValue("daily", "notes"))}</textarea></div>
        </div>
        <button class="btn" data-action="daily-save">Save to job record</button>` : emptyState("No open jobs", "", `<button class="btn" data-action="onb-newjob">New job</button>`)}
      </div>
      <div class="card">
        <h2>Recent records</h2>
        ${records.length ? records.slice().reverse().map(r => {
          const j = state.jobs.find(x => x.id === r.job_id);
          return `<div class="list-item"><strong>${fmtDate(r.at)}</strong> · ${j ? `<a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a>` : "Deleted job"}<div>${r.notes}</div></div>`;
        }).join("") : `<p class="sub">None yet.</p>`}
      </div>
    `;
  },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  // "New daily record" on a job page (T9): the Daily record with that job picked.
  "daily-for-job": (el, id) => { rememberDailyJob(id); nav("daily"); },
  "daily-save": async () => {
    const notes = document.querySelector("#daily-notes")?.value || "";
    const job = document.querySelector("#daily-job")?.value;
    if (!job) { toast("Pick a job."); document.querySelector("#daily-job")?.focus(); return; }
    if (!notes.trim()) { toast("Write the diary entry."); document.querySelector("#daily-notes")?.focus(); return; }
    // Remembered before saving: the save redraws the page, which picks the job last used.
    rememberDailyJob(job);
    const r = await post(`/api/jobs/${job}/daily-record`, { notes });
    if (r && r.ok) { clearForm("daily"); render(); }   // saved: the box is empty for the next one
  },
};
