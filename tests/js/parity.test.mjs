// The app's copies of the server's rules say what the server says: node --test tests/js
// Run as a US Eastern viewer, where a day the server keeps from midnight UTC starts the evening before.
process.env.TZ = "America/New_York";

const { test } = await import("node:test");
const assert = (await import("node:assert/strict")).default;
const { followUpDue, jobActions, waitingText, retainageNote, closeBlockerActions } = await import("../../static/js/jobs.js");
const { followUpEvent } = await import("../../static/js/calendar.js");
const { billedThisMonth } = await import("../../static/js/today.js");
const { sovMilli, sovCents, sovBp, sovLineProblem } = await import("../../static/js/sov.js");
const { money } = await import("../../static/js/format.js");

const job = (over = {}) => ({
  id: "j1", code: "PINE", closed: false, work_status: "in_progress", blockers: [], close_blockers: [], invoices: [],
  waiver_due: false, waiver_sent: false, payer: { approver: "Sam", follow_up_days: 7 },
  money: { unbilled_completed_cents: 0, retainage_open_cents: 0, outstanding_cents: 0 },
  ...over,
});
const keys = (a) => [a.primary, ...a.more].filter(Boolean).map(x => x.key);

test("a noted check-again date is due from midnight UTC, as on the server, and shows as that day", () => {
  const j = job({ contact_noted_at: "2026-10-28T12:00:00+00:00", expected_payment_on: "2026-11-05",
    invoices: [{ id: "a", number: "PA-1", status: "sent", sent_at: "2026-10-01T12:00:00+00:00", last_follow_up_at: "2026-10-28T12:00:00+00:00" }] });
  const f = followUpDue(j);
  assert.equal(f.due.toISOString(), "2026-11-05T00:00:00.000Z");
  assert.match(waitingText(j, new Date("2026-11-04T12:00:00Z")), /check again Nov 5, 2026\.$/);
  const before = jobActions(j, { now: new Date("2026-11-04T12:00:00Z") });
  assert.ok(before.more.some(a => a.key === "follow" && a.why === "Before the follow-up date, Nov 5, 2026."));
  // 8 pm Nov 4 in New York is midnight UTC Nov 5: Today's row is up, and so is the job page's button.
  const evening = jobActions(j, { now: new Date("2026-11-05T01:00:00Z") });
  assert.equal(evening.primary.label, "Send follow-up");
});

test("a promised date shows the day after it", () => {
  const j = job({ invoices: [{ id: "a", number: "PA-1", status: "approved", sent_at: "2026-10-01T12:00:00+00:00", expected_payment_on: "2026-11-20" }] });
  const f = followUpDue(j);
  assert.equal(f.due.toISOString(), "2026-11-21T00:00:00.000Z");
  assert.ok(jobActions(j, { now: new Date("2026-11-01T12:00:00Z") }).more.some(a => a.why === "Before the follow-up date, Nov 21, 2026."));
});

test("the Calendar's follow-up is the date Today raises it", () => {
  const sent = { id: "a", number: "PA-1", status: "sent", sent_at: "2026-10-01T15:00:00+00:00" };
  // A follow-up went out Oct 8: the next is Oct 15, not overdue on Oct 10.
  const followed = job({ invoices: [{ ...sent, last_follow_up_at: "2026-10-08T15:00:00+00:00" }] });
  assert.deepEqual(followUpEvent(followed, new Date("2026-10-10T12:00:00Z")), { day: "2026-10-15", overdue: false });
  // After a call, the date they noted.
  const called = job({ contact_noted_at: "2026-10-10T15:00:00+00:00", expected_payment_on: "2026-10-25",
    invoices: [{ ...sent, last_follow_up_at: "2026-10-10T15:00:00+00:00" }] });
  assert.deepEqual(followUpEvent(called, new Date("2026-10-11T12:00:00Z")), { day: "2026-10-25", overdue: false });
  // Approved and part-paid pay apps are still chased; one event, on the oldest.
  const approved = job({ invoices: [{ ...sent, status: "approved" }, { ...sent, id: "b", status: "partial", sent_at: "2026-10-05T15:00:00+00:00" }] });
  assert.deepEqual(followUpEvent(approved, new Date("2026-10-10T12:00:00Z")), { day: "2026-10-08", overdue: true });
  assert.equal(followUpEvent(job({ invoices: [{ ...sent, status: "paid" }] })), null);
});

test("Billed this month leaves out pay apps pulled back, and goes by the UTC month like the helper", () => {
  const jobs = [job({ invoices: [
    { status: "void", sent_at: "2026-10-05T14:00:00+00:00", amount_cents: 1000000 },
    { status: "sent", sent_at: "2026-10-07T14:00:00+00:00", amount_cents: 950000 },
    { status: "draft", amount_cents: 1 },
    { status: "paid", sent_at: "2026-09-30T23:30:00+00:00", amount_cents: 5 },
  ] })];
  assert.equal(billedThisMonth(jobs, new Date("2026-10-20T12:00:00Z")), 950000);
  assert.equal(billedThisMonth(jobs, new Date("2026-09-30T23:45:00Z")), 5);
});

test("an older job's closeout items come from the server, so Ask for retainage waits for them", () => {
  const money_ = { unbilled_completed_cents: 0, retainage_open_cents: 760000, outstanding_cents: 0 };
  const j = job({ work_status: "complete", money: money_, closeout_packet: [], closeout_missing: ["Punch-list signoff"],
    close_blockers: ["Retainage"] });
  assert.ok(!keys(jobActions(j)).includes("retainage"));
  assert.equal(retainageNote(j), "Closeout paperwork: 1 item missing.");
  const labels = closeBlockerActions(j, { kind: "retainage" }).map(a => a.label);
  assert.deepEqual(labels, ["Record retainage paid", "Open closeout paperwork"]);
  assert.doesNotMatch(closeBlockerActions(j, { kind: "retainage" })[1].attrs, /data-inv="closeout"/, "no closeout packet to open");
  assert.equal(jobActions(job({ ...j, closeout_missing: [] })).primary.key, "retainage");
});

test("the schedule editor refuses the decimals the server refuses", () => {
  assert.ok(Number.isNaN(sovMilli("1.2345")));
  assert.ok(Number.isNaN(sovCents("12.345")));
  assert.ok(Number.isNaN(sovBp("15.555")));
  assert.equal(sovMilli("1.2340"), 1234, "trailing zeros are fine");
  assert.equal(sovCents("12.30"), 1230);
  assert.equal(sovBp("15.55%"), 1555);
  assert.equal(sovMilli("1.005"), 1005);
  assert.equal(sovLineProblem({ description: "Tile", quantity: "1.2345", rate: "10" }), "fix");
  assert.equal(sovLineProblem({ description: "Tile", quantity: "2", rate: "12.345" }), "fix");
});

test("money is US style whatever the browser's locale", () => {
  const original = Number.prototype.toLocaleString;
  Number.prototype.toLocaleString = function (locales, options) { return original.call(this, locales ?? "de-DE", options); };
  try {
    assert.equal(money(123450), "$1,234.50");
  } finally {
    Number.prototype.toLocaleString = original;
  }
});
