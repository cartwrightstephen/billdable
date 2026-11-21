// Fixes from the review of Duracash Round 2 (Nov 17) on the page: the helper's "Which job?" choice
// asks again with that job's id.
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
const helper = await import("../../static/js/views/helper.js");

const job = (over = {}) => ({ id: "j1", name: "Oak Street kitchen", closed: false, work_status: "in_progress", invoices: [], change_orders: [],
  payments: [], money: {}, records: [], ...over });

test("item 13: a Which job? choice asks again with that job's id", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState({ company: { name: "Pine" }, jobs: [job()], payers: [], inbox: [], email_log: [], email_log_total: 0 });
  core.nav("dashboard");
  dom.requests.length = 0;
  dom.answers = [(p) => p === "/api/helper/ask" ? { status: 200, body: { text: "ok", actions: [] } } : undefined];
  helper.helperLog.length = 0;
  helper.helperLog.push({ who: "helper", text: "Which job do you mean?", actions: [{ type: "ask", q: "status of kitchen remodel", job_id: "j1", label: "Kitchen remodel" }] });
  const btn = { dataset: { helperMsg: "0", helperAction: "0" } };
  await handlers.click({ target: { closest: (sel) => (sel === "[data-helper-action]" ? btn : null) } });
  await settle();
  const asked = dom.requests.find(r => r.path === "/api/helper/ask");
  assert.deepEqual([asked.body.q, asked.body.job_id], ["status of kitchen remodel", "j1"]);
  assert.deepEqual(helper.helperAskBody("x"), { q: "x", view: "dashboard", job_id: "" });
  dom.answers = [];
});
