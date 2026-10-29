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
const money = await import("../../static/js/views/money.js");
const jobPage = await import("../../static/js/views/job.js");

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
  assert.match(today.views.daily(), /data-form-key="daily"[\s\S]*<textarea id="daily-notes"[^>]*data-field="notes"/);
  forms.formDraft.daily = { notes: "Framing done <east> wall" };
  assert.match(today.views.daily(), /data-field="notes"[^>]*>Framing done &lt;east&gt; wall<\/textarea>/);
  dom.bySelector["#daily-notes"] = { value: "Framing done <east> wall" };
  dom.bySelector["#daily-job"] = { value: "j1" };
  dom.answers = [ok("/api/jobs/j1/daily-record")];
  await today.actions["daily-save"]();
  assert.equal(forms.formDraft.daily, undefined);
  assert.match(today.views.daily(), /data-field="notes"[^>]*><\/textarea>/);
  delete dom.bySelector["#daily-notes"]; delete dom.bySelector["#daily-job"];
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

test("Payments: the pasted QuickBooks CSV stays through a redraw and goes once read", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  page.setLocation("payments");
  core.setState(company());
  forms.formDraft.qbocsv = { csv: "Date,Customer,Amount" };
  assert.match(money.views.payments(), /data-form-key="qbocsv"[\s\S]*<textarea id="qbo-csv"[^>]*data-field="csv"[^>]*>Date,Customer,Amount<\/textarea>/);
  dom.byId["qbo-csv"] = { value: "Date,Customer,Amount" };
  dom.answers = [(p) => (p === "/api/import/payments-csv" ? { status: 200, body: { rows: [], warnings: [] } } : undefined)];
  await money.actions["qbo-read"]();
  assert.equal(forms.formDraft.qbocsv, undefined);
  await money.actions["qbo-cancel"]();
  assert.match(money.views.payments(), /data-field="csv"[^>]*><\/textarea>/);
  delete dom.byId["qbo-csv"];
});

test("Job page: 'Customer wants changes' and a record's Edit box keep what was typed", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  page.setLocation("job", "j1");
  core.setState(company());
  page.formOpen.pencilchanges = true;
  forms.formDraft.pencilchanges = { note: "Fix quantities" };
  jobPage.actions["record-edit"]({ dataset: { item: "r1" } });
  forms.formDraft["record-r1"] = { label: "Rough-in passed, city", kind: "photo" };
  const html = jobPage.views.job();
  assert.match(html, /data-form-key="pencilchanges"[\s\S]*<input id="pencil-note"[^>]*data-field="note"[^>]*value="Fix quantities"/);
  assert.match(html, /data-form-key="record-r1"[\s\S]*<input id="rec-edit-label-r1"[^>]*data-field="label"[^>]*value="Rough-in passed, city"/);
  assert.match(html, /<select id="rec-edit-kind-r1"[^>]*data-field="kind"[^>]*>[\s\S]*<option value="photo" selected>/);
  // Saved: what was typed goes.
  dom.byId["pencil-note"] = { value: "Fix quantities" };
  dom.answers = [ok("/api/jobs/j1/pencil-response"), ok("/api/jobs/j1/edit-record/r1")];
  await jobPage.actions["pencil-changes"]({ dataset: {} }, "j1");
  assert.equal(forms.formDraft.pencilchanges, undefined);
  dom.byId["rec-edit-label-r1"] = { value: "Rough-in passed, city", focus() {} };
  dom.byId["rec-edit-kind-r1"] = { value: "photo" };
  await jobPage.actions["record-save"]({ dataset: { item: "r1" } }, "j1");
  assert.equal(forms.formDraft["record-r1"], undefined);
  // Cancel on a record's Edit drops what was typed there too.
  jobPage.actions["record-edit"]({ dataset: { item: "r1" } });
  forms.formDraft["record-r1"] = { label: "Half typed" };
  jobPage.actions["record-edit"]({ dataset: { item: "" } });
  assert.equal(forms.formDraft["record-r1"], undefined);
  for (const id of ["pencil-note", "rec-edit-label-r1", "rec-edit-kind-r1"]) delete dom.byId[id];
  page.formOpen.pencilchanges = false;
});
