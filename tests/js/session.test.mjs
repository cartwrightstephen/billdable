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
const money = await import("../../static/js/views/payments.js");
const auth = await import("../../static/js/views/auth.js");
const reg = await import("../../static/js/views/index.js");

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
  await reg.actions["inbox-read-payments"]({ dataset: { file: "f1" } });
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

// ----- idle sign-out (G2) -----
const idle = await import("../../static/js/idle.js");

test("the idle count warns two minutes before the end, and halfway through a short time", () => {
  const min = 60 * 1000;
  assert.equal(idle.idleStage(57 * min, 0, 60), "active");
  assert.equal(idle.idleStage(58 * min, 0, 60), "warn");
  assert.equal(idle.idleStage(60 * min, 0, 60), "out");
  assert.equal(idle.idleStage(29 * 1000, 0, 1), "active");
  assert.equal(idle.idleStage(30 * 1000, 0, 1), "warn");
  assert.equal(idle.idleWords(60), "Signed out after an hour without use.");
  assert.equal(idle.idleWords(15), "Signed out after 15 minutes without use.");
});

test("an hour without a click or key signs out with the reason, and Sign in says it", async () => {
  page.setMe({ name: "Ann", company_id: "co-a", role: "owner" });
  core.setState(company("Company A"));
  dom.answers = [(p) => (p === "/api/auth/logout" ? { status: 200, body: {} } : undefined)];
  const realNow = Date.now;
  let now = realNow();
  Date.now = () => now;
  try {
    idle.startIdleWatch(60);
    assert.ok(idle.idleWatching());
    now += 58 * 60 * 1000;
    idle.idleCheck();
    assert.ok(idle.idleWatching(), "warned, still signed in");
    now += 2 * 60 * 1000;
    idle.idleCheck();
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(idle.idleWatching(), false);
    assert.equal(page.state, null, "signed out: nothing of the company stays");
    assert.equal(page.signedOutNote, "Signed out after an hour without use.");
    assert.ok(dom.requests.some(r => r.path === "/api/auth/logout"));
  } finally { Date.now = realNow; idle.stopIdleWatch(); page.setSignedOutNote(""); }
});

test("a 401 from an idle session shows the reason on Sign in", async () => {
  page.setMe({ name: "Ann", company_id: "co-a", role: "owner" });
  core.setState(company("Company A"));
  dom.answers = [(p) => (p === "/api/state" ? { status: 401, body: { error: "Signed out after an hour without use.", idle: true } } : undefined)];
  await core.load();
  assert.equal(page.me, null);
  assert.equal(page.signedOutNote, "Signed out after an hour without use.");
  page.setSignedOutNote("");
});
