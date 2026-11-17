// Round 2 job rules (Stephen, 10 Nov 2026): a change order without a price is a task for those who
// manage jobs (decision 2), Finish job with closeout documents missing asks for the reason
// (decision 4), and Review & send shows who has approved (decision 8).
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
const today = await import("../../static/js/today.js");
const { approvalHtml } = await import("../../static/js/views/payapps.js");
const { coAmountText } = await import("../../static/js/views/job-cos.js");
const { closeoutOverrideNote } = await import("../../static/js/views/job.js");
const { coAccruedHtml } = await import("../../static/js/views/changeorders.js");
const { approverPool } = await import("../../static/js/approvers.js");

const money = (over = {}) => ({ contract_cents: 450000, approved_co_cents: 0, contract_total_cents: 450000, billed_cents: 0,
  collected_cents: 0, outstanding_cents: 0, unbilled_completed_cents: 0, retainage_held_cents: 0, retainage_released_cents: 0,
  retainage_open_cents: 0, pending_co_cents: 0, ...over });
const unpriced = { id: "co-oak-2", number: 2, title: "Gate", status: "pending", amount_cents: 0, unpriced: true, needs: ["a price"],
  accrued: { hours_milli: 3500, entries: [{ at: "2026-11-12T15:00:00+00:00", description: "Posts set", hours_milli: 3500 }], record_ids: ["r-1"] } };
const job = (over = {}) => ({
  id: "j-1", code: "OAK", name: "Oak St", address: "1 Oak St", payer: { id: "p-1", name: "Northline GC", role: "GC", client_type: "gc" },
  payer_id: "p-1", closed: false, work_status: "in_progress", blockers: [], close_blockers: ["not done"], close_blocker_items: [], invoices: [],
  change_orders: [], payments: [], records: [{ id: "r-1", kind: "ticket", label: "Time sheet Nov 12" }], packet: [], waiver_due: false, waiver_sent: false,
  money: money(), started_on: "2026-11-01", retainage_pct: 5, ...over,
});

test("a change order without a price: a task for those who manage jobs, in the Dashboard row's words, not the main step", () => {
  const j = job({ change_orders: [unpriced] });
  const owner = jobs.jobPageActions(j, { perms: { owner: true } });
  const task = owner.tasks.find(t => t.key === "coprice");
  assert.equal(task.label, "Add the price to CO 2");
  assert.equal(task.label, jobs.coPriceLabel(unpriced));
  assert.match(task.attrs, /data-action="open-co" data-job="j-1" data-co="co-oak-2"/);
  assert.ok(task.allowed);
  assert.notEqual(jobs.jobActions(j, { perms: { owner: true } }).step.key, "coprice");
  const member = jobs.jobPageActions(j, { perms: { list: ["record_payments"] } });
  assert.equal(member.tasks.find(t => t.key === "coprice").allowed, false);
  // Priced, the task goes.
  assert.equal(jobs.jobPageActions(job({ change_orders: [{ ...unpriced, unpriced: false, needs: [], amount_cents: 20000 }] })).tasks.length, 0);
});

test("the Dashboard's row opens the change order", () => {
  const html = today.rowButtons({ kind: "co_info", job_id: "j-1", co_id: "co-oak-2", button: "Add the price to CO 2" }, { owner: true });
  assert.match(html, /data-action="open-co" data-job="j-1" data-co="co-oak-2">Add the price to CO 2</);
});

test("no price reads as such, and the hours gathered from job records show on its page", () => {
  assert.equal(coAmountText(unpriced), "No price yet");
  assert.equal(coAmountText({ amount_cents: 125000 }), "$1,250.00");
  const html = coAccruedHtml(job(), unpriced);
  assert.match(html, /From job records/);
  assert.match(html, /3\.5 h/);
  assert.match(html, /Posts set/);
  assert.match(html, /Time sheet Nov 12/);
  assert.equal(coAccruedHtml(job(), { ...unpriced, accrued: { hours_milli: 0, entries: [], record_ids: [] } }), "");
});

test("the change order page offers the price, not a signature, until it has one", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  const j = job({ change_orders: [unpriced] });
  core.setState({ company: { name: "Pine" }, jobs: [j], payers: [j.payer], inbox: [], email_log: [], files: [] });
  page.setLocation("co-detail", j.id, unpriced.id);
  const html = reg.views.coDetail();
  assert.match(html, /data-action="co-price"/);
  assert.doesNotMatch(html, /Request signature/);
  assert.match(html, /Waiting on<\/span><span>A price/);
  assert.doesNotMatch(html, /Revised contract sum/);
});

test("finished without closeout documents: who, when and why on the job file", () => {
  assert.equal(closeoutOverrideNote(job()), "");
  const note = closeoutOverrideNote(job({ closed: true, closeout_override: { by: "Sam", at: "2026-11-16T15:00:00+00:00", reason: "Owner has the binder", missing: ["As-builts"] } }));
  assert.match(note, /Finished without As-builts\. Override by Sam, .*: Owner has the binder/);
});

test("Finish job with closeout documents missing opens the reason form instead of finishing", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  const j = job({ work_status: "complete", close_blockers: [], closeout_missing: ["As-builts"] });
  core.setState({ company: { name: "Pine" }, jobs: [j], payers: [j.payer], inbox: [], email_log: [], files: [] });
  page.setLocation("job", j.id);
  let posted = false;
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => { posted = true; return { ok: true, status: 200, json: async () => ({}) }; };
  try {
    await reg.actions.close({ dataset: { id: j.id } }, j.id);
  } finally { globalThis.fetch = realFetch; }
  assert.equal(posted, false);
  assert.equal(page.formOpen.closeoverride, true);
  const html = reg.views.job();
  assert.match(html, /Finish without closeout documents/);
  assert.match(html, /Missing: As-builts\./);
  assert.match(html, /data-action="close-override"/);
  page.formOpen.closeoverride = false;
});

test("Review & send lists the approvers; Approve is for the one signed in, once it is ready to send", () => {
  const inv = { number: "PA-1", status: "awaiting_approval", job_id: "j-1",
    approval: { required: [{ id: "u-ana", name: "Ana Ruiz", approved_at: "2026-11-16T15:00:00+00:00" }, { id: "u-ben", name: "Ben Ode", approved_at: "" }], waiting: ["Ben Ode"] } };
  const forBen = approvalHtml(inv, "u-ben", true);
  assert.match(forBen, /Ana Ruiz<\/span><span>Approved /);
  assert.match(forBen, /Ben Ode<\/span><span><span class="pill amber">Waiting/);
  assert.match(forBen, /data-action="payapp-approve" data-id="j-1" data-inv="" data-stamp="">Approve PA-1</);
  // The version shown goes with the click, so a draft changed since isn't approved (B13).
  const shown = approvalHtml({ ...inv, id: "inv-1", approval: { ...inv.approval, stamp: "abc123" } }, "u-ben", true);
  assert.match(shown, /data-inv="inv-1" data-stamp="abc123">Approve PA-1</);
  assert.doesNotMatch(approvalHtml(inv, "u-ana", true), /payapp-approve/);
  assert.doesNotMatch(approvalHtml(inv, "u-ben", false), /payapp-approve/);
  assert.doesNotMatch(approvalHtml({ ...inv, status: "draft" }, "u-ben", true), /payapp-approve/);
  assert.equal(approvalHtml({ number: "PA-1", status: "awaiting_approval", approval: { required: [], waiting: [] } }, "u-ben", true), "");
});

test("only people whose profile approves pay apps can be named", () => {
  const team = { profiles: [{ id: "owner", perms: ["send_payapps", "approve_payapps"] }, { id: "member", perms: ["record_payments"] }, { id: "pm", perms: ["approve_payapps"] }],
    users: [{ id: "u-1", name: "Pat", profile_id: "owner" }, { id: "u-2", name: "Fred", profile_id: "member" }, { id: "u-3", name: "Pam", profile_id: "pm" }, { id: "u-4", email: "old@x.example", role: "owner" }] };
  assert.deepEqual(approverPool(team), [{ id: "u-1", name: "Pat" }, { id: "u-3", name: "Pam" }, { id: "u-4", name: "old@x.example" }]);
  assert.equal(jobs.mayTake("payapp-approve", { list: ["send_payapps"] }), false);
  assert.equal(jobs.mayTake("payapp-approve", { list: ["approve_payapps"] }), true);
  assert.equal(jobs.mayTake("co-price", { list: ["send_payapps"] }), false);
});
