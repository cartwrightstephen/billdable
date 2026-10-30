// Settings, Company details: what was typed stays through a redraw (a switch saved, the
// QuickBooks status arriving) until it is saved. node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const forms = await import("../../static/js/forms.js");
const settings = await import("../../static/js/views/settings.js");
const reg = await import("../../static/js/views/index.js");

const company = (over = {}) => ({ company: { id: "co-p", name: "Pine & Co", phone: "", address: "", license: "", ...over }, jobs: [], payers: [], inbox: [], email_log: [], email_log_total: 0 });

test("typed company details survive a redraw, and each box is part of the company form", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  page.setLocation("company");
  core.setState(company());
  const saved = reg.views.company();
  assert.match(saved, /data-form-key="company"/);
  for (const [id, field] of [["co-name", "name"], ["co-phone", "phone"], ["co-address", "address"], ["co-license", "license"], ["co-style", "billing_style"]])
    assert.match(saved, new RegExp(`id="${id}" data-field="${field}"`));
  assert.match(saved, /id="co-name" data-field="name" value="Pine &amp; Co"/);
  // Typed (watchForms keeps it), then a switch saves and the page is drawn again.
  forms.formDraft.company = { address: "2 Elm St, Decatur, GA", license: 'GA "123"' };
  const redrawn = reg.views.company();
  assert.match(redrawn, /id="co-address" data-field="address" value="2 Elm St, Decatur, GA"/);
  assert.match(redrawn, /id="co-license" data-field="license" value="GA &quot;123&quot;"/);
  assert.match(redrawn, /id="co-name" data-field="name" value="Pine &amp; Co"/);
});

test("once saved, the boxes show what was saved", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company());
  forms.formDraft.company = { address: "2  Elm St" };
  const box = (value) => ({ value });
  Object.assign(dom.byId, { "co-name": box("Pine & Co"), "co-address": box("2  Elm St"), "co-phone": box(""), "co-license": box(""), "co-style": box("pay_apps") });
  dom.answers = [(p) => (p === "/api/company" ? { status: 200, body: company({ address: "2 Elm St" }) } : undefined)];
  await reg.actions["save-company"]();
  assert.equal(forms.formDraft.company, undefined);
  assert.match(reg.views.company(), /id="co-address" data-field="address" value="2 Elm St"/);
  for (const id of ["co-name", "co-address", "co-phone", "co-license", "co-style"]) delete dom.byId[id];
});
