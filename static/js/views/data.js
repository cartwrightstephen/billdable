// Import/Export: every manual import and export of data, one tab each (I1). Import gathers the
// file box, the paste box, the QuickBooks payments import, the quantities done from the
// contractor's own records (M1) and a way to a job's schedule paste; Export gathers the company
// file, the CSV lists (jobs, pay apps, payments, change orders), each sent pay app's PDF and
// QuickBooks file, the calendar feed and the logs. Emails and texts that come in by themselves
// are on Communications.
import { money, fmtDate, words, esc, escDeep, unesc } from "../format.js";
import { pathFor } from "../routes.js";
import { jobFilterOptions, openJobs } from "../jobs.js";
import { moreButton } from "../dom.js";
import { field, formValues, refuseForm, draftValue, clearForm } from "../forms.js";
import { postJson } from "../net.js";
import { termText } from "../terms.js";
import { state, me, onSessionEnd } from "../state.js";
import { toast, nav, emptyState, listPage, render, can, may, setState } from "../app-core.js";
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

// Quantities from a file (M1): what was sent ({job_id, name, csv | xlsx}), the server's preview
// of it (escaped), and what the last import added and couldn't.
let qtySent = null;
let qtyPreview = null;
let qtyResult = null;

// Signed out: a job picked in the filter, and a file being imported, are that company's.
onSessionEnd(() => { sentJobFilter = "all"; qtySent = null; qtyPreview = null; qtyResult = null; });

// The open jobs whose schedule of values quantities can go on.
export function qtyJobs(jobs) {
  return openJobs(jobs).filter(j => j.sov_view && j.sov_view.present);
}

// Import's Quantities done card (M1): a job, then a CSV or Excel file (or rows pasted), a preview
// row by row, and Add to the schedule of values for the rows that can go on.
export function qtyImportHtml() {
  const jobs = qtyJobs(state.jobs);
  if (!jobs.length) return emptyState("No job has a schedule of values", "", `<button class="btn" data-action="nav" data-view="jobs">Go to Jobs</button>`);
  if (!may("sov-progress")) return `<p class="sub">Your profile doesn't include recording progress, so someone who has it imports these.</p>`;
  const done = qtyResult ? `<div class="qty-result">
      <p><strong>${qtyResult.added.length} line${qtyResult.added.length === 1 ? "" : "s"} added${qtyResult.number ? ` to ${qtyResult.number}` : ""}.</strong></p>
      ${qtyResult.added.map(a => `<div class="list-item">${a.line}: +${a.add} (now ${a.new_total})</div>`).join("")}
      ${qtyResult.not_added.length ? `<h3>Not added</h3>${qtyResult.not_added.map(r => `<div class="list-item">Row ${r.n} (${r.what}): ${r.problem}</div>`).join("")}` : ""}
      ${qtyResult.job_id ? `<div class="form-inline"><button class="btn small secondary" data-action="open-job" data-id="${qtyResult.job_id}">Open the job</button></div>` : ""}
    </div>` : "";
  if (qtyPreview) {
    const p = qtyPreview;
    const job = jobs.find(j => j.id === p.job_id);
    return `${p.warnings.length ? `<div class="sov-warn">${p.warnings.map(w => `<div>${w}</div>`).join("")}</div>` : ""}
      <p><strong>${job ? job.name : ""}</strong>${qtySent && qtySent.name ? ` · ${esc(qtySent.name)}` : ""}</p>
      ${p.blocked ? `<p class="form-error" role="alert">${p.blocked}</p>` : ""}
      ${p.rows.length ? `<div class="table-wrap"><table class="qty-preview">
        <tr><th>Row</th><th>Line</th><th>Done to date</th><th>Add</th><th>New total</th><th>Scheduled</th><th>Can't add because</th></tr>
        ${p.rows.map(r => `<tr${r.problem ? ` class="qty-problem"` : ""}><td>${r.n}</td><td>${r.line || [r.item, r.description].filter(Boolean).join(" ")}</td>
          <td>${r.done || "—"}</td><td>${r.add || r.qty}</td><td>${r.new_total || "—"}</td><td>${r.scheduled || "—"}</td><td>${r.problem || "—"}</td></tr>`).join("")}
      </table></div>` : emptyState("No rows with a quantity")}
      <div class="form-inline">${p.can_add ? `<button class="btn" data-action="qty-apply">Add ${p.can_add} to the schedule of values</button>` : ""}<button class="btn secondary" data-action="qty-cancel">Cancel</button></div>`;
  }
  return `${done}<div data-form-key="qtyimport">
      <div class="form-row">${field({ form: "qtyimport", name: "job", id: "qty-import-job", label: "Job", value: jobs.length === 1 ? unesc(jobs[0].id) : "",
        options: [["", "Pick a job…"], ...jobs.map(j => [unesc(j.id), `${unesc(j.name)} (${unesc(j.code)})`])] })}</div>
      <textarea id="qty-csv" data-field="csv" rows="4" class="full-input" placeholder="Item or description, and the quantity this period (a lump sum as % done)" aria-label="Quantities this period">${esc(draftValue("qtyimport", "csv"))}</textarea>
    </div>
    <div class="form-inline"><button class="btn small" data-action="qty-read">Read quantities</button><button class="btn small secondary" data-action="qty-choose-file">Choose the file</button></div>`;
}

// Send the sheet for a preview: nothing changes on the server until Add is tapped.
async function qtyRead(body) {
  const r = await postJson("/api/import/quantities/preview", body);
  if (!r.ok) { toast(r.data.error || "Couldn't read it."); return; }
  qtySent = body;
  qtyPreview = escDeep(r.data);
  qtyResult = null;
  render();
}

// The job picked on the card, refused under the box when none is.
function qtyJobPicked() {
  const v = formValues("qtyimport");
  if (refuseForm("qtyimport", v.job ? {} : { job: "Pick a job." })) return null;
  return v;
}

const MAX_QTY_XLSX = 700 * 1024;   // base64 of it has to fit in a 1 MB request, as on Payments

function sovPickerHtml() {
  const open = state.jobs.filter(j => !j.closed);
  if (!open.length) return `<p class="sub">Add a job first; its schedule of values is pasted on the job page.</p>`;
  // Only a job whose schedule can still be set (no pay app yet, work not done): picking another
  // would open a schedule that can't change (D4, sov_view.editable from domain._sov_view).
  const ready = open.filter(j => j.sov_view && j.sov_view.editable);
  if (!ready.length) return `<p class="sub">Every open job has a pay app out or its work done, so its schedule of values is set.</p>`;
  return `<p class="sub">Paste the lines from your estimate, a spreadsheet or a PDF onto a job's schedule. Set it before the job's first pay app.</p>
      <div class="form-inline">
        <label for="sov-import-job" class="inline-label">Job</label>
        <select id="sov-import-job">${ready.map(j => `<option value="${j.id}">${j.name} (${j.code})</option>`).join("")}</select>
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
      <div class="card" id="qty-import">
        <h2>Quantities done</h2>
        ${qtyImportHtml()}
      </div>
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
      <div class="card" id="export-lists">
        <h2>Lists for a spreadsheet (CSV)</h2>
        <p class="sub">Finished jobs included.</p>
        <div class="form-inline export-lists">
          <a class="btn secondary" href="/api/export/jobs.csv" download>Jobs</a>
          <a class="btn secondary" href="/api/export/payapps.csv" download>${termText("Pay apps")}</a>
          <a class="btn secondary" href="/api/export/payments.csv" download>Payments</a>
          <a class="btn secondary" href="/api/export/change-orders.csv" download>Change orders</a>
        </div>
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
  "qty-read": () => {
    const v = qtyJobPicked();
    if (!v) return null;
    if (!(v.csv || "").trim()) { toast("Paste the rows first, or choose the file."); return null; }
    return qtyRead({ job_id: v.job, csv: v.csv, name: "" });
  },
  "qty-choose-file": () => {
    const v = qtyJobPicked();
    if (!v) return;
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".csv,.xlsx,.txt";
    input.addEventListener("change", async () => {
      const file = input.files[0];
      if (!file) return;
      const xlsx = /\.xlsx$/i.test(file.name);
      if (xlsx && file.size > MAX_QTY_XLSX) { toast("That Excel file is too large. Save it as CSV and try again."); return; }
      let body;
      if (xlsx) {
        const bytes = new Uint8Array(await file.arrayBuffer());
        let bin = "";
        for (let k = 0; k < bytes.length; k += 0x8000) bin += String.fromCharCode(...bytes.subarray(k, k + 0x8000));
        body = { job_id: v.job, xlsx: btoa(bin), name: file.name };
      } else {
        body = { job_id: v.job, csv: await file.text(), name: file.name };
      }
      await qtyRead(body);
    });
    input.click();
  },
  // The same sheet goes again: the server reads it afresh and adds only what can be added.
  "qty-apply": async () => {
    if (!qtySent) return;
    const r = await postJson("/api/import/quantities/apply", { ...qtySent, basis: unesc(qtyPreview.basis || "") });
    if (!r.ok) { toast(r.data.error || "Nothing was added."); return; }
    const result = escDeep(r.data.quantity_import || { added: [], not_added: [], number: "" });
    setState(r.data);
    qtyResult = { ...result, job_id: escDeep(qtySent.job_id) };
    qtySent = null;
    qtyPreview = null;
    clearForm("qtyimport");
    toast(result.not_added.length ? `Added ${result.added.length}; ${result.not_added.length} not added.` : `Added ${result.added.length} to the schedule of values.`);
    render();
  },
  "qty-cancel": () => { qtySent = null; qtyPreview = null; render(); },
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
