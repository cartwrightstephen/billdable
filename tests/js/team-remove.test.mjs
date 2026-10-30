// Removing a team member changes the inbound email, text and calendar addresses on the server;
// the Inbox and Calendar then ask for the new ones instead of showing the old.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom, stubEl } from "./fakedom.mjs";

const dom = installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const inbox = await import("../../static/js/views/inbox.js");
const settings = await import("../../static/js/views/settings.js");
const reg = await import("../../static/js/views/index.js");

const company = () => ({ company: { name: "Pine" }, jobs: [], payers: [], inbox: [], email_log: [], email_log_total: 0 });

// askConfirm's dialog, answered with its main button.
function confirmEverything() {
  const made = [];
  const real = document.createElement;
  document.createElement = (tag) => {
    const handlers = {};
    const el = stubEl({ tag, addEventListener: (type, fn) => { handlers[type] = fn; }, close() {}, remove() {},
      showModal() { made.find(b => b.tag === "button" && b.className.startsWith("btn") && !b.className.includes("secondary"))?.click(); } });
    el.click = () => handlers.click && handlers.click();
    made.push(el);
    return el;
  };
  return () => { document.createElement = real; };
}

test("after a member is removed, the Inbox asks for the new addresses", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company());
  let token = "OLD";
  dom.answers = [
    (p) => (p === "/api/import/settings" ? { status: 200, body: { email_webhook: `https://x/api/inbound/email/${token}`, sms_webhook: token, calendar_url: `https://x/cal/${token}` } } : undefined),
    (p) => (p === "/api/team/u2/remove" ? (token = "NEW", { status: 200, body: { users: [], invites: [] } }) : undefined),
  ];
  const cal = { innerHTML: "" };
  dom.byId["cal-url"] = cal;
  await inbox.loadImportSettings();
  assert.match(cal.innerHTML, /OLD/);
  const restore = confirmEverything();
  try { await reg.actions["team-remove"]({ dataset: { user: "u2", name: "Sam" } }); }
  finally { restore(); }
  await inbox.loadImportSettings();
  assert.match(cal.innerHTML, /NEW/);
  delete dom.byId["cal-url"];
});
