// Money > Financials and the money strip (Stephen, Nov 13): the company's main figures on every
// page, and a Financials tab with what a bank asks for. Both need "See company financials".
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
globalThis.requestAnimationFrame = (f) => f();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");
const fin = await import("../../static/js/views/financials.js");
const routes = await import("../../static/js/routes.js");

const owner = { name: "Pat", company_id: "co-p", role: "owner" };
const member = { name: "Fred", company_id: "co-p", role: "member", perms: ["record_payments"] };
const given = { ...member, perms: ["record_payments", "financials"] };
const totals = { outstanding_cents: 17765000, unbilled_cents: 13100000, retainage_cents: 1695000, backlog_cents: 5300000 };
function at(view, me = owner, jobs = [{ id: "j-1" }]) {
  page.setMe(me);
  core.setState({ company: { name: "Pine" }, jobs, payers: [], inbox: [], email_log: [], files: [], totals });
  page.setLocation(view, null);
}

test("the strip: four figures in whole dollars, each opening its page", () => {
  at("jobs");
  const html = fin.moneyStripHtml();
  const items = [...html.matchAll(/data-view="([\w-]+)"><span class="strip-k">([^<]+)<\/span> <span class="strip-v">([^<]+)</g)].map(m => [m[2], m[3], m[1]]);
  assert.deepEqual(items, [["Owed to you", "$177,650", "collections"], ["Done, not billed", "$131,000", "jobs"],
    ["Retainage held", "$16,950", "retainage"], ["Backlog", "$53,000", "financials"]]);
  assert.match(html, /strip-item strip-hot[^>]*data-view="jobs"/, "work waiting to be billed stands out");
});

test("the strip is on every page but the Overview, whose tiles show the same figures", () => {
  for (const view of ["jobs", "payapps", "inbox", "company", "financials"]) { at(view); assert.ok(fin.moneyStripHtml(), view); }
  at("dashboard");
  assert.equal(fin.moneyStripHtml(), "");
});

test("only those who may see the financials get the strip and the page", () => {
  at("jobs", member);
  assert.equal(fin.moneyStripHtml(), "");
  at("financials", member);
  assert.match(reg.views.financials(), /Ask an owner for "See company financials"/);
  at("jobs", given);
  assert.ok(fin.moneyStripHtml());
});

test("a company with no jobs yet has no strip", () => {
  at("jobs", owner, []);
  assert.equal(fin.moneyStripHtml(), "");
});

test("Financials has its own address under Money", () => {
  assert.equal(routes.pathFor("financials"), "/money/financials");
  assert.deepEqual(routes.routeFromPath("/money/financials"), { view: "financials" });
});
