// Settings, Company details: one form with one Save at the bottom (S1), the address in four boxes
// (S3), each box checked by its rule (G1); what was typed stays through a redraw until it is
// saved. node --test tests/js
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

// The company form's boxes, as formValues reads them.
function boxes(values) {
  const all = Object.entries(values).map(([field, value]) => ({ dataset: { field }, value }));
  dom.all['[data-form-key="company"] [data-field]'] = all;
}

test("typed company details survive a redraw, and each box is part of the company form", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  page.setLocation("company");
  core.setState(company());
  const saved = reg.views.company();
  assert.match(saved, /data-form-key="company"/);
  for (const [id, field] of [["co-name", "name"], ["co-phone", "phone"], ["co-address", "street"], ["co-city", "city"], ["co-state", "state"], ["co-zip", "zip"], ["co-license", "license"]])
    assert.match(saved, new RegExp(`id="${id}" name="${field}" data-field="${field}"`));
  assert.match(saved, /id="co-name" name="name" data-field="name"[^>]*value="Pine &amp; Co"/);
  assert.match(saved, /<option value="GA">GA<\/option>/);
  // One Save, at the bottom of the page, after the switches; no Save company details.
  assert.equal(saved.match(/data-action="save-company"/g).length, 1);
  assert.ok(saved.lastIndexOf("save-company") > saved.indexOf("switches-card"));
  assert.doesNotMatch(saved, /Save company details/);
  // Typed (watchForms keeps it), then the page is drawn again.
  forms.formDraft.company = { street: "2 Elm St", license: 'GA "123"' };
  const redrawn = reg.views.company();
  assert.match(redrawn, /id="co-address" name="street" data-field="street"[^>]*value="2 Elm St"/);
  assert.match(redrawn, /id="co-license" name="license" data-field="license"[^>]*value="GA &quot;123&quot;"/);
  delete forms.formDraft.company;
});

test("a ticked switch waits for Save, which sends it with the details", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company({ street: "2 Elm St", city: "Decatur", state: "GA", zip: "30030", address: "2 Elm St, Decatur, GA 30030" }));
  settings.companySwitch({ dataset: { companySwitch: "daily_email" }, checked: false });
  assert.equal(dom.requests.filter(r => r.path === "/api/company").length, 0, "a tick doesn't save");
  assert.doesNotMatch(reg.views.company(), /id="sw-daily_email"[^>]*checked/, "the tick is kept through a redraw");
  boxes({ name: "Pine & Co", phone: "4045550123", street: "2 Elm St", city: "Decatur", state: "GA", zip: "30030", license: "" });
  dom.answers = [(p) => (p === "/api/company" ? { status: 200, body: company({ phone: "(404) 555-0123", daily_email: false }) } : undefined)];
  await reg.actions["save-company"]();
  const sent = dom.requests.filter(r => r.path === "/api/company").pop();
  assert.equal(sent.body.daily_email, false);
  assert.equal(sent.body.phone, "4045550123");
  assert.equal(sent.body.street, "2 Elm St");
  // How you bill is no longer on the page (S4): the save leaves the company's billing style alone.
  assert.equal("billing_style" in sent.body, false);
  assert.equal(forms.formDraft.company, undefined);
});

test("Save marks the wrong boxes first and sends nothing", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company());
  const before = dom.requests.length;
  boxes({ name: "", phone: "555-0101", street: "2 Elm St", city: "", state: "", zip: "300", license: "#1" });
  await reg.actions["save-company"]();
  assert.equal(dom.requests.length, before);
  assert.deepEqual(Object.keys(forms.formErrors.company).filter(k => k !== "_form").sort(), ["city", "license", "name", "phone", "state", "zip"]);
  delete forms.formErrors.company;
  // A value saved before the rules isn't refused until it is edited.
  const { errors } = settings.companyChanges({ name: "Pine", phone: "555-0101" }, { name: "Pine", phone: "555-0101" }, {});
  assert.deepEqual(errors, {});
  delete dom.all['[data-form-key="company"] [data-field]'];
});

test("My account has one Save at the bottom, and it saves the name", async () => {
  page.setMe({ id: "u1", name: "Pat", email: "pat@pine.example", company_id: "co-p", role: "owner", email_verified: true });
  core.setState(company());
  const html = reg.views.account();
  assert.doesNotMatch(html, /save-my-name|Save name/);
  assert.equal(html.match(/data-action="save-account"/g).length, 1);
  assert.ok(html.lastIndexOf("save-account") > html.indexOf("two-step"), "below the cards");
  dom.all['[data-form-key="account"] [data-field]'] = [{ dataset: { field: "name" }, value: "Pat Pine" }];
  dom.answers = [(p) => (p === "/api/auth/name" ? { status: 200, body: { user: { ...page.me, name: "Pat Pine" } } } : undefined)];
  await reg.actions["save-account"]();
  assert.equal(dom.requests.filter(r => r.path === "/api/auth/name").pop().body.name, "Pat Pine");
  assert.equal(page.me.name, "Pat Pine");
  delete dom.all['[data-form-key="account"] [data-field]'];
});
