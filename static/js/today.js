// Today's queue (UI/UX Option B): everything that needs a person, most urgent first, each with
// the one button that does it. The rows come from the server (domain.today_rows, sent in
// /api/state as today_rows, R4-10), the same rows the daily email lists, so the rules live in one
// place; this file only draws them. Nothing that emails a pay app or a waiver goes out from here:
// those open the pay app's "Before you send" card, or the waiver page with its type, which show
// what leaves and to whom. A follow-up names its recipient on the row, and drafting or filing
// sends nothing. The page's state comes in as an argument, so node can test it
// (tests/js/today.test.mjs).

import { termText } from "./terms.js";
import { RECORD_KINDS } from "./jobs.js";

const WENT_OUT = ["sent", "approved", "partial", "paid"];
export const QUEUE_SHOWN = 3;   // rows shown before "and N more"

// What each kind of row's main button does: its data attributes, and whether it opens the job
// itself (then the row has no separate Open button). The button's words come with the row.
const ACTIONS = {
  email_failed: (r) => `data-action="email-retry" data-email="${r.email_id}"`,
  says_done: (r) => `data-action="inbox-mark-done" data-item="${r.item_id}"`,
  blocked: (r) => `data-action="open-packet" data-job="${r.job_id}"${r.invoice_id ? ` data-inv="${r.invoice_id}"` : ""}`,
  ready: (r) => `data-action="open-payapp" data-job="${r.job_id}" data-inv="${r.invoice_id}" data-focus=".send-check"`,
  waiver: (r) => `data-action="open-waiver" data-id="${r.job_id}"`,
  not_billed: (r) => `data-action="draft" data-id="${r.job_id}"`,
  due_soon: (r) => `data-action="open-job" data-id="${r.job_id}" data-focus=".next-card"`,
  new_job: (r) => `data-action="open-job" data-id="${r.job_id}" data-focus=".next-card"`,
  follow_up: (r) => `data-action="follow-up" data-id="${r.job_id}"`,
  inbox: (r) => `data-action="inbox-file-suggested" data-item="${r.item_id}" data-job="${r.job_id}"`,
};
const OPENS_JOB = ["ready", "waiver", "due_soon", "new_job"];

// The server's rows (already HTML-escaped on arrival, like the rest of state), most urgent first.
export function todayQueue(state) {
  return state.today_rows || [];
}

// An Inbox row files under the kind the Inbox guessed: the row shows it after its button
// ("Add to this job as Lien waiver") in the Inbox's own kind select, so it can be changed first
// and what is shown is what is filed. The select has the Inbox card's id, which the button reads.
export function inboxKindSelect(r) {
  const guessed = RECORD_KINDS[r.item_kind] ? r.item_kind : "note";
  const options = Object.entries(RECORD_KINDS).map(([k, v]) => `<option value="${k}"${k === guessed ? " selected" : ""}>${termText(v)}</option>`).join("");
  return `<label class="row-kind">as <select id="inb-kind-${r.item_id}" aria-label="File as">${options}</select></label>`;
}

// One row's buttons: the main one, a phone call for a follow-up (R4-06), and Open unless the
// main button already opens the job. A pay app the client approved (R4-08) has no main button.
export function rowButtons(r) {
  const attrs = ACTIONS[r.kind];
  const main = attrs && r.button ? `<button class="btn small" ${attrs(r)}>${r.button}</button>` + (r.kind === "inbox" ? inboxKindSelect(r) : "") : "";
  const spoke = r.kind === "follow_up" ? `<button class="btn small secondary" data-action="spoke" data-id="${r.job_id}">Spoke to them…</button>` : "";
  const open = OPENS_JOB.includes(r.kind) ? "" : `<button class="btn small secondary" data-action="open-job" data-id="${r.job_id}">Open</button>`;
  return main + spoke + open;
}

// The rows, three at a time until "and N more" is tapped (expanded; reset when the page changes).
// With nothing waiting, "Nothing is waiting on you." only once a pay app has gone out; before
// that, what will show up here.
export function queueHtml(queue, { expanded = false, anySent = false } = {}) {
  if (!queue.length) return anySent ? `<p class="sub">Nothing is waiting on you.</p>`
    : `<p class="sub">${termText("Work shows up here when a pay app is ready to send, paperwork is missing, a waiver or a follow-up is due, finished work isn't billed, or the Inbox has something for a job.")}</p>`;
  const rows = expanded ? queue : queue.slice(0, QUEUE_SHOWN);
  const more = queue.length - rows.length;
  return rows.map(r => `
          <div class="action-item">
            <div class="action-text"><span class="pill ${r.tone || ""}">${r.label}</span> <strong>${r.job}</strong><div class="next-action">${r.text}${r.note ? ` <span class="sub">(${r.note})</span>` : ""}</div></div>
            <div class="action-buttons">${rowButtons(r)}</div>
          </div>`).join("")
    + (more > 0 ? `<button class="linkish queue-more" data-action="queue-more" aria-expanded="false">and ${more} more</button>` : "");
}

// A pay app has gone out from this company: open jobs carry their pay apps; a closed job was billed.
// The Today tile "Billed this month": pay apps sent this calendar month and not pulled back, the
// same sum as the helper's (domain.BILLED_STATUSES, by the month of sent_at in UTC).
export function billedThisMonth(jobs, now = new Date()) {
  const month = now.toISOString().slice(0, 7);
  let total = 0;
  (jobs || []).forEach(j => (j.invoices || []).forEach(inv => {
    if (WENT_OUT.includes(inv.status) && inv.sent_at && String(inv.sent_at).slice(0, 7) === month) total += inv.amount_cents;
  }));
  return total;
}

export const anyPayAppSent = (state) => (state.jobs || []).some(j => (j.invoices || []).some(i => i.sent_at || WENT_OUT.includes(i.status)))
  || (state.closed_jobs_total || 0) > 0;
