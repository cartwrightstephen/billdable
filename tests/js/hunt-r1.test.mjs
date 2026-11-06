// Fixes from the independent hunt of Round 1 (Nov 6) and its acceptance test: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { rowButtons } from "../../static/js/today.js";

test("a Job's done? row offers Final pay app only to someone who may mark work done", () => {
  const r = { kind: "says_done", job_id: "j1", item_id: "in-1", button: "Final pay app" };
  assert.match(rowButtons(r, { owner: true }), /data-action="inbox-mark-done"/);
  const member = rowButtons(r, { owner: false, list: ["record_payments"] });
  assert.doesNotMatch(member, /inbox-mark-done/);
  assert.match(member, /data-action="open-job"/, "the row still opens the job");
  assert.match(rowButtons(r, { owner: false, list: ["manage_jobs"] }), /inbox-mark-done/);
});
