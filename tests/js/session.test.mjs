// Signing out keeps nothing of the company in the page: the next company to sign in in the
// same tab never sees the last one's jobs, addresses, results or QuickBooks rows.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
const page = await import("../../static/js/state.js");
const core = await import("../../static/js/app-core.js");
const inbox = await import("../../static/js/views/inbox.js");
const today = await import("../../static/js/views/today.js");
const money = await import("../../static/js/views/money.js");
const auth = await import("../../static/js/views/auth.js");

const company = (name, jobs = []) => ({ company: { name }, jobs, payers: [], inbox: [], email_log: [], email_log_total: 0 });
const closedJobA = { id: "job-a-old", name: "A's old job", code: "AOLD", payer_id: "", closed: true, invoices: [], payments: [] };

async function useCompanyA() {
  page.setMe({ name: "Ann", company_id: "co-a", role: "owner" });
  core.setState(company("Company A"));
  dom.answers = [
    (p) => p === "/api/jobs/job-a-old" ? { status: 200, body: { job: closedJobA } } : undefined,
    (p) => p === "/api/import/settings" ? { status: 200, body: { email_webhook: "https://x/api/inbound/email/A-SECRET", sms_webhook: "A-SMS", calendar_url: "A-CAL" } } : undefined,
    (p) => p.startsWith("/api/metrics") ? { status: 200, body: { by_payer: [{ payer: "A's customer" }] } } : undefined,
    (p) => p === "/api/qbo/status" ? { status: 200, body: { connected: true, company: "A books" } } : undefined,
    (p) => p === "/api/import/payments-csv" ? { status: 200, body: { rows: [{ payer: "A's customer" }], warnings: [] } } : undefined,
    (p) => p.startsWith("/api/outbox") ? { status: 200, body: { entries: [{ id: "e1", to: "a@a.example" }], total: 1 } } : undefined,
    (p) => p === "/api/auth/logout" ? { status: 200, body: {} } : undefined,
  ];
  await core.loadJobDetail("job-a-old");
  await inbox.loadImportSettings();
  await today.loadMetrics();
  await money.loadQboStatus();
  await money.actions["inbox-read-payments"]({ dataset: { file: "f1" } });
  core.listPage("outbox");
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(core.jobDetails["job-a-old"], "A's closed job was fetched");
  assert.ok(today.metricsData && money.qboStatus && money.paymentImport, "A's results and QuickBooks rows are in the page");
}

test("after logging out, the next company's state has none of the last one's jobs or lists", async () => {
  await useCompanyA();
  await auth.actions.logout();
  assert.equal(page.state, null);
  assert.deepEqual(core.jobDetails, {});
  assert.equal(core.missingJobs.size, 0);
  assert.deepEqual(core.listPages, {});
  assert.equal(today.metricsData, null);
  assert.equal(money.qboStatus, null);
  assert.equal(money.paymentImport, null);
  // Company B signs in in the same tab.
  page.setMe({ name: "Bo", company_id: "co-b", role: "owner" });
  core.setState(company("Company B"));
  assert.deepEqual(page.state.jobs.map(j => j.id), []);
});

test("company B's Inbox asks for its own addresses, not A's kept ones", async () => {
  await useCompanyA();
  page.clearSession();   // the session ran out (a 401) and someone else signs in
  page.setMe({ name: "Bo", company_id: "co-b", role: "owner" });
  core.setState(company("Company B"));
  dom.requests.length = 0;
  dom.answers.unshift((p) => p === "/api/import/settings" ? { status: 200, body: { email_webhook: "B-MAIL", sms_webhook: "B-SMS", calendar_url: "B-CAL" } } : undefined);
  const shown = { innerHTML: "" };
  dom.byId["email-webhook"] = shown;
  await inbox.loadImportSettings();
  assert.ok(dom.requests.some(r => r.path === "/api/import/settings"), "B's settings are fetched");
  assert.equal(shown.innerHTML, "B-MAIL");
  delete dom.byId["email-webhook"];
  dom.answers.shift();
});

test("an answer for the last company that lands after sign-out is dropped", async () => {
  page.setMe({ name: "Ann", company_id: "co-a", role: "owner" });
  core.setState(company("Company A"));
  let release;
  const held = new Promise((r) => { release = r; });
  dom.answers = [async (p) => { if (p.startsWith("/api/metrics") || p === "/api/qbo/status" || p === "/api/import/settings") { await held; return { status: 200, body: { from: "A" } }; } }];
  const loading = [today.loadMetrics(), money.loadQboStatus(), inbox.loadImportSettings()];
  page.clearSession();
  page.setMe({ name: "Bo", company_id: "co-b", role: "owner" });
  core.setState(company("Company B"));
  release();
  await Promise.all(loading);
  assert.equal(today.metricsData, null);
  assert.equal(money.qboStatus, null);
});
