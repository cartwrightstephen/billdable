// Fixes from the review of Duracash Round 2 (Nov 17) on the page: "Mark job done" from a message
// with an earlier pay app unpaid opens the Final pay app form for the reason, and an unpriced change
// order in the Final pay app confirm reads "no price yet".
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom, stubEl } from "./fakedom.mjs";

const dom = installFakeDom();
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
const settle = () => new Promise(r => setTimeout(r, 5));
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const inbox = await import("../../static/js/views/inbox.js");
const { finalPayAppConfirm } = await import("../../static/js/jobs.js");

const job = (over = {}) => ({ id: "j1", name: "Oak Street kitchen", closed: false, work_status: "in_progress", invoices: [], change_orders: [],
  payments: [], money: {}, records: [], ...over });

// askConfirm builds its dialog with createElement: keep its buttons so a test can press one.
const made = [];
const realCreate = document.createElement;
document.createElement = (tag) => {
  if (tag === "textarea") return realCreate(tag);
  const el = stubEl({ listeners: {}, close() {}, remove() {}, showModal() {}, addEventListener(kind, fn) { this.listeners[kind] = fn; } });
  made.push(el);
  return el;
};
const press = (label) => made.find(el => el.textContent === label && el.listeners.click).listeners.click();

test("item 1: with a pay app unpaid, the message is filed and the Final pay app form opens for the reason", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  const unpaid = { id: "i1", number: "PA-OAK-01", status: "sent", amount_cents: 100000 };
  const before = { company: { name: "Pine" }, jobs: [job({ invoices: [unpaid] })], payers: [], email_log: [], email_log_total: 0,
    inbox: [{ id: "inb-1", status: "new", kind: "note", says_done_job_id: "j1", from: "GC" }] };
  core.setState(before);
  core.nav("inbox");
  const after = { ...before, inbox: [{ ...before.inbox[0], status: "filed", filed_job_id: "j1" }] };
  dom.answers = [(p) => p === "/api/jobs/j1/final-preview" ? { status: 200, body: { drafted: true, final: true, number: "PA-OAK-02", amount_cents: 50000, unpaid: ["PA-OAK-01"] } }
    : p === "/api/inbox/inb-1/mark-done" ? { status: 200, body: after } : undefined];
  made.length = 0;
  const run = inbox.actions["inbox-mark-done"]({ dataset: { item: "inb-1" } });
  await settle();
  const text = made.find(el => el.id === "confirm-text").textContent;
  assert.match(text, /^PA-OAK-01 is not paid yet\. File the message and open Final pay app on Oak Street kitchen/);
  press("Open Final pay app");
  await run;
  assert.deepEqual([page.currentView, page.currentJobId, page.formOpen.done], ["job", "j1", true]);
  dom.answers = [];
});

test("item 1: nothing unpaid keeps the usual confirm", () => {
  const p = { drafted: true, final: true, number: "PA-02", amount_cents: 50000, unpaid: [] };
  assert.deepEqual(inbox.inboxDoneConfirm(job(), p), finalPayAppConfirm(job(), p));
  assert.equal(inbox.doneFormNeeded(job({ work_status: "complete" })), false);
  assert.equal(inbox.doneFormNeeded(job()), true);
});

test("item 6: an unpriced change order reads no price yet, not $0.00", () => {
  const p = { drafted: true, final: false, number: "PA-02", amount_cents: 50000, pending_cos: [{ number: 1, amount_cents: 0, unpriced: true }, { number: 2, amount_cents: 20000 }] };
  const { text } = finalPayAppConfirm(job(), p);
  assert.match(text, /CO 1 \(no price yet\), CO 2 \(\$200\.00\)/);
  assert.doesNotMatch(text, /\$0\.00/);
});
