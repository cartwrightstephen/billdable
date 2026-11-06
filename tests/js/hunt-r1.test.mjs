// Fixes from the independent hunt of Round 1 (Nov 6) and its acceptance test: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
globalThis.requestAnimationFrame = (f) => f();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");
const { rowButtons } = await import("../../static/js/today.js");
const paperwork = await import("../../static/js/views/paperwork.js");

test("a Job's done? row offers Final pay app only to someone who may mark work done", () => {
  const r = { kind: "says_done", job_id: "j1", item_id: "in-1", button: "Final pay app" };
  assert.match(rowButtons(r, { owner: true }), /data-action="inbox-mark-done"/);
  const member = rowButtons(r, { owner: false, list: ["record_payments"] });
  assert.doesNotMatch(member, /inbox-mark-done/);
  assert.match(member, /data-action="open-job"/, "the row still opens the job");
  assert.match(rowButtons(r, { owner: false, list: ["manage_jobs"] }), /inbox-mark-done/);
});

test("a photo on a waiver item takes one tap that says which waiver it is", () => {
  assert.equal(paperwork.waiverLookWords({ waiver_kind: "conditional_progress" }), "I looked at it: it's the conditional waiver for this pay app");
  assert.equal(paperwork.waiverLookWords({ waiver_kind: "unconditional_final" }), "I looked at it: it's the unconditional waiver for this pay app");
  assert.equal(paperwork.confirmedText({ confirmed_by: "Sam", confirmed_at: "2026-11-06T10:00:00+00:00", looked_as: "conditional" }),
    "Looked at by Sam on Nov 6, 2026: the conditional waiver");
  assert.match(paperwork.confirmedText({ confirmed_by: "Sam", confirmed_at: "2026-11-06T10:00:00+00:00", unread: "photo" }), /^Photo added by Sam/);
});

test("New job's retainage follows the saved client's own type, not a type left in the form", async () => {
  const jobs = await import("../../static/js/jobs.js");
  const owner = { name: "Dana Lee", client_type: "homeowner", retainage_default: 0 };
  assert.equal(jobs.newJobRetainage(owner, "gc"), 0, "a GC picked for another client, or read from a contract, doesn't count");
  assert.equal(jobs.newJobRetainage({ name: "Park GC", client_type: "gc", retainage_default: 10 }, "homeowner"), 10);
  assert.equal(jobs.newJobRetainage({ name: "Park GC", client_type: "gc", retainage_pct: 5, retainage_default: 5 }, "homeowner"), 5);
  assert.equal(jobs.newJobRetainage({ name: "Old", retainage_default: 10 }, "homeowner"), 0, "a client saved without a type takes the one picked");
});

test("a file the app put on an item says it was suggested from the job records, not added by duracash", () => {
  const t = paperwork.confirmedText({ confirmed_by: "duracash", confirmed_at: "2026-11-06T10:00:00+00:00", unread: "photo" });
  assert.equal(t, "Suggested from the job records on Nov 6, 2026");
  assert.doesNotMatch(t, /duracash/i);
});

test("Record retainage paid is offered once the retainage can be released, with what is held filled in", async () => {
  const jobs = await import("../../static/js/jobs.js");
  const money = { retainage_open_cents: 50000, retainage_released_cents: 0 };
  assert.equal(jobs.retainageReleasable({ work_status: "in_progress", money }), false, "not from the first pay app on");
  assert.equal(jobs.retainageReleasable({ work_status: "complete", money }), true);
  assert.equal(jobs.retainageReleasable({ work_status: "in_progress", retainage_chases: 1, money }), true, "asked for");
  assert.equal(jobs.retainageReleasable({ work_status: "in_progress", money: { ...money, retainage_released_cents: 100 } }), true, "some came in");
  assert.equal(jobs.retainageReleasable({ work_status: "complete", money: { retainage_open_cents: 0 } }), false);
  const src = (await import("node:fs")).readFileSync(new URL("../../static/js/views/job.js", import.meta.url), "utf8");
  assert.match(src, /id: "retpay-amt", label: "Retainage received \(\$\)", cents: j\.money\.retainage_open_cents/);
});

test("the schedule's summary says what only a draft holds is on the draft, and the numbers count schedule lines", async () => {
  const { sovSummary } = await import("../../static/js/views/job-sov.js");
  const v = (lines, over = {}) => ({ present: true, lines, totals: { scheduled_cents: 1000000 }, op_billed_cents: 0, op_sent_cents: 0, ...over });
  assert.equal(sovSummary(v([{ billed_cents: 250000, sent_cents: 0 }])), "1 line · 25% on the draft");
  assert.equal(sovSummary(v([{ billed_cents: 250000, sent_cents: 100000 }])), "1 line · 10% billed, 25% with the draft");
  assert.equal(sovSummary(v([{ billed_cents: 250000, sent_cents: 250000 }])), "1 line · 25% billed");
  const src = (await import("node:fs")).readFileSync(new URL("../../static/js/views/payapps.js", import.meta.url), "utf8");
  assert.match(src, /const lines = \(inv\.sov_lines \|\| \[\]\)\.length/);
});

test("closeout documents show once the work is done or one of them is in", async () => {
  const jobs = await import("../../static/js/jobs.js");
  const items = [{ id: "c1", required: true, present: false, attachments: [] }];
  assert.equal(jobs.closeoutShown({ work_status: "in_progress", closeout_packet: items }), false, "not from the first progress draft");
  assert.equal(jobs.closeoutShown({ work_status: "complete", closeout_packet: items }), true);
  assert.equal(jobs.closeoutShown({ work_status: "in_progress", closeout_packet: [{ ...items[0], attachments: [{ record_id: "r" }] }] }), true, "one filed");
  assert.equal(jobs.closeoutShown({ work_status: "complete", closeout_packet: [] }), false);
});
