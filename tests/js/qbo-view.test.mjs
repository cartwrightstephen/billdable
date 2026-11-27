// QuickBooks both ways (Nov 27): the QuickBooks card's Sync now, items and client links, and the
// pay app page's Send to QuickBooks, each shown only to those whose permissions cover it.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
globalThis.requestAnimationFrame = () => 0;
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const pay = await import("../../static/js/views/payments.js");

const inv = { id: "inv-1", number: "PA-OAK-02", status: "sent", amount_cents: 100_000 };
const job = { id: "job-oak", name: "Oak deck", closed: false, invoices: [inv], payments: [], change_orders: [] };
const company = (qbo) => ({ company: { name: "Pine", ...(qbo ? { qbo } : {}) }, jobs: [job], payers: [{ id: "payer-1", name: "Ellison Residential" }],
  inbox: [], email_log: [], email_log_total: 0, files: [] });

async function connected() {
  dom.answers.push((path) => (path === "/api/qbo/status" ? { status: 200, body: { configured: true, connected: true, environment: "sandbox", connected_at: "2026-11-27" } } : undefined));
  await pay.loadQboStatus();
}

test("Send to QuickBooks shows on a sent pay app when connected, for someone who sees the financials", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner", perms: ["financials", "company", "record_payments"] });
  core.setState(company());
  assert.equal(pay.qboPushHtml(job, inv), "", "not connected: nothing");
  await connected();
  assert.match(pay.qboPushHtml(job, inv), /data-action="qbo-push" data-job="job-oak" data-inv="inv-1">Send to QuickBooks</);
  const sent = { ...inv, qbo: { doc_number: "PA-OAK-02", pushed_at: "2026-11-27T10:00:00+00:00", pushed_by: "Pat" } };
  assert.match(pay.qboPushHtml(job, sent), /Send to QuickBooks again/);
  assert.match(pay.qboPushHtml(job, sent), /In QuickBooks as invoice PA-OAK-02/);
  page.setMe({ name: "Mo", company_id: "co-p", role: "member", perms: ["record_payments"] });
  assert.doesNotMatch(pay.qboPushHtml(job, sent), /data-action="qbo-push"/, "a Member without financials sees where it went, not the button");
});

test("the card: Sync now for those who record payments; items and links for those who hold company and financials", async () => {
  await connected();
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner", perms: ["financials", "company", "record_payments"] });
  core.setState(company({ billing_item: { id: "1", name: "Construction billing" }, retainage_item: null }));
  let card = pay.qboCardHtml(false);
  assert.match(card, /data-action="qbo-sync-now">Sync now</);
  assert.match(card, /Pay apps go in on <strong>Construction billing<\/strong>, with no retainage item chosen/);
  assert.match(card, /data-action="qbo-items">Choose items</);
  assert.match(card, /data-action="qbo-customers">Link clients</);
  assert.doesNotMatch(card, /Read-only/);
  page.setMe({ name: "Mo", company_id: "co-p", role: "member", perms: ["record_payments"] });
  card = pay.qboCardHtml(false);
  assert.match(card, /Sync now/);
  assert.doesNotMatch(card, /qbo-items|qbo-customers/);
});

test("Choose items and Save send the picked ids; Sync now records what QuickBooks has and says so", async () => {
  await connected();
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner", perms: ["financials", "company", "record_payments"] });
  core.setState(company());
  dom.answers.unshift((path, init) => {
    if (path === "/api/qbo/items") return { status: 200, body: { items: [{ id: "1", name: "Construction billing", account: "Income" }, { id: "2", name: "Retainage", account: "Retainage Receivable" }], mapping: {} } };
    if (path === "/api/qbo/settings") return { status: 200, body: company({ billing_item: { id: "1", name: "Construction billing" }, retainage_item: { id: "2", name: "Retainage" } }) };
    if (path === "/api/qbo/sync") return { status: 200, body: { ...company(), qbo_sync: { recorded: [{ number: "PA-OAK-02", amount_cents: 100_000 }], problems: [], checked: 1 } } };
    return undefined;
  });
  await pay.actions["qbo-items"]();
  assert.match(pay.qboCardHtml(false), /id="qbo-billing-item"[\s\S]*Retainage Receivable/);
  dom.byId["qbo-billing-item"] = { value: "1" };
  dom.byId["qbo-retainage-item"] = { value: "2" };
  await pay.actions["qbo-save-items"]();
  const saved = dom.requests.find(r => r.path === "/api/qbo/settings");
  assert.deepEqual(saved.body, { billing_item: { id: "1", name: "Construction billing" }, retainage_item: { id: "2", name: "Retainage" } });
  assert.doesNotMatch(pay.qboCardHtml(false), /id="qbo-billing-item"/, "closed once saved");
  await pay.actions["qbo-sync-now"]();
  assert.match(pay.qboCardHtml(false), /Recorded from QuickBooks: PA-OAK-02 \$1,000\.00/);
});
