// Today's queue (R2-04): node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { todayQueue, queueHtml, anyPayAppSent, QUEUE_SHOWN } from "../../static/js/today.js";

const NOW = new Date("2026-10-27T15:00:00");
const job = (over = {}) => ({
  id: "j1", name: "Porch rebuild", code: "PORCH", closed: false, work_status: "in_progress", blockers: [], close_blockers: [], invoices: [],
  waiver_due: false, waiver_sent: false, payer: { name: "Dana Lee", approver: "Dana Lee", ap_email: "dana@example.com" },
  money: { unbilled_completed_cents: 0, retainage_open_cents: 0, outstanding_cents: 0 },
  ...over,
});
const state = (jobs, over = {}) => ({ jobs, inbox: [], email_failures: [], closed_jobs_total: 0, ...over });

test("one new job shows Not billed yet, opening the job at its next step", () => {
  const [row, ...rest] = todayQueue(state([job()]), NOW);
  assert.equal(rest.length, 0);
  assert.equal(row.rank, 5);
  assert.match(row.pill, /Not billed yet/);
  assert.equal(row.text, "Porch rebuild has no pay app yet. Bill the work done so far, or mark it done.");
  assert.match(row.button, /data-action="open-job" data-id="j1" data-focus="\.next-card"/);
  assert.equal(row.opensJob, true);
});

test("a voided draft doesn't count as billed; a sent pay app does", () => {
  assert.equal(todayQueue(state([job({ invoices: [{ id: "i1", status: "void" }] })]), NOW).length, 1);
  const sent = job({ invoices: [{ id: "i1", number: "PA-1", status: "sent", sent_at: NOW.toISOString() }] });
  assert.deepEqual(todayQueue(state([sent]), NOW), []);
  assert.equal(anyPayAppSent(state([sent])), true);
  assert.equal(anyPayAppSent(state([job()])), false);
  assert.equal(anyPayAppSent(state([], { closed_jobs_total: 2 })), true);
});

test("a draft ready to send comes before an unbilled job", () => {
  const draft = job({ id: "j2", name: "Bath", invoices: [{ id: "i2", number: "PA-BATH-01", status: "draft", amount_cents: 100000 }] });
  const rows = todayQueue(state([job(), draft]), NOW);
  assert.deepEqual(rows.map(r => r.j.id), ["j2", "j1"]);
  assert.match(rows[0].pill, /Ready to send/);
});

test("closed jobs and summaries are left out", () => {
  assert.deepEqual(todayQueue(state([job({ closed: true }), job({ id: "j3", summary: true })]), NOW), []);
});

test("three rows, then 'and N more' until expanded", () => {
  const jobs = Array.from({ length: 5 }, (_, n) => job({ id: `j${n}`, name: `Job ${n}` }));
  const queue = todayQueue(state(jobs), NOW);
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

test("a follow-up row names every unpaid pay app and offers Spoke to them; a noted call hides it", () => {  // R4-06
  const invoices = [{ id: "a", number: "PA-1", status: "sent", sent_at: "2026-10-01T12:00:00+00:00" },
    { id: "b", number: "PA-2", status: "partial", sent_at: "2026-10-10T12:00:00+00:00" }];
  const [row] = todayQueue(state([job({ invoices })]), NOW);
  assert.match(row.pill, /Follow up/);
  assert.match(row.text, /^PA-1 and PA-2 are unpaid; the oldest was sent/);
  assert.match(row.button, /data-action="spoke" data-id="j1">Spoke to them…</);
  const noted = invoices.map(i => ({ ...i, last_follow_up_at: "2026-10-27T12:00:00+00:00" }));
  assert.deepEqual(todayQueue(state([job({ invoices: noted, contact_noted_at: "2026-10-27T12:00:00+00:00", expected_payment_on: "2026-10-30" })]), NOW), []);
  assert.equal(todayQueue(state([job({ invoices: noted, contact_noted_at: "2026-10-27T12:00:00+00:00", expected_payment_on: "2026-10-30" })]), new Date("2026-10-30T09:00:00")).length, 1);
});

test("a pay app approved on the customer's page shows the date to expect payment; no follow-up until after it", () => {  // R4-08
  const invoices = [{ id: "a", number: "PA-1", status: "approved", sent_at: "2026-10-01T12:00:00+00:00",
    approved_by_payer: "Kim Park", approved_at: "2026-10-20T12:00:00+00:00", expected_payment_on: "2026-11-05" }];
  const [row] = todayQueue(state([job({ invoices })]), NOW);
  assert.match(row.pill, /Approved/);
  assert.match(row.text, /^Approved by Kim Park, expect payment Nov 5, 2026 \(PA-1\)\.$/);
  assert.equal(row.button, "");
  // The day after the date given, the follow-up row is back.
  const [late] = todayQueue(state([job({ invoices })]), new Date("2026-11-06T09:00:00Z"));
  assert.match(late.pill, /Follow up/);
});
