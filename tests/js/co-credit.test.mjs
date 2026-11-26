// A negative change order (Stephen's mother, Nov 21): the form has a plain choice, Adds to the
// contract or Credit (deducts). Picked "credit", the amount and cost lines are typed as plain
// figures and sent as minus; "(1,250)" reads as a credit too; a credit says so where it shows.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
globalThis.requestAnimationFrame ||= (fn) => fn();
const page = await import("../../static/js/state.js");
const core = await import("../../static/js/app-core.js");
const cos = await import("../../static/js/views/job-cos.js");
const { centsFrom } = await import("../../static/js/format.js");

const job = { id: "ja", name: "A job", code: "JA", records: [], change_orders: [], invoices: [], payments: [] };
const F = '[data-form-key="newco"] [data-field]';
const fields = (o) => Object.entries(o).map(([field, value]) => ({ dataset: { field }, value }));
const lastPost = () => dom.requests.filter(r => r.method === "POST").at(-1);

test("the form offers Adds to the contract or Credit (deducts), adds picked to start", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState({ company: { name: "Pine" }, jobs: [job], payers: [], inbox: [], email_log: [], files: [] });
  const html = cos.coFormFields(job);
  assert.match(html, /data-for="co-kind" data-value="add" aria-pressed="true">Adds to the contract</);
  assert.match(html, /data-for="co-kind" data-value="deduct" aria-pressed="false">Credit \(deducts\)</);
});

test("Credit (deducts) sends a plain amount as a credit", async () => {
  dom.all[F] = fields({ title: "Drop the panel upgrade", reason: "owner_request", kind: "deduct", amount_cents: "1,250" });
  dom.requests.length = 0;
  await cos.addChangeOrder("ja");
  assert.equal(lastPost().path, "/api/jobs/ja/add-co");
  assert.equal(lastPost().body.amount_cents, -125_000);
  delete dom.all[F];
});

test("Credit (deducts) with cost lines makes each line and the total a credit", async () => {
  dom.all[F] = fields({ title: "Owner buys fixtures", reason: "owner_request", kind: "deduct", amount_cents: "",
    item_label_0: "Fixtures", item_amount_0: "800", item_label_1: "Labor", item_amount_1: "200" });
  dom.requests.length = 0;
  // Two cost line rows on the form.
  cos.actions["co-add-item"](); cos.actions["co-add-item"]();
  await cos.addChangeOrder("ja");
  const body = lastPost().body;
  assert.deepEqual(body.items.map(i => i.amount_cents), [-80_000, -20_000]);
  assert.equal(body.amount_cents, -100_000);
  delete dom.all[F];
  cos.actions["new-co-done"]();
});

test("Adds to the contract keeps an amount as typed, and a typed minus is still a credit", async () => {
  dom.all[F] = fields({ title: "Two outlets", reason: "owner_request", kind: "add", amount_cents: "500" });
  dom.requests.length = 0;
  await cos.addChangeOrder("ja");
  assert.equal(lastPost().body.amount_cents, 50_000);
  dom.all[F] = fields({ title: "Two outlets", reason: "owner_request", kind: "add", amount_cents: "-500" });
  await cos.addChangeOrder("ja");
  assert.equal(lastPost().body.amount_cents, -50_000);
  delete dom.all[F];
});

test("pricing a change order later has the same choice", async () => {
  const co = { id: "co-ja-1", number: 1, title: "Extra", amount_cents: 0, unpriced: true, status: "pending", included_in_billing: false };
  dom.all['[data-form-key="coprice-co-ja-1"] [data-field]'] = fields({ kind: "deduct", amount_cents: "300" });
  dom.requests.length = 0;
  await cos.actions["co-price"]({ dataset: { co: co.id } }, "ja");
  assert.equal(lastPost().path, "/api/jobs/ja/co-price/co-ja-1");
  assert.equal(lastPost().body.amount_cents, -30_000);
  delete dom.all['[data-form-key="coprice-co-ja-1"] [data-field]'];
});

test("a bookkeeper's (1,250.00) and a typographic minus read as credits", () => {
  assert.equal(centsFrom("(1,250.00)").cents, -125_000);
  assert.equal(centsFrom("($1,250)").cents, -125_000);
  assert.equal(centsFrom("−1,250").cents, -125_000);
  assert.ok(centsFrom("(-1,250)").error, "a doubled sign is refused");
  assert.ok(centsFrom("(12").error);
});

test("a credit reads as a credit, not a bare minus", () => {
  assert.equal(cos.coAmountText({ amount_cents: -125_000 }), "Credit of $1,250.00");
  assert.equal(cos.coAmountText({ amount_cents: 125_000 }), "$1,250.00");
  assert.equal(cos.coAmountText({ amount_cents: 0, unpriced: true }), "No price yet");
});
