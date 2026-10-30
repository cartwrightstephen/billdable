// The Inbox, and bringing things in: uploads (choose, drop or paste a file anywhere), pasted
// text, and the forwarding addresses for email, texts and the calendar. The Inbox page shows
// what is waiting and everything received (C1); the file and paste boxes are on Import (data.js).
import { esc, unesc, escDeep, money, fmtDate } from "../format.js";
import { RECORD_KINDS, SOURCE_LABELS, defaultJobId, jobFilterOptions } from "../jobs.js";
import { readJson, setNetBanner, inBatches } from "../net.js";
import { askConfirm } from "../dom.js";
import { webcalUrl } from "../calendar.js";
import { jobsInOrder, choiceJobs, choiceLine, pasteGuess } from "../inbox.js";
import { termText } from "../terms.js";
import { draftValue, clearForm } from "../forms.js";
import { state, me, currentView, currentJobId, clearSession, onSessionEnd } from "../state.js";
import { load, toast, renderLanding, render, clearJobDetails, post, setState, DEMO_COMPANY_ID, emptyState, nav, listPage, can } from "../app-core.js";
import { moreButton } from "../dom.js";
import { printSheet } from "./communications.js";

// Files added on Import go with the job last picked under "Which job?" on this device (R5-07).
// "" is "Not sure, file later". Kept in memory too, for a private window.
const UPLOAD_JOB_KEY = "duracash-upload-job";
let uploadJob = null;
// null until a pick is made on this device; "" after "Not sure, file later" was picked.
function lastUploadJob() {
  if (uploadJob !== null) return uploadJob;
  try { return localStorage.getItem(UPLOAD_JOB_KEY); } catch (_) { return null; }
}
export function rememberUploadJob(id) {
  uploadJob = unesc(id);
  try { localStorage.setItem(UPLOAD_JOB_KEY, uploadJob); } catch (_) { /* private window: remembered until reload */ }
}
const FILE_ACCEPT = document.getElementById("file-input")?.getAttribute("accept") || "";
let importSettings = null;
// The addresses changed on the server (a team member removed): ask for them again when shown.
export function forgetImportSettings() { importSettings = null; }

export function newInboxCount() {
  return (state && state.inbox ? state.inbox.filter(i => i.status === "new").length : 0);
}

const MAX_UPLOAD_MB = 15;
const UPLOADS_AT_ONCE = 3;

// extra.attach: a paperwork item the upload is attached to; extra.co: a change order the upload
// is the signed copy of (R3-05, R3-06). Both need fileNow.
async function uploadFiles(fileList, jobId = "", fileNow = false, kind = "", extra = {}) {
  const files = [...(fileList || [])];
  if (!files.length) return;
  let ok = 0, signedOut = false;
  const failed = [];
  const one = async (f) => {
    if (signedOut) return;
    if (f.size > MAX_UPLOAD_MB * 1024 * 1024) { failed.push(`${f.name}: larger than ${MAX_UPLOAD_MB} MB`); return; }
    const name = f.name && f.name !== "image.png" ? f.name : `Screenshot ${new Date().toISOString().slice(0, 16).replace("T", " ").replace(":", ".")}.png`;
    const also = fileNow ? `${extra.attach ? `&attach=${encodeURIComponent(extra.attach)}` : ""}${extra.co ? `&co=${encodeURIComponent(extra.co)}` : ""}${extra.waiverKind ? `&waiver_kind=${encodeURIComponent(extra.waiverKind)}` : ""}` : "";
    const query = jobId ? `?job=${encodeURIComponent(jobId)}${fileNow ? "&file=1" : ""}${fileNow && kind ? `&kind=${encodeURIComponent(kind)}` : ""}${also}` : "";
    try {
      const res = await fetch(`/api/import/file${query}`, {
        method: "POST",
        headers: { "Content-Type": f.type || "application/octet-stream", "X-Filename": encodeURIComponent(name) },
        body: f,
      });
      const data = await readJson(res);
      if (res.status === 401) { signedOut = true; return; }
      if (res.ok) { ok++; setNetBanner(false); }
      else failed.push(data.error || `${name}: upload failed`);
    } catch (_) {
      setNetBanner(true);
      failed.push(`${name}: can't reach Duracash`);
    }
  };
  // Two or three at a time, saying how far it has got (R6-04).
  if (files.length > 1) toast(`Uploading 1 of ${files.length}…`);
  await inBatches(files, UPLOADS_AT_ONCE, one, (done, total) => { if (done < total) toast(`Uploading ${done + 1} of ${total}…`); });
  if (signedOut) { clearSession(); renderLanding(); return; }
  // The answers can arrive out of order, so the page reads the company once at the end.
  if (ok) { clearJobDetails(); await load(); }
  const where = !fileNow ? "in the Inbox" : extra.attach ? "added to the job record and attached" : extra.co ? "added to the job record; the change order is signed" : "added to the job record";
  toast([ok ? `${ok} file${ok === 1 ? "" : "s"} ${where}.` : "", failed.join("; ")].filter(Boolean).join(" "));
  render();
}

async function postImport(path, body) {
  let res;
  try {
    res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch (_) { setNetBanner(true); toast("Not saved: can't reach Duracash."); return false; }
  const data = await readJson(res);
  if (!res.ok) { toast(data.error || "Import failed."); return false; }
  setState(data);
  toast(body.job_id ? "Added to the job record." : "Added to the Inbox.");
  render();
  return true;
}

// One request at a time: Export's calendar card and each redraw of these pages all ask.
let importSettingsAsking = false;
export async function loadImportSettings() {
  if (importSettings) { fillImportSettings(); return; }
  if (importSettingsAsking) return;
  importSettingsAsking = true;
  const asked = me;
  try {
    const res = await fetch("/api/import/settings");
    const data = res.ok ? await readJson(res) : null;
    // Signed out (or someone else signed in) while this was asked: it is the last company's.
    if (data && me === asked) { importSettings = escDeep(data); fillImportSettings(); }
  } catch (_) { /* the cards keep their "loading" text; the banner covers outages */ }
  finally { importSettingsAsking = false; }
}

function fillImportSettings() {
  const s = importSettings;
  if (!s) return;
  const set = (id, value) => { const el = document.getElementById(id); if (el) el.innerHTML = value; };
  const isDemo = me && me.company_id === DEMO_COMPANY_ID;
  set("outbox-mode", isDemo
    ? "This is the demo account: nothing is emailed from it."
    : s.mail_configured
      ? `Approved emails are sent from <strong>${s.mail_from}</strong> with your company name, and replies go to the person who approved them. Up to ${s.mail_daily_limit} a day.`
      : "Email delivery is not set up on this server yet, so nothing here has left Duracash. Once it is, use Send again on any row.");
  set("email-webhook", s.email_webhook);
  set("sms-webhook", s.sms_webhook);
  set("email-check", s.email_check === "password" ? "The server asks the email service for its password (INBOUND_WEBHOOK_PASSWORD): put it in the address, as https://duracash:PASSWORD@… ."
    : s.email_check === "signature" ? "The server checks the email service's signature (INBOUND_SIGNING_KEY) on every message."
    : "");
  set("cal-url", s.calendar_url);
  // The same address as webcal://, which a phone's calendar app opens as a subscription (R6-04).
  const webcal = document.getElementById("cal-webcal");
  const feed = webcalUrl(unesc(String(s.calendar_url || "")));
  if (webcal && feed) { webcal.href = feed; webcal.hidden = false; }
  set("email-address", s.email_address ? `Forward or BCC job email to <strong>${s.email_address}</strong>.` : "No forwarding address is set up yet (an email provider has to be connected first; see steps).");
  set("sms-number", s.sms_number ? `Text photos and notes to <strong>${s.sms_number}</strong>.` : "No texting number is set up yet (a Twilio number has to be connected first; see steps).");
}

// The calendar feed on Export: the private address with Copy, and the phone's one-tap link. Drawn
// from the addresses already asked for, else filled in when they arrive (fillImportSettings).
export function calendarFeedHtml() {
  if (!importSettings) loadImportSettings();
  const url = importSettings ? importSettings.calendar_url : "";
  const feed = url ? webcalUrl(unesc(String(url))) : "";
  return `<p><a class="btn secondary" id="cal-webcal" href="${feed ? esc(feed) : "/data/export"}"${feed ? "" : " hidden"}>Add to my phone's calendar</a></p>
        <div class="copy-row"><code id="cal-url">${url || "Loading…"}</code><button class="btn small secondary" data-action="copy" data-target="cal-url">Copy</button>${newAddressButton("calendar")}</div>`;
}

// Replacing a private web address needs Edit company details and connections (the server's
// /api/import/rotate refuses anyone else).
export function newAddressButton(kind) {
  return can("company") ? `<button class="btn small secondary" data-action="rotate-token" data-kind="${kind}">Make a new address</button>` : "";
}

// Import's "Which job?" for files added there: the last one picked, or the only open job.
function uploadJobOptions() {
  const openJobs = state.jobs.filter(j => !j.closed);
  // "Not sure" stays picked once chosen, even with one open job.
  // A redraw keeps what the box on screen shows (a pick made while the page was loading), then
  // what was last picked on this device.
  const onScreen = document.getElementById("upload-job");
  const last = onScreen && onScreen.value ? onScreen.value : lastUploadJob();
  const pick = last === "" ? "" : defaultJobId(openJobs, last || "");
  return `<option value="">Not sure, file later</option>` + openJobs.map(j => `<option value="${j.id}"${pick === j.id ? " selected" : ""}>${j.name} (${j.code})</option>`).join("");
}

// The job for files added under "Which job?" (on Import): suggested on the Inbox card, never
// filed by itself. Read from the box itself whenever it is on the page, so it doesn't depend on
// which view name was set when the page last drew.
function inboxUploadJob() {
  return document.getElementById("upload-job")?.value || "";
}

// The file box on Import: Which job?, Take a photo and Choose files, and one line of what it takes.
// On a phone the Inbox shows only the camera button, which the Take a photo shortcut focuses
// (its id keeps the focus when the page redraws).
export function dropzoneHtml(cameraOnly = false) {
  if (cameraOnly) {
    return `<div class="dropzone touch-only" tabindex="0"><div class="capture-buttons">
        <button class="btn" id="inbox-camera" data-action="pick-files" data-capture="1">Take a photo</button></div></div>`;
  }
  return `<div class="dropzone" tabindex="0">
        <label class="upload-job" for="upload-job">Which job? <select id="upload-job">${uploadJobOptions()}</select></label>
        <div class="capture-buttons touch-only">
          <button class="btn" data-action="pick-files" data-ask-job="1" data-capture="1">Take a photo</button>
          <button class="btn secondary" data-action="pick-files" data-ask-job="1">Choose files</button>
        </div>
        <div class="pointer-only"><button class="btn secondary" data-action="pick-files" data-ask-job="1">Choose files</button></div>
        <span class="sub">Photos, PDFs, Office files, CSV or saved emails, up to ${MAX_UPLOAD_MB} MB.<span class="pointer-only"> Drop them anywhere or press Ctrl+V / ⌘V.</span></span>
      </div>`;
}

// The paste box, on Import and (with jobId) on a job page, where it files straight to the job.
// "Something else" until the text looks like notes or an email (R5-06).
// What was pasted, and the kind (picked, else guessed from it), stay through a redraw until added.
export function pasteBoxHtml(jobId = "") {
  const text = draftValue("paste", "text");
  const picked = draftValue("paste", "source");
  const source = picked || (text ? pasteGuess(text).source : "paste");
  const opt = (value, label) => `<option value="${value}"${value === source ? " selected" : ""}>${label}</option>`;
  return `<div data-form-key="paste"><div class="form-inline">
          <select id="paste-source" data-field="source" aria-label="What are you pasting?"${picked ? ' data-touched="1"' : ""}>
            ${opt("email", "An email")}
            ${opt("sms", "Text messages")}
            ${opt("notes", "Notes: one daily record per date")}
            ${opt("paste", "Something else")}
          </select>
        </div>
        <textarea id="paste-text" data-field="text" rows="6" placeholder="Paste here. For notes, start each day with a date, for example:&#10;10/12 Framing done on Oak Street, 4 crew&#10;10/13 Inspector passed rough-in">${esc(text)}</textarea>
        <p id="paste-hint" class="sub paste-hint" role="status"></p>
        <div class="form-inline"><button class="btn" data-action="import-text"${jobId ? ` data-job="${jobId}"` : ""}>${jobId ? "Add to job" : "Add to Inbox"}</button></div></div>`;
}

// Typing or pasting in the box: pick what it looks like, unless the person already chose.
export function guessPasteSource() {
  const select = document.getElementById("paste-source");
  const hint = document.getElementById("paste-hint");
  if (!select || select.dataset.touched) return;
  const guess = pasteGuess(document.getElementById("paste-text")?.value || "");
  select.value = guess.source;
  if (hint) hint.textContent = guess.hint;
}

// Inbox cards whose job and kind selects are open ("Other job" / "Change type"); the rest show
// one button for the suggested job (R5-05).
const inboxOpen = new Set();

// Everything received (C1): the filters and sort, sent to GET /api/inbox as they are picked.
const NO_FILTER = { source: "", status: "", job: "", sort: "newest" };
let receivedFilter = { ...NO_FILTER };
export function setReceivedFilter(name, value) {
  if (name in NO_FILTER) receivedFilter = { ...receivedFilter, [name]: value || NO_FILTER[name] };
}
// The query for the list: only what was picked, the default sort left out.
export function receivedQuery(f = receivedFilter) {
  return Object.keys(NO_FILTER).filter(k => f[k] && f[k] !== NO_FILTER[k]).map(k => `${k}=${encodeURIComponent(unesc(f[k]))}`).join("&");
}
document.addEventListener("change", (e) => {
  const s = e.target && e.target.closest ? e.target.closest("select[data-received-filter]") : null;
  if (!s) return;
  setReceivedFilter(s.dataset.receivedFilter, s.value);
  render();
});

// Signed out: the private email, text and calendar addresses, and a job picked in a filter, are that company's.
onSessionEnd(() => { importSettings = null; inboxOpen.clear(); receivedFilter = { ...NO_FILTER }; });

// A row's status in words: Waiting, Filed to <job> as <kind>, Filed automatically to <job>, or Dismissed.
export function receivedStatus(r) {
  const job = r.job_name || "a job";
  if (r.status === "new") return r.job_name ? `Waiting (suggested: ${r.job_name})` : "Waiting";
  if (r.status === "auto") return `Filed automatically to ${job}`;
  if (r.status === "filed") return `Filed to ${job} as ${termText(RECORD_KINDS[r.kind] || r.kind || "a record")}`;
  return "Dismissed";
}

// A row's files, each opening in a new tab; a file taken off the job record since says so.
export function receivedFilesHtml(r) {
  const links = (r.files || []).map(f => `<a href="/api/files/${f.id}" target="_blank" rel="noopener">${f.name}</a>`).join(" · ");
  const gone = r.files_gone ? `${r.files_gone} file${r.files_gone === 1 ? "" : "s"} no longer on the job record` : "";
  return links || gone ? `<div class="sub">Files: ${[links, gone].filter(Boolean).join(" · ")}</div>` : "";
}

const SOURCE_OPTIONS = () => Object.entries(SOURCE_LABELS);
const STATUS_OPTIONS = [["new", "Waiting"], ["filed", "Filed"], ["auto", "Filed automatically"], ["dismissed", "Dismissed"]];
const SORT_OPTIONS = [["newest", "Newest first"], ["oldest", "Oldest first"], ["from", "Sender (A-Z)"], ["job", "Job (A-Z)"]];

function receivedSelect(name, label, all, options, more = "") {
  const opts = (all ? [["", all]] : []).concat(options);
  return `<label for="received-${name}" class="inline-label">${label}</label>
        <select id="received-${name}" data-received-filter="${name}">
          ${opts.map(([v, l]) => `<option value="${v}"${receivedFilter[name] === v ? " selected" : ""}>${l}</option>`).join("")}${more}
        </select>`;
}

function receivedHtml() {
  const page = listPage("inbox", receivedQuery());
  const rows = page ? page.entries : [];
  const filtered = !!receivedQuery({ ...receivedFilter, sort: "newest" });
  return `<div class="card received">
      <h2>Everything received</h2>
      <div class="filter-bar">
        ${receivedSelect("source", "Came in by", "All", SOURCE_OPTIONS())}
        ${receivedSelect("status", "Status", "All", STATUS_OPTIONS)}
        ${receivedSelect("job", "Job", "All jobs", [], jobFilterOptions(state.jobs, receivedFilter.job))}
        ${receivedSelect("sort", "Sort", "", SORT_OPTIONS)}
      </div>
      ${rows.length ? `<div class="table-wrap"><table>
        <tr><th>Received</th><th>From</th><th>Subject</th><th>Came in by</th><th>Status</th></tr>
        ${rows.map(r => `<tr data-received="${r.id}"><td>${fmtDate(r.sent_at || r.received_at)}</td><td>${r.from || "—"}</td>
          <td>${r.title}${receivedFilesHtml(r)}</td><td>${SOURCE_LABELS[r.source] || r.source}</td>
          <td>${receivedStatus(r)}${r.why ? `<div class="sub">${r.why}</div>` : ""}
            <div class="row-actions">${r.status === "new" ? "" : `<button class="btn small secondary" data-action="inbox-undo" data-item="${r.id}">Undo</button>`}<button class="linkish" data-action="print-received" data-item="${r.id}">Print</button></div></td></tr>`).join("")}
      </table></div>${moreButton("inbox", page)}`
      : !page ? `<p class="sub">Loading…</p>` : emptyState(filtered ? "Nothing matches these filters." : "Nothing has come in yet.")}
    </div>`;
}

function inboxItemCard(i) {
  const openJobs = state.jobs.filter(j => !j.closed);
  const files = (i.file_ids || []).map(fid => (state.files || []).find(x => x.id === fid)).filter(Boolean);
  const title = i.subject || (files[0] && files[0].name) || (i.text || "").split("\n")[0].slice(0, 80) || "Untitled";
  const preview = (i.text || "").slice(0, 600);
  const kinds = Object.entries(RECORD_KINDS).map(([k, v]) => `<option value="${k}" ${i.kind === k ? "selected" : ""}>${termText(v)}</option>`).join("");
  const suggested = i.suggested_job_id && openJobs.find(j => j.id === i.suggested_job_id);
  const choices = suggested ? [] : choiceJobs(i, openJobs);
  // Nothing suggested and only one open job: that one (R2-07).
  const pick = i.suggested_job_id || defaultJobId(openJobs);
  const jobs = `<option value="">Pick a job…</option>` + jobsInOrder(openJobs, i.candidates || []).map(j => `<option value="${j.id}" ${pick === j.id ? "selected" : ""}>${j.name} (${j.code})</option>`).join("");
  const selects = !suggested || inboxOpen.has(i.id);
  const kindName = termText(RECORD_KINDS[i.kind] || i.kind);
  return `<div class="inbox-item" data-item="${i.id}">
    <div class="inbox-head"><span class="pill">${SOURCE_LABELS[i.source] || i.source}</span> <strong>${title}</strong>
      <span class="sub">${i.from ? "from " + i.from + " · " : ""}${fmtDate(i.sent_at || i.received_at)}${i.note_date ? " · dated " + fmtDate(i.note_date) : ""}</span></div>
    ${preview ? `<details><summary>${preview.slice(0, 140)}${preview.length > 140 ? "…" : ""}</summary><pre class="inbox-text">${preview}</pre></details>` : ""}
    ${files.length ? `<div class="sub">Files: ${files.map(f => `<a href="/api/files/${f.id}" target="_blank" rel="noopener">${f.name}</a>`).join(" · ")}</div>` : ""}
    ${(i.amounts || []).length || (i.dates || []).length ? `<div class="sub">Found: ${(i.amounts || []).map(a => money(a)).join(", ")}${(i.amounts || []).length && (i.dates || []).length ? " · " : ""}${(i.dates || []).map(fmtDate).join(", ")}</div>` : ""}
    ${(i.skipped_attachments || []).length ? `<div class="field-error">${i.skipped_attachments.join(" ")}</div>` : ""}
    ${choices.length ? `<div class="inbox-choices"><span class="sub">${choiceLine(choices, state.payers || [])}</span>
      ${choices.map(j => `<button class="btn small secondary" data-action="inbox-file-suggested" data-item="${i.id}" data-job="${j.id}">${j.name}</button>`).join("")}</div>`
      : `<div class="sub">${suggested ? "Suggested because it " + i.match_reason : i.match_reason}</div>`}
    ${i.payments_csv && files[0] ? `<div class="notice">This looks like a payments export. <button class="btn small" data-action="inbox-read-payments" data-file="${files[0].id}">Read as payments (${i.payment_rows} row${i.payment_rows === 1 ? "" : "s"})</button></div>` : ""}
    ${i.says_done_job_id ? `<div class="notice says-done">This says the job is done. <button class="btn small" data-action="inbox-mark-done" data-item="${i.id}">Mark ${(state.jobs.find(j => j.id === i.says_done_job_id) || {}).name || "the job"} done and draft the pay app</button></div>` : ""}
    ${suggested ? `<div class="form-inline inbox-one-tap">
      <button class="btn" data-action="inbox-file-suggested" data-item="${i.id}" data-job="${suggested.id}">Add to ${suggested.name}</button>
      ${selects ? "" : `<button class="linkish" data-action="inbox-other" data-item="${i.id}" data-focus="job">Other job</button>
      <button class="linkish" data-action="inbox-other" data-item="${i.id}" data-focus="kind">Change type (${kindName})</button>`}
      <button class="linkish" data-action="inbox-dismiss" data-item="${i.id}">Dismiss</button>
    </div>` : ""}
    ${selects ? `<div class="form-inline">
      <select id="inb-job-${i.id}" aria-label="Job">${jobs}</select>
      <select id="inb-kind-${i.id}" aria-label="Kind">${kinds}</select>
      <button class="btn small${suggested ? " secondary" : ""}" data-action="inbox-file" data-item="${i.id}">Add to job</button>
      ${suggested ? "" : `<button class="linkish" data-action="inbox-dismiss" data-item="${i.id}">Dismiss</button>`}
    </div>` : ""}
  </div>`;
}

// Drop files anywhere, or paste a screenshot, to import. On a job page they
// go straight onto that job's record; elsewhere they land in the Inbox.
let dragDepth = 0;
function dropOverlay(on) {
  let el = document.getElementById("drop-overlay");
  if (!el && on) {
    el = document.createElement("div");
    el.id = "drop-overlay";
    el.className = "drop-overlay";
    document.body.appendChild(el);
  }
  if (!el) return;
  const job = currentView === "job" && state && state.jobs.find(j => j.id === currentJobId && !j.closed);
  el.textContent = job ? `Drop to add to ${job.name}` : "Drop to add to the Inbox";
  el.style.display = on ? "flex" : "none";
}
function importTargetJob() {
  const job = currentView === "job" && state && state.jobs.find(j => j.id === currentJobId && !j.closed);
  return job ? job.id : "";
}
document.addEventListener("dragenter", (e) => {
  if (!state || !e.dataTransfer || ![...e.dataTransfer.types].includes("Files")) return;
  dragDepth++;
  dropOverlay(true);
});
document.addEventListener("dragover", (e) => {
  if (!state || !e.dataTransfer || ![...e.dataTransfer.types].includes("Files")) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "copy";
});
document.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) dropOverlay(false);
});
document.addEventListener("drop", (e) => {
  dragDepth = 0;
  dropOverlay(false);
  if (!state || !e.dataTransfer) return;
  if (!e.dataTransfer.files.length) {
    if ([...e.dataTransfer.types].includes("Files")) { e.preventDefault(); toast("That item has no file to import. Save it as a file first, then drop the file."); }
    return;
  }
  e.preventDefault();
  const jobId = importTargetJob();
  uploadFiles(e.dataTransfer.files, jobId || inboxUploadJob(), !!jobId);
});
window.addEventListener("blur", () => { dragDepth = 0; dropOverlay(false); });
document.addEventListener("dragend", () => { dragDepth = 0; dropOverlay(false); });
document.addEventListener("paste", (e) => {
  if (!state || !e.clipboardData) return;
  const files = [...e.clipboardData.files];
  if (!files.length) return; // plain text pastes behave normally
  // Copying cells from Excel or Word puts both text and a picture on the clipboard.
  // In a text box, the text is what the person meant.
  const target = e.target;
  const editable = target && (target.tagName === "TEXTAREA" || target.tagName === "INPUT" || target.isContentEditable);
  if (editable && e.clipboardData.types.includes("text/plain")) return;
  e.preventDefault();
  const jobId = importTargetJob();
  uploadFiles(files, jobId || inboxUploadJob(), !!jobId);
});
// One file picker for every screen ("choose files" on the Inbox or a job page).
document.getElementById("file-input").addEventListener("change", (e) => {
  const input = e.target;
  const jobId = input.dataset.job || "", kind = input.dataset.kind || "", suggest = input.dataset.suggest || "";
  const extra = { attach: input.dataset.attach || "", co: input.dataset.co || "", waiverKind: input.dataset.waiverKind || "" };
  const files = [...input.files];
  input.value = "";
  input.dataset.job = "";   // the next pick starts with no job unless its button sets one
  input.dataset.kind = "";
  input.dataset.attach = "";
  input.dataset.co = "";
  input.dataset.waiverKind = "";
  input.dataset.suggest = "";
  if (!jobId) { uploadFiles(files, suggest, false); return; }
  // One file goes on a paperwork item or a change order; the rest of a multi-pick only on the job record.
  if ((extra.attach || extra.co) && files.length > 1) {
    uploadFiles(files.slice(0, 1), jobId, !!jobId, kind, extra).then(() => uploadFiles(files.slice(1), jobId, !!jobId, kind));
    return;
  }
  uploadFiles(files, jobId, !!jobId, kind, extra);
});

// This module's pages, by the name their address opens (routes.js).
export const views = {
  inbox() {
    const items = (state.inbox || []).filter(i => i.status === "new").slice().reverse();
    const owner = can("company");   // the server sends the provider web addresses only with this (R6-07)
    const withSuggestion = items.filter(i => i.suggested_job_id).length;
    return `
      <h1>Inbox</h1>
      <div class="card inbox-waiting">
        <div class="top"><h2>Waiting (${items.length})</h2>${withSuggestion ? `<button class="btn small secondary" data-action="inbox-file-all">${withSuggestion === 1 ? "Add it to its suggested job" : `Add all ${withSuggestion} to their suggested jobs`}</button>` : ""}</div>
        ${items.length ? items.map(inboxItemCard).join("") : emptyState("Nothing waiting.")}
      </div>
      ${dropzoneHtml(true)}
      <p class="sub">Add files or paste notes on <a href="/data/import" data-action="nav" data-view="import">Import</a>.</p>
      ${receivedHtml()}
      <details class="card">
        <summary><strong>Bring emails and texts in automatically</strong></summary>
        <h3>Email</h3>
        <p id="email-address" class="sub">Loading…</p>
        ${owner ? `<p class="sub">Steps: in an email service that can post incoming mail to a web address (SendGrid Inbound Parse, Mailgun routes or Postmark inbound), set up an inbound address and point it at this private web address. Then forward or BCC job emails to that inbound address. Attachments come in as files.</p>
        <div class="copy-row"><code id="email-webhook">Loading…</code><button class="btn small secondary" data-action="copy" data-target="email-webhook">Copy</button>${newAddressButton("email")}</div>
        <p id="email-check" class="sub"></p>` : ""}
        <h3>Text messages</h3>
        <p id="sms-number" class="sub">Loading…</p>
        ${owner ? `<p class="sub">Steps: in Twilio, buy a number and set its incoming-message webhook to this private web address. Set TWILIO_AUTH_TOKEN on the server so Duracash checks each message really came from Twilio; with it set, photos and PDFs sent by text are saved with the Inbox item. Without it they show as links.</p>
        <div class="copy-row"><code id="sms-webhook">Loading…</code><button class="btn small secondary" data-action="copy" data-target="sms-webhook">Copy</button>${newAddressButton("sms")}</div>
        <p class="sub">Anyone who knows these addresses can drop items in your Inbox. They are reviewed before filing. Make a new address if one leaks.</p>`
        : `<p class="sub">An owner connects the email service and the texting number. Items sent to them wait here to be reviewed before filing.</p>`}
      </details>
    `;
  },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in app.js.
export const actions = {
  "pick-files": (el) => {
    const input = document.getElementById("file-input");
    if (input) {
      input.dataset.job = el.dataset.job || "";
      input.dataset.kind = el.dataset.kind || "";   // e.g. "waiver" from the waiver page
      input.dataset.attach = el.dataset.attach || "";   // a paperwork item to attach it to
      input.dataset.co = el.dataset.co || "";   // a change order it is the signed copy of
      input.dataset.waiverKind = el.dataset.waiverKind || "";   // the type of a signed lien waiver (M14)
      // The Inbox's choose files: the job picked under "Which job?", suggested and not filed.
      input.dataset.suggest = el.dataset.askJob ? inboxUploadJob() : "";
      // "Take a photo" opens the rear camera directly; "choose files" offers every source.
      if (el.dataset.capture) { input.setAttribute("capture", "environment"); input.setAttribute("accept", "image/*"); }
      else { input.removeAttribute("capture"); input.setAttribute("accept", FILE_ACCEPT); }
      input.click();
    }
  },
  "import-text": async (el) => {
    const text = document.querySelector("#paste-text")?.value || "";
    const source = document.querySelector("#paste-source")?.value || "paste";
    const jobId = el.dataset.job || "";
    if (!text.trim()) { toast("Paste some text first."); return; }
    const ok = await postImport("/api/import/text" + (jobId ? "?file=1" : ""), { text, source, job_id: jobId });
    // Added: the box and its kind start over, as for the next paste.
    if (ok) { clearForm("paste"); const box = document.querySelector("#paste-text"); if (box) box.value = ""; render(); }
  },
  "inbox-file": async (el) => {
    const itemId = el.dataset.item;
    const jobId = document.querySelector(`#inb-job-${itemId}`)?.value || "";
    const kind = document.querySelector(`#inb-kind-${itemId}`)?.value || "";
    if (!jobId) { toast("Pick a job for this item."); return; }
    await post(`/api/inbox/${itemId}/file`, { job_id: jobId, kind });
  },
  // One toast for the lot (R5-05), not a "Saved" per item.
  "inbox-file-all": async () => {
    // Every card's picks are read before the first save: each save redraws the cards, which
    // would show the suggested job and kind again.
    const picks = (state.inbox || []).filter(i => i.status === "new" && i.suggested_job_id).map(i => {
      const select = document.querySelector(`#inb-job-${i.id}`);
      const jobId = select ? select.value : i.suggested_job_id;   // a row set back to "Pick a job" is skipped
      return { id: i.id, jobId, kind: document.querySelector(`#inb-kind-${i.id}`)?.value || i.kind };
    }).filter(p => p.jobId);
    let filed = 0;
    for (const p of picks) {
      const r = await post(`/api/inbox/${p.id}/file`, { job_id: p.jobId, kind: p.kind }, { quiet: true });
      if (r && r.ok) filed++;
    }
    if (filed) toast(`Added ${filed} item${filed === 1 ? "" : "s"} to ${filed === 1 ? "its job" : "their jobs"}.`);
  },
  "inbox-other": (el) => {
    inboxOpen.add(el.dataset.item);
    render();
    document.querySelector(`#inb-${el.dataset.focus === "kind" ? "kind" : "job"}-${el.dataset.item}`)?.focus();
  },
  "inbox-dismiss": async (el) => {
    const id = el.dataset.item;
    const r = await post(`/api/inbox/${id}/dismiss`, {}, { quiet: true });
    if (r && r.ok) toast("Dismissed.", { label: "Undo", run: () => post(`/api/inbox/${id}/undo`) });
  },
  // The message may only sound like the job is done: filing it is the other way out.
  "inbox-mark-done": async (el) => {
    const it = (state.inbox || []).find(x => x.id === el.dataset.item);
    const j = it && state.jobs.find(x => x.id === it.says_done_job_id);
    const answer = await askConfirm(`Mark ${unesc(j ? j.name : "the job")} done? ${termText("This bills the rest of the contract on a final pay app.")}`, "Mark done and draft", false, "Just file the message");
    if (!answer) return;
    if (answer === "alt") { if (it) await post(`/api/inbox/${it.id}/file`, { job_id: it.says_done_job_id, kind: it.kind }); return; }
    const r = await post(`/api/inbox/${el.dataset.item}/mark-done`);
    const item = r && r.ok && (state.inbox || []).find(x => x.id === el.dataset.item);
    if (item) nav("job", item.filed_job_id);
  },
  "inbox-undo": (el) => post(`/api/inbox/${el.dataset.item}/undo`),
  // One row of Everything received on paper: who, when, how it came in, what became of it, its text and files.
  "print-received": (el) => {
    const r = ((listPage("inbox", receivedQuery()) || {}).entries || []).find(x => x.id === el.dataset.item);
    if (!r) return;
    printSheet([["From", r.from || "—"], ["Received", fmtDate(r.sent_at || r.received_at)], ["Came in by", SOURCE_LABELS[r.source] || r.source],
      ["Status", receivedStatus(r)]], r.title, r.text, (r.files || []).map(f => f.name).concat(r.files_gone ? [`${r.files_gone} file${r.files_gone === 1 ? "" : "s"} no longer on the job record`] : []));
  },
  "rotate-token": async (el) => {
    if (!(await askConfirm("Make a new address? The old one stops working right away.", "Make a new address"))) return;
    // Through post(), so a refusal (members can't do this) shows like any other.
    const r = await post("/api/import/rotate", { kind: el.dataset.kind }, { quiet: true });
    if (!r || !r.ok) return;
    toast("New address made. The old one no longer works.");
    importSettings = null;
    render();
  },
  // The kind select counts: the card's when it is open, else Today's row's (it starts at the guessed kind).
  "inbox-file-suggested": (el) => post(`/api/inbox/${el.dataset.item}/file`, { job_id: el.dataset.job, kind: document.querySelector(`#inb-kind-${el.dataset.item}`)?.value || "" }),
};
