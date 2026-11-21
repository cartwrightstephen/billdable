// Fixes from the review of Duracash Round 2 (Nov 17) on the page: no Georgia form to fill in on a
// sent pay app whose waiver is settled.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
await import("../../static/js/app-core.js");
const { filledFormOffered } = await import("../../static/js/views/paperwork.js");

test("item 8: no filled-in form on a sent pay app whose waiver is settled", () => {
  assert.equal(filledFormOffered(null), true);
  assert.equal(filledFormOffered({ status: "draft" }), true);
  assert.equal(filledFormOffered({ status: "sent" }), true);
  assert.equal(filledFormOffered({ status: "paid", waiver_due: true, waiver_sent: false }), true, "its waiver is still owed");
  assert.equal(filledFormOffered({ status: "paid", waiver_due: true, waiver_sent: true }), false);
  assert.equal(filledFormOffered({ status: "paid", waiver_due: false, waiver_sent: true }), false, "settled by the part waivers");
});
