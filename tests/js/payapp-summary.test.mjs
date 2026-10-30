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
