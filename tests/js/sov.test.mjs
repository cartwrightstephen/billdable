// Schedule of values arithmetic, the same as sov.py: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { sovMilli, sovCents, lumpDone, sovExtend, sovMarkup, fmtQty, sheetColumns, continuationHtml, qtyToDate, sovLineProblem, sovDraftLockHtml, sovFixedNote, sovPeriodHtml, sovProgressHtml, sovLostLines } from "../../static/js/sov.js";
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

test("a schedule fixed by a draft with the client says so and offers Delete draft", () => {
  const job = (present, lock) => ({ id: "job-1", sov_view: { present, editable: false, draft_lock: lock } });
  assert.equal(sovDraftLockHtml(job(true, null), true), "", "no such draft: no hint");
  const html = sovDraftLockHtml(job(true, { id: "inv-1", number: "PA-2" }), true);
  assert.match(html, /The schedule is fixed while PA-2 is with the client for review\. Record their answer or delete the draft to change it\./);
  assert.match(html, /<button class="btn small secondary" data-action="void-draft" data-id="job-1">Delete draft<\/button>/);
  assert.doesNotMatch(html, /style=|onclick=/);
  const member = sovDraftLockHtml(job(true, { id: "inv-1", number: "PA-2" }), false);
  assert.match(member, /Record their answer, or ask an owner to delete the draft, to change it\./);
  assert.doesNotMatch(member, /<button/, "only someone who may delete the draft gets the button");
  assert.match(sovDraftLockHtml(job(false, { id: "inv-1", number: "PA-1" }), true), /A schedule can't be set while PA-1 is with the client/);
});

test("what the card says when a schedule can't be set or changed: never nothing (Nov 3 Decision 2)", () => {
  const note = (v) => sovFixedNote({ id: "j", sov_view: { editable: false, ...v } }, true);
  assert.equal(sovFixedNote({ id: "j", sov_view: { editable: true } }, true), "", "it can be changed");
  assert.match(note({ present: false, sent: "PA-1" }), /This job bills in dollars: PA-1 was sent without a schedule of values, and a schedule can only be set before the first pay app is sent\./);
  assert.match(note({ present: false, sent: "" }), /This job bills in dollars\./);
  assert.match(note({ present: true, sent: "PA-1" }), /Fixed since PA-1 was sent\. Scope changes go through change orders/);
  assert.match(note({ present: true, sent: "" }), /work is finished/);
});

const periodLine = (extra) => ({ line_id: "l1", item: "1", description: "Rough-in", unit: "ea", lump_sum: false, qty_this_milli: 20000, from_records_milli: 12500,
  record_count: 2, adds_up_cents: 90000, suggested_cents: 90000, final_cents: 90000, changed: false, credit: false, room_cents: 1800000, ...extra });
const periodJob = (period) => ({ id: "j1", sov_view: { present: true, op_bp: 1500, period: { from: "", to: "", draft: null, lines: [], op_suggested_cents: 0, op_final_cents: 0,
  unsigned_cents: 0, retainage_pct: 10, suggested_gross_cents: 0, suggested_retainage_cents: 0, suggested_net_cents: 0, final_gross_cents: 0, final_retainage_cents: 0, final_net_cents: 0, changed: false, ...period } } });

test("the billing period shows each line's quantity, what it adds up to and the suggested value", () => {
  const html = sovPeriodHtml(periodJob({ from: "2026-10-31", lines: [periodLine({}), periodLine({ line_id: "l2", description: "Idle", qty_this_milli: 0, from_records_milli: 0, record_count: 0, suggested_cents: 0, adds_up_cents: 0 })],
    op_suggested_cents: 13500, suggested_gross_cents: 103500, suggested_retainage_cents: 10350, suggested_net_cents: 93150 }));
  assert.match(html, /Since Oct 31/);
  assert.match(html, /20 ea<div class="sub">12\.5 ea from 2 job records<\/div>/);
  assert.match(html, /\$900\.00/);
  assert.match(html, /Overhead and profit 15%/);
  assert.match(html, /Suggested pay app value<\/span><span>\$1,035\.00/);
  assert.match(html, /Less retainage 10%/);
  assert.match(html, /Suggested amount due<\/span><span>\$931\.50/);
  assert.doesNotMatch(html, /Idle/, "a line with nothing this period is left out until there is a draft to change");
  assert.doesNotMatch(html, /data-sova|style=|onclick=/);
  assert.match(sovPeriodHtml(periodJob({})), /No quantities recorded yet/);
  assert.equal(sovPeriodHtml({ sov_view: { present: false } }), "");
});

test("a lump sum's quantity is a percent", () => {
  const html = sovPeriodHtml(periodJob({ lines: [periodLine({ unit: "LS", lump_sum: true, qty_this_milli: 355, from_records_milli: 125, record_count: 1 })] }));
  assert.match(html, /35\.5%<div class="sub">12\.5% from 1 job record<\/div>/);
});

test("on a draft each line has a box for the amount, with the suggestion beside it", () => {
  const draft = { id: "inv-1", number: "PA-1", with_client: false };
  const period = { draft, lines: [periodLine({ final_cents: 75055, changed: true })], suggested_gross_cents: 90000, final_gross_cents: 75055, changed: true };
  const html = sovPeriodHtml(periodJob(period), true);
  assert.match(html, /data-form-key="sovamounts"/);
  assert.match(html, /<input id="sova-l1"[^>]* data-sova="l1"[^>]* value="750\.55"/);
  assert.match(html, /data-action="sov-amounts" data-id="j1">Save amounts</);
  assert.match(html, /data-action="sov-amounts-reset" data-id="j1">Back to the suggested amounts</);
  assert.match(html, /On PA-1<\/span><span>\$750\.55/);
  assert.match(html, /Suggested pay app value<\/span><span>\$900\.00/);
  // The progress form reads it only, and so does a draft with the client.
  assert.doesNotMatch(sovPeriodHtml(periodJob(period)), /data-sova/);
  assert.doesNotMatch(sovPeriodHtml(periodJob({ ...period, draft: { ...draft, with_client: true } }), true), /data-sova/);
  // A credit comes off as it is.
  assert.doesNotMatch(sovPeriodHtml(periodJob({ draft, lines: [periodLine({ credit: true })] }), true), /data-sova/);
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

test("the progress form starts from the draft's own typed figure, and a blank box is the suggestion", () => {
  const j = { id: "j1", invoices: [], sov_view: { lines: [
    { id: "l1", item: "1", description: "Tile", lump_sum: false, qty_milli: 100000, unit: "ea", done_milli: 50000, form_done_milli: 30000, sent_qty_milli: 0, billed_qty_milli: 30000, stored_cents: 0 },
    { id: "l2", item: "2", description: "All of it", lump_sum: false, qty_milli: 100000, unit: "ea", done_milli: 100000, form_done_milli: 100000, sent_qty_milli: 0, billed_qty_milli: 100000, stored_cents: 0 },
  ] } };
  const html = sovProgressHtml(j);
  assert.match(html, /id="sovp-done-l1"[^>]*value="30"/);
  // No explanatory paragraph (Round 1 acceptance, item 23: Stephen, no flavor text).
  assert.doesNotMatch(html, /Each line is filled in|Clear a box to bill what is suggested/);
  assert.doesNotMatch(html.match(/<input id="sovp-done-l2"[^>]*>/)[0], /disabled/, "only what went out closes a line");
});

test("replacing lines names what is recorded on the ones going", () => {
  const lines = [{ id: "a", to_date_cents: 500000 }, { id: "b", to_date_cents: 0 }, { id: "c", to_date_cents: 100 }, { id: "d", to_date_cents: 100, co_id: "co" }];
  assert.deepEqual(sovLostLines(lines, ["c"]).map(l => l.id), ["a"]);
});

test("a change order billed unsigned has its own row on the continuation sheet", () => {
  const inv = { amount_cents: 200000, retainage_pct: 0, sov_sheet: [{ item: "1", description: "Line", unit: "LS", scheduled_cents: 200000, amount_cents: 100000, to_date_cents: 100000, work_this_cents: 100000, prev_work_cents: 0 }],
    co_rows: [{ co_id: "c", number: 1, scheduled_cents: 100000, previous_cents: 0, this_cents: 100000, to_date_cents: 100000 }] };
  const html = continuationHtml(inv, {});
  assert.match(html, /CO 1<\/td><td class="num">\$1,000\.00<\/td><td class="num">\$0\.00<\/td><td class="num">\$1,000\.00/);
});
