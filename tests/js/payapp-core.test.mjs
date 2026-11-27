// Pay-app core (Nov 27): the continuation sheet's retainage by line, the earlier pay apps card,
// the stored materials backup card and question, and the retainage release card.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
globalThis.requestAnimationFrame ||= (fn) => fn();
await import("../../static/js/app-core.js");
const { sheetColumns, continuationHtml } = await import("../../static/js/sov.js");
const core = await import("../../static/js/views/job-payapp-core.js");
const paperwork = await import("../../static/js/views/paperwork.js");
const { canPullBack } = await import("../../static/js/jobs.js");

const entry = (over = {}) => ({ line_id: "l1", item: "1", description: "Materials", unit: "ea", scheduled_cents: 500000, amount_cents: 100000,
  to_date_cents: 250000, prev_work_cents: 0, work_this_cents: 100000, stored_cents: 150000, qty_milli_to_date: 20000, qty_milli_this: 20000, ...over });

test("G703 columns: previous + this period + stored = to date, and a line's own retainage percent", () => {
  const c = sheetColumns(entry(), 10);
  assert.equal(c.previous + c.thisPeriod + c.stored, 250000);
  assert.equal(c.retainage, 25000);
  assert.equal(sheetColumns(entry({ retainage_pct: 0 }), 10).retainage, 0);
  assert.equal(sheetColumns(entry({ retainage_pct: 5 }), 10).retainage, 12500);
  const html = continuationHtml({ sov_sheet: [entry({ retainage_pct: 5 }), entry({ line_id: "l2", description: "Labor" })], retainage_pct: 0 }, {});
  assert.match(html, /<th>Retainage<\/th>/);
  assert.match(html, /each line's percent/);
});

test("earlier pay apps: listed with what was billed and paid, never pulled back", () => {
  const inv = { id: "h1", number: "PA-MID-03", imported: true, status: "paid", period_to: "2026-09-30", gross_cents: 400000, retainage_cents: 40000, amount_cents: 360000,
    history_files: [{ record_id: "r1", label: "PA 3.pdf", file_ids: ["f1"] }] };
  const j = { id: "j1", closed: false, invoices: [inv], payments: [{ invoice_id: "h1", amount_cents: 360000 }], earlier_payapps: { count: 1, can_enter: true, next_number: "PA-MID-04" }, sov_view: { present: true, lines: [] } };
  const html = core.earlierCardHtml(j);
  assert.match(html, /Earlier pay apps \(before Duracash\)/);
  assert.match(html, /PA-MID-03/);
  assert.match(html, /\$3,600\.00 billed · \$3,600\.00 paid/);
  assert.match(html, /href="\/api\/files\/f1"/);
  assert.match(html, /The next pay app here is PA-MID-04/);
  assert.equal(core.earlierCardHtml({ ...j, earlier_payapps: { count: 0, can_enter: true } }), "");
  assert.ok(!canPullBack({ ...j, invoices: [{ ...inv, status: "sent" }], payments: [] }, { ...inv, status: "sent" }));
  assert.match(core.importedNoteHtml(inv), /Billed before Duracash/);
});

test("stored materials: each line's backup, and Yes on the question opens their boxes", () => {
  const inv = { id: "d1", status: "draft", stored_backup: [{ line_id: "l1", name: "2. Materials", new_cents: 150000, stored_cents: 150000, records: [], ok: false, item_id: "pkt-1" }] };
  const html = core.storedBackupHtml({ id: "j1" }, inv);
  assert.match(html, /data-sov-line="l1"/);
  assert.match(html, /data-attach="pkt-1"/);
  assert.match(html, /No backup yet/);
  const asking = { id: "d1", status: "draft", packet: [{ id: "c", label: "Stored materials documents", required: true, present: false, gate: "soft", soft_question: "Any stored materials received this period to bill?" }] };
  assert.match(paperwork.softChecksHtml({ id: "j1" }, asking), /data-action="stored-enter"/);
  const backup = { id: "d1", status: "draft", packet: [{ ...asking.packet[0], covers: ["2. Materials"], soft_question: "Stored materials billed on 2. Materials: is each line's supplier invoice, receipt or photo checked, or not needed this time?" }] };
  const out = paperwork.softChecksHtml({ id: "j1" }, backup);
  assert.doesNotMatch(out, /stored-enter/);
  assert.match(out, /data-answer="checked">Checked</);
});

test("retainage release: the card offers it while retainage is held and no draft is open", () => {
  const j = { id: "j1", retainage_release: { held_cents: 125000, drafted_cents: 0, open_draft: false, final_ready: false, work_done: false } };
  assert.match(core.releaseCardHtml(j), /Bill retainage release/);
  assert.match(core.releaseCardHtml({ ...j, retainage_release: { ...j.retainage_release, open_draft: true } }), /Send or delete the open draft first/);
  assert.equal(core.releaseCardHtml({ ...j, retainage_release: { ...j.retainage_release, held_cents: 0 } }), "");
});
