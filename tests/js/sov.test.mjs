// Schedule of values arithmetic, the same as sov.py: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { sovMilli, sovCents, lumpDone, sovExtend, sovMarkup, fmtQty, sheetColumns, continuationHtml, qtyToDate, sovLineProblem, sovDraftLockHtml, sovProgressHtml } from "../../static/js/sov.js";
import { formDraft, clearForm } from "../../static/js/forms.js";

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
  assert.match(member, /The schedule is fixed while PA-2 is a draft\. An owner can delete the draft to change it\./);
  assert.doesNotMatch(member, /<button/, "only someone who may delete the draft gets the button");
});

test("a job with no schedule and only a draft says how to set one up (Oct 23 note 8)", () => {
  const job = (lock) => ({ id: "job-1", sov_view: { present: false, editable: false, draft_lock: lock } });
  assert.equal(sovDraftLockHtml(job(null), true), "", "a sent pay app: the card stays hidden");
  const owner = sovDraftLockHtml(job({ id: "inv-1", number: "PA-1" }), true);
  assert.match(owner, /To bill line by line, delete draft PA-1 first, then set up a schedule of values\./);
  assert.match(owner, /data-action="void-draft" data-id="job-1">Delete draft<\/button>/);
  assert.doesNotMatch(owner, /style=|onclick=/);
  const member = sovDraftLockHtml(job({ id: "inv-1", number: "PA-1" }), false);
  assert.match(member, /an owner has to delete draft PA-1 first\. Then you can set up a schedule of values\./);
  assert.doesNotMatch(member, /<button/);
});

// What was typed in the line-by-line progress form survives a redraw (the phone menu, another
// form opening, a failed save), as the dollars form's boxes do. Nothing typed: the saved values.
test("the progress form keeps what was typed on each line, and the note", () => {
  const j = { id: "j1", sov_view: { lines: [
    { id: "l1", item: "1", description: "Tile", lump_sum: false, qty_milli: 5000000, unit: "SF", done_milli: 1000000, stored_cents: 0, billed_qty_milli: 0 },
    { id: "l2", item: "2", description: "Demo", lump_sum: true, qty_milli: 1000, unit: "LS", done_milli: 250, stored_cents: 50000, billed_qty_milli: 0 },
  ] } };
  const saved = sovProgressHtml(j);
  assert.match(saved, /data-sovp="l1"[^>]* value="1000"/);
  assert.match(saved, /data-sovs="l2"[^>]* value="500.00"/);
  assert.match(saved, /data-form-key="sovprogress"/);
  formDraft.sovprogress = { done_l1: "2400", stored_l1: "1,200", done_l2: "60", stored_l2: "", notes: 'East "wall" <done>' };
  const typed = sovProgressHtml(j);
  assert.match(typed, /data-sovp="l1"[^>]* data-field="done_l1"[^>]* value="2400"/);
  assert.match(typed, /data-sovs="l1"[^>]* data-field="stored_l1"[^>]* value="1,200"/);
  assert.match(typed, /data-sovp="l2"[^>]* value="60"/);
  assert.match(typed, /data-sovs="l2"[^>]* value=""/);
  assert.match(typed, /id="progress-notes" data-field="notes" value="East &quot;wall&quot; &lt;done&gt;"/);
  clearForm("sovprogress");
});

test("a lump-sum percent goes to the server exactly, never rounded (99.95% is not 100%)", () => {
  assert.equal(lumpDone("99.9"), "0.999");
  assert.equal(lumpDone("50"), "0.500");
  assert.equal(lumpDone("100"), "1.000");
  assert.equal(lumpDone("12.0"), "0.120");
  assert.equal(lumpDone("99.95"), null, "a hundredth of a percent can't be held, so it is refused, not rounded up");
  assert.equal(lumpDone("33.33"), null);
  assert.equal(lumpDone("12.25"), null);
  assert.equal(lumpDone("lots"), "lots", "not a number: the server says what is wrong");
});

test("the continuation sheet has the quantity completed to date, blank for a lump sum (M2)", () => {
  const html = continuationHtml({ sov_sheet: [
    { item: "1", description: "Rough-in", unit: "ea", scheduled_cents: 1_800_000, to_date_cents: 810_000, amount_cents: 360_000, qty_milli_to_date: 180_000 },
    { item: "2", description: "Panel", unit: "LS", scheduled_cents: 1_250_000, to_date_cents: 625_000, amount_cents: 625_000, qty_milli_to_date: 500 },
  ] }, {});
  assert.match(html, /<th>Stored<\/th><th>Qty to date<\/th><th>Total to date<\/th>/);
  assert.match(html, /<td class="num">180 ea<\/td>/);
  assert.match(html, /Panel[\s\S]*?<td class="num"><\/td><td class="num">\$6,250\.00<\/td>/, "a lump sum has no quantity");
  assert.equal(qtyToDate({ unit: "ea" }), "", "an entry from before quantities were kept");
});
