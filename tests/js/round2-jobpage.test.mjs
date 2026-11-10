// The job page's buttons (Yusuf, 10 Nov 2026): New Change Order, Progress Pay App, Final Pay App and
// Record a Payment side by side; a To do banner only while there is something to do now, each task
// its own button; no Next step line and no Bill / Get paid / Close out headings.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
globalThis.requestAnimationFrame = (f) => f();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");
const jobs = await import("../../static/js/jobs.js");
const terms = await import("../../static/js/terms.js");

const company = () => ({ company: { name: "Pine" }, jobs: [], payers: [], inbox: [], email_log: [], email_log_total: 0, files: [] });
const money = (over = {}) => ({ contract_cents: 450000, approved_co_cents: 0, contract_total_cents: 450000, billed_cents: 0,
  collected_cents: 0, outstanding_cents: 0, unbilled_completed_cents: 0, retainage_held_cents: 0, retainage_released_cents: 0,
  retainage_open_cents: 0, pending_co_cents: 0, ...over });
const job = (over = {}) => ({
  id: "j-1", code: "OAK", name: "Oak St", address: "1 Oak St", payer: { id: "p-1", name: "Northline GC", role: "GC", client_type: "gc" },
  payer_id: "p-1", closed: false, work_status: "in_progress", blockers: [], close_blockers: ["not done"], close_blocker_items: [], invoices: [],
  change_orders: [], payments: [], records: [], packet: [], waiver_due: false, waiver_sent: false, money: money(), started_on: "2026-11-01",
  retainage_pct: 5, ...over,
});
const owner = { name: "Pat", company_id: "co-p", role: "owner" };
const member = { name: "Fred", company_id: "co-p", role: "member", perms: ["record_payments"] };
function draw(j, me = owner) {
  page.setMe(me);
  core.setState({ ...company(), jobs: [j], payers: [j.payer] });
  page.setLocation("job", j.id);
  return reg.views.job();
}
const mainOf = (r) => r.main.map(a => [a.label, a.enabled]);

test("a new job: the four buttons in order, Record a Payment off until a pay app is out, and no banner", () => {
  const r = jobs.jobPageActions(job(), { perms: { owner: true } });
  assert.deepEqual(mainOf(r), [["New Change Order", true], ["Progress Pay App", true], ["Final Pay App", true], ["Record a Payment", false]]);
  assert.deepEqual(r.tasks, []);
  const html = draw(job());
  assert.match(html, /<div class="main-actions"><button class="btn main-action main-co" data-action="new-co">New Change Order<\/button><button class="btn main-action main-progress" data-action="toggle-form" data-form="progress">Progress Pay App<\/button><button class="btn main-action main-done" data-action="toggle-form" data-form="done">Final Pay App<\/button><button class="btn main-action main-match" disabled>Record a Payment<\/button><\/div>/);
  assert.doesNotMatch(html, /task-card|Next step|next-card|action-group|<h3>Bill<\/h3>|Get paid|Close out/);
  assert.match(html, /data-form="conversation">Log a conversation</, "the occasional ones stay on the page");
});

test("someone without the permission doesn't see Final Pay App; the rest stay", () => {
  const r = jobs.jobPageActions(job(), { perms: { list: ["record_payments"] } });
  assert.deepEqual(r.main.map(a => a.key), ["co", "progress", "match"]);
  assert.doesNotMatch(draw(job(), member), /data-form="done"/);
  const none = jobs.jobPageActions(job(), { perms: { list: [] } });
  assert.deepEqual(none.main.map(a => a.key), ["co", "progress"], "Record a Payment takes Record payments");
});

test("a draft is a task: Review & send with its number; Delete draft stays in the quiet row", () => {
  const j = job({ invoices: [{ id: "i1", number: "PA-1", status: "awaiting_approval", amount_cents: 100000, packet: [] }] });
  const r = jobs.jobPageActions(j, { perms: { owner: true } });
  assert.deepEqual(r.tasks.map(t => t.label), ["Review & send PA-1"]);
  assert.ok(r.others.some(a => a.key === "void"));
  const html = draw(j);
  assert.match(html, /<section class="task-card" aria-label="To do">[\s\S]*<button class="btn task-action" data-action="open-payapp" data-job="j-1" data-inv="i1" data-focus=".send-check">Review & send PA-1<\/button>/);
  // Someone who may not send sees the task, without a button.
  const m = jobs.jobPageActions(j, { perms: { list: ["record_payments"] } });
  assert.equal(m.tasks[0].allowed, false);
  assert.match(draw(j, member), /<span class="task-waiting">Review & send PA-1<\/span>/);
});

test("a sent pay app turns on Record a Payment; finished work turns off the two pay app buttons", () => {
  const sent = job({ invoices: [{ id: "i1", number: "PA-1", status: "sent", amount_cents: 100000, remaining_cents: 100000, sent_at: new Date().toISOString(), due_on: "2099-01-01" }] });
  assert.deepEqual(mainOf(jobs.jobPageActions(sent, { perms: { owner: true } })),
    [["New Change Order", true], ["Progress Pay App", true], ["Final Pay App", true], ["Record a Payment", true]]);
  const finished = job({ work_status: "complete", close_blockers: [] });
  const r = jobs.jobPageActions(finished, { perms: { owner: true } });
  assert.deepEqual(mainOf(r), [["New Change Order", true], ["Progress Pay App", false], ["Final Pay App", false], ["Record a Payment", false]]);
  assert.deepEqual(r.tasks.map(t => t.label), ["Finish job"], "nothing left: Finish job is the task");
  assert.ok(!r.others.some(a => a.key === "close"));
});

test("a blocked pay app: the banner lists what is missing, with the button that fixes it", () => {
  const j = job({ blocked: true, blockers: ["Signed contract"], blocker_button: "Add the missing documents", blocker_items: [{ item_id: "x1" }],
    invoices: [{ id: "i1", number: "PA-1", status: "awaiting_approval", amount_cents: 100000, packet: [] }] });
  const r = jobs.jobPageActions(j, { perms: { owner: true } });
  assert.deepEqual(r.blockers, ["Signed contract"]);
  assert.equal(r.tasks[0].key, "clear");
  assert.match(draw(j), /<section class="task-card is-blocked" aria-label="To do">\s*<ul class="blocker-list"><li>Signed contract<\/li><\/ul>/);
});

test("a company that says draw sees Progress Draw and Final Draw", () => {
  terms.setBillingStyle("draws");
  try {
    const r = jobs.jobPageActions(job(), { perms: { owner: true } });
    assert.deepEqual(r.main.map(a => a.label), ["New Change Order", "Progress Draw", "Final Draw", "Record a Payment"]);
  } finally { terms.setBillingStyle("pay_apps"); }
});

test("a finished job has none of it", () => {
  const r = jobs.jobPageActions(job({ closed: true }), { perms: { owner: true } });
  assert.deepEqual(r, { main: [], tasks: [], blockers: [], others: [] });
});
