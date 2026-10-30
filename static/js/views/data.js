// Import/Export: every manual import and export of data, one tab each (I1). Import gathers the
// file box, the paste box, the QuickBooks payments import and a way to a job's schedule paste;
// Export gathers the company file, each sent pay app's PDF and QuickBooks file, the calendar feed
// and the logs. Emails and texts that come in by themselves are on Communications.
import { money, fmtDate, words } from "../format.js";
import { pathFor } from "../routes.js";
import { jobFilterOptions } from "../jobs.js";
import { moreButton } from "../dom.js";
import { state, me, onSessionEnd } from "../state.js";
import { toast, nav, emptyState, listPage, render, can } from "../app-core.js";
import { dropzoneHtml, pasteBoxHtml, calendarFeedHtml } from "./inbox.js";
import { paymentsImportHtml, qboCardHtml, qboStatus, loadQboStatus } from "./payments.js";
import { qboCsvLink, QBO_READY } from "./money.js";

// Export's Sent pay apps: one job's, or every job's.
let sentJobFilter = "all";
export function setSentJobFilter(value) { sentJobFilter = value || "all"; }
document.addEventListener("change", (e) => {
  const s = e.target && e.target.closest ? e.target.closest("select[data-sent-filter]") : null;
  if (!s) return;
  setSentJobFilter(s.value);
  render();
});

// Signed out: a job picked in the filter is that company's.
onSessionEnd(() => { sentJobFilter = "all"; });

function sovPickerHtml() {
  const open = state.jobs.filter(j => !j.closed);
  if (!open.length) return `<p class="sub">Add a job first; its schedule of values is pasted on the job page.</p>`;
  return `<p class="sub">Paste the lines from your estimate, a spreadsheet or a PDF onto a job's schedule. Set it before the job's first pay app.</p>
      <div class="form-inline">
        <label for="sov-import-job" class="inline-label">Job</label>
        <select id="sov-import-job">${open.map(j => `<option value="${j.id}">${j.name} (${j.code})</option>`).join("")}</select>
        <button class="btn small" data-action="import-sov">Open its schedule</button>
      </div>`;
}

// The PDF and the QuickBooks file of one sent pay app.
export function sentPayAppRow(r) {
  return `<tr><td><a href="${pathFor("payapp-detail", r.job_id, r.invoice_id)}" data-action="open-payapp" data-job="${r.job_id}" data-inv="${r.invoice_id}">${r.number}</a></td>
      <td>${r.job}</td><td>${fmtDate(r.sent_at)}</td><td class="num">${money(r.amount_cents)}</td><td>${words(r.status)}</td>
      <td class="row-actions"><a class="btn secondary small" href="/api/jobs/${r.job_id}/payapp/${r.invoice_id}.pdf" target="_blank" rel="noopener">PDF</a>${QBO_READY.has(r.status) ? qboCsvLink(r.job_id, r.invoice_id) : ""}</td></tr>`;
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  import() {
    if (!qboStatus) loadQboStatus();
    return `
      <h1>Import</h1>
      <div class="card">
        <h2>Files and photos</h2>
        ${dropzoneHtml()}
      </div>
      <div class="card">
        <h2>Paste an email, a text thread, or notes</h2>
        ${pasteBoxHtml()}
      </div>
      ${qboCardHtml(true)}
      ${paymentsImportHtml()}
      <div class="card" id="sov-import">
        <h2>Schedule of values</h2>
        ${sovPickerHtml()}
      </div>
      <p class="sub">Emails and texts forwarded to Duracash arrive in the <a href="/communications" data-action="nav" data-view="inbox">Inbox</a>.</p>
    `;
  },
  export() {
    const owner = can("data");   // GET /api/export needs Export and reset data
    const page = listPage("sentpayapps", sentJobFilter);
    const rows = page ? page.entries : [];
    return `
      <h1>Export</h1>
      <div class="card" id="export-everything">
        <h2>Everything (one file)</h2>
        ${owner ? `<p class="sub">Every job, client, record and email in your company, as one file.</p>
        <a class="btn secondary" href="/api/export" download>Download everything</a>`
        : `<p class="sub">An owner can download everything in the company as one file.</p>`}
      </div>
      <div class="card" id="export-payapps">
        <h2>Sent pay apps</h2>
        <div class="filter-bar">
          <label for="sent-filter" class="inline-label">Job</label>
          <select id="sent-filter" data-sent-filter="1">
            <option value="all">All jobs</option>
            ${jobFilterOptions(state.jobs, sentJobFilter)}
          </select>
        </div>
        ${rows.length ? `<div class="table-wrap"><table>
          <tr><th>Pay app</th><th>Job</th><th>Sent</th><th>Amount</th><th>Status</th><th>Download</th></tr>
          ${rows.map(sentPayAppRow).join("")}
        </table></div>${moreButton("sentpayapps", page)}`
        : !page ? `<p class="sub">Loading…</p>` : emptyState("No pay apps sent yet")}
      </div>
      <div class="card" id="export-calendar">
        <h2>Calendar feed</h2>
        <p class="sub">Every job's due dates and follow-ups, for Google Calendar, Apple or Outlook. Anyone with this address can see the dates and job names.</p>
        ${calendarFeedHtml()}
      </div>
      <div class="card">
        <h2>Logs</h2>
        <p><a href="/communications/outbox" data-action="nav" data-view="outbox">Outbox</a>: every email sent, with its attachments, to read or print.</p>
        <p><a href="/settings/audit" data-action="nav" data-view="audit">Activity log</a>: every change, who made it and when.</p>
      </div>
    `;
  },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  // The job's schedule card, opened for pasting: Set up, or Edit when it has one.
  "import-sov": () => {
    const id = document.getElementById("sov-import-job")?.value || "";
    if (!id) { toast("Pick a job first."); return; }
    nav("job", id);
    const card = document.getElementById("sov-card");
    if (!card) return;
    card.querySelector('[data-action="sov-start"]')?.click();
    (document.getElementById("sov-paste") || card).scrollIntoView({ block: "center" });
    document.getElementById("sov-paste")?.focus();
  },
};
