// New job record after the Nov 26 walkthrough: the time sheet's rows, Remove on a row before
// saving, a quantity past what a line has left asks first, and Edit opens the same form.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
globalThis.requestAnimationFrame ||= (fn) => fn();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const forms = await import("../../static/js/forms.js");
const reg = await import("../../static/js/views/index.js");
const daily = await import("../../static/js/views/daily.js");

const job = (over = {}) => ({ id: "j1", name: "Oak job", code: "OAK", payer_id: "p1", closed: false, work_status: "in_progress", invoices: [], payments: [],
  change_orders: [], records: [], blockers: [], close_blockers: [],
  money: { contract_cents: 1000000, unbilled_completed_cents: 0, retainage_open_cents: 0, outstanding_cents: 0, billed_cents: 0, received_cents: 0 }, ...over });
const company = (jobs = [job()], extra = {}) => ({ company: { name: "Pine" }, jobs, payers: [{ id: "p1", name: "Oak Builders" }],
  inbox: [], daily_records: [], files: [], email_log: [], email_log_total: 0, ...extra });

test("the time sheet: crew rows to entries, the total, and the count from the rows", () => {
  const users = [{ id: "u1", name: "Stephen" }];
  daily.daily.rows.crew = 3;
  const v = { ts_pick_0: "u1", ts_hours_0: "8", ts_pick_1: "__other", ts_name_1: "Jose", ts_hours_1: "4.5", ts_pick_2: "", ts_hours_2: "" };
  const rows = daily.crewRows(v, users);
  assert.deepEqual(rows, [{ name: "Stephen", hours: "8", member_id: "u1" }, { name: "Jose", hours: "4.5" }]);
  assert.equal(daily.crewTotal(rows), 12.5);
  assert.deepEqual(daily.recordEntries(v, [], null, users), [{ section: "time_sheets", row: 0, crew: rows }]);
  daily.daily.rows.crew = 1;
});

test("Remove takes a row off before saving; what was typed below it moves up", () => {
  forms.formDraft.daily = { delivery_supplier_0: "ABC", delivery_supplier_1: "Ferguson", delivery_what_1: "Sink", ts_name_0: "A", ts_hours_0: "1", ts_name_1: "B", ts_hours_1: "2" };
  daily.daily.open.add("delivery");
  daily.daily.rows.delivery = 2;
  daily.daily.files.delivery_1 = [{ name: "t.jpg" }];
  daily.dropRow("delivery", 0);
  assert.equal(daily.daily.rows.delivery, 1);
  assert.equal(forms.formDraft.daily.delivery_supplier_0, "Ferguson");
  assert.equal(forms.formDraft.daily.delivery_what_0, "Sink");
  assert.equal(forms.formDraft.daily.delivery_supplier_1, undefined);
  assert.deepEqual(daily.daily.files.delivery_0.map(f => f.name), ["t.jpg"], "its files move with it");
  daily.dropRow("delivery", 0);
  assert.ok(!daily.daily.open.has("delivery"), "the last row closes the section");
  daily.daily.rows.crew = 2;
  daily.dropRow("crew", 1);
  assert.deepEqual([forms.formDraft.daily.ts_name_0, forms.formDraft.daily.ts_name_1], ["A", undefined]);
  daily.daily.files = {};
  forms.clearForm("daily");
});

test("every row on the form has Remove, and the page draws it", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company());
  page.setLocation("daily");
  daily.daily.open.add("invoices");
  const html = reg.views.daily();
  assert.match(html, /data-action="daily-row-remove" data-kind="invoices" data-n="0"/);
  assert.match(html, /data-action="daily-row-remove" data-kind="crew" data-n="0"/);
  assert.match(html, /data-action="daily-more" data-kind="crew">Add a person</);
  assert.doesNotMatch(html, /Log a conversation/);
  daily.daily.open.clear();
});

test("more than a line has left asks, with Record anyway", () => {
  // The server refuses with over_schedule (domain.clean_daily_details); the page asks with these words.
  assert.match(daily.overScheduleText({ blockers: ["2 EA is more than the 1 EA left on Sink."] }),
    /^2 EA is more than the 1 EA left on Sink\. Only what is left is added to the schedule of values; the rest is kept on this record\. Record it anyway\?$/);
});

test("Edit on a diary entry fills the same form from what it saved", () => {
  const users = [];
  const sovJob = job({ sov_view: { present: true, lines: [{ id: "l1", item: "1", description: "Sink", unit: "EA", qty_milli: 2000, done_milli: 1000 }] },
    records: [
      { id: "rd", kind: "daily", label: "Diary entry 2026-11-26", daily_record_id: "dr1" },
      { id: "rt", kind: "ticket", ticket_type: "delivery", label: "Delivery ticket: ABC #7", note: "Toilet", daily_record_id: "dr1" },
      { id: "rs", kind: "ticket", ticket_type: "time_sheet", label: "Time sheet: 1 crew, 8 h", crew: [{ name: "Jose", hours_milli: 8000 }], daily_record_id: "dr1", file_ids: ["f1"] },
      { id: "rc", kind: "conversation", label: "Conversation with Sam", note: "Pays Friday Check again 2026-12-04.", daily_record_id: "dr1" },
    ] });
  core.setState(company([sovJob], { files: [{ id: "f1", name: "sheet.jpg" }] }));
  const dr = { id: "dr1", job_id: "j1", at: "2026-11-26T15:00:00+00:00", notes: "First day", materials: "Caulking",
    sections: { quantities: [{ line_id: "l1", qty_milli: 1000 }], punch: ["Patch wall"] } };
  const p = daily.editPrefill(sovJob, dr);
  assert.equal(p.draft.notes, "First day");
  assert.equal(p.draft.date, "2026-11-26");
  assert.equal(p.draft.qty_l1, "1");
  assert.equal(p.draft.punch, "Patch wall");
  assert.ok(p.checked.has("punch"));
  assert.deepEqual([p.draft.delivery_supplier_0, p.draft.delivery_number_0, p.draft.delivery_what_0], ["ABC", "7", "Toilet"]);
  assert.deepEqual([p.draft.ts_pick_0, p.draft.ts_name_0, p.draft.ts_hours_0], ["__other", "Jose", "8"]);
  assert.deepEqual([p.draft.conversation_text_0, p.draft.conversation_next_on_0], ["Pays Friday", "2026-12-04"]);
  assert.deepEqual(p.editing.recordIds, { delivery_0: "rt", time_sheets_0: "rs", conversation_0: "rc" });
  assert.deepEqual(p.editing.files, { time_sheets_0: ["sheet.jpg"] });
  // The rows go back with their record ids.
  daily.daily.rows.crew = 1;
  const entries = daily.recordEntries({ ...p.draft }, [], p.editing, users);
  assert.deepEqual(entries.map(e => [e.section, e.record_id]), [["delivery", "rt"], ["time_sheets", "rs"], ["conversation", "rc"]]);
  // Opened from the job page, the form says it is an edit and saves to that record.
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState({ ...company([sovJob], { files: [{ id: "f1", name: "sheet.jpg" }] }), daily_records: [dr] });
  reg.actions["daily-edit"]({ dataset: { item: "dr1" } }, "j1");
  page.setLocation("daily");
  const html = reg.views.daily();
  assert.match(html, /<h1>Edit job record<\/h1>/);
  assert.match(html, /Save changes/);
  assert.match(html, /✓ sheet\.jpg/);
  assert.match(html, /id="daily-delivery-supplier-0"[^>]*value="ABC"/);
  reg.actions["daily-edit-cancel"]();
  assert.equal(daily.daily.editing, null);
});

test("the Job records card: a diary entry's Edit opens its form; any other record keeps the label edit", async () => {
  const { recordCardHtml } = await import("../../static/js/views/job-record.js");
  core.setState(company([job()], { daily_records: [{ id: "dr1", job_id: "j1", at: "2026-11-26T12:00:00", notes: "x" }] }));
  const html = recordCardHtml(job({ records: [{ id: "rd", kind: "daily", label: "Diary entry 2026-11-26", daily_record_id: "dr1" }, { id: "rn", kind: "note", label: "Note" }] }));
  assert.match(html, /data-action="daily-edit" data-id="j1" data-item="dr1"/);
  assert.match(html, /data-action="record-edit" data-item="rn"/);
});

test("a record's label doesn't repeat the kind its pill says (Nov 27: \"Diary entry Diary entry\")", async () => {
  const rec = await import("../../static/js/views/job-record.js");
  assert.equal(rec.labelAfterKind("Diary entry 2026-11-27: Contract amount 40% done to date", "Diary entry"), "2026-11-27: Contract amount 40% done to date");
  assert.equal(rec.labelAfterKind("Delivery ticket: ABC #7", "Delivery ticket"), "ABC #7");
  assert.equal(rec.labelAfterKind("Diary entry", "Diary entry"), "Diary entry", "nothing else to say: kept");
  assert.equal(rec.labelAfterKind("Invoices for Oak", "Invoice"), "Invoices for Oak", "only the whole word");
  assert.equal(rec.labelAfterKind("COI 2026", "Insurance"), "COI 2026");
  const html = rec.recordCardHtml(job({ records: [{ id: "r1", kind: "daily", label: "Diary entry 2026-11-27", note: "Walked it", added_at: "2026-11-27T12:00:00+00:00" }] }));
  assert.doesNotMatch(html, /Diary entry<\/span> Diary entry/);
  assert.match(html, /2026-11-27/);
});
