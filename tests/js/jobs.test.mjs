// Where a job stands: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { recordFace, finalPayAppConfirm, jobStatus, isBlocked, needsPaperwork, blockerButton, nextStep, waiverKindLabel, coStatusWords, waiverSendForm, waiverHint, waiverTypeText, WAIVER_KINDS, WAIVER_HINTS, defaultJobId, billedWork, progressFigures, followUpDue, jobActions, retainageNote, waitingText, closeBlockerActions, mayTake, shortPayable, shortPayLabel, canPullBack, payAppList } from "../../static/js/jobs.js";
import { invoicePill } from "../../static/js/format.js";
import { setBillingStyle } from "../../static/js/terms.js";

const job = (over = {}) => ({
  id: "j1", closed: false, work_status: "in_progress", blockers: [], close_blockers: [], invoices: [],
  waiver_due: false, waiver_sent: false,
  money: { unbilled_completed_cents: 0, retainage_open_cents: 0, outstanding_cents: 0 },
  ...over,
});
const inv = (status) => ({ id: `i-${status}`, status });

test("a new job is in progress and asks for progress", () => {
  assert.deepEqual(jobStatus(job()), ["In progress", ""]);
  assert.match(nextStep(job()), /^Bill the work so far with Progress Pay App/);
});

// Whether a job is blocked, and by paperwork only, is the server's answer (domain.is_blocked,
// needs_paperwork, blocker_button; tests/test_today_rows.py): the app reads it off the job (R4-10).
test("blocked is the server's flag; before that the blockers are what the next pay app needs", () => {
  const waiting = job({ blockers: ["Signed contract"], blocked: false });
  assert.equal(isBlocked(waiting), false);
  assert.match(nextStep(waiting), /The next pay app needs: Signed contract/);
  const drafted = job({ blockers: ["Signed contract"], invoices: [inv("awaiting_approval")], blocked: true, blocker_button: "Clear 1 blocker" });
  assert.equal(isBlocked(drafted), true);
  assert.equal(isBlocked(job({ blockers: ["Signed contract"], invoices: [inv("draft")] })), false, "no flag, not blocked");
  assert.deepEqual(jobStatus(drafted), ["Blocked", "red"]);
  assert.equal(nextStep(drafted), "Clear 1 blocker before it can go: Signed contract");
});

test("missing paperwork alone is amber and says what to attach; an unsigned CO stays red", () => {  // R1-08
  const paper = job({ blockers: ["Signed contract is missing.", "Completion photos are missing."], missing_paperwork: ["Signed contract", "Completion photos"], invoices: [inv("draft")],
    blocked: true, needs_paperwork: true, blocker_button: "Add 2 missing documents" });
  assert.equal(needsPaperwork(paper), true);
  assert.deepEqual(jobStatus(paper), ["Needs supporting documents", "amber"]);
  assert.equal(nextStep(paper), "Add 2 missing documents before it can go: Signed contract, Completion photos");
  assert.equal(blockerButton(paper), "Add 2 missing documents");
  const co = job({ blockers: ["Signed contract is missing.", "Change Order 2 is included in the billing amount but has not been signed."], missing_paperwork: ["Signed contract"], invoices: [inv("draft")],
    blocked: true, needs_paperwork: false, blocker_button: "Clear 2 blockers" });
  assert.equal(needsPaperwork(co), false);
  assert.deepEqual(jobStatus(co), ["Blocked", "red"]);
  assert.equal(blockerButton(co), "Clear 2 blockers");
});

test("each stage of the money path has one status and one next step", () => {
  assert.deepEqual(jobStatus(job({ invoices: [inv("draft")] })), ["Ready to send", "copper"]);
  assert.deepEqual(jobStatus(job({ invoices: [inv("sent")] })), ["Sent", "amber"]);
  assert.match(nextStep(job({ invoices: [inv("sent")] })), /^Waiting on the client/);
  assert.deepEqual(jobStatus(job({ invoices: [inv("partial")] })), ["Partly paid", "amber"]);
  assert.deepEqual(jobStatus(job({ waiver_due: true })), ["Waiver due", "amber"]);
  const done = { work_status: "complete" };
  assert.deepEqual(jobStatus(job({ ...done, money: { unbilled_completed_cents: 500, retainage_open_cents: 0, outstanding_cents: 0 } })), ["Ready to bill", "copper"]);
  assert.deepEqual(jobStatus(job({ ...done, invoices: [inv("paid")], money: { unbilled_completed_cents: 0, retainage_open_cents: 100, outstanding_cents: 0 } })), ["Held back", "amber"]);
  const paid = job({ ...done, invoices: [inv("paid")] });
  assert.deepEqual(jobStatus(paid), ["Paid", "green"]);
  assert.equal(nextStep(paid), "Ready to finish the job. Finish Job.");
  assert.equal(nextStep(job({ ...done, invoices: [inv("paid")], close_blockers: ["Final waiver"] })), "Before you finish: Final waiver");
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

test("a waiver not sent yet reads Not chosen yet, and each type has its one-line hint (R6-10)", () => {
  assert.equal(waiverTypeText(job(), null), "Not chosen yet");
  assert.equal(waiverTypeText(job(), { waiver_kind: "conditional_final" }), "Conditional, final payment");
  assert.equal(waiverTypeText(job({ waiver_kind: "unconditional_progress" }), null), "Unconditional, progress payment");
  assert.deepEqual(Object.keys(WAIVER_HINTS), Object.keys(WAIVER_KINDS));
  for (const k of Object.keys(WAIVER_KINDS)) assert.ok(waiverHint(k).length > 10 && !waiverHint(k).includes("\n"), k);
  assert.equal(waiverHint("other"), "");
  // The send form shows the hint of the type it starts on, keyed like the picker.
  assert.match(waiverSendForm(job(), { id: "inv-4" }), /id="waiver-kind-inv-4"[\s\S]*data-waiver-hint="inv-4">You already have the money/);
});

test("forms start on the job last used, else the only open job (R2-07)", () => {
  const a = { id: "a" }, b = { id: "b" };
  assert.equal(defaultJobId([a]), "a");
  assert.equal(defaultJobId([a, b]), "");
  assert.equal(defaultJobId([a, b], "b"), "b");
  assert.equal(defaultJobId([a], "gone"), "a", "a closed or deleted job is not kept");
  assert.equal(defaultJobId([]), "");
});

// R3-01: the progress form's three ways in, and the pay app it drafts.
const billing = (over = {}) => job({
  retainage_pct: 10, change_orders: [],
  money: { contract_total_cents: 10_000_000, unbilled_completed_cents: 0, retainage_open_cents: 0, outstanding_cents: 0 },
  invoices: [
    { id: "a", status: "paid", amount_cents: 2_700_000, retainage_cents: 300_000, gross_cents: 3_000_000 },
    { id: "b", status: "void", amount_cents: 900_000, retainage_cents: 100_000, gross_cents: 1_000_000 },
    { id: "c", status: "draft", amount_cents: 900_000, retainage_cents: 100_000, gross_cents: 1_000_000 },
  ],
  ...over,
});

test("billed work counts gross on pay apps that count, not a void one or the open draft", () => {
  assert.equal(billedWork(billing()), 3_000_000);
  assert.equal(billedWork(job({ invoices: [{ status: "sent", amount_cents: 950, retainage_cents: 50 }] })), 1000);
});

test("this draw is added to what is billed, less retainage", () => {
  const f = progressFigures(billing(), "period", "$20,000");
  assert.equal(f.field, "period_cents");
  assert.equal(f.completed, 5_000_000);
  assert.equal(f.period, 2_000_000);
  assert.equal(f.retained, 200_000);
  assert.equal(f.net, 1_800_000);
});

test("total done so far and percent complete give the same pay app", () => {
  const total = progressFigures(billing(), "total", "50,000");
  const pct = progressFigures(billing(), "percent", "50");
  assert.equal(total.field, "completed_cents");
  assert.equal(total.period, 2_000_000);
  assert.equal(pct.completed, 5_000_000);
  assert.equal(pct.net, total.net);
});

test("the progress form refuses what the server would", () => {
  assert.match(progressFigures(billing(), "period", "").error, /this draw/);
  assert.match(progressFigures(billing(), "period", "80,000").error, /\$70,000\.00 left/);
  assert.match(progressFigures(billing(), "total", "30,000").error, /already billed/);
  assert.match(progressFigures(billing(), "percent", "120").error, /1 to 100/);
});

test("an unsigned change order on the bill rides on the same pay app", () => {
  const f = progressFigures(billing({ change_orders: [{ status: "pending", included_in_billing: true, amount_cents: 100_000 }] }), "period", "1,000");
  assert.equal(f.gross, 200_000);
  assert.equal(f.retained, 20_000);
});

// R3-03: the next follow-up is the customer's days after the last one, or after the send.
test("follow-up due date follows the customer's days", () => {
  const sent = job({ payer: { follow_up_days: 5 }, invoices: [{ status: "sent", sent_at: "2026-10-01T12:00:00+00:00" }] });
  assert.equal(followUpDue(sent).due.toISOString(), "2026-10-06T12:00:00.000Z");
  const again = job({ payer: {}, invoices: [{ status: "partial", sent_at: "2026-10-01T12:00:00+00:00", last_follow_up_at: "2026-10-10T12:00:00+00:00" }] });
  assert.equal(followUpDue(again).due.toISOString(), "2026-10-17T12:00:00.000Z");
  assert.equal(followUpDue(job({ invoices: [{ status: "paid", sent_at: "2026-10-01" }] })), null);
});

// The job page's buttons (R3-04): one main, the rest all shown in their groups.
const keys = (list) => list.map(a => a.key);

test("an in-progress job with a draft: Review & send, then the draft's two next steps", () => {
  const j = job({ invoices: [{ id: "d1", status: "draft" }], close_blockers: ["Work is not marked done."] });
  const { primary, more } = jobActions(j);
  assert.equal(primary.label, "Review & Send");
  assert.deepEqual(keys(more), ["conversation", "pencil", "packet", "progress", "done", "co", "void"]);
  assert.ok(more.every(a => ["bill", "paid", "close"].includes(a.group) && !("why" in a)));
  assert.deepEqual(more.filter(a => a.minor).map(a => a.label), ["Delete Draft"], "smaller, but shown");
  assert.equal(jobActions(job({ closed: true })).primary, null);
});

test("waiting on the customer: Record a Payment is the main button until the follow-up is due", () => {
  const sent = { id: "s1", status: "sent", number: "PA-1", sent_at: "2026-10-01T12:00:00+00:00" };
  const j = job({ payer: { approver: "Sam Jones", follow_up_days: "5" }, invoices: [sent] });
  const before = jobActions(j, { now: new Date("2026-10-03T12:00:00Z") });
  assert.equal(before.primary.label, "Record a Payment");
  assert.equal(before.more[0].label, "Follow Up Now");
  assert.match(before.more[0].attrs, /data-early="1"/);
  assert.match(waitingText(j, new Date("2026-10-03T12:00:00Z")), /^Waiting on Sam Jones\. Sent .+; follow-up due .+\.$/);
  // Once the date has passed the card says so, beside the main Send follow-up.
  assert.match(waitingText(j, new Date("2026-10-07T12:00:00Z")), /; a follow-up has been due since .+\.$/);
  const after = jobActions(j, { now: new Date("2026-10-07T12:00:00Z") });
  assert.equal(after.primary.label, "Send Follow-Up");
  assert.doesNotMatch(after.primary.attrs, /data-early/);
  assert.equal(after.more[0].label, "Record a Payment");
  assert.equal(waitingText(job()), "");
});

const jobOf = job;
test("Ask for retainage shows only once the work is done and the closeout paperwork is in", () => {
  const money = { unbilled_completed_cents: 0, retainage_open_cents: 50000, outstanding_cents: 0 };
  const job = (over) => jobOf({ close_blockers: ["$500.00 of retainage is still open."], ...over });
  const running = job({ money });
  assert.ok(!keys(jobActions(running).more).includes("retainage"));
  assert.equal(retainageNote(running), "Asked for after Final Pay App.");  // M24
  const missing = job({ money, work_status: "complete", closeout_packet: [{ required: true, present: false }], closeout_missing: ["Punch-list signoff"] });
  assert.ok(!keys([jobActions(missing).primary, ...jobActions(missing).more].filter(Boolean)).includes("retainage"));
  assert.equal(retainageNote(missing), "Closeout documents: 1 item missing.");
  const ready = job({ money, work_status: "complete", closeout_packet: [{ required: true, present: true }], closeout_missing: [] });
  assert.equal(jobActions(ready).primary.key, "retainage");
  assert.equal(retainageNote(ready), "");
});

// domain.chase_retainage waits the customer's days between contacts after the last request.
test("after a retainage request, Ask for retainage waits for the customer's days between contacts", () => {
  const money = { unbilled_completed_cents: 0, retainage_open_cents: 500000, outstanding_cents: 0 };
  const now = new Date("2026-10-29T15:00:00Z");
  const asked = jobOf({ money, work_status: "complete", closeout_missing: [], invoices: [inv("paid")],
    close_blockers: ["$5,000.00 of retainage is still open."], payer: { name: "ABC", follow_up_days: 7 },
    last_retainage_chase_at: "2026-10-28T15:00:00+00:00" });
  const a = jobActions(asked, { now });
  assert.ok(!keys([a.primary, ...a.more].filter(Boolean)).includes("retainage"), "the server would refuse it until Nov 4");
  assert.ok(keys([a.primary, ...a.more]).includes("retpay"), "recording it paid stays");
  assert.match(nextStep(asked, now), /^Asked for the \$[\d,.]+ held back Oct 28, 2026\. Ask again from Nov 4, 2026, or Record Retainage Paid\.$/);
  assert.match(retainageNote(asked, now), /Asked for it Oct 28, 2026; ask again from Nov 4, 2026\./);
  assert.deepEqual(closeBlockerActions(asked, { kind: "retainage" }, null, now).map(x => x.label), ["Record Retainage Paid"]);
  // From the due moment on, it is the main step again.
  const later = new Date("2026-11-04T15:00:00Z");
  assert.equal(jobActions(asked, { now: later }).primary.key, "retainage");
  assert.match(nextStep(asked, later), /^\$[\d,.]+ held back\. Closeout documents are in\. Ask for Retainage\.$/);
  assert.equal(retainageNote(asked, later), "");
  assert.deepEqual(closeBlockerActions(asked, { kind: "retainage" }, null, later).map(x => x.label), ["Record Retainage Paid", "Ask for Retainage"]);
  // Before the job is done nothing is asked for (M24), whatever an older request says.
  const part = jobOf({ money, payer: { follow_up_days: 7 }, last_retainage_chase_at: "2026-10-28T15:00:00+00:00" });
  assert.equal(retainageNote(part, now), "Asked for after Final Pay App.");
});

// domain.chase_retainage refuses a done job with closeout paperwork missing before it looks at the wait.
test("a done job asked for part of its retainage early still says the closeout paperwork is what holds it", () => {
  const money = { unbilled_completed_cents: 0, retainage_open_cents: 500000, outstanding_cents: 0 };
  const now = new Date("2026-10-29T15:00:00Z");
  const asked = jobOf({ money, work_status: "complete", closeout_missing: ["Punch-list signoff"], invoices: [inv("paid")],
    payer: { name: "ABC", follow_up_days: 7 }, last_retainage_chase_at: "2026-10-28T15:00:00+00:00" });
  assert.equal(nextStep(asked, now), "Closeout: missing Punch-list signoff. Open Closeout Documents.");
  assert.doesNotMatch(nextStep(asked, now), /Ask again from/);
  assert.equal(retainageNote(asked, now), "Closeout documents: 1 item missing.");
});

test("only an unsigned change order in the way: Get CO N signed opens it", () => {  // R3-05
  const text = "Change Order 2 is included in the billing amount but has not been signed.";
  const j = job({ blockers: [text], missing_paperwork: [], invoices: [{ id: "d1", status: "draft" }],
    blocker_items: [{ kind: "co", text, co_id: "co-x-2", number: 2 }], blocked: true, blocker_button: "Get CO 2 Signed" });
  assert.equal(blockerButton(j), "Get CO 2 Signed");
  const { primary } = jobActions(j);
  assert.equal(primary.label, "Get CO 2 Signed");
  assert.match(primary.attrs, /data-action="open-co" data-job="j1" data-co="co-x-2"/);
});

test("each thing standing between the job and Finish job has its buttons (R3-08)", () => {
  const done = jobOf({ work_status: "complete", closeout_packet: [{ required: true, present: false }], closeout_missing: ["Punch-list signoff"] });
  const labels = (b, j = done) => closeBlockerActions(j, b).map(a => a.label);
  assert.deepEqual(labels({ kind: "outstanding", text: "$500.00 is still owed on pay apps sent." }), ["Record a Payment"]);
  assert.deepEqual(labels({ kind: "co", text: "CO 2 is not signed yet.", co_id: "co-2", number: 2 }), ["Get CO 2 Signed", "Withdraw"]);
  assert.match(closeBlockerActions(done, { kind: "co", co_id: "co-2", number: 2 })[1].attrs, /data-action="withdraw-co".*data-co="co-2"/);
  assert.deepEqual(labels({ kind: "retainage" }), ["Record Retainage Paid", "Open Closeout Documents"]);
  assert.deepEqual(labels({ kind: "retainage" }, jobOf({ work_status: "complete" })), ["Record Retainage Paid", "Ask for Retainage"]);
  assert.deepEqual(labels({ kind: "waiver" }), ["Send Waiver"]);
  assert.deepEqual(labels({ kind: "draft", invoice_id: "i1", number: "PA-1" }), ["Review & Send", "Delete Draft"]);
  assert.deepEqual(labels({ kind: "unbilled" }), ["Draft Pay App"]);
  assert.deepEqual(labels({ kind: "something new" }), []);
});

test("a part-paid pay app offers to accept the short pay, in the close list too", () => {  // R4-01
  const partial = { id: "i2", number: "PA-02", status: "partial", amount_cents: 3000000, remaining_cents: 125000 };
  const j = job({ work_status: "complete", invoices: [{ id: "i1", status: "paid", remaining_cents: 0 }, partial] });
  assert.deepEqual(shortPayable(j), [partial]);
  assert.equal(shortPayLabel(partial), "Accept $1,250.00 short pay…");
  const actions = closeBlockerActions(j, { kind: "outstanding", text: "$1,250.00 is still owed on pay apps sent." });
  assert.deepEqual(actions.map(a => a.label), ["Record a Payment", "Accept $1,250.00 short pay…"]);
  assert.match(actions[1].attrs, /data-action="short-pay-open" data-job="j1" data-inv="i2"/);
  assert.deepEqual(shortPayable(job({ invoices: [{ id: "i3", status: "sent", remaining_cents: 5000 }] })), []);
});

test("undo done is offered only while the server says it can be", () => {  // R4-02
  const keys = (j) => { const { primary, more } = jobActions(j); return [primary, ...more].filter(Boolean).map(a => a.key); };
  assert.ok(keys(job({ work_status: "complete", can_undo_done: true, invoices: [inv("draft")] })).includes("undo"));
  assert.ok(!keys(job({ work_status: "complete", can_undo_done: false })).includes("undo"));
});

test("only the newest sent pay app with nothing paid can be pulled back", () => {  // R4-03
  const first = { id: "a", status: "sent" }, second = { id: "b", status: "sent" }, gone = { id: "c", status: "void" };
  const j = job({ invoices: [first, second, gone], payments: [] });
  assert.equal(canPullBack(j, second), true);
  assert.equal(canPullBack(j, first), false);
  assert.equal(canPullBack(job({ invoices: [first], payments: [{ invoice_id: "a" }] }), first), false);
  assert.equal(canPullBack(job({ invoices: [first], closed: true }), first), false);
  assert.equal(canPullBack(job({ invoices: [{ id: "d", status: "partial" }] }), { id: "d", status: "partial" }), false);
});

// R4-06: one follow-up for every unpaid pay app; a call noted with a date holds it until then.
test("follow-ups name every unpaid pay app, and a noted call waits for its date", () => {
  const a = { id: "a", number: "PA-1", status: "approved", sent_at: "2026-09-30T12:00:00+00:00" };
  const b = { id: "b", number: "PA-2", status: "sent", sent_at: "2026-10-20T12:00:00+00:00" };
  const j = job({ payer: { approver: "Sam Jones", follow_up_days: 7 }, invoices: [a, b] });
  const f = followUpDue(j);
  assert.equal(f.invoice, a, "the oldest, approved counts too");
  assert.equal(payAppList(f.invoices), "PA-1 and PA-2");
  assert.equal(payAppList([a, b, { number: "PA-3" }]), "PA-1, PA-2 and PA-3");
  assert.ok(jobActions(j, { now: new Date("2026-10-28T12:00:00Z") }).more.some(x => x.key === "conversation" && x.label === "Log a Conversation"));
  const called = job({ ...j, contact_noted_at: "2026-10-28T12:00:00+00:00", expected_payment_on: "2026-11-04",
    invoices: [{ ...a, last_follow_up_at: "2026-10-28T12:00:00+00:00" }, { ...b, last_follow_up_at: "2026-10-28T12:00:00+00:00" }] });
  assert.equal(followUpDue(called).day.getDate(), 4);
  assert.equal(followUpDue(called).noted, true);
  assert.match(waitingText(called, new Date("2026-10-29T12:00:00")), /^Waiting on Sam Jones\. Conversation .+; check again Nov 4, 2026\.$/);
  // A follow-up emailed since (later than the call) goes back to the usual days.
  const later = job({ ...called, invoices: [{ ...a, last_follow_up_at: "2026-10-29T12:00:00+00:00" }] });
  assert.equal(followUpDue(later).noted, undefined);
});

// R4-04: the final pay app's pill, and its waiver preselected as final.
test("a final pay app says Final and preselects a final waiver", () => {
  assert.match(invoicePill({ status: "sent", final: true }), /Final/);
  assert.doesNotMatch(invoicePill({ status: "void", final: true }), /Final/);
  assert.match(waiverSendForm(job(), { id: "inv-3", final: true }), /<option value="unconditional_final" selected>/);
});

test("a person sees only the job actions their profile allows (S6)", () => {
  const member = { owner: false, list: ["record_payments"] };
  assert.equal(mayTake("send", member), false);
  assert.equal(mayTake("draft", member), true);
  assert.equal(mayTake("match-payment", member), true);
  assert.equal(mayTake("send", { owner: false, list: ["send_payapps"] }), true);
  assert.equal(mayTake("close", { owner: false, list: ["send_payapps"] }), false);
  assert.equal(mayTake("close", { owner: true }), true, "a page from before profiles: the role");
  assert.equal(mayTake("close", { owner: false }), false);
  const j = job({ invoices: [{ id: "d1", status: "draft" }] });
  const all = (r) => keys([r.primary, ...r.more].filter(Boolean));
  assert.ok(all(jobActions(j)).includes("send"));
  const asMember = all(jobActions(j, { perms: member }));
  assert.ok(!asMember.includes("send") && !asMember.includes("void"), asMember.join());
  assert.ok(asMember.includes("pencil") && asMember.includes("packet"));
  assert.ok(all(jobActions(j, { perms: { owner: false, list: ["send_payapps"] } })).includes("send"));
  const draft = { kind: "draft", invoice_id: "d1", number: "PA-1" };
  assert.deepEqual(closeBlockerActions(j, draft, member).map(a => a.label), []);
  assert.deepEqual(closeBlockerActions(j, { kind: "waiver" }, member).map(a => a.label), []);
  assert.deepEqual(closeBlockerActions(j, { kind: "unbilled" }, member).map(a => a.label), ["Draft Pay App"]);
});

test("a change order's pill reads Signed or Unsigned, never Approved or Pending (glossary)", () => {
  assert.equal(coStatusWords("pending"), "Unsigned");
  assert.equal(coStatusWords("approved"), "Signed");
  assert.equal(coStatusWords("withdrawn"), "Withdrawn");
  assert.equal(coStatusWords("some_new"), "Some new");
});

// ---------- jobs-clients (Stephen's Oct 29 review) ----------
import { clientTypeLabel, CLIENT_TYPES, openJobs, groupRecords, recordKindWord, RECORD_GROUPS, RECORD_KINDS, packetChoices, packetText, paperworkChoices, startDateLimits } from "../../static/js/jobs.js";

test("the job page lists the blockers under the Next step line, so the line doesn't repeat the first (J6)", () => {
  const drafted = job({ blockers: ["Signed contract", "Daily tickets"], invoices: [inv("awaiting_approval")], blocked: true });
  assert.equal(nextStep(drafted), "Fix this before it can go: Signed contract");
  assert.equal(nextStep(drafted, new Date(), { listed: true }), "Fix this before it can go:");
  const paperwork = job({ blockers: ["Photos"], missing_paperwork: ["Photos"], needs_paperwork: true, blocked: true, invoices: [inv("awaiting_approval")] });
  assert.doesNotMatch(nextStep(paperwork, new Date(), { listed: true }), /Photos/);
});

test("client type: five kinds, and a client saved before types reads from its role (J2)", () => {
  assert.equal(CLIENT_TYPES.length, 5);
  assert.equal(clientTypeLabel({ client_type: "sub" }), "Another subcontractor (you are a lower tier)");
  assert.equal(clientTypeLabel({ role: "GC" }), "General contractor");
  assert.equal(clientTypeLabel({ role: "Owner" }), "Owner or developer");
  assert.equal(clientTypeLabel({ client_type: "public", role: "Owner" }), "Public agency");
});

test("a finished job leaves the working lists (J9)", () => {
  assert.deepEqual(openJobs([{ id: "a" }, { id: "b", closed: true }]).map(j => j.id), ["a"]);
  assert.deepEqual(openJobs(undefined), []);
});

test("the job records group by kind with counts, newest first; every kind has a group (J5)", () => {
  const recs = [{ id: 1, kind: "photo" }, { id: 2, kind: "ticket" }, { id: 3, kind: "photo" }, { id: 4, kind: "mystery" }, { id: 5, kind: "supplier_invoice" },
    { id: 6, kind: "ticket", ticket_type: "time_sheet" }, { id: 7, kind: "ticket", ticket_type: "delivery" }, { id: 8, kind: "inspection" }];
  const groups = groupRecords(recs);
  assert.deepEqual(groups.map(g => [g.key, g.records.length]), [["delivery", 1], ["photo", 2], ["time_sheets", 1], ["tickets", 1], ["invoices", 1], ["inspections", 1], ["other", 1]]);
  assert.deepEqual(groups[1].records.map(r => r.id), [3, 1]);
  assert.equal(recordKindWord({ kind: "ticket", ticket_type: "time_sheet" }), "Time sheet");
  assert.equal(recordKindWord({ kind: "ticket" }), "Delivery ticket or time sheet");
  const grouped = new Set(RECORD_GROUPS.flatMap(([, , kinds]) => kinds));
  assert.deepEqual(Object.keys(RECORD_KINDS).filter(k => !grouped.has(k)), []);
  assert.deepEqual(groupRecords([]), []);
});

test("the paperwork checkboxes are the server's catalog, save as the comma list it reads, and read back (S11)", () => {
  // As light_snapshot sends domain.PAPERWORK_CATALOG (escaped, like the rest of the state).
  const catalog = [
    { key: "contract", label: "Signed contract", closeout: false }, { key: "insurance", label: "Insurance certificate", closeout: false },
    { key: "ticket", label: "Daily tickets and time sheets", closeout: false }, { key: "punch", label: "Punch list sign-off", closeout: true },
  ];
  assert.deepEqual(paperworkChoices(catalog), [["Each pay app", ["Signed contract", "Insurance certificate", "Daily tickets and time sheets"]], ["At closeout", ["Punch list sign-off"]]]);
  assert.deepEqual(paperworkChoices(undefined), [["Each pay app", []], ["At closeout", []]]);
  const text = packetText(["Insurance certificate", "Signed contract", "Daily tickets and time sheets"], "Certified payroll; Gate pass", catalog);
  assert.equal(text, "Signed contract, Insurance certificate, Daily tickets and time sheets, Certified payroll, Gate pass");
  assert.deepEqual(packetChoices(["signed  contract", "Daily tickets and time sheets", "Certified payroll", "Gate pass"], catalog),
    { checked: ["Signed contract", "Daily tickets and time sheets"], other: "Certified payroll, Gate pass" });
  assert.equal(packetText([], "", catalog), "");
});

test("the start date box takes about 10 years back and 2 ahead (J8)", () => {
  const l = startDateLimits(new Date(2026, 9, 30));
  assert.deepEqual(l, { min: "2016-10-30", max: "2028-10-29", today: "2026-10-30" });
});

// The Next step line names the main button under it, in the same words (Nov 3 review, criteria 60, 61).
const fullMoney = (over = {}) => ({ unbilled_completed_cents: 0, retainage_open_cents: 0, outstanding_cents: 0, ...over });
const sent = { id: "s1", status: "sent", number: "PA-1", sent_at: "2026-10-01T12:00:00+00:00" };
const now = new Date("2026-10-03T12:00:00Z");
const cases = {
  "new job in progress": [job(), "Progress Pay App"],
  "draft ready": [job({ invoices: [{ id: "d", status: "awaiting_approval", number: "PA-1", amount_cents: 500000 }] }), "Review & Send"],
  "draft missing a document": [job({ blocked: true, needs_paperwork: true, blockers: ["Conditional waiver"], missing_paperwork: ["Conditional waiver"],
    blocker_button: "Add 1 missing document", invoices: [{ id: "d", status: "draft", number: "PA-1" }] }), "Add 1 missing document"],
  "unsigned change order": [job({ blocked: true, blockers: ["Change Order 2 ..."], blocker_items: [{ kind: "co", co_id: "c2", number: 2 }],
    blocker_button: "Get CO 2 signed", invoices: [{ id: "d", status: "draft", number: "PA-1" }] }), "Get CO 2 signed"],
  "draft out for client review": [job({ invoices: [{ id: "d", status: "awaiting_approval", number: "PA-1", pencil_status: "sent" }] }), "Client Agreed to the Draft"],
  "sent, follow-up not due": [job({ payer: { approver: "Sam", follow_up_days: "5" }, invoices: [sent] }), "Record a Payment"],
  "sent, follow-up due": [job({ payer: { approver: "Sam", follow_up_days: "1" }, invoices: [sent] }), "Send Follow-Up"],
  "paid, waiver due": [job({ waiver_due: true, invoices: [inv("paid")] }), "Choose Waiver and Send"],
  "finished work to bill, retainage held too": [job({ work_status: "complete", money: fullMoney({ unbilled_completed_cents: 5000, retainage_open_cents: 900 }) }), "Draft Pay App"],
  "retainage held, closeout documents missing": [job({ work_status: "complete", closeout_missing: ["Punch list"], close_blockers: ["x"], money: fullMoney({ retainage_open_cents: 900 }) }), "Open Closeout Documents"],
  "retainage held, closeout documents in": [job({ work_status: "complete", closeout_missing: [], close_blockers: ["x"], money: fullMoney({ retainage_open_cents: 900 }) }), "Ask for Retainage"],
  "no retainage, closeout documents missing": [job({ work_status: "complete", closeout_missing: ["Punch list"], money: fullMoney() }), "Open Closeout Documents"],
  "complete with nothing owed": [job({ work_status: "complete", money: fullMoney() }), "Finish Job"],
};
for (const [name, [j, label]] of Object.entries(cases)) {
  test(`next step names its main button: ${name}`, () => {
    assert.equal(jobActions(j, { now }).primary.label, label);
    assert.ok(nextStep(j, now).includes(label), nextStep(j, now));
  });
}

test("next step: a draft out for review isn't ready to send, and the wrong retainage and finish lines are gone", () => {
  const out = cases["draft out for client review"][0];
  assert.doesNotMatch(nextStep(out, now), /is ready|Review & Send/);
  assert.ok(jobActions(out, { now }).more.some(a => a.key === "send"), "send stays, second");
  assert.match(nextStep(cases["retainage held, closeout documents in"][0], now), /Closeout documents are in/);
  assert.doesNotMatch(nextStep(cases["no retainage, closeout documents missing"][0], now), /Ready to finish/);
  assert.match(nextStep(cases["no retainage, closeout documents missing"][0], now), /missing Punch list/);
  assert.ok(jobActions(cases["no retainage, closeout documents missing"][0], { now }).more.some(a => a.key === "close"), "it can still be finished, with a warning");
});

test("Final Pay App's confirm says the amount, what it does, and when it isn't the final one", () => {
  const j = { name: "Oak kitchen", sov_view: { present: true } };
  const p = { drafted: true, final: true, number: "PA-3", amount_cents: 4500000, gross_cents: 5000000, retainage_cents: 500000, replaces: "", pending_cos: [] };
  const yes = finalPayAppConfirm(j, p);
  assert.equal(yes.button, "Final Pay App");
  assert.match(yes.text, /^Final Pay App for Oak kitchen: \$45,000\.00 \(\$50,000\.00 less \$5,000\.00 retainage\)\. It marks the work finished and sets every schedule line to 100%\.$/);
  const no = finalPayAppConfirm(j, { ...p, final: false, pending_cos: [{ number: 2, amount_cents: 1000000 }] });
  assert.equal(no.button, "Draft Pay App");
  assert.match(no.text, /^This won't be the final pay app for Oak kitchen: CO 2 \(\$10,000\.00\) isn't signed or on the bill\./);
  assert.match(finalPayAppConfirm({ name: "A" }, { drafted: false }).text, /nothing left to bill/);
  setBillingStyle("draws");
  try { assert.equal(finalPayAppConfirm(j, p).button, "Final Draw"); } finally { setBillingStyle("pay_apps"); }
});

test("Next step: the wording after the acceptance test (criteria 59, 61)", () => {
  const base = { id: "j1", blocked: true, needs_paperwork: true, invoices: [{ id: "a", number: "PA-1", status: "draft", amount_cents: 100 }], money: { unbilled_completed_cents: 0, retainage_open_cents: 0 }, payer: { name: "Oak", ap_email: "a@b.co" } };
  // A missing document's button lands on it.
  const missing = job({ ...base, blockers: ["Conditional waiver is missing."], blocker_button: "Add 1 missing document", missing_paperwork: ["Conditional waiver"],
    blocker_items: [{ kind: "paperwork", text: "Conditional waiver is missing.", item_id: "p1", waiting: false }] });
  assert.equal(nextStep(missing), "Add 1 missing document before it can go: Conditional waiver");
  assert.match(jobActions(missing).primary.attrs, /data-action="open-packet" data-job="j1" data-inv="a" data-item="p1"/);
  // A file waiting for a person says to look and confirm.
  const waiting = job({ ...missing, blockers: ["Conditional waiver: look at scan.pdf and confirm it."], blocker_button: "Check 1 document",
    blocker_items: [{ kind: "paperwork", text: "Conditional waiver: look at scan.pdf and confirm it.", item_id: "p1", waiting: true }] });
  assert.equal(nextStep(waiting), "Conditional waiver: look at scan.pdf and confirm it.");
  assert.equal(nextStep(waiting, new Date(), { listed: true }), "Check 1 document before it can go:");
  // Several things, not all documents.
  const fix = job({ ...base, needs_paperwork: false, blockers: ["Change Order 2 is unsigned."], blocker_button: "Fix 3 things before sending", blocker_items: [] });
  assert.equal(nextStep(fix), "Fix 3 things before sending: Change Order 2 is unsigned.");
  // A draft to a client with no email names it.
  const draft = job({ ...base, blocked: false, needs_paperwork: false, blockers: [], payer: { name: "Oak", ap_email: "" }, invoices: [{ id: "a", number: "PA-1", status: "draft", amount_cents: 3600000 }] });
  assert.equal(nextStep(draft), "PA-1 for $36,000.00 to Oak is ready. Add the pay app email for Oak, then Review & Send.");
});

test("a note from before conversations were records reads and files as a conversation", () => {
  const old = { id: "r1", kind: "note", label: "Spoke to Sam", note: "Friday" };
  assert.deepEqual(recordFace(old), { ...old, kind: "conversation", label: "Conversation with Sam" });
  const other = { id: "r2", kind: "note", label: "Gate code", note: "1234" };
  assert.equal(recordFace(other), other);
  assert.deepEqual(groupRecords([old, other]).map(g => [g.key, g.records.length]), [["conversation", 1], ["other", 1]]);
});
