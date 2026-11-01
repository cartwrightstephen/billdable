// Import/Export (Stephen's Oct 29 review): Import's Quantities done reads a CSV or Excel sheet
// against a job's schedule of values, shows each row, and adds only on the tap (M1); Export lists
// jobs, pay apps, payments and change orders as CSV files (I1). node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
globalThis.requestAnimationFrame = () => 0;
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const data = await import("../../static/js/views/data.js");

const company = (jobs) => ({ company: { name: "Pine" }, jobs, payers: [], inbox: [], email_log: [], email_log_total: 0, files: [] });
const job = (id, name, present, extra = {}) => ({ id, name, code: id.toUpperCase(), closed: false, invoices: [], payments: [], change_orders: [],
  sov_view: { present, editable: false }, ...extra });

test("Quantities done lists only open jobs with a schedule of values; with none it is a title and a button", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company([job("ja", "Elm wiring", true), job("jb", "Oak deck", false), job("jc", "Old wiring", true, { closed: true })]));
  const card = data.views.import().split('id="qty-import"')[1];
  assert.match(card, /<h2>Quantities done<\/h2>/);
  assert.match(card, /<select id="qty-import-job"[^>]*><option value="">Pick a job…<\/option><option value="ja" selected>Elm wiring \(JA\)<\/option><\/select>/);
  assert.doesNotMatch(card, /Oak deck|Old wiring/);
  assert.match(card, /data-action="qty-read"/);
  assert.match(card, /data-action="qty-choose-file"/);
  core.setState(company([job("jb", "Oak deck", false)]));
  const none = data.views.import().split('id="qty-import"')[1];
  assert.match(none, /<div class="empty-state"><strong>No job has a schedule of values<\/strong><button class="btn" data-action="nav" data-view="jobs">Go to Jobs<\/button><\/div>/);
});

test("Read needs a job and rows; the preview escapes what the file said, and Add sends the same sheet", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company([job("ja", "Elm wiring", true), job("jd", "Pine wiring", true)]));
  dom.all['[data-form-key="qtyimport"] [data-field]'] = [{ dataset: { field: "job" }, value: "" }, { dataset: { field: "csv" }, value: "1,30" }];
  dom.requests.length = 0;
  await data.actions["qty-read"]();
  assert.equal(dom.requests.length, 0, "no job, nothing sent");
  dom.all['[data-form-key="qtyimport"] [data-field]'][0].value = "ja";
  const preview = { job_id: "ja", basis: "b1", warnings: ["Row 5 (Total) looks like a total and was skipped."], blocked: "", can_add: 1, rows: [
    { n: 2, item: "1", description: "Rough-in", qty: "30", line: "1. Rough-in", done: "0 ea", add: "30 ea", new_total: "30 ea", scheduled: "400 ea", problem: "" },
    { n: 3, item: "", description: "<img src=x onerror=alert(1)>", qty: "4", line: "", done: "", add: "", new_total: "", scheduled: "", problem: "No line on this job's schedule of values matches \"<img>\"." },
  ] };
  dom.answers = [(path) => (path === "/api/import/quantities/preview" ? { status: 200, body: preview } : undefined)];
  await data.actions["qty-read"]();
  assert.deepEqual(dom.requests.map(r => [r.path, r.body]), [["/api/import/quantities/preview", { job_id: "ja", csv: "1,30", name: "" }]]);
  const html = data.views.import();
  // Only the Line and "Can't add because" cells wrap anywhere; the numbers stay on one line.
  assert.match(html, /<td class="num">2<\/td><td class="qty-text">1\. Rough-in<\/td>\s*<td class="num">0 ea<\/td><td class="num">30 ea<\/td><td class="num">30 ea<\/td><td class="num">400 ea<\/td><td class="qty-text">—<\/td>/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /data-action="qty-apply">Add 1 to the schedule of values</);
  assert.match(html, /Row 5 \(Total\) looks like a total/);
  assert.doesNotMatch(html, /style=|onclick=/);
  const css = readFileSync(new URL("../../static/styles.css", import.meta.url), "utf8");
  assert.match(css, /\.qty-preview td\.qty-text \{ overflow-wrap: anywhere; \}/);
  assert.match(css, /\.qty-preview td\.num \{ white-space: nowrap; \}/);
  assert.doesNotMatch(css, /\.qty-preview td \{ overflow-wrap/);
  dom.requests.length = 0;
  const after = { ...company([job("ja", "Elm wiring", true), job("jd", "Pine wiring", true)]),
    quantity_import: { added: [{ n: 2, line: "1. Rough-in", add: "30 ea", new_total: "30 ea" }], not_added: [{ n: 3, what: "Gutters", problem: "No line matches." }], number: "PA-JA-01" } };
  dom.answers = [(path) => (path === "/api/import/quantities/apply" ? { status: 200, body: after } : undefined)];
  await data.actions["qty-apply"]();
  assert.deepEqual(dom.requests.map(r => [r.path, r.body]), [["/api/import/quantities/apply", { job_id: "ja", csv: "1,30", name: "", basis: "b1" }]]);
  const done = data.views.import();
  assert.match(done, /1 line added to PA-JA-01\./);
  assert.match(done, /<h3>Not added<\/h3><div class="list-item">Row 3 \(Gutters\): No line matches\.<\/div>/);
  assert.match(done, /data-action="open-job" data-id="ja">Open the job</);
  delete dom.all['[data-form-key="qtyimport"] [data-field]'];
  dom.answers = [];
});

test("a job that can't take quantities now: the reason, and no Add button", async () => {
  core.setState(company([job("ja", "Elm wiring", true)]));
  dom.all['[data-form-key="qtyimport"] [data-field]'] = [{ dataset: { field: "job" }, value: "ja" }, { dataset: { field: "csv" }, value: "1,30" }];
  dom.answers = [(path) => (path === "/api/import/quantities/preview"
    ? { status: 200, body: { job_id: "ja", warnings: [], blocked: "The job is marked done, so every line is already counted as done.", can_add: 0, rows: [] } } : undefined)];
  await data.actions["qty-read"]();
  const html = data.views.import();
  assert.match(html, /The job is marked done/);
  assert.doesNotMatch(html, /qty-apply/);
  data.actions["qty-cancel"]();
  assert.match(data.views.import(), /data-action="qty-read"/);
  delete dom.all['[data-form-key="qtyimport"] [data-field]'];
  dom.answers = [];
});

test("Export has the four CSV lists, for a member too", () => {
  for (const role of ["owner", "member"]) {
    page.setMe({ name: "Pat", company_id: "co-p", role });
    core.setState(company([]));
    page.setLocation("export");
    const card = data.views.export().split('id="export-lists"')[1].split('id="export-payapps"')[0];
    for (const [href, label] of [["jobs", "Jobs"], ["payapps", "Pay apps"], ["payments", "Payments"], ["change-orders", "Change orders"]])
      assert.match(card, new RegExp(`href="/api/export/${href}\\.csv" download>${label}<`), `${role}: ${label}`);
  }
});
