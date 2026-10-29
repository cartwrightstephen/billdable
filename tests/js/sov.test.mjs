// Schedule of values arithmetic, the same as sov.py: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { sovMilli, sovCents, sovExtend, sovMarkup, fmtQty, sheetColumns, continuationHtml, sovLineProblem, sovDraftLockHtml } from "../../static/js/sov.js";

test("quantities in thousandths and money in cents, from what people type", () => {
  assert.equal(sovMilli("1,250.5"), 1250500);
  assert.equal(sovCents("$42.10"), 4210);
  assert.ok(Number.isNaN(sovMilli("lots")));
  assert.equal(fmtQty(1250500), "1,250.5");
});

test("extensions and markup round half up in whole cents", () => {
  assert.equal(sovExtend(2500, 1999), 4998, "2.5 x $19.99 = $49.975 -> $49.98");
  assert.equal(sovExtend(1000, 4850000), 4850000);
  assert.equal(sovMarkup(10000, 1250), 1250, "12.5% of $100");
  assert.equal(sovMarkup(333, 1000), 33);
});

test("continuation sheet columns", () => {
  const e = { scheduled_cents: 100000, to_date_cents: 60000, amount_cents: 20000, stored_cents: 5000 };
  assert.deepEqual(sheetColumns(e, 10), { previous: 35000, thisPeriod: 20000, stored: 5000, pct: 60, balance: 40000, retainage: 6000 });
  const sent = { ...e, work_this_cents: 15000, prev_work_cents: 40000 };
  assert.equal(sheetColumns(sent, 0).thisPeriod, 15000);
  assert.equal(sheetColumns(sent, 0).previous, 40000);
  assert.equal(sheetColumns({ scheduled_cents: 0, to_date_cents: 0, amount_cents: 0 }, 0).pct, 0);
});

test("a pay app without lines has no continuation sheet", () => {
  assert.equal(continuationHtml({ sov_lines: [] }, {}), "");
  const html = continuationHtml({ sov_lines: [{ item: "1", description: "Framing", unit: "LS", scheduled_cents: 100000, to_date_cents: 50000, amount_cents: 50000 }] }, { retainage_pct: 5 });
  assert.match(html, /Retainage 5%/);
  assert.match(html, /Framing/);
});

test("an editor line with no price says so, as the server refuses it (Oct 23 note 9)", () => {
  assert.equal(sovLineProblem({ description: "", quantity: "", rate: "" }), "", "a blank line is ignored");
  assert.equal(sovLineProblem({ description: "Site cleanup", quantity: "3", unit: "loads", rate: "" }), "price");
  assert.equal(sovLineProblem({ description: "Site cleanup", quantity: "3", unit: "loads", rate: "  " }), "price");
  assert.equal(sovLineProblem({ description: "Site cleanup", quantity: "3", unit: "loads", rate: "450" }), "");
  assert.equal(sovLineProblem({ description: "Panel", quantity: "", rate: "12500" }), "", "no quantity is a lump sum");
  assert.equal(sovLineProblem({ description: "", quantity: "3", rate: "" }), "fix", "no description comes first");
  assert.equal(sovLineProblem({ description: "X", quantity: "0", rate: "" }), "fix");
  assert.equal(sovLineProblem({ description: "X", quantity: "2", rate: "0" }), "fix");
});

test("a schedule fixed only by a draft says so and offers Delete draft (Oct 23 note 8)", () => {
  const job = (present, lock) => ({ id: "job-1", sov_view: { present, editable: false, draft_lock: lock } });
  assert.equal(sovDraftLockHtml(job(true, null), true), "", "a sent pay app: no hint");
  const html = sovDraftLockHtml(job(true, { id: "inv-1", number: "PA-2" }), true);
  assert.match(html, /The schedule is fixed while PA-2 is a draft\. Delete the draft to change it\./);
  assert.match(html, /<button class="btn small secondary" data-action="void-draft" data-id="job-1">Delete draft<\/button>/);
  assert.doesNotMatch(html, /style=|onclick=/);
  const member = sovDraftLockHtml(job(true, { id: "inv-1", number: "PA-2" }), false);
  assert.match(member, /Delete the draft to change it/);
  assert.doesNotMatch(member, /<button/, "only someone who may delete the draft gets the button");
  assert.equal(sovDraftLockHtml(job(false, { id: "inv-1", number: "PA-1" }), true), "", "no schedule: the card stays hidden");
});
