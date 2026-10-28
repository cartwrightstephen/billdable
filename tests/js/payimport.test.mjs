// The Payments page's QuickBooks import (R5-03): node --test tests/js
// Picking a pay app on a row that needs one, splitting a payment larger than its pay app, and what
// stays on the page after Record.
import { test } from "node:test";
import assert from "node:assert/strict";
import { canRecord, needsPick, pickPayApp, splitTooMuch, picksFrom, afterRecord } from "../../static/js/payimport.js";

const cand = (n, owed = 100000) => ({ job_id: `j${n}`, job: `Job ${n}`, invoice_id: `inv-${n}`, number: `PA-J${n}-01`, owed_cents: owed });
const row = (over = {}) => ({ line: 2, date: "2026-10-14", ref: "4471", customer: "Ironleaf", amount_cents: 100000, memo: "",
  status: "match", reason: "", job_id: "j1", invoice_id: "inv-1", number: "PA-J1-01", job: "Job 1", candidates: [cand(1), cand(2)], ...over });

test("a pick-one row can be ticked once a pay app is picked", () => {
  const r = row({ status: "pick one", job_id: null, invoice_id: null, number: null });
  assert.equal(canRecord(r), false);
  assert.equal(needsPick(r), true);
  const picked = pickPayApp(r, "1");
  assert.equal(canRecord(picked), true);
  assert.equal(picked.invoice_id, "inv-2");
  assert.equal(picked.ticked, true);
  assert.equal(canRecord(pickPayApp(picked, "")), false);
});

test("a no-match row with no open pay apps has nothing to pick", () => {
  assert.equal(needsPick(row({ status: "no match", job_id: null, invoice_id: null, candidates: [] })), false);
  assert.equal(needsPick(row()), false, "a match keeps its pay app");
});

test("too much splits into what is owed and the rest", () => {
  const r = row({ status: "too much", job_id: null, invoice_id: null, number: null, amount_cents: 150000, split: cand(1, 100000) });
  const rows = splitTooMuch([r], 0);
  assert.equal(rows.length, 2);
  assert.deepEqual([rows[0].status, rows[0].amount_cents, rows[0].invoice_id], ["match", 100000, "inv-1"]);
  assert.deepEqual([rows[1].status, rows[1].amount_cents, rows[1].ref], ["no match", 50000, "4471 (rest)"]);
  assert.deepEqual(rows[1].candidates.map(c => c.invoice_id), ["inv-2"]);
  assert.match(rows[0].reason, /the rest, \$500\.00, is on the next line/);
});

test("only ticked rows with a pay app are sent, each with its row number", () => {
  const rows = [row(), row({ status: "no match", job_id: null, invoice_id: null }), row({ ref: "9", ticked: false })];
  assert.deepEqual(picksFrom(rows).map(p => [p.key, p.reference]), [[0, "4471"]]);
});

test("after Record: recorded rows go, a refused one stays with the reason", () => {
  const rows = [row(), row({ ref: "2", status: "pick one", job_id: null, invoice_id: null }), row({ ref: "3", invoice_id: "inv-2" }), row({ ref: "4" })];
  const out = afterRecord(rows, [{ key: 0, ok: true }, { key: 2, ok: false, error: "Use another reference." }, { key: 3, ok: true }]);
  assert.deepEqual(out.rows.map(r => [r.ref, r.status]), [["2", "pick one"], ["3", "not recorded"]]);
  assert.equal(out.rows[1].reason, "Use another reference.");
  assert.equal(out.toast, "2 recorded. 1 not recorded; the reason is on its line below. 1 needs a pay app picked below.");
});

test("the import closes once everything is recorded", () => {
  const out = afterRecord([row(), row({ ref: "5", status: "already recorded", job_id: null, invoice_id: null })], [{ key: 0, ok: true }]);
  assert.equal(out.rows, null);
  assert.equal(out.toast, "1 recorded.");
  assert.equal(afterRecord([row(), row({ ref: "2", status: "pick one", job_id: null })], [{ key: 0, ok: true }]).toast,
    "1 recorded. 1 needs a pay app picked below.");
});
