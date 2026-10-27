// Today's queue (UI/UX Option B): everything that needs a person, most urgent first, each with
// the one button that does it. Nothing that emails a pay app or a waiver goes out from here: those
// open the pay app's "Before you send" card, or the waiver page with its type, which show what
// leaves and to whom. A follow-up names its recipient on the row, and drafting or filing sends
// nothing. The page's state comes in as an argument, so node can test it (tests/js/today.test.mjs).

import { money, toDate, fmtDate } from "./format.js";
import { SOURCE_LABELS, isBlocked, blockerButton, statusPill, followUpDue } from "./jobs.js";

const DRAFTS = ["draft", "awaiting_approval"];
const WENT_OUT = ["sent", "approved", "partial", "paid"];
export const QUEUE_SHOWN = 3;   // rows shown before "and N more"

// A pay app that counts: not voided, not replaced by a progress correction.
export const billed = (j) => (j.invoices || []).some(i => i.status !== "void" && i.status !== "replaced");

export function todayQueue(state, now = new Date()) {
  const out = [];
  const add = (rank, j, pill, text, button, opensJob = false) => out.push({ rank, j, pill, text, button, opensJob });
  const tag = (word, color = "") => `<span class="pill ${color}">${word}</span>`;
  const jobs = state.jobs || [];
  const today = new Date(now.toDateString());
  const inbox = (state.inbox || []).filter(i => i.status === "new");
  // An email that didn't go out (the server lists the latest per pay app or follow-up, open jobs only).
  (state.email_failures || []).forEach(e => {
    const j = jobs.find(x => x.id === e.job_id && !x.closed);
    if (j) add(0.5, j, tag("Didn't go out", "red"), `${e.subject} to ${e.to}: ${e.status}`,
      `<button class="btn small" data-action="email-retry" data-email="${e.id}">Send again</button>`);
  });
  inbox.filter(i => i.says_done_job_id).forEach(i => {
    const j = jobs.find(x => x.id === i.says_done_job_id && !x.closed);
    if (j) add(0, j, tag("Job's done?", "copper"), `${SOURCE_LABELS[i.source] || i.source}${i.from ? " from " + i.from : ""}: ${(i.text || i.subject || "").split("\n")[0].slice(0, 120)}`,
      `<button class="btn small" data-action="inbox-mark-done" data-item="${i.id}">Mark done and draft</button>`);
  });
  jobs.filter(j => !j.closed && !j.summary).forEach(j => {
    const draft = j.invoices.find(i => DRAFTS.includes(i.status));
    // The payer's monthly cutoff (playbook): within five days, and nothing billed since the last one.
    const nextDue = toDate(j.payer.payapp_due_next), prevDue = toDate(j.payer.payapp_due_prev);
    // Only a pay app that went out counts: a draft stuck on paperwork still leaves the cutoff ahead.
    const billedThisCycle = prevDue && j.invoices.some(i => WENT_OUT.includes(i.status) && (toDate(i.sent_at || i.created_at) || 0) > prevDue);
    const mayBill = j.work_status !== "complete" || j.money.unbilled_completed_cents > 0;
    const dueSoon = nextDue && mayBill && !billedThisCycle && (nextDue - today) <= 5 * 86400000;
    const dueText = dueSoon ? ` Pay app due to ${j.payer.name} by ${fmtDate(j.payer.payapp_due_next)}.` : "";
    const before = out.length;
    // One next step per job, in the job page's order (jobActions): blockers first.
    if (isBlocked(j)) add(3, j, statusPill(j), `${j.blockers[0]}${j.blockers.length > 1 ? ` And ${j.blockers.length - 1} more.` : ""}`,
      `<button class="btn small" data-action="open-packet" data-job="${j.id}"${draft ? ` data-inv="${draft.id}"` : ""}>${blockerButton(j)}</button>`);
    else if (draft) add(1, j, tag("Ready to send", "green"), `${draft.number} for ${money(draft.amount_cents)} to ${j.payer.approver || j.payer.name}. Check what goes out, then approve.`,
      `<button class="btn small" data-action="open-payapp" data-job="${j.id}" data-inv="${draft.id}" data-focus=".send-check">Review &amp; send</button>`, true);
    else if (j.waiver_due && !j.waiver_sent) add(2, j, tag("Waiver due", "amber"), "Paid in full: the customer is waiting for the lien waiver.",
      `<button class="btn small" data-action="open-waiver" data-id="${j.id}">Review waiver</button>`, true);
    else if (j.work_status === "complete" && j.money.unbilled_completed_cents > 0)
      add(5, j, tag("Not billed", "copper"), `${money(j.money.unbilled_completed_cents)} of finished work has no pay app yet. Drafting sends nothing.`,
        `<button class="btn small" data-action="draft" data-id="${j.id}">Draft pay app</button>`);
    else if (dueSoon) add(2.5, j, tag("Due soon", "amber"), `Record this month's progress and draft the pay app.`,
      `<button class="btn small" data-action="open-job" data-id="${j.id}" data-focus=".next-card">Bill progress</button>`, true);
    // A new job (R2-04): nothing billed on it yet, so Today says what comes next instead of nothing.
    else if (!billed(j)) add(5, j, tag("Not billed yet", "copper"), `${j.name} has no pay app yet. Bill the work done so far, or mark it done.`,
      `<button class="btn small" data-action="open-job" data-id="${j.id}" data-focus=".next-card">Bill progress</button>`, true);
    // A cutoff makes the job's row more urgent, and says so.
    if (dueSoon && out.length > before) { const row = out[out.length - 1]; row.text += row.text.includes("Pay app due to") ? "" : dueText; row.rank = Math.min(row.rank, 1.5); }
    // A pay app already sent and unpaid past the payer's follow-up window is its own row.
    const follow = followUpDue(j);
    if (follow) {
      const sent = follow.invoice;
      if (now.getTime() >= follow.due.getTime())
        add(4, j, tag("Follow up", "copper"), `${sent.number} was sent ${fmtDate(sent.sent_at)}${sent.last_follow_up_at ? `, last follow-up ${fmtDate(sent.last_follow_up_at)}` : ""}. Emails ${j.payer.ap_email || "the customer"}.`,
          `<button class="btn small" data-action="follow-up" data-id="${j.id}">Send follow-up</button>`);
    }
  });
  inbox.filter(i => !i.says_done_job_id && i.suggested_job_id).forEach(i => {
    const j = jobs.find(x => x.id === i.suggested_job_id && !x.closed);
    if (j) add(6, j, tag("Inbox"), `${(i.subject || i.text || "An item").split("\n")[0].slice(0, 100)}${i.match_reason ? ` <span class="sub">(${i.match_reason})</span>` : ""}`,
      `<button class="btn small" data-action="inbox-file-suggested" data-item="${i.id}" data-job="${j.id}">File to this job</button>`);
  });
  return out.sort((a, b) => a.rank - b.rank);
}

// The rows, three at a time until "and N more" is tapped (expanded; reset when the page changes).
// With nothing waiting, "Nothing is waiting on you." only once a pay app has gone out; before
// that, what will show up here.
export function queueHtml(queue, { expanded = false, anySent = false } = {}) {
  if (!queue.length) return anySent ? `<p class="sub">Nothing is waiting on you.</p>`
    : `<p class="sub">Work shows up here when a pay app is ready to send, paperwork is missing, a waiver or a follow-up is due, finished work isn't billed, or the Inbox has something for a job.</p>`;
  const rows = expanded ? queue : queue.slice(0, QUEUE_SHOWN);
  const more = queue.length - rows.length;
  return rows.map(q => `
          <div class="action-item">
            <div class="action-text">${q.pill} <strong>${q.j.name}</strong><div class="next-action">${q.text}</div></div>
            <div class="action-buttons">${q.button}${q.opensJob ? "" : `<button class="btn small secondary" data-action="open-job" data-id="${q.j.id}">Open</button>`}</div>
          </div>`).join("")
    + (more > 0 ? `<button class="linkish queue-more" data-action="queue-more" aria-expanded="false">and ${more} more</button>` : "");
}

// A pay app has gone out from this company: open jobs carry their pay apps; a closed job was billed.
export const anyPayAppSent = (state) => (state.jobs || []).some(j => (j.invoices || []).some(i => i.sent_at || WENT_OUT.includes(i.status)))
  || (state.closed_jobs_total || 0) > 0;
