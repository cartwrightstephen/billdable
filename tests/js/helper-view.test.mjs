// The helper's buttons (Round 2, Stephen 10 Nov, decision 12): each opens a page or a form and none
// changes data; a reply with `go` opens its first button at once.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom, stubEl } from "./fakedom.mjs";

const dom = installFakeDom();
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
const settle = () => new Promise(r => setTimeout(r, 5));
const handlers = {};
dom.byId["ai-panel"] = stubEl({ addEventListener: (kind, fn) => { handlers[kind] = fn; } });
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const forms = await import("../../static/js/forms.js");
const helper = await import("../../static/js/views/helper.js");

const job = (id, name, extra = {}) => ({ id, name, closed: false, work_status: "in_progress", invoices: [], change_orders: [], payments: [], money: {}, ...extra });
const company = () => ({ company: { name: "Pine" }, jobs: [job("j1", "Oak Street kitchen")], payers: [{ id: "p1", name: "Northline GC" }], inbox: [], email_log: [], email_log_total: 0 });

function signedIn() {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company());
  core.nav("dashboard");
  helper.helperLog.length = 0;
  dom.requests.length = 0;
}

async function tap(action) {
  helper.helperLog.push({ who: "helper", text: "x", actions: [action] });
  const btn = { dataset: { helperMsg: String(helper.helperLog.length - 1), helperAction: "0" } };
  await handlers.click({ target: { closest: (sel) => (sel === "[data-helper-action]" ? btn : null) } });
}

test("a button that would post is never run: the helper changes nothing", async () => {
  signedIn();
  await tap({ type: "post", label: "Send the follow-up", path: "/api/jobs/j1/follow-up" });
  assert.deepEqual(dom.requests.filter(r => r.method === "POST"), []);
  assert.equal(page.currentView, "dashboard");
  assert.ok(!helper.OPENS.has("post"));
});

test("a page, a job and a client open from their buttons", async () => {
  signedIn();
  await tap({ type: "nav", view: "collections", label: "Open Unpaid" });
  assert.equal(page.currentView, "collections");
  await tap({ type: "job", job_id: "j1", label: "Open Oak Street kitchen" });
  assert.deepEqual([page.currentView, page.currentJobId], ["job", "j1"]);
  await tap({ type: "client", payer_id: "p1", label: "Open Northline GC" });
  assert.equal(page.currentView, "playbook");
});

test("each form opens on its job, empty but for what is obvious, and nothing is posted", async () => {
  signedIn();
  await tap({ type: "form", form: "newco", job_id: "j1", label: "New Change Order: Oak Street kitchen" });
  assert.deepEqual([page.currentView, page.currentJobId, page.formOpen.newco], ["job", "j1", true]);
  await tap({ type: "form", form: "match", job_id: "j1", label: "Record a Payment: Oak Street kitchen" });
  assert.deepEqual([page.currentView, page.formOpen.match], ["job", true]);
  await tap({ type: "form", form: "progress", job_id: "j1", label: "Progress Pay App: Oak Street kitchen" });
  assert.deepEqual([page.currentView, page.currentJobId], ["payapp-new", "j1"], "its own page (Nov 26)");
  await tap({ type: "form", form: "daily", job_id: "j1", label: "New job record: Oak Street kitchen" });
  assert.equal(page.currentView, "daily");
  await tap({ type: "form", form: "newjob", client: "Northline GC", name: "Elm Street", label: "New job" });
  assert.deepEqual([page.currentView, page.formOpen.newjob], ["jobs", true]);
  assert.equal(forms.draftValue("newjob", "customer"), "Northline GC");
  assert.equal(forms.draftValue("newjob", "client_mode"), "existing");
  assert.equal(forms.draftValue("newjob", "name"), "Elm Street");
  assert.deepEqual(dom.requests.filter(r => r.method === "POST"), []);
});

test("\"take me to Unpaid\": a reply with go opens the page straight away", async () => {
  signedIn();
  dom.answers = [(p) => (p === "/api/helper/ask" ? { status: 200, body: { text: "Opening Unpaid.", actions: [{ type: "nav", view: "collections", label: "Open Unpaid" }], go: 0, log_id: "q-1" } } : undefined)];
  await handlers.click({ target: { closest: (sel) => (sel === "[data-helper-ask]" ? { textContent: "take me to unpaid" } : null) } });
  await settle();
  assert.equal(page.currentView, "collections");
  assert.equal(helper.helperLog.at(-1).text, "Opening Unpaid.");
  // A post the server should never send is dropped before it is drawn.
  dom.answers = [(p) => (p === "/api/helper/ask" ? { status: 200, body: { text: "x", actions: [{ type: "post", path: "/api/jobs/j1/follow-up", label: "Send" }], go: 0 } } : undefined)];
  await handlers.click({ target: { closest: (sel) => (sel === "[data-helper-ask]" ? { textContent: "anything" } : null) } });
  await settle();
  assert.deepEqual(helper.helperLog.at(-1).actions, []);
  assert.deepEqual(dom.requests.filter(r => r.method === "POST").map(r => r.path), ["/api/helper/ask", "/api/helper/ask"]);
});

test("Settings, Helper answers is Page not found for anyone but the Duracash team (D6)", async () => {
  const reg = await import("../../static/js/views/index.js");
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company());
  page.setAccount({ helperAdmin: false });
  const html = reg.views["helper-admin"]();
  assert.match(html, /Page not found/);
  assert.doesNotMatch(html, /HELPER_ADMIN_USER_IDS|BILLING_ENFORCE|helper-admin-body/);
  page.setAccount({ helperAdmin: true });
  assert.match(reg.views["helper-admin"](), /<h1>Helper answers<\/h1>/);
  page.setAccount({ helperAdmin: false });
});
