// The Inbox, and bringing things in: uploads (choose, drop or paste a file anywhere), pasted
// text, and the forwarding addresses for email, texts and the calendar.
import { unesc, escDeep, money, fmtDate } from "../format.js";
import { RECORD_KINDS, SOURCE_LABELS, defaultJobId } from "../jobs.js";
import { readJson, setNetBanner, inBatches } from "../net.js";
import { askConfirm } from "../dom.js";
import { webcalUrl } from "../calendar.js";
import { jobsInOrder, choiceJobs, choiceLine, pasteGuess } from "../inbox.js";
import { termText } from "../terms.js";
import { state, me, currentView, currentJobId, clearSession, onSessionEnd } from "../state.js";
import { load, toast, renderLanding, render, clearJobDetails, post, setState, DEMO_COMPANY_ID, emptyState, nav } from "../app-core.js";

// Files added in the Inbox go with the job last picked under "Which job?" on this device (R5-07).
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
    const also = fileNow ? `${extra.attach ? `&attach=${encodeURIComponent(extra.attach)}` : ""}${extra.co ? `&co=${encodeURIComponent(extra.co)}` : ""}` : "";
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

export async function loadImportSettings() {
  if (importSettings) { fillImportSettings(); return; }
  const asked = me;
  try {
    const res = await fetch("/api/import/settings");
    const data = res.ok ? await readJson(res) : null;
    // Signed out (or someone else signed in) while this was asked: it is the last company's.
    if (data && me === asked) { importSettings = escDeep(data); fillImportSettings(); }
  } catch (_) { /* the cards keep their "loading" text; the banner covers outages */ }
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

// Only an owner can replace a private web address (the server refuses members too).
export function newAddressButton(kind) {
  return me && me.role === "owner" ? `<button class="btn small secondary" data-action="rotate-token" data-kind="${kind}">Make a new address</button>` : "";
}

// The Inbox's "Which job?" for files added there: the last one picked, or the only open job.
function uploadJobOptions() {
  const openJobs = state.jobs.filter(j => !j.closed);
  // "Not sure" stays picked once chosen, even with one open job.
  const last = lastUploadJob();
  const pick = last === "" ? "" : defaultJobId(openJobs, last || "");
  return `<option value="">Not sure, file later</option>` + openJobs.map(j => `<option value="${j.id}"${pick === j.id ? " selected" : ""}>${j.name} (${j.code})</option>`).join("");
}

// The job for files added on the Inbox page: suggested on the card, never filed by itself.
function inboxUploadJob() {
  return currentView === "inbox" ? document.getElementById("upload-job")?.value || "" : "";
}

// The paste box, on the Inbox and (with jobId) on a job page, where it files straight to the job.
// "Something else" until the text looks like notes or an email (R5-06).
export function pasteBoxHtml(jobId = "") {
  return `<div class="form-inline">
          <select id="paste-source" aria-label="What are you pasting?">
            <option value="email">An email</option>
            <option value="sms">Text messages</option>
            <option value="notes">Notes: one daily record per date</option>
            <option value="paste" selected>Something else</option>
          </select>
        </div>
        <textarea id="paste-text" rows="6" placeholder="Paste here. For notes, start each day with a date, for example:&#10;10/12 Framing done on Oak Street, 4 crew&#10;10/13 Inspector passed rough-in"></textarea>
        <p id="paste-hint" class="sub paste-hint" role="status"></p>
        <div class="form-inline"><button class="btn" data-action="import-text"${jobId ? ` data-job="${jobId}"` : ""}>${jobId ? "Add to job" : "Add to Inbox"}</button>
        <span class="sub">Notes split at every line that starts with a date (10/12, Oct 12, 2026-10-12). Exported Apple Notes or a typed-up notebook work.</span></div>`;
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

// Signed out: the private email, text and calendar addresses are that company's.
onSessionEnd(() => { importSettings = null; inboxOpen.clear(); });

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
  const extra = { attach: input.dataset.attach || "", co: input.dataset.co || "" };
  const files = [...input.files];
  input.value = "";
  input.dataset.job = "";   // the next pick starts with no job unless its button sets one
  input.dataset.kind = "";
  input.dataset.attach = "";
  input.dataset.co = "";
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
    const done = (state.inbox || []).filter(i => i.status !== "new").slice(-20).reverse();
    const owner = me && me.role === "owner";   // the provider web addresses are an owner's (R6-07)
    const withSuggestion = items.filter(i => i.suggested_job_id).length;
    const jobName = (id) => (state.jobs.find(j => j.id === id) || {}).name || "a job";
    return `
      <h1>Inbox</h1>
      <p class="sub">Bring in photos, PDFs, saved emails, text threads and notes. Duracash suggests the job from the job code, address, name or sender; you file it with one click. Nothing is filed without you.</p>
      <div class="card inbox-waiting">
        <div class="top"><h2>Waiting (${items.length})</h2>${withSuggestion ? `<button class="btn small secondary" data-action="inbox-file-all">${withSuggestion === 1 ? "Add it to its suggested job" : `Add all ${withSuggestion} to their suggested jobs`}</button>` : ""}</div>
        ${items.length ? items.map(inboxItemCard).join("") : emptyState("Nothing waiting", "Take a photo, add files, paste an email or notes below, or set up email and text forwarding.")}
      </div>
      <div class="dropzone" tabindex="0">
        <label class="upload-job" for="upload-job">Which job? <select id="upload-job">${uploadJobOptions()}</select></label>
        <div class="capture-buttons touch-only">
          <button class="btn" data-action="pick-files" data-ask-job="1" data-capture="1">Take a photo</button>
          <button class="btn secondary" data-action="pick-files" data-ask-job="1">Choose files</button>
        </div>
        <span class="sub touch-only">Photos, PDFs, Word, Excel, CSV, saved emails, up to ${MAX_UPLOAD_MB} MB each. They wait below for you to add them to the job.</span>
        <div class="pointer-only">
          <strong>Drop files anywhere on this page</strong>
          <span class="sub">Photos, PDFs, Word, Excel, CSV, saved emails (.eml), up to ${MAX_UPLOAD_MB} MB each. Or <button class="linkish" data-action="pick-files" data-ask-job="1">choose files</button>, or press Ctrl+V / ⌘V to paste a screenshot. They wait below for you to add them to the job.</span>
        </div>
      </div>
      <div class="card">
        <h2>Paste an email, a text thread, or notes</h2>
        ${pasteBoxHtml()}
      </div>
      ${done.length ? `<details class="card"><summary><strong>Recently added or dismissed</strong></summary>
        ${done.map(i => `<div class="list-item">${i.status === "filed" ? `${i.auto_filed ? "Reply filed automatically" : "Filed"} to ${jobName(i.filed_job_id)} as ${termText(RECORD_KINDS[i.filed_kind] || i.filed_kind)}` : "Dismissed"}: ${i.subject || (i.text || "").slice(0, 60) || "item"} <button class="btn small secondary" data-action="inbox-undo" data-item="${i.id}">Undo</button></div>`).join("")}
      </details>` : ""}
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
    if (ok) { const box = document.querySelector("#paste-text"); if (box) box.value = ""; }
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
