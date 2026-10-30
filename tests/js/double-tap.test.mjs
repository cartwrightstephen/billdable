// A second tap on a button whose action is still waiting on the server does nothing: a double
// tap on Record must not record the payment twice. node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom, stubEl } from "./fakedom.mjs";

const dom = installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");

const company = () => ({ company: { name: "Pine" }, jobs: [{ id: "j1", name: "Oak", code: "OAK", payer_id: "", closed: false, invoices: [], payments: [], change_orders: [] }],
  payers: [], inbox: [], email_log: [], email_log_total: 0 });
const box = (field, value) => ({ dataset: { field }, value });

test("a double tap on Record sends one payment; a tap after the answer sends another", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company());
  dom.all['[data-form-key="match"] [data-field]'] = [box("amount_cents", "4,000"), box("received_on", "2026-10-20"), box("reference", ""), box("invoice_id", "inv1")];
  let release;
  const gate = new Promise((r) => { release = r; });
  dom.requests.length = 0;
  dom.answers = [async (p) => (p === "/api/jobs/j1/match-payment" ? (await gate, { status: 200, body: company() }) : undefined)];
  const record = stubEl({ dataset: { action: "match", id: "j1" } });
  const first = core.runAction(reg.actions.match, record, {});
  const second = core.runAction(reg.actions.match, record, {});
  release();
  await first; await second;
  const sent = () => dom.requests.filter(r => r.path === "/api/jobs/j1/match-payment").length;
  assert.equal(sent(), 1);
  await core.runAction(reg.actions.match, record, {});
  assert.equal(sent(), 2);
  delete dom.all['[data-form-key="match"] [data-field]'];
});

test("a button for another job, or an action that finishes at once, is not held back", async () => {
  let runs = 0;
  const slow = () => { runs++; return new Promise((r) => setTimeout(r, 5)); };
  const a = core.runAction(slow, stubEl({ dataset: { action: "slow", id: "j1" } }), {});
  const b = core.runAction(slow, stubEl({ dataset: { action: "slow", id: "j2" } }), {});
  await a; await b;
  assert.equal(runs, 2);
  let menus = 0;
  const menu = () => { menus++; };
  core.runAction(menu, stubEl({ dataset: { action: "menu" } }), {});
  core.runAction(menu, stubEl({ dataset: { action: "menu" } }), {});
  assert.equal(menus, 2);
});
