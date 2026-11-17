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

test("New job record: the note typed stays through a redraw and goes once saved", async () => {
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

test("New job record: crew, weather and the ticked sections stay through a redraw; the body sends them", async () => {  // T11, T12
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
  assert.doesNotMatch(html, /data-daily-section="quantities"/, "quantities are open, not ticked (Round 1)");
  assert.match(html, /data-field="qty_l1" value="120"/);
  assert.doesNotMatch(html, /data-field="qty_l2"/, "a finished line takes no more");
  assert.match(html, /<option value="co1">CO 2 Extra outlets<\/option>/);
  assert.doesNotMatch(html, /id="daily-punch"/, "an unticked section stays closed");
  // A job without a schedule has no Quantities box.
  core.setState(company([job()]));
  assert.doesNotMatch(reg.views.daily(), /Quantities put in place today/);
  const body = daily.dailyBody({ notes: "Framing", crew_count: "4", weather: "Rain", qty_l1: "120", qty_l2: "", co_0: "co1", co_desc_0: "Two circuits", co_hours_0: "3",
    punch: "Patch" }, sovJob, new Set(["quantities", "co_work"]));
  assert.deepEqual(body, { notes: "Framing", crew_count: "4", weather: "Rain",
    sections: { quantities: [{ line_id: "l1", qty: "120" }], co_work: [{ co_id: "co1", description: "Two circuits", hours: "3" }] } });
  daily.daily.checked.clear();
  forms.clearForm("daily");
});

test("New job record: Recent diary entries show the crew, the sections and links to the files attached", async () => {  // T11, T12
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
  assert.match(meta, /Files: <a href="\/api\/files\/f1"[^>]*>deck.jpg<\/a>/);  const kept = daily.recordMeta({ sections: { quantities: [{ line_id: "l2", qty_milli: 1000 }] },
    quantities_not_applied: "2. Panel: 125% done is more than 100%. Extra scope is a change order. They are kept on this daily record." }, sovJob);
  assert.match(kept, /Quantities: 100% Panel[\s\S]*Not added to the schedule of values: 2\. Panel: 125% done is more than 100%/, "B5: saved, and why not added");
});

test("New job record: a section for each document, none behind a dropdown, every kind still offered", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company());
  page.setLocation("daily");
  const daily = await import("../../static/js/views/daily.js");
  // Each document is a chip that opens its section (Round 1, item 15); here every one is open.
  for (const key of ["delivery", "photos", "time_sheets", "invoices", "inspections", "other"]) daily.daily.open.add(key);
  const html = reg.views.daily();
  daily.daily.open.clear();
  assert.match(html, /<h1>New job record<\/h1>/);
  assert.match(html, /id="daily-date"[^>]*type="date"/);
  for (const title of ["Delivery tickets", "Progress photos", "Time sheets", "Invoices", "Inspections", "Other documents and notes"]) assert.ok(html.includes(`<h3>${title}</h3>`), title);
  assert.doesNotMatch(html, /<details/);
  for (const key of ["delivery_0", "photos_0", "time_sheets_0", "invoices_0", "inspections_0", "other_0"]) {
    assert.match(html, new RegExp(`data-record-files="${key}" accept="image/\\*" capture="environment"`), `${key}: the phone camera`);
    assert.match(html, new RegExp(`data-record-files="${key}" multiple`), `${key}: choose files`);
  }
  const { RECORD_KINDS } = await import("../../static/js/jobs.js");
  const other = html.split('id="daily-other-kind-0"')[1].split("</select>")[0];
  for (const kind of Object.keys(RECORD_KINDS).filter(k => k !== "daily")) assert.ok(other.includes(`value="${kind}"`), kind);
  assert.ok(!other.includes('value="daily"'));
});

test("New job record: rows become entries with their files; a Kind alone is nothing", async () => {
  const daily = await import("../../static/js/views/daily.js");
  const v = { notes: "x", delivery_supplier_0: "ABC", delivery_number_0: "", delivery_what_0: "", photos_note_0: "", time_sheets_who_1: "Ana", time_sheets_hours_1: "8", other_kind_0: "insurance", other_label_0: "", other_note_0: "", invoices_amount_0: "" };
  const entries = daily.recordEntries(v, [{ key: "photos_0", name: "a.jpg", record_id: "r1" }, { key: "photos_0", name: "b.jpg", record_id: "r2" }]);
  assert.deepEqual(entries, [
    { section: "delivery", row: 0, supplier: "ABC" },
    { section: "photos", row: 0, attachments: ["r1", "r2"] },
    { section: "time_sheets", row: 1, who: "Ana", hours: "8" },
  ]);
  assert.equal(daily.savedWords("x", entries), "1 diary entry, 1 delivery ticket, 2 progress photos, 1 time sheet");
  assert.deepEqual(daily.recordEntries({ other_kind_0: "insurance" }), []);
});

test("New job record: a failed upload keeps what was typed and the files; the retry doesn't send twice", async () => {
  const daily = await import("../../static/js/views/daily.js");
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company());
  page.setLocation("daily");
  forms.formDraft.daily = { notes: "Tile day", delivery_supplier_0: "ABC" };
  daily.daily.files.delivery_0 = [{ name: "t.jpg", size: 10, type: "image/jpeg", toString: () => "null" }];
  dom.bySelector["#daily-notes"] = { value: "Tile day" };
  dom.bySelector["#daily-job"] = { value: "j1" };
  dom.requests.length = 0;
  dom.answers = [(p) => (String(p).startsWith("/api/import/file") ? { status: 500, body: { error: "Upload failed." } } : undefined)];
  await reg.actions["daily-save"]();
  assert.ok(dom.requests.some(r => String(r.path).includes("kind=ticket")), "goes up as a ticket");
  assert.ok(!dom.requests.some(r => String(r.path).endsWith("/daily-record")), "nothing saved yet");
  assert.equal(forms.formDraft.daily.delivery_supplier_0, "ABC");
  assert.equal(daily.daily.files.delivery_0.length, 1);
  assert.match(reg.views.daily(), /t\.jpg/);
  delete dom.bySelector["#daily-notes"]; delete dom.bySelector["#daily-job"];
  daily.daily.files = {};
  forms.clearForm("daily");
});

test("the Job records card: New job record and the records, nothing else; files are dropped on New job record (Yusuf, 10 Nov)", async () => {
  const { recordCardHtml } = await import("../../static/js/views/job-record.js");
  const html = recordCardHtml(job());
  assert.match(html, /<h2>Job records<\/h2>/);
  assert.match(html, /data-action="daily-for-job" data-id="j1">New job record</);
  assert.match(html, /Rough-in passed/);
  assert.doesNotMatch(html, /Add a note to the record|Paste an email|paste-text|dropzone|Take a photo|Add a file|import-for-job/);
  const closed = recordCardHtml(job({ closed: true }));
  assert.doesNotMatch(closed, /New job record/);
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
  assert.match(sovProgressHtml(sovJob([draft])), /This replaces PA-OAK-3 \(\$4,500\.00\)\. Its number and supporting documents stay\./);
  assert.doesNotMatch(sovProgressHtml(sovJob([{ ...draft, pencil_status: "sent" }])), /This replaces/);
  assert.doesNotMatch(sovProgressHtml(sovJob([{ ...draft, status: "sent" }])), /This replaces/);
});

test("Schedule progress: each box has a stable id, so a redraw (the activity list loading) puts the cursor back", async () => {
  const { sovProgressHtml } = await import("../../static/js/sov.js");
  const lines = [{ id: "l1", item: "1", description: "Rough-in", unit: "LF", qty_milli: 400000, done_milli: 100000, stored_cents: 0 }];
  const html = sovProgressHtml(job({ sov_view: { present: true, lines } }));
  assert.match(html, /<input id="sovp-done-l1" class="sov-in sov-num" data-sovp="l1"/);
  assert.match(html, /<input id="sovp-stored-l1" class="sov-in sov-num" data-sovs="l1"/);
});
