// Schedule of values arithmetic, the same as sov.py: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { sovMilli, sovCents, sovExtend, sovMarkup, fmtQty, sheetColumns, continuationHtml } from "../../static/js/sov.js";

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
