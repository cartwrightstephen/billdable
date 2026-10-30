// Page addresses: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { VIEW_PATHS, PATH_ALIASES, DETAIL_PATHS, DETAIL_PARENT, pathFor, routeFromPath, refusalLink, refusalLinkData, refusalToastAction } from "../../static/js/routes.js";

test("every view's address leads back to that view", () => {
  for (const [view, path] of Object.entries(VIEW_PATHS)) assert.deepEqual(routeFromPath(path), { view }, path);
  assert.deepEqual(routeFromPath("/"), { view: "dashboard" });
  assert.deepEqual(routeFromPath("/dashboard/"), { view: "dashboard" }, "a trailing slash is the same page");
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
  assert.equal(pathFor("job", null), "/dashboard", "a job page without a job is the Dashboard");
  assert.equal(pathFor("nowhere"), "/dashboard");
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

test("a refusal's link opens the page that fixes it, and nothing else", () => {  // Oct 23 note #13
  assert.deepEqual(refusalLink({ label: "Open the paperwork for PA-OAK-3", view: "packet-detail", job_id: "j1", detail: "inv1" }),
    { label: "Open the paperwork for PA-OAK-3", view: "packet-detail", jobId: "j1", detailId: "inv1", href: "/money/packet/j1/inv1" });
  assert.deepEqual(refusalLink({ label: "Open PA-OAK-3", view: "payapp-detail", job_id: "j1", detail: "inv1" }).href, "/money/pay-app/j1/inv1");
  assert.equal(refusalLink({ label: "Open the paperwork", view: "packet-detail", job_id: "j1", detail: "" }).detailId, null);
  for (const bad of [undefined, null, "x", {}, { label: "Go", view: "settings", job_id: "j1" }, { label: "Go", view: "packet-detail" }, { view: "packet-detail", job_id: "j1" }]) {
    assert.equal(refusalLink(bad), null, JSON.stringify(bad));
  }
});

test("the refusal card's button and the toast's both open the page", () => {  // Oct 23 note #13, review
  const navs = [];
  const go = (view, jobId, detailId) => navs.push([view, jobId, detailId]);
  // What static/app.js's "nav" handler does with the card button's data-*.
  const click = (data) => go(data.view, data.id || null, data.detail || null);
  const draft = refusalLink({ label: "Open the paperwork for PA-OAK-3", view: "packet-detail", job_id: "j1", detail: "inv1" });
  assert.deepEqual(refusalLinkData(draft), { action: "nav", view: "packet-detail", id: "j1", detail: "inv1" });
  click(refusalLinkData(draft));
  const toastAction = refusalToastAction(draft, go);
  assert.equal(toastAction.label, "Open the paperwork for PA-OAK-3");
  toastAction.run();
  const jobPaperwork = refusalLink({ label: "Open the paperwork", view: "packet-detail", job_id: "j1", detail: "" });
  assert.deepEqual(refusalLinkData(jobPaperwork), { action: "nav", view: "packet-detail", id: "j1" });
  click(refusalLinkData(jobPaperwork));
  refusalToastAction(jobPaperwork, go).run();
  assert.deepEqual(navs, [["packet-detail", "j1", "inv1"], ["packet-detail", "j1", "inv1"], ["packet-detail", "j1", null], ["packet-detail", "j1", null]]);
  // No link from the server: no button on the card, a plain toast.
  assert.equal(refusalLinkData(null), null);
  assert.equal(refusalToastAction(null, go), null);
});

test("the Oct 29 addresses, and every old one still opens its page", () => {
  for (const [view, path] of [["dashboard", "/dashboard"], ["results", "/dashboard/results"], ["calendar", "/dashboard/calendar"],
    ["daily", "/jobs/daily"], ["playbook", "/jobs/clients"], ["inbox", "/communications"], ["outbox", "/communications/outbox"],
    ["import", "/data/import"], ["export", "/data/export"], ["automations", "/automations"]]) {
    assert.equal(pathFor(view), path);
    assert.deepEqual(routeFromPath(path), { view }, path);
  }
  for (const [old, view] of [["/today", "dashboard"], ["/today/results", "results"], ["/today/calendar", "calendar"], ["/today/daily", "daily"],
    ["/settings/playbook", "playbook"], ["/inbox", "inbox"], ["/money/outbox", "outbox"], ["/settings/help", "account"], ["/settings", "company"]]) {
    assert.deepEqual(routeFromPath(old), { view }, old);
  }
  for (const [path, view] of Object.entries(PATH_ALIASES)) {
    assert.ok(VIEW_PATHS[view], `${path} opens a page that has an address`);
    assert.equal(Object.values(VIEW_PATHS).includes(path), false, `${path} is only an alias`);
  }
  assert.equal(VIEW_PATHS.help, undefined, "Help is gone");
  assert.equal(VIEW_PATHS.outbox.startsWith("/money"), false, "the Outbox left Money");
});

test("/jobs/daily and /jobs/clients are pages, not job ids", () => {
  assert.deepEqual(routeFromPath("/jobs/daily"), { view: "daily" });
  assert.deepEqual(routeFromPath("/jobs/clients"), { view: "playbook" });
  assert.deepEqual(routeFromPath("/jobs/history"), { view: "history" });
  assert.deepEqual(routeFromPath("/jobs/job-daily"), { view: "job", jobId: "job-daily" });
});
