// The To Do list (R2-04, R4-10, T3): node --test tests/js
// The rules are the server's (domain.today_rows; tests/test_today_rows.py). Here: the rows it sends
// become one main button each, Open where the button doesn't open the job, a table with the
// activity, job, ball in court, assignee and due date, three rows until "and N more", and the
// empty-state wording.
import { test } from "node:test";
import assert from "node:assert/strict";
import { todayQueue, rowButtons, todoHtml, assignSelect, anyPayAppSent, QUEUE_SHOWN } from "../../static/js/today.js";
import { todoItems } from "../../static/js/tasks.js";
import { RECORD_KINDS } from "../../static/js/jobs.js";

const row = (over = {}) => ({
  rank: 5, kind: "new_job", job_id: "j1", job: "Porch rebuild", payer: "Dana Lee", label: "Not billed yet", tone: "copper",
  text: "Porch rebuild has no pay app yet. Bill the work done so far, or mark it done.", note: "", button: "Bill progress",
  path: "/jobs/j1", invoice_id: "", co_id: "", email_id: "", item_id: "", key: "new_job:j1:", due: "2026-10-30", ball_in_court: "us",
  assignee_id: "", assignee_name: "", ...over,
});
// The rows as the To Do table draws them.
const queueHtml = (rows, opts = {}) => todoHtml(todoItems({ today_rows: rows }), { total: rows.length, ...opts });
const state = (rows, over = {}) => ({ jobs: [], today_rows: rows, closed_jobs_total: 0, ...over });
const mainButtons = (html) => (html.match(/class="btn small"/g) || []).length;

test("the queue is the server's rows, in the order sent", () => {
  const rows = [row({ kind: "ready", rank: 1 }), row({ rank: 5 })];
  assert.deepEqual(todayQueue(state(rows)), rows);
  assert.deepEqual(todayQueue({ jobs: [] }), []);
});

test("a new job shows Not billed yet; its button opens the job at its next step, so no Open", () => {
  const html = queueHtml([row()]);
  assert.match(html, /<td class="todo-activity"><span class="pill copper">Not billed yet<\/span><div class="next-action">Porch rebuild has no pay app yet\. Bill the work done so far, or mark it done\.<\/div><\/td>/);
  assert.match(html, /<a href="\/jobs\/j1" data-action="open-job" data-id="j1">Porch rebuild<\/a>/);
  assert.match(html, /<button class="btn small" data-action="open-job" data-id="j1" data-focus="\.next-card">Bill progress<\/button><\/div>/);
  assert.doesNotMatch(html, />Open</);
});

test("the To Do table has Activity, Job, Ball in court, Assignee and Due columns", () => {  // T3
  const html = todoHtml(todoItems({ today_rows: [row({ ball_in_court: "client", assignee_id: "u2", due: "2026-10-20" })] }),
    { people: [{ id: "u1", name: "Pat" }, { id: "u2", name: "Mo" }], today: "2026-10-28" });
  assert.match(html, /<tr><th>Activity<\/th><th>Job<\/th><th>Ball in court<\/th><th>Assignee<\/th><th>Due<\/th><th><\/th><\/tr>/);
  assert.match(html, /<span class="pill ball-client">Client<\/span>/);
  assert.match(html, /<option value="u2" selected>Mo<\/option>/);
  assert.match(html, /<tr class="action-item overdue">/);
  assert.match(html, /Oct 20, 2026 <span class="pill red">Overdue<\/span>/);
});

test("a task added by hand is ticked off with Done and opens its job", () => {  // G5
  const items = todoItems({ jobs: [{ id: "j1", name: "Porch rebuild" }], today_rows: [],
    tasks: [{ id: "task-1", title: "Order tile", job_id: "j1", status: "open", ball_in_court: "us", due: "" }] });
  const html = todoHtml(items);
  assert.match(html, /<strong>Order tile<\/strong>/);
  assert.match(html, /data-action="task-done" data-id="task-1">Done</);
  assert.match(html, /data-action="open-job" data-id="j1">Open</);
  assert.match(html, /<td class="todo-due">No date<\/td>/);
  const select = assignSelect(items[0], [{ id: "u1", name: "Pat" }]);
  assert.match(select, /data-todo-assign="task" data-key="task-1" aria-label="Assignee: Order tile, Porch rebuild"><option value="">Unassigned<\/option><option value="u1">Pat<\/option>/);
});

test("with items but none in the period, it says so", () => {
  assert.equal(todoHtml([], { total: 2 }), `<p class="sub">Nothing due in this period.</p>`);
});

test("each kind of row has its one main button, with the ids it needs", () => {
  const cases = [
    [row({ kind: "email_failed", button: "Send again", email_id: "e1" }), /data-action="email-retry" data-email="e1">Send again</, true],
    [row({ kind: "says_done", button: "Mark done and draft", item_id: "x1" }), /data-action="inbox-mark-done" data-item="x1">Mark done and draft</, true],
    [row({ kind: "blocked", button: "Add 1 missing document", invoice_id: "d1" }), /data-action="open-packet" data-job="j1" data-inv="d1">Add 1 missing document</, true],
    [row({ kind: "blocked", button: "Clear 1 blocker" }), /data-action="open-packet" data-job="j1">Clear 1 blocker</, true],
    [row({ kind: "ready", button: "Review &amp; send", invoice_id: "d1" }), /data-action="open-payapp" data-job="j1" data-inv="d1" data-focus="\.send-check">Review &amp; send</, false],
    [row({ kind: "waiver", button: "Review waiver" }), /data-action="open-waiver" data-id="j1">Review waiver</, false],
    [row({ kind: "not_billed", button: "Draft pay app" }), /data-action="draft" data-id="j1">Draft pay app</, true],
    [row({ kind: "due_soon", button: "Bill progress" }), /data-action="open-job" data-id="j1" data-focus="\.next-card">Bill progress</, false],
    [row({ kind: "follow_up", button: "Send follow-up" }), /data-action="follow-up" data-id="j1">Send follow-up</, true],
    [row({ kind: "inbox", button: "Add to this job", item_id: "x2" }), /data-action="inbox-file-suggested" data-item="x2" data-job="j1">Add to this job</, true],
  ];
  for (const [r, pattern, open] of cases) {
    const html = rowButtons(r);
    assert.match(html, pattern, r.kind);
    assert.equal(mainButtons(html), 1, `${r.kind}: one main button`);
    assert.equal(/data-action="open-job" data-id="j1">Open</.test(html), open, `${r.kind}: Open`);
  }
});

test("a follow-up offers Spoke to them as a second button", () => {  // R4-06
  const html = rowButtons(row({ kind: "follow_up", button: "Send follow-up" }));
  assert.match(html, /<button class="btn small secondary" data-action="spoke" data-id="j1">Spoke to them…<\/button>/);
});

test("a pay app the customer approved has no main button, only Open", () => {  // R4-08
  const html = rowButtons(row({ kind: "approved", button: "", label: "Approved", tone: "green" }));
  assert.equal(mainButtons(html), 0);
  assert.match(html, /data-action="open-job" data-id="j1">Open</);
});

test("an Inbox row says why it matched the job, after the text", () => {
  const html = queueHtml([row({ kind: "inbox", label: "Inbox", tone: "", text: "Signed CO 3", note: "names the job", button: "Add to this job" })]);
  assert.match(html, /<span class="pill ">Inbox<\/span>/);
  assert.match(html, /Signed CO 3 <span class="sub">\(names the job\)<\/span>/);
});

test("three rows, then 'and N more' until expanded", () => {
  const queue = Array.from({ length: 5 }, (_, n) => row({ job_id: `j${n}`, job: `Job ${n}` }));
  const short = queueHtml(queue);
  assert.equal(short.match(/class="action-item"/g).length, QUEUE_SHOWN);
  assert.match(short, /data-action="queue-more"[^>]*>and 2 more</);
  const all = queueHtml(queue, { expanded: true });
  assert.equal(all.match(/class="action-item"/g).length, 5);
  assert.doesNotMatch(all, /queue-more/);
  assert.doesNotMatch(queueHtml(queue.slice(0, 3)), /queue-more/);
});

test("'Nothing is waiting on you.' only once a pay app has gone out", () => {
  assert.equal(queueHtml([], { anySent: true }), `<p class="sub">Nothing is waiting on you.</p>`);
  assert.doesNotMatch(queueHtml([], { anySent: false }), /Nothing is waiting/);
  assert.match(queueHtml([], { anySent: false }), /Work shows up here/);
});

test("a pay app has gone out once one was sent or a job was closed", () => {
  const sent = { id: "j1", invoices: [{ id: "i1", number: "PA-1", status: "sent", sent_at: "2026-10-27T12:00:00+00:00" }] };
  assert.equal(anyPayAppSent(state([], { jobs: [sent] })), true);
  assert.equal(anyPayAppSent(state([], { jobs: [{ id: "j2", invoices: [{ id: "i2", status: "void" }] }] })), false);
  assert.equal(anyPayAppSent(state([], { closed_jobs_total: 2 })), true);
});

test("an Inbox row shows the kind it files as, in a select to change it first", () => {  // Oct 23 note #46
  const html = rowButtons(row({ kind: "inbox", button: "Add to this job", item_id: "x2", item_kind: "waiver" }));
  // "Add to this job as Lien waiver": the button, then the kind, then Open.
  assert.match(html, /data-item="x2" data-job="j1">Add to this job<\/button><label class="row-kind">as <select id="inb-kind-x2" aria-label="File as">/);
  assert.match(html, /<option value="waiver" selected>Lien waiver<\/option>/);
  assert.equal((html.match(/ selected>/g) || []).length, 1);
  assert.equal((html.match(/<option /g) || []).length, Object.keys(RECORD_KINDS).length, "the Inbox's kinds list");
  assert.ok(html.indexOf("inb-kind-x2") < html.indexOf(">Open<"));
  // A kind the list doesn't have starts at Note rather than showing one thing and filing another.
  assert.match(rowButtons(row({ kind: "inbox", button: "Add to this job", item_id: "x3", item_kind: "nope" })), /<option value="note" selected>Note<\/option>/);
  // Other rows have no kind select.
  assert.doesNotMatch(rowButtons(row({ kind: "says_done", button: "Mark done and draft", item_id: "x1" })), /<select/);
  assert.doesNotMatch(queueHtml([row()]), /<select id="inb-kind/);
});
