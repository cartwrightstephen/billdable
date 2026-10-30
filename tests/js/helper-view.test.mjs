// The helper's buttons that change something ("Send the follow-up"): it says Done only when the
// server did it, and a refusal is told in the helper, not under some other button on the page.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom, stubEl } from "./fakedom.mjs";

const dom = installFakeDom();
const handlers = {};
dom.byId["ai-panel"] = stubEl({ addEventListener: (kind, fn) => { handlers[kind] = fn; } });
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const helper = await import("../../static/js/views/helper.js");

const company = () => ({ company: { name: "Pine" }, jobs: [], payers: [], inbox: [], email_log: [], email_log_total: 0 });

async function tapPost(answer) {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company());
  helper.helperLog.length = 0;
  helper.helperLog.push({ who: "helper", text: "OAK-1 is due a follow-up.", actions: [{ type: "post", label: "Send the follow-up", path: "/api/jobs/j1/follow-up" }] });
  dom.answers = [answer];
  // The last button tapped on the page itself, still there: a refusal must not land under it.
  const placed = [];
  core.setActionEl(stubEl({ dataset: { action: "tab" }, closest: () => stubEl({ insertAdjacentElement: (_, card) => placed.push(card) }) }));
  const btn = { dataset: { helperMsg: "0", helperAction: "0" } };
  await handlers.click({ target: { closest: (sel) => (sel === "[data-helper-action]" ? btn : null) } });
  return { said: helper.helperLog.at(-1).text, placed };
}

test("a refused follow-up is not reported as done, and the reasons show in the helper", async () => {
  const { said, placed } = await tapPost(() => ({ status: 409, body: { error: "Confirm your email first.", blockers: ["Nothing is sent until your email is confirmed."] } }));
  assert.doesNotMatch(said, /^Done/);
  assert.match(said, /Not done\. Confirm your email first\./);
  assert.match(said, /- Nothing is sent until your email is confirmed\./);
  assert.deepEqual(placed, []);
});

test("offline, the helper says it wasn't done", async () => {
  const { said } = await tapPost(() => "offline");
  assert.doesNotMatch(said, /^Done/);
  assert.match(said, /^Not done\./);
});

test("a follow-up that went out says Done", async () => {
  const { said } = await tapPost((p) => (p === "/api/jobs/j1/follow-up" ? { status: 200, body: company() } : undefined));
  assert.match(said, /^Done\. The page is updated/);
});

test("Settings, Helper answers is Page not found for anyone but the Duracash team (D6)", async () => {
  const reg = await import("../../static/js/views/index.js");
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company());
  page.setAccount({ helperAdmin: false });
  const html = reg.views["helper-admin"]();
  assert.match(html, /Page not found/);
  assert.doesNotMatch(html, /HELPER_ADMIN_USER_IDS|BILLING_ENFORCE|helper-admin-body/);
  page.setAccount({ helperAdmin: true });
  assert.match(reg.views["helper-admin"](), /<h1>Helper answers<\/h1>/);
  page.setAccount({ helperAdmin: false });
});
