// Money > Change orders: the New change order form's Job box starts on "Pick a job…" when there
// is more than one open job, so a change order never lands on a job nobody picked.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const money = await import("../../static/js/views/money.js");

const job = (id, name) => ({ id, name, code: id.toUpperCase(), payer_id: "", closed: false, invoices: [], payments: [], change_orders: [] });
const company = (jobs) => ({ company: { name: "Pine" }, jobs, payers: [], inbox: [], email_log: [], email_log_total: 0 });

test("with several open jobs the Job box starts on Pick a job, and Create refuses until one is picked", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company([job("ja", "Oldest job"), job("jb", "Newer job")]));
  const html = money.views.cos();
  assert.match(html, /<select id="new-co-job"[^>]*><option value="" selected>Pick a job…<\/option><option value="ja">Oldest job<\/option>/);
  dom.all['[data-form-key="newco"] [data-field]'] = [{ dataset: { field: "job" }, value: "" }, { dataset: { field: "title" }, value: "Two outlets" }, { dataset: { field: "amount_cents" }, value: "500" }];
  dom.requests.length = 0;
  await money.actions["add-co-from-list"]();
  assert.equal(dom.requests.length, 0);
  delete dom.all['[data-form-key="newco"] [data-field]'];
});

test("with one open job it is picked already", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company([job("ja", "Only job")]));
  assert.match(money.views.cos(), /<option value="ja" selected>Only job<\/option>/);
});
