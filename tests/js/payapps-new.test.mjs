// Money > Pay apps: New pay app (Stephen's Oct 29 review, G10). A pay app is drafted on its job's
// page; the list asks which job when more than one can be billed (only jobs the job page would
// offer Draft pay app or Progress pay app on), then takes the person to that job's own step.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
globalThis.requestAnimationFrame = () => 0;
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");
const { payAppStarts } = await import("../../static/js/jobs.js");

const money = (extra = {}) => ({ contract_cents: 1_000_000, contract_total_cents: 1_000_000, unbilled_completed_cents: 0, retainage_open_cents: 0, ...extra });
const job = (id, name, extra = {}) => ({ id, name, code: id.toUpperCase(), payer_id: "", payer: {}, closed: false, work_status: "in_progress",
  invoices: [], payments: [], change_orders: [], records: [], blockers: [], close_blockers: ["x"], money: money(), ...extra });
const company = (jobs) => ({ company: { name: "Pine" }, jobs, payers: [], inbox: [], email_log: [], email_log_total: 0, files: [] });

const going = job("jg", "Going job");
const doneToBill = job("jd", "Done job", { work_status: "complete", money: money({ unbilled_completed_cents: 50_000 }) });
const doneDrafted = job("jx", "Drafted job", { work_status: "complete", money: money({ unbilled_completed_cents: 50_000 }),
  invoices: [{ id: "i1", number: "PA-JX-01", status: "draft", amount_cents: 50_000 }] });
const doneSettled = job("js", "Settled job", { work_status: "complete" });
const finished = job("jf", "Finished job", { closed: true });

test("only jobs the job page would offer a pay app on, with that page's step", () => {
  const starts = payAppStarts([going, doneToBill, doneDrafted, doneSettled, finished]);
  assert.deepEqual(starts.map(s => [s.job.id, s.step]), [["jg", "progress"], ["jd", "draft"]]);
});

test("a job with a draft open, or with the client to approve, or with nothing left to bill isn't listed", () => {
  const goingDrafted = job("jgd", "Going drafted", { invoices: [{ id: "i2", number: "PA-JGD-01", status: "draft", amount_cents: 1 }] });
  const goingAwaiting = job("jga", "Going awaiting", { invoices: [{ id: "i3", number: "PA-JGA-01", status: "awaiting_approval", amount_cents: 1 }] });
  const goingBilled = job("jgb", "Going billed", { money: money({ billed_cents: 900_000, retainage_held_cents: 100_000 }),
    invoices: [{ id: "i4", number: "PA-JGB-01", status: "sent", amount_cents: 900_000 }] });
  const goingPart = job("jgp", "Going part billed", { money: money({ billed_cents: 450_000, retainage_held_cents: 50_000 }),
    invoices: [{ id: "i5", number: "PA-JGP-01", status: "paid", amount_cents: 450_000 }] });
  const starts = payAppStarts([going, goingDrafted, goingAwaiting, goingBilled, goingPart]);
  assert.deepEqual(starts.map(s => s.job.id), ["jg", "jgp"]);
});

test("the list has New pay app; with several jobs to bill it asks which job, listing only those", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company([going, doneToBill, doneSettled, finished]));
  page.setLocation("payapps");
  let html = reg.views.payapps();
  assert.match(html, /data-action="payapp-new"[^>]*>New pay app</);
  assert.doesNotMatch(html, /new-payapp-job/, "no picker until the button is tapped");
  page.formOpen.papick = true;
  html = reg.views.payapps();
  assert.match(html, /<select id="new-payapp-job"[^>]*><option value="" selected>Pick a job…<\/option><option value="jg">Going job<\/option><option value="jd">Done job<\/option><\/select>/);
  assert.doesNotMatch(html, /Settled job|Finished job/);
  assert.doesNotMatch(html, /style=|onclick=/);
  page.formOpen.papick = false;
});

test("nothing to bill: no New pay app button", () => {
  core.setState(company([doneSettled, doneDrafted, finished]));
  assert.doesNotMatch(reg.views.payapps(), /payapp-new/);
});

test("Continue needs a job; a job in progress opens its Progress pay app form", async () => {
  core.setState(company([going, doneToBill]));
  page.formOpen.papick = true;
  dom.all['[data-form-key="papick"] [data-field]'] = [{ dataset: { field: "job" }, value: "" }];
  dom.requests.length = 0;
  await reg.actions["payapp-new-open"]();
  assert.equal(page.formOpen.progress, undefined, "nothing opens until a job is picked");
  dom.all['[data-form-key="papick"] [data-field]'] = [{ dataset: { field: "job" }, value: "jg" }];
  await reg.actions["payapp-new-open"]();
  assert.equal(page.currentView, "job");
  assert.equal(page.currentJobId, "jg");
  assert.equal(page.formOpen.progress, true, "the job page's own progress form");
  assert.equal(dom.requests.length, 0, "nothing drafted yet");
  delete dom.all['[data-form-key="papick"] [data-field]'];
  page.formOpen.progress = false;
});

test("a job marked done drafts the way its page's Draft pay app does", async () => {
  core.setState(company([doneToBill]));
  dom.requests.length = 0;
  dom.answers.unshift((path) => (path === "/api/jobs/jd/draft" ? { status: 200, body: company([doneToBill]) } : undefined));
  await reg.actions["payapp-new"]();
  assert.equal(page.currentJobId, "jd");
  assert.deepEqual(dom.requests.map(r => [r.method, r.path]), [["POST", "/api/jobs/jd/draft"]]);
  dom.answers.shift();
});

test("a member's job page buttons decide: drafting is everyone's", () => {
  const starts = payAppStarts([going, doneToBill], { list: [] });
  assert.equal(starts.length, 2);
});
