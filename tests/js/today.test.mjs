// Today's queue (R2-04, R4-10): node --test tests/js
// The rules are the server's (domain.today_rows; tests/test_today_rows.py). Here: the rows it sends
// become one main button each, Open where the button doesn't open the job, three rows until
// "and N more", and the empty-state wording.
import { test } from "node:test";
import assert from "node:assert/strict";
import { todayQueue, rowButtons, queueHtml, anyPayAppSent, QUEUE_SHOWN } from "../../static/js/today.js";

const row = (over = {}) => ({
  rank: 5, kind: "new_job", job_id: "j1", job: "Porch rebuild", payer: "Dana Lee", label: "Not billed yet", tone: "copper",
  text: "Porch rebuild has no pay app yet. Bill the work done so far, or mark it done.", note: "", button: "Bill progress",
  path: "/jobs/j1", invoice_id: "", co_id: "", email_id: "", item_id: "", ...over,
});
const state = (rows, over = {}) => ({ jobs: [], today_rows: rows, closed_jobs_total: 0, ...over });
const mainButtons = (html) => (html.match(/class="btn small"/g) || []).length;

test("the queue is the server's rows, in the order sent", () => {
  const rows = [row({ kind: "ready", rank: 1 }), row({ rank: 5 })];
  assert.deepEqual(todayQueue(state(rows)), rows);
  assert.deepEqual(todayQueue({ jobs: [] }), []);
});

test("a new job shows Not billed yet; its button opens the job at its next step, so no Open", () => {
  const html = queueHtml([row()]);
  assert.match(html, /<span class="pill copper">Not billed yet<\/span> <strong>Porch rebuild<\/strong>/);
  assert.match(html, /<div class="next-action">Porch rebuild has no pay app yet\. Bill the work done so far, or mark it done\.<\/div>/);
  assert.match(html, /<button class="btn small" data-action="open-job" data-id="j1" data-focus="\.next-card">Bill progress<\/button><\/div>/);
  assert.doesNotMatch(html, />Open</);
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
