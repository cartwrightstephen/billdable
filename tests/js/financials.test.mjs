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

test("a month whose only money is a refund draws below the line, never a negative height (B11)", () => {
  const rows = [{ month: "2026-10", billed_cents: 500000, collected_cents: 300000 },
                { month: "2026-11", billed_cents: 0, collected_cents: -200000 }];
  const svg = fin.monthlyChart(rows);
  const heights = [...svg.matchAll(/height="(-?[\d.]+)"/g)].map(m => Number(m[1]));
  assert.ok(heights.every(h => h >= 0), `no negative heights: ${heights}`);
  const axis = Number(svg.match(/class="bar-axis"[^>]*y1="([\d.]+)"/)[1]);
  const refund = svg.match(/collected -\$2,000\.00<\/title>[\s\S]*?<rect class="bar-collected" x="[\d.]+" y="([\d.]+)" width="[\d.]+" height="([\d.]+)"/);
  assert.ok(refund, "the tooltip keeps the true figure");
  assert.equal(Number(refund[1]), axis, "the refund bar starts at the line and hangs below it");
  assert.ok(Number(refund[2]) > 0);
  const oct = svg.match(/<rect class="bar-billed" x="[\d.]+" y="([\d.]+)" width="[\d.]+" height="([\d.]+)"/);
  assert.equal(Number(oct[1]) + Number(oct[2]), axis, "a positive bar stands on the line");
});
