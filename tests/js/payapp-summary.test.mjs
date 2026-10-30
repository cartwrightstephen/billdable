// A pay app's page shows its summary, lines 1-9 as the PDF prints them, from the server's figures
// (Stephen's Oct 29 review, M2). node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const { payappSummaryHtml } = await import("../../static/js/views/payapps.js");

test("the summary card reads the figures in the PDF's order, with prior payments received", () => {
  const figures = { original: 3_450_000, net_co: 0, unsigned_co: 100_000, contract_to_date: 3_550_000, completed: 1_200_000, retainage: 120_000,
    earned: 1_080_000, previous: 600_000, due: 480_000, balance: 2_470_000, released: 0, received_before: 500_000 };
  const html = payappSummaryHtml({ figures });
  const order = ["Contract sum", "Approved change orders", "Unsigned change orders billed here", "Contract sum to date", "Completed and stored to date",
    "Retainage held", "Earned less retainage", "Less previous pay apps", "Current amount due", "Balance to finish, including retainage"];
  let at = -1;
  for (const label of order) { const i = html.indexOf(`>${label}<`); assert.ok(i > at, label); at = i; }
  assert.match(html, /Current amount due<\/span><span>\$4,800\.00/);
  assert.match(html, /Prior payments received: \$5,000\.00/);
  assert.doesNotMatch(html, /style=|onclick=/);
  assert.doesNotMatch(payappSummaryHtml({ figures: { ...figures, unsigned_co: 0 } }), /Unsigned change orders billed here/);
  assert.equal(payappSummaryHtml({}), "", "a deleted draft has no figures");
});

test("a draft whose quantities came from daily records says so, as the schedule card does (M1)", async () => {
  const core = await import("../../static/js/app-core.js");
  const page = await import("../../static/js/state.js");
  const reg = await import("../../static/js/views/index.js");
  const { draftFromDailyOn } = await import("../../static/js/jobs.js");
  const sov_view = { present: true, lines: [{ id: "l1", from_daily_on: "2026-10-28" }, { id: "l2", from_daily_on: "2026-10-30" }, { id: "l3" }] };
  const draft = { id: "i2", number: "PA-02", status: "draft", amount_cents: 1000, lines: [], packet: [],
    sov_lines: [{ line_id: "l1", qty_milli_this: 2000 }, { line_id: "l2", qty_milli_this: 1000 }, { line_id: "l3", qty_milli_this: 500 }]
      .map(e => ({ item: "1", description: "Pavers", unit: "SF", rate_cents: 100, scheduled_cents: 10000, qty_milli: 100000, qty_milli_to_date: e.qty_milli_this, amount_cents: 100, to_date_cents: 100, ...e })) };
  const j = { id: "ja", name: "Pine", code: "PINE", payer_id: "p1", payer: { name: "Ridge", ap_email: "ap@x.example" }, invoices: [draft], payments: [],
    change_orders: [], blockers: [], money: {}, sov_view };
  assert.equal(draftFromDailyOn(j, draft), "2026-10-30", "the latest day among the lines it bills");
  assert.equal(draftFromDailyOn(j, { ...draft, sov_lines: [{ line_id: "l2", qty_milli_this: 0 }, { line_id: "l3", qty_milli_this: 500 }] }), "", "typed, or not billed this period");
  assert.equal(draftFromDailyOn(j, { ...draft, status: "sent" }), "", "only while it is a draft");
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState({ company: { name: "Pine" }, jobs: [j], payers: [{ id: "p1", name: "Ridge" }], inbox: [], email_log: [], email_log_total: 0 });
  page.setLocation("payapp-detail", "ja", "i2");
  assert.match(reg.views.payappDetail(), /<p class="sov-daily payapp-daily">Updated from daily records on Oct 30, 2026<\/p>/);
});
