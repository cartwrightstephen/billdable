// Where a job stands: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { jobStatus, isBlocked, needsPaperwork, blockerButton, nextStep, waiverKindLabel, waiverSendForm } from "../../static/js/jobs.js";

const job = (over = {}) => ({
  id: "j1", closed: false, work_status: "in_progress", blockers: [], close_blockers: [], invoices: [],
  waiver_due: false, waiver_sent: false,
  money: { unbilled_completed_cents: 0, retainage_open_cents: 0, outstanding_cents: 0 },
  ...over,
});
const inv = (status) => ({ id: `i-${status}`, status });

test("a new job is in progress and asks for progress", () => {
  assert.deepEqual(jobStatus(job()), ["In progress", ""]);
  assert.match(nextStep(job()), /^Record progress/);
});

test("blockers count only once there is a draft or the work is done", () => {
  const waiting = job({ blockers: ["Signed contract"] });
  assert.equal(isBlocked(waiting), false);
  assert.match(nextStep(waiting), /The next pay app needs: Signed contract/);
  const drafted = job({ blockers: ["Signed contract"], invoices: [inv("awaiting_approval")] });
  assert.equal(isBlocked(drafted), true);
  assert.deepEqual(jobStatus(drafted), ["Blocked", "red"]);
  assert.equal(nextStep(drafted), "Clear blockers: Signed contract");
});

test("missing paperwork alone is amber and says what to attach; an unsigned CO stays red", () => {  // R1-08
  const paper = job({ blockers: ["Signed contract is missing.", "Completion photos are missing."], missing_paperwork: ["Signed contract", "Completion photos"], invoices: [inv("draft")] });
  assert.equal(needsPaperwork(paper), true);
  assert.deepEqual(jobStatus(paper), ["Needs paperwork", "amber"]);
  assert.equal(nextStep(paper), "Attach the paperwork, then send: Signed contract, Completion photos");
  assert.equal(blockerButton(paper), "Add 2 missing documents");
  const co = job({ blockers: ["Signed contract is missing.", "Change Order 2 is included in the billing amount but has not been signed."], missing_paperwork: ["Signed contract"], invoices: [inv("draft")] });
  assert.equal(needsPaperwork(co), false);
  assert.deepEqual(jobStatus(co), ["Blocked", "red"]);
  assert.equal(blockerButton(co), "Clear 2 blockers");
});

test("each stage of the money path has one status and one next step", () => {
  assert.deepEqual(jobStatus(job({ invoices: [inv("draft")] })), ["Ready to send", "copper"]);
  assert.deepEqual(jobStatus(job({ invoices: [inv("sent")] })), ["Sent", "amber"]);
  assert.match(nextStep(job({ invoices: [inv("sent")] })), /^Waiting on the payer/);
  assert.deepEqual(jobStatus(job({ invoices: [inv("partial")] })), ["Partly paid", "amber"]);
  assert.deepEqual(jobStatus(job({ waiver_due: true })), ["Waiver due", "amber"]);
  const done = { work_status: "complete" };
  assert.deepEqual(jobStatus(job({ ...done, money: { unbilled_completed_cents: 500, retainage_open_cents: 0, outstanding_cents: 0 } })), ["Ready to bill", "copper"]);
  assert.deepEqual(jobStatus(job({ ...done, invoices: [inv("paid")], money: { unbilled_completed_cents: 0, retainage_open_cents: 100, outstanding_cents: 0 } })), ["Retainage open", "amber"]);
  const paid = job({ ...done, invoices: [inv("paid")] });
  assert.deepEqual(jobStatus(paid), ["Paid", "green"]);
  assert.equal(nextStep(paid), "Ready to close for the record.");
  assert.equal(nextStep(job({ ...done, invoices: [inv("paid")], close_blockers: ["Final waiver"] })), "Before closing: Final waiver");
  assert.deepEqual(jobStatus(job({ closed: true })), ["Closed", "green"]);
});

test("waiver kinds read as words, and the send form picks the likely one", () => {
  assert.equal(waiverKindLabel("conditional_final"), "Conditional, final payment");
  assert.equal(waiverKindLabel("other"), "other");
  assert.match(waiverSendForm(job(), null), /<option value="unconditional_progress" selected>/);
  const final = job({ work_status: "complete", invoices: [inv("paid")] });
  assert.match(waiverSendForm(final, { id: "inv-9" }), /<option value="unconditional_final" selected>[\s\S]*data-inv="inv-9"/);
  // Sending is off until the email is confirmed or the customer has an email (R1-05, R1-07).
  assert.doesNotMatch(waiverSendForm(job(), null), /disabled/);
  assert.match(waiverSendForm(job(), null, true), /data-action="waiver"[^>]* disabled>/);
});
