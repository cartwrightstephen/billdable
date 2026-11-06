// The Dashboard's To Do list (UI/UX Option B, T3): everything that needs a person, each with
// the one button that does it, its ball in court, assignee and due date (static/js/tasks.js
// merges these rows with the tasks people add and filters them by period). The rows come from the server (domain.today_rows, sent in
// /api/state as today_rows, R4-10), the same rows the daily email lists, so the rules live in one
// place; this file only draws them. Nothing that emails a pay app or a waiver goes out from here:
// those open the pay app's "Review & send" card, or the waiver page with its type, which show
// what leaves and to whom. A follow-up names its recipient on the row, and drafting or filing
// sends nothing. The page's state comes in as an argument, so node can test it
// (tests/js/today.test.mjs).

import { termText } from "./terms.js";
import { FILE_KINDS, RECORD_KINDS, fileKindPick } from "./jobs.js";
import { pathFor } from "./routes.js";
import { BALL_WORDS } from "./tasks.js";

const WENT_OUT = ["sent", "approved", "partial", "paid"];
export const QUEUE_SHOWN = 3;   // rows shown before "and N more"

// What each kind of row's main button does: its data attributes, and whether it opens the job
// itself (then the row has no separate Open button). The button's words come with the row.
const ACTIONS = {
  email_failed: (r) => `data-action="email-retry" data-email="${r.email_id}"`,
  says_done: (r) => `data-action="inbox-mark-done" data-item="${r.item_id}"`,
  // Only unsigned change orders hold it up (the row has a co_id): to the change order, as on the job page.
  blocked: (r) => r.co_id ? `data-action="open-co" data-job="${r.job_id}" data-co="${r.co_id}"` : `data-action="open-packet" data-job="${r.job_id}"${r.invoice_id ? ` data-inv="${r.invoice_id}"` : ""}${r.packet_item_id ? ` data-item="${r.packet_item_id}"` : ""}`,
  ready: (r) => `data-action="open-payapp" data-job="${r.job_id}" data-inv="${r.invoice_id}" data-focus=".send-check"`,
  waiver: (r) => `data-action="open-waiver" data-id="${r.job_id}"`,
  not_billed: (r) => `data-action="draft" data-id="${r.job_id}"`,
  due_soon: (r) => `data-action="open-job" data-id="${r.job_id}" data-focus=".next-card"`,
  new_job: (r) => `data-action="open-job" data-id="${r.job_id}" data-focus=".next-card"`,
  follow_up: (r) => `data-action="follow-up" data-id="${r.job_id}"`,
  inbox: (r) => `data-action="inbox-file-suggested" data-item="${r.item_id}" data-job="${r.job_id}"`,
  closeout: (r) => `data-action="open-job" data-id="${r.job_id}" data-focus=".next-card"`,
  retainage: (r) => `data-action="open-job" data-id="${r.job_id}" data-focus=".next-card"`,
  finish: (r) => `data-action="open-job" data-id="${r.job_id}" data-focus=".next-card"`,
  finish_held: (r) => `data-action="open-job" data-id="${r.job_id}" data-focus=".close-blockers"`,
  retainage_wait: (r) => `data-action="open-job" data-id="${r.job_id}" data-focus=".next-card"`,
};
const OPENS_JOB = ["ready", "waiver", "due_soon", "new_job", "closeout", "retainage", "finish", "finish_held", "retainage_wait"];

// The server's rows (already HTML-escaped on arrival, like the rest of state), most urgent first.
export function todayQueue(state) {
  return state.today_rows || [];
}

// An Inbox row files under the kind the Inbox guessed: the row shows it after its button
// ("Add to this job as Lien waiver") in the Inbox's own kind select, so it can be changed first
// and what is shown is what is filed. The select has the Inbox card's id, which the button reads.
export function inboxKindSelect(r) {
  const guessed = fileKindPick(r.item_kind, r.text);
  const options = FILE_KINDS.map(([k, v]) => `<option value="${k}"${k === guessed ? " selected" : ""}>${termText(v)}</option>`).join("");
  return `<label class="row-kind">as <select id="inb-kind-${r.item_id}" aria-label="File as">${options}</select></label>`;
}

// One row's buttons: the main one, a conversation for a follow-up (R4-06), and Open unless the
// main button already opens the job. A pay app the client approved (R4-08) has no main button.
export function rowButtons(r) {
  const attrs = ACTIONS[r.kind];
  const main = attrs && r.button ? `<button class="btn small" ${attrs(r)}>${r.button}</button>` + (r.kind === "inbox" ? inboxKindSelect(r) : "") : "";
  const talked = r.kind === "follow_up" ? `<button class="btn small secondary" data-action="log-conversation" data-id="${r.job_id}">Log a conversation</button>` : "";
  const open = OPENS_JOB.includes(r.kind) ? "" : `<button class="btn small secondary" data-action="open-job" data-id="${r.job_id}">Open</button>`;
  return main + talked + open;
}

// One To Do row's buttons: a row the jobs make keeps its own (rowButtons); a task added by hand
// is ticked off with Done, and opens its job when it has one.
export function itemButtons(i) {
  if (i.source === "row") return rowButtons(i.row);
  // A reminder fired in the app (My account > Notifications): Open reads it and goes there.
  if (i.source === "reminder") return `<button class="btn small" data-action="reminder-open" data-note="${i.key}" data-path="${i.path}">Open</button>`
    + `<button class="btn small secondary" data-action="reminder-read" data-note="${i.key}">Mark read</button>`;
  return `<button class="btn small" data-action="task-done" data-id="${i.key}">Done</button>`
    + (i.job_id ? `<button class="btn small secondary" data-action="open-job" data-id="${i.job_id}">Open</button>` : "");
}

// Who an item is assigned to: a select of the team, "Unassigned" first. Changing it saves.
export function assignSelect(i, people) {
  const label = `Assignee: ${i.source === "row" ? i.row.label : i.title}${i.job ? `, ${i.job}` : ""}`;
  return `<select class="todo-assign" data-todo-assign="${i.source}" data-key="${i.key}" aria-label="${label}">`
    + `<option value="">Unassigned</option>`
    + people.map(p => `<option value="${p.id}"${p.id === i.assignee_id ? " selected" : ""}>${p.name}</option>`).join("")
    + `</select>`;
}

// When an item is due, as a date box beside its assignee: changing it saves (a row the jobs make
// through /api/tasks/assign as its due_override; a task by its id). Cleared, a row goes back to
// its own date and a task has none.
export function dueInput(i) {
  const label = `Due date: ${i.source === "row" ? i.row.label : i.title}${i.job ? `, ${i.job}` : ""}`;
  return `<input type="date" class="todo-due-date" data-todo-due="${i.source}" data-key="${i.key}" value="${i.due || ""}" aria-label="${label}">`;
}

// The To Do card's table (T3): Activity, Job, Ball in court, Assignee, Due and the buttons, three
// rows until "and N more" is tapped (expanded; reset when the page changes). total is how many
// items there are before the period and Mine filters: with none at all, "Nothing is waiting on
// you." once a pay app has gone out, and before that what will show up here.
export function todoHtml(items, { expanded = false, anySent = false, total = items.length, people = [], today = "" } = {}) {
  if (!total) return anySent ? `<p class="sub">Nothing is waiting on you.</p>`
    : `<p class="sub">${termText("Work shows up here when a pay app is ready to send, supporting documents are missing, a waiver or a follow-up is due, finished work isn't billed, or the Inbox has something for a job.")}</p>`;
  if (!items.length) return `<p class="sub">Nothing due in this period.</p>`;
  const rows = expanded ? items : items.slice(0, QUEUE_SHOWN);
  // Who and when are worth a box only once the team has two people (round 1, item 8).
  const team = people.length > 1;
  const more = items.length - rows.length;
  const activity = (i) => i.source === "row"
    ? `<span class="pill ${i.row.tone || ""}">${i.row.label}</span><div class="next-action">${i.row.text}${i.row.note ? ` <span class="sub">(${i.row.note})</span>` : ""}</div>`
    : i.source === "reminder" ? `<span class="pill">Reminder</span><div class="next-action">${i.text}</div>`
    : `<strong>${i.title}</strong>`;
  return `<div class="table-wrap todo-wrap"><table class="todo-table">
          <tr><th>Activity</th><th>Job</th><th>Ball in court</th>${team ? "<th>Assignee</th><th>Due</th>" : ""}<th></th></tr>
          ${rows.map(i => {
            const late = i.due && today && i.due < today;
            return `<tr class="action-item${late ? " overdue" : ""}">
            <td class="todo-activity">${activity(i)}${late && !team ? ` <span class="pill red">Overdue</span>` : ""}</td>
            <td class="todo-job">${i.job_id ? `<a href="${pathFor("job", i.job_id)}" data-action="open-job" data-id="${i.job_id}">${i.job}</a>` : ""}</td>
            <td class="todo-ball"><span class="pill ball-${i.ball}">${BALL_WORDS[i.ball] || "Us"}</span></td>
            ${!team ? "" : i.source === "reminder" ? "<td></td><td></td>" : `<td>${assignSelect(i, people)}</td>
            <td class="todo-due">${dueInput(i)}${late ? ` <span class="pill red">Overdue</span>` : ""}</td>`}
            <td><div class="action-buttons">${itemButtons(i)}</div></td>
          </tr>`;
          }).join("")}
        </table></div>`
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
