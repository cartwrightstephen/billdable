// Money > Change orders and a change order's page (Stephen's Oct 29 review): the list has no form
// of its own; New change order asks for the job and opens that job's form (G10). A change order
// shows its reason, cost lines, contract sum and history (M7, M8, M10), and the amber pill when
// work started before it was signed (M9). node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");
const cos = await import("../../static/js/views/job-cos.js");

const job = (id, name, extra = {}) => ({ id, name, code: id.toUpperCase(), payer_id: "", payer: { approver: "Sam" }, closed: false, invoices: [], payments: [], change_orders: [], records: [], money: { contract_cents: 1_000_000 }, ...extra });
const company = (jobs) => ({ company: { name: "Pine" }, jobs, payers: [], inbox: [], email_log: [], email_log_total: 0, files: [] });

test("the list has no form; New change order with several open jobs asks for the job first", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company([job("ja", "Oldest job"), job("jb", "Newer job")]));
  let html = reg.views.cos();
  assert.doesNotMatch(html, /data-form-key="newco"/, "the change order form lives on the job page");
  assert.match(html, /data-action="co-new"[^>]*>New change order</);
  page.formOpen.copick = true;
  html = reg.views.cos();
  assert.match(html, /<select id="new-co-job"[^>]*><option value="" selected>Pick a job…<\/option><option value="ja">Oldest job<\/option>/);
  dom.all['[data-form-key="copick"] [data-field]'] = [{ dataset: { field: "job" }, value: "" }];
  dom.requests.length = 0;
  reg.actions["co-new-open"]();
  assert.equal(page.formOpen.newco, undefined, "nothing opens until a job is picked");
  delete dom.all['[data-form-key="copick"] [data-field]'];
  page.formOpen.copick = false;
});

test("the New change order form asks the reason, pricing, cost lines and the day work started", () => {
  const html = cos.coFormFields(job("ja", "A job", { records: [{ id: "rec-1", kind: "supplier_invoice", label: "Tile quote" }, { id: "rec-2", kind: "daily", label: "Monday" }] }));
  assert.match(html, /<select id="co-reason"[^>]*><option value="" selected>Pick why the scope changed…<\/option><option value="owner_request">Owner request<\/option>/);
  for (const s of ["Unforeseen condition", "Design issue", "Value engineering", "Description (required for Other)", "Contract section or original scope it changes",
    "Time and materials", "Add a cost line", "Work started on (optional)", "Backup from the job record"]) assert.ok(html.includes(s), s);
  assert.match(html, /data-co-backup="rec-1"/);
  assert.doesNotMatch(html, /data-co-backup="rec-2"/, "a daily record isn't backup");
  assert.doesNotMatch(html, /style=|onclick=/);
});

test("Create refuses without a reason, and Other needs a description", async () => {
  dom.all['[data-form-key="newco"] [data-field]'] = [{ dataset: { field: "title" }, value: "Two outlets" }, { dataset: { field: "amount_cents" }, value: "500" }, { dataset: { field: "reason" }, value: "" }];
  dom.requests.length = 0;
  assert.equal(await cos.addChangeOrder("ja"), null);
  dom.all['[data-form-key="newco"] [data-field]'][2].value = "other";
  assert.equal(await cos.addChangeOrder("ja"), null);
  assert.equal(dom.requests.length, 0);
  delete dom.all['[data-form-key="newco"] [data-field]'];
});

test("a change order's page: reason, cost lines, contract sum if signed, history and the work-started pill", () => {
  const co = { id: "co-1", number: 2, title: "Extra tile", amount_cents: 45_000, status: "pending", included_in_billing: false, reason: "unforeseen_condition",
    pricing: "tm", items: [{ label: "Labor", amount_cents: 30_000 }, { label: "Material", amount_cents: 15_000 }], contract_ref: "Section 09 30 00",
    schedule_days: 2, work_started_on: "2026-10-01", work_started_unsigned: true, support_record_ids: ["rec-1"],
    history: [{ at: "2026-10-02T10:00:00+00:00", what: "created", by: "Pat" }, { at: "2026-10-03T10:00:00+00:00", what: "work_started", by: "Pat", note: "2026-10-01" }] };
  const signed = { id: "co-0", number: 1, title: "Outlets", amount_cents: 10_000, status: "approved" };
  core.setState(company([job("ja", "A job", { change_orders: [signed, co], records: [{ id: "rec-1", kind: "supplier_invoice", label: "Tile quote" }] })]));
  page.setLocation("co-detail", "ja", "co-1");
  const html = reg.views.coDetail();
  assert.match(html, /Work started before signing/);
  assert.match(html, /Unforeseen condition/);
  assert.match(html, /Time and materials/);
  assert.match(html, /Labor<\/span><span>\$300\.00/);
  assert.match(html, /Section 09 30 00/);
  assert.match(html, /\+2 days/);
  assert.match(html, /Tile quote/, "the backup is listed");
  assert.match(html, /Signed change orders before this one<\/span><span>\$100\.00/);
  assert.match(html, /Revised contract sum if signed<\/span><span>\$10,550\.00/);
  assert.match(html, /<h2>History<\/h2>[\s\S]*Created[\s\S]*Work started/);
  assert.doesNotMatch(html, /style=|onclick=/);
});

test("a signed change order waits on the client logging it, and an older one shows its stamps as history", () => {
  const co = { id: "co-1", number: 1, title: "Extra", amount_cents: 5_000, status: "approved", approved_at: "2026-10-04T10:00:00+00:00", approved_by: "Kim (signed online)",
    created_at: "2026-10-01T10:00:00+00:00", created_by: "Pat", signed_evidence: { label: "Scan", by: "Pat", at: "2026-10-04T10:00:00+00:00", signer_name: "Kim Park", signed_on: "2026-10-03" } };
  core.setState(company([job("ja", "A job", { change_orders: [co] })]));
  page.setLocation("co-detail", "ja", "co-1");
  const html = reg.views.coDetail();
  assert.match(html, /Client to log it on their end/);
  assert.match(html, /data-action="co-logged"[^>]*data-logged="1"[^>]*>Client logged it on their end</);
  assert.match(html, /Kim Park, Oct 3, 2026 \(on paper\)/);
  assert.match(html, /Revised contract sum<\/span>/);
  assert.match(html, /Created[\s\S]*Pat, [\s\S]*Signed[\s\S]*Kim \(signed online\)/);
});

test("a job without change orders: its page points to the job's form instead of a form of its own", () => {
  core.setState(company([job("ja", "A job")]));
  page.setLocation("co-detail", "ja", "");
  const html = reg.views.coDetail();
  assert.match(html, /No change orders on A job/);
  assert.match(html, /data-action="co-open-form" data-id="ja">New change order</);
  assert.doesNotMatch(html, /data-form-key="newco"/);
});
