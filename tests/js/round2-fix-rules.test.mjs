// Fixes from the second review of Duracash Round 2 (Nov 17), on the page.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { closeBlockerActions } from "../../static/js/jobs.js";

const done = { id: "j1", work_status: "complete", invoices: [], payments: [], change_orders: [], closeout_packet: [],
  money: { retainage_open_cents: 100000 } };

test("B34: Record a payment and Record retainage paid next to the blockers need Record payments", () => {
  const pm = { list: ["manage_jobs", "close_jobs", "send_payapps"] };
  const labels = (b, perms) => closeBlockerActions(done, b, perms).map(a => a.label);
  assert.ok(!labels({ kind: "outstanding" }, pm).includes("Record a payment"));
  assert.ok(!labels({ kind: "retainage" }, pm).includes("Record retainage paid"));
  const office = { list: ["record_payments"] };
  assert.ok(labels({ kind: "outstanding" }, office).includes("Record a payment"));
  assert.ok(labels({ kind: "retainage" }, office).includes("Record retainage paid"));
});
