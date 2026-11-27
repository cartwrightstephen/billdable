// After the Nov 26 walkthrough: soft checks at Review & send and the job's hard stops and checks,
// Progress pay app on its own page with the pay period's quantities, and the Dashboard's reminders.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
globalThis.requestAnimationFrame ||= (fn) => fn();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");
const paperwork = await import("../../static/js/views/paperwork.js");
const jobPaperwork = await import("../../static/js/views/job-paperwork.js");
const { pathFor, routeFromPath } = await import("../../static/js/routes.js");
const { rowButtons } = await import("../../static/js/today.js");

const payer = { id: "p1", name: "Oak Builders", approver: "Sam", ap_email: "ap@oak.example" };
const job = (over = {}) => ({ id: "j1", name: "Oak job", code: "OAK", payer_id: "p1", payer, closed: false, work_status: "in_progress", invoices: [], payments: [],
  change_orders: [], records: [], blockers: [], close_blockers: [],
  money: { contract_cents: 1000000, contract_total_cents: 1000000, unbilled_completed_cents: 0, retainage_open_cents: 0, outstanding_cents: 0, billed_cents: 0, received_cents: 0 }, ...over });
const company = (jobs) => ({ company: { name: "Pine" }, jobs, payers: [payer], inbox: [], daily_records: [], files: [], email_log: [], email_log_total: 0 });

test("soft items are asked at Review & send; a hard one is what is missing", () => {
  const inv = { id: "d1", status: "draft", packet: [
    { id: "a", label: "Signed contract", required: true, present: false, gate: "hard", blocker: "Signed contract is missing." },
    { id: "b", label: "Progress photos", required: true, present: false, gate: "soft", soft_question: "Progress photos: checked, or not needed this time?" },
    { id: "c", label: "Stored materials documents", required: true, present: false, gate: "soft", soft_question: "Any stored materials received this period to bill?" },
  ] };
  const j = job({ invoices: [inv] });
  assert.deepEqual(inv.packet.filter(paperwork.isMissing).map(p => p.id), ["a"]);
  const html = paperwork.softChecksHtml(j, inv);
  assert.match(html, /Check before it goes \(2\)/);
  assert.match(html, /data-action="packet-soft-check" data-id="j1" data-item="b" data-answer="checked">Checked</);
  assert.match(html, /data-item="b" data-answer="not_needed">Not needed this time</);
  assert.match(html, /Any stored materials received this period to bill\?/);
  // Yes opens the stored materials boxes on the Progress pay app page (Nov 27, pay-app core).
  assert.match(html, /data-action="stored-enter" data-id="j1">Yes, enter them</);
  assert.match(html, /data-item="c" data-answer="not_needed">None this period</);
  assert.ok(paperwork.softWaiting(inv));
  inv.packet[1].soft_check = { answer: "checked", by: "Ana", at: "2026-11-27T12:00:00" };
  inv.packet[2].soft_check = { answer: "not_needed", by: "Ana", at: "2026-11-27T12:00:00" };
  assert.ok(!paperwork.softWaiting(inv));
  const answered = paperwork.softChecksHtml(j, inv);
  assert.match(answered, /Checked: Ana, Nov 27, 2026/);
  assert.match(answered, /No stored materials this period: Ana/);
  assert.match(answered, /data-answer="">Change</);
});

test("the job's supporting documents say which are hard stops and which are checks, and the form sets them", () => {
  const view = { own: false, labels: ["Signed contract", "Progress photos"], none: false, gates: [
    { key: "waiver", label: "Conditional waiver", gate: "hard", default: "hard" },
    { key: "contract", label: "Signed contract", gate: "hard", default: "hard" },
    { key: "photo", label: "Progress photos", gate: "soft", default: "soft" },
  ] };
  const rows = jobPaperwork.gateRowsHtml(view);
  assert.match(rows, /Signed contract<\/span> <span class="pill gate-hard">Required \(hard stop\)/);
  assert.match(rows, /Progress photos<\/span> <span class="pill gate-soft">Check \(soft\)/);
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState({ ...company([job()]), paperwork_catalog: [] });
  page.formOpen["jobpaperwork-j1"] = true;
  const html = jobPaperwork.jobPaperworkHtml(job({ paperwork_view: view }));
  page.formOpen["jobpaperwork-j1"] = false;
  assert.match(html, /id="jobpaperwork-j1-gate-photo"/);
  assert.match(html, /<option value="soft" selected>Check \(soft\)<\/option>/);
  assert.deepEqual(jobPaperwork.gateValues({ gate_photo: "hard", gate_waiver: "soft", packet_other: "x", gate_x: "maybe" }), { photo: "hard", waiver: "soft" });
  assert.match(html, /id="jobpaperwork-j1-gate-contract"/, "an owner can make a hard stop a check");
  // Without Send pay apps, a hard stop stays one here (the server refuses it too; Nov 27 audit);
  // a check can still be made a hard stop.
  page.setMe({ name: "Jo", company_id: "co-p", role: "member", perms: ["manage_jobs"] });
  page.formOpen["jobpaperwork-j1"] = true;
  const theirs = jobPaperwork.jobPaperworkHtml(job({ paperwork_view: view }));
  page.formOpen["jobpaperwork-j1"] = false;
  assert.doesNotMatch(theirs, /id="jobpaperwork-j1-gate-contract"/);
  assert.match(theirs, /Signed contract<\/span> <span class="pill gate-hard">Required \(hard stop\)<\/span> <span class="sub">Someone who can send pay apps can make it a check/);
  assert.match(theirs, /id="jobpaperwork-j1-gate-photo"/);
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
});

test("Progress pay app has its own page, with the pay period's quantities", async () => {
  assert.equal(pathFor("payapp-new", "j1"), "/money/new-pay-app/j1");
  assert.deepEqual(routeFromPath("/money/new-pay-app/j1"), { view: "payapp-new", jobId: "j1", detailId: null });
  const lines = [{ id: "l1", item: "1", description: "Sink", unit: "EA", qty_milli: 4000, done_milli: 3000, form_done_milli: 3000, billed_qty_milli: 0, lump_sum: false }];
  const j = job({ sov_view: { present: true, lines }, pay_period: { from: "2026-11-16", to: "2026-12-15", end_day: 15, after_milli: { l1: 1000 } }, payapp_due_on: "2026-12-15" });
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company([j]));
  page.setLocation("payapp-new", "j1");
  const html = reg.views["payapp-new"]();
  assert.match(html, /<h1>Progress pay app<\/h1>/);
  assert.match(html, /pay period Nov 16, 2026 to Dec 15, 2026/);
  assert.match(html, /id="sovp-done-l1"[^>]*value="2"/, "the 1 recorded after the period waits for the next pay app");
  assert.match(html, /Quantities recorded after Dec 15, 2026 wait for the next pay app/);
  assert.match(html, /id="sovprogress-period"[^>]*value="2026-12-15"|value="2026-12-15"[^>]*id="sovprogress-period"/, "Period to starts at the period's end");
  // The job page's Progress Pay App button opens it.
  const { jobPageActions } = await import("../../static/js/jobs.js");
  const progress = jobPageActions(job(), { perms: { owner: true } }).main.find(a => a.key === "progress");
  assert.equal(progress.attrs, 'data-action="progress-open" data-id="j1"');
});

test("the Dashboard's reminders: New job record, the change order's work, and Dismiss", () => {
  const noRecord = rowButtons({ kind: "no_record", job_id: "j1", key: "no_record:j1:2026-11-26", button: "New job record" });
  assert.match(noRecord, /data-action="daily-for-job" data-id="j1">New job record</);
  assert.match(noRecord, /data-action="dismiss-row" data-id="j1" data-key="no_record:j1:2026-11-26">Dismiss for today</);
  const co = rowButtons({ kind: "co_no_work", job_id: "j1", co_id: "co-1", key: "co_no_work:j1:co-1", button: "New job record" });
  assert.match(co, /data-action="daily-co-work" data-id="j1" data-co="co-1">New job record</);
  assert.match(co, />Dismiss</);
});
