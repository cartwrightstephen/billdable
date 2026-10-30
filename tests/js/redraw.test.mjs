// What is typed in a box stays through a redraw (the phone menu opened and closed, an upload
// finishing) until it is saved: each box is a form field (data-field) that watchForms keeps.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const forms = await import("../../static/js/forms.js");
const today = await import("../../static/js/views/today.js");
const inbox = await import("../../static/js/views/inbox.js");
const reg = await import("../../static/js/views/index.js");

const job = (over = {}) => ({ id: "j1", name: "Oak job", code: "OAK", payer_id: "p1", closed: false, work_status: "in_progress", invoices: [], payments: [],
  change_orders: [], records: [{ id: "r1", kind: "note", label: "Rough-in passed", added_at: "2026-10-20T12:00:00" }], blockers: [], close_blockers: [],
  money: { contract_cents: 1000000, unbilled_completed_cents: 0, retainage_open_cents: 0, outstanding_cents: 0, billed_cents: 0, received_cents: 0 }, ...over });
const company = (jobs = [job()]) => ({ company: { name: "Pine" }, jobs, payers: [{ id: "p1", name: "Oak Builders", follow_up_days: 7, usual_approval_days: 14 }],
  inbox: [], daily_records: [], files: [], email_log: [], email_log_total: 0 });
const ok = (path) => (p) => (p === path ? { status: 200, body: company() } : undefined);

test("Daily record: the note typed stays through a redraw and goes once saved", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  page.setLocation("daily");
  core.setState(company());
  assert.match(reg.views.daily(), /data-form-key="daily"[\s\S]*<textarea id="daily-notes"[^>]*data-field="notes"/);
  forms.formDraft.daily = { notes: "Framing done <east> wall" };
  assert.match(reg.views.daily(), /data-field="notes"[^>]*>Framing done &lt;east&gt; wall<\/textarea>/);
  dom.bySelector["#daily-notes"] = { value: "Framing done <east> wall" };
  dom.bySelector["#daily-job"] = { value: "j1" };
  dom.answers = [ok("/api/jobs/j1/daily-record")];
  await reg.actions["daily-save"]();
  assert.equal(forms.formDraft.daily, undefined);
  assert.match(reg.views.daily(), /data-field="notes"[^>]*><\/textarea>/);
  delete dom.bySelector["#daily-notes"]; delete dom.bySelector["#daily-job"];
});

test("Daily record: crew, weather and the ticked sections stay through a redraw; the body sends them", async () => {  // T11, T12
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  const sovJob = job({ sov_view: { present: true, lines: [
    { id: "l1", item: "1", description: "Rough-in wiring", unit: "LF", qty_milli: 400000, done_milli: 100000 },
    { id: "l2", item: "2", description: "Panel", unit: "LS", qty_milli: 1000, done_milli: 1000 },
  ] }, change_orders: [{ id: "co1", number: 2, title: "Extra outlets", status: "approved" }] });
  core.setState(company([sovJob]));
  page.setLocation("daily");
  const daily = await import("../../static/js/views/daily.js");
  forms.formDraft.daily = { crew_count: "4", weather: "Rain", materials: "Wire <12 boxes>", qty_l1: "120" };
  daily.daily.checked.add("quantities");
  daily.daily.checked.add("co_work");
  const html = reg.views.daily();
  assert.match(html, /id="daily-crew"[^>]*type="number" value="4"/);
  assert.match(html, /<option value="Rain" selected>Rain<\/option>/);
  assert.match(html, /data-field="materials"[^>]*>Wire &lt;12 boxes&gt;<\/textarea>/);
  assert.match(html, /data-daily-section="quantities" checked/);
  assert.match(html, /data-field="qty_l1" value="120"/);
  assert.doesNotMatch(html, /data-field="qty_l2"/, "a finished line takes no more");
  assert.match(html, /<option value="co1">CO 2 Extra outlets<\/option>/);
  assert.doesNotMatch(html, /id="daily-punch"/, "an unticked section stays closed");
  // A job without a schedule has no Quantities box.
  core.setState(company([job()]));
  assert.doesNotMatch(reg.views.daily(), /data-daily-section="quantities"/);
  const body = daily.dailyBody({ notes: "Framing", crew_count: "4", weather: "Rain", qty_l1: "120", qty_l2: "", co_0: "co1", co_desc_0: "Two circuits", co_hours_0: "3",
    punch: "Patch" }, sovJob, new Set(["quantities", "co_work"]));
  assert.deepEqual(body, { notes: "Framing", crew_count: "4", weather: "Rain",
    sections: { quantities: [{ line_id: "l1", qty: "120" }], co_work: [{ co_id: "co1", description: "Two circuits", hours: "3" }] } });
  daily.daily.checked.clear();
  forms.clearForm("daily");
});

test("Daily record: Recent records show the crew, the sections and links to the files attached", async () => {  // T11, T12
  const daily = await import("../../static/js/views/daily.js");
  const sovJob = job({ sov_view: { present: true, lines: [
    { id: "l1", item: "1", description: "Rough-in wiring", unit: "LF", qty_milli: 400000, done_milli: 220000 },
    { id: "l2", item: "2", description: "Panel", unit: "LS", qty_milli: 1000, done_milli: 250 },
  ] }, change_orders: [{ id: "co1", number: 2, title: "Extra outlets", status: "approved" }],
  records: [{ id: "r9", kind: "photo", label: "deck.jpg", file_ids: ["f1"] }] });
  core.setState({ ...company([sovJob]), files: [{ id: "f1", name: "deck.jpg" }] });
  const meta = daily.recordMeta({ crew_names: ["Ana", "Ben"], weather: "Rain", attachments: ["r9"], sections: {
    quantities: [{ line_id: "l1", qty_milli: 120000 }, { line_id: "l2", qty_milli: 250 }],
    co_work: [{ co_id: "co1", description: "Two circuits", hours_milli: 3000 }, { co_id: "", description: "Moved a box", hours_milli: 0 }],
    inspections: [{ type: "discussion", who: "GC super", outcome: "Start Monday" }], punch: ["Patch"] } }, sovJob);
  assert.match(meta, /<div>Crew: Ana, Ben<\/div>/);
  assert.match(meta, /Weather: Rain/);
  assert.match(meta, /Quantities: 120 LF Rough-in wiring, 25% Panel/);
  assert.match(meta, /Change order work \(CO 2\): Two circuits, 3 h/);
  assert.match(meta, /Change order work \(new, not priced yet\): Moved a box</);
  assert.match(meta, /Discussion with GC super: Start Monday/);
  assert.match(meta, /New punchlist items: Patch/);
  assert.match(meta, /Files: <a href="\/api\/files\/f1"[^>]*>deck.jpg<\/a>/);
});

test("Paste box: the pasted text, and what it looks like, stay through a redraw and go once added", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company());
  assert.match(inbox.pasteBoxHtml(), /data-form-key="paste"[\s\S]*<textarea id="paste-text"[^>]*data-field="text"/);
  const notes = "10/12 Framing done on Oak Street\n10/13 Inspector passed rough-in";
  forms.formDraft.paste = { text: notes };
  const drawn = inbox.pasteBoxHtml();
  assert.match(drawn, /data-field="text"[^>]*>10\/12 Framing done on Oak Street\n10\/13 Inspector passed rough-in<\/textarea>/);
  assert.match(drawn, /<option value="notes" selected>/);
  // A kind picked by hand is kept as picked.
  forms.formDraft.paste = { text: notes, source: "email" };
  assert.match(inbox.pasteBoxHtml(), /<option value="email" selected>/);
  const box = { value: notes };
  dom.bySelector["#paste-text"] = box;
  dom.bySelector["#paste-source"] = { value: "notes" };
  dom.answers = [ok("/api/import/text")];
  await inbox.actions["import-text"]({ dataset: {} });
  assert.equal(forms.formDraft.paste, undefined);
  assert.equal(box.value, "");
  delete dom.bySelector["#paste-text"]; delete dom.bySelector["#paste-source"];
});

test("Import: the pasted QuickBooks CSV stays through a redraw and goes once read", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  page.setLocation("import");
  core.setState(company());
  forms.formDraft.qbocsv = { csv: "Date,Customer,Amount" };
  assert.match(reg.views.import(), /data-form-key="qbocsv"[\s\S]*<textarea id="qbo-csv"[^>]*data-field="csv"[^>]*>Date,Customer,Amount<\/textarea>/);
  dom.byId["qbo-csv"] = { value: "Date,Customer,Amount" };
  dom.answers = [(p) => (p === "/api/import/payments-csv" ? { status: 200, body: { rows: [], warnings: [] } } : undefined)];
  await reg.actions["qbo-read"]();
  assert.equal(forms.formDraft.qbocsv, undefined);
  await reg.actions["qbo-cancel"]();
  assert.match(reg.views.import(), /data-field="csv"[^>]*><\/textarea>/);
  delete dom.byId["qbo-csv"];
});

test("Job page: 'Customer wants changes' and a record's Edit box keep what was typed", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  page.setLocation("job", "j1");
  core.setState(company());
  page.formOpen.pencilchanges = true;
  forms.formDraft.pencilchanges = { note: "Fix quantities" };
  reg.actions["record-edit"]({ dataset: { item: "r1" } });
  forms.formDraft["record-r1"] = { label: "Rough-in passed, city", kind: "photo" };
  const html = reg.views.job();
  assert.match(html, /data-form-key="pencilchanges"[\s\S]*<input id="pencil-note"[^>]*data-field="note"[^>]*value="Fix quantities"/);
  assert.match(html, /data-form-key="record-r1"[\s\S]*<input id="rec-edit-label-r1"[^>]*data-field="label"[^>]*value="Rough-in passed, city"/);
  assert.match(html, /<select id="rec-edit-kind-r1"[^>]*data-field="kind"[^>]*>[\s\S]*<option value="photo" selected>/);
  // Saved: what was typed goes.
  dom.byId["pencil-note"] = { value: "Fix quantities" };
  dom.answers = [ok("/api/jobs/j1/pencil-response"), ok("/api/jobs/j1/edit-record/r1")];
  await reg.actions["pencil-changes"]({ dataset: {} }, "j1");
  assert.equal(forms.formDraft.pencilchanges, undefined);
  dom.byId["rec-edit-label-r1"] = { value: "Rough-in passed, city", focus() {} };
  dom.byId["rec-edit-kind-r1"] = { value: "photo" };
  await reg.actions["record-save"]({ dataset: { item: "r1" } }, "j1");
  assert.equal(forms.formDraft["record-r1"], undefined);
  // Cancel on a record's Edit drops what was typed there too.
  reg.actions["record-edit"]({ dataset: { item: "r1" } });
  forms.formDraft["record-r1"] = { label: "Half typed" };
  reg.actions["record-edit"]({ dataset: { item: "" } });
  assert.equal(forms.formDraft["record-r1"], undefined);
  for (const id of ["pencil-note", "rec-edit-label-r1", "rec-edit-kind-r1"]) delete dom.byId[id];
  page.formOpen.pencilchanges = false;
});

test("Schedule progress: an open draft is named as the one it replaces, until it is with the client", async () => {  // M1
  const { sovProgressHtml } = await import("../../static/js/sov.js");
  const lines = [{ id: "l1", item: "1", description: "Rough-in", unit: "LF", qty_milli: 400000, done_milli: 100000, stored_cents: 0 }];
  const draft = { id: "i1", number: "PA-OAK-3", status: "draft", amount_cents: 450000 };
  const sovJob = (invoices) => job({ invoices, sov_view: { present: true, lines } });
  assert.match(sovProgressHtml(sovJob([draft])), /This replaces PA-OAK-3 \(\$4,500\.00\)\. Its number and paperwork stay\./);
  assert.doesNotMatch(sovProgressHtml(sovJob([{ ...draft, pencil_status: "sent" }])), /This replaces/);
  assert.doesNotMatch(sovProgressHtml(sovJob([{ ...draft, status: "sent" }])), /This replaces/);
});
