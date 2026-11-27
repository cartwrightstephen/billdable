// Money hunt, Nov 27: Bill by dollars on a one-line schedule reads its line, not the whole job.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const jobs = await import("../../static/js/jobs.js");

const lump = (over = {}) => ({ id: "l1", item: "1", description: "Contract amount", lump_sum: true, unit: "LS", qty_milli: 1000, rate_cents: 1000000,
  scheduled_cents: 1000000, done_milli: 300, pct: 30, to_date_cents: 300000, billed_cents: 300000, billed_qty_milli: 300, sent_cents: 300000, ...over });
const job = (over = {}) => ({
  id: "j-1", code: "PEAC", name: "Peachtree", work_status: "in_progress", retainage_pct: 10, change_orders: [], payments: [],
  // PA-01 billed 30% of the line and a $1,000 change order still unsigned: $4,000 of work.
  invoices: [{ id: "i1", number: "PA-01", status: "sent", amount_cents: 360000, retainage_cents: 40000, gross_cents: 400000 }],
  money: { contract_total_cents: 1000000 },
  sov_view: { present: true, auto: true, lines: [lump()], op_bp: 0 },
  ...over,
});

test("a change order billed unsigned isn't counted as billed on the line", () => {
  const f = jobs.lumpDollars(job(), "period", "2,000");
  // $3,000 on the line before, $2,000 now: 50% done, not the 60% the job's $4,000 of work made it.
  assert.equal(f.error, "");
  assert.equal(f.done, "0.500");
  assert.equal(f.amount, "2000.00");
  assert.match(jobs.lumpDollars(job(), "period", "7,500").error, /\$7,000\.00 left/);
  assert.equal(jobs.lumpDollars(job(), "total", "10,000").amount, "7000.00");
});

test("the preview holds the line's own retainage, and the draft's percent for this period before it", () => {
  const own = job({ sov_view: { present: true, auto: true, lines: [lump({ retainage_pct: 5 })], op_bp: 0 } });
  assert.equal(jobs.lumpDollars(own, "period", "2,000").figures.retained, 10000);
  const period = job({ invoices: [...job().invoices, { id: "i2", number: "PA-02", status: "draft", amount_cents: 0, gross_cents: 0, retainage_pct_override: 0 }] });
  assert.equal(jobs.lumpDollars(period, "period", "2,000").figures.retained, 0);
  // A job billed in dollars (no schedule) holds the job's percent, as before.
  const dollars = job({ sov_view: undefined });
  assert.equal(jobs.progressFigures(dollars, "period", "2,000").retained, 20000);
});
