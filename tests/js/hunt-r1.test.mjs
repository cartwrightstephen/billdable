// Fixes from the independent hunt of Round 1 (Nov 6) and its acceptance test: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
globalThis.requestAnimationFrame = (f) => f();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");
const { rowButtons } = await import("../../static/js/today.js");
const paperwork = await import("../../static/js/views/paperwork.js");

test("a Job's done? row offers Final pay app only to someone who may mark work done", () => {
  const r = { kind: "says_done", job_id: "j1", item_id: "in-1", button: "Final pay app" };
  assert.match(rowButtons(r, { owner: true }), /data-action="inbox-mark-done"/);
  const member = rowButtons(r, { owner: false, list: ["record_payments"] });
  assert.doesNotMatch(member, /inbox-mark-done/);
  assert.match(member, /data-action="open-job"/, "the row still opens the job");
  assert.match(rowButtons(r, { owner: false, list: ["manage_jobs"] }), /inbox-mark-done/);
});

test("a photo on a waiver item takes one tap that says which waiver it is", () => {
  assert.equal(paperwork.waiverLookWords({ waiver_kind: "conditional_progress" }), "I looked at it: it's the conditional waiver for this pay app");
  assert.equal(paperwork.waiverLookWords({ waiver_kind: "unconditional_final" }), "I looked at it: it's the unconditional waiver for this pay app");
  assert.equal(paperwork.confirmedText({ confirmed_by: "Sam", confirmed_at: "2026-11-06T10:00:00+00:00", looked_as: "conditional" }),
    "Looked at by Sam on Nov 6, 2026: the conditional waiver");
  assert.match(paperwork.confirmedText({ confirmed_by: "Sam", confirmed_at: "2026-11-06T10:00:00+00:00", unread: "photo" }), /^Photo added by Sam/);
});
