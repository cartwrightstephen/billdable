// Page addresses: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { VIEW_PATHS, DETAIL_PATHS, DETAIL_PARENT, pathFor, routeFromPath } from "../../static/js/routes.js";

test("every view's address leads back to that view", () => {
  for (const [view, path] of Object.entries(VIEW_PATHS)) assert.deepEqual(routeFromPath(path), { view }, path);
  assert.deepEqual(routeFromPath("/"), { view: "dashboard" });
  assert.deepEqual(routeFromPath("/today/"), { view: "dashboard" }, "a trailing slash is the same page");
});

test("job and detail pages carry their ids", () => {
  assert.deepEqual(routeFromPath("/jobs/job-1"), { view: "job", jobId: "job-1" });
  assert.deepEqual(routeFromPath("/money/pay-app/job-1/inv-2"), { view: "payapp-detail", jobId: "job-1", detailId: "inv-2" });
  assert.deepEqual(routeFromPath("/money/waiver/job-1"), { view: "waiver-detail", jobId: "job-1", detailId: null });
  for (const view of Object.keys(DETAIL_PATHS)) {
    const r = routeFromPath(pathFor(view, "j 1", "d/2"));
    assert.deepEqual(r, { view, jobId: "j 1", detailId: "d/2" }, view);
  }
});

test("pathFor round-trips escaped ids", () => {
  // Ids in the page's state are escaped once (escDeep); the address uses the raw text.
  assert.equal(pathFor("job", "a&amp;b"), "/jobs/a%26b");
  assert.deepEqual(routeFromPath(pathFor("job", "a&amp;b")), { view: "job", jobId: "a&b" });
  assert.equal(pathFor("job", null), "/today", "a job page without a job is Today");
  assert.equal(pathFor("nowhere"), "/today");
});

test("unknown or broken addresses are refused", () => {
  for (const bad of ["/nope", "/jobs/a/b", "/money/pay-app", "/money/pay-app/j/i/x", "/jobs/%E0%A4%A"]) assert.equal(routeFromPath(bad), null, bad);
});

test("Settings has a page each, /settings opens Company, and Paperwork belongs to Pay apps", () => {  // R6-03
  for (const [view, path] of [["company", "/settings/company"], ["team", "/settings/team"], ["account", "/settings/account"], ["billing", "/settings/billing"]]) {
    assert.equal(pathFor(view), path);
    assert.deepEqual(routeFromPath(path), { view });
  }
  assert.deepEqual(routeFromPath("/settings"), { view: "company" }, "the old address still opens");
  assert.deepEqual(routeFromPath("/money/packets"), { view: "packets" });
  assert.equal(DETAIL_PARENT.packets, "payapps");
  assert.equal(DETAIL_PARENT["packet-detail"], "payapps");
});
