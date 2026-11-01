// The field rules on the page (G1, static/js/validate.js): the same case table as the server's
// (tests/validate_cases.txt), a box checked and tidied when it is left, and addresses.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installFakeDom } from "./fakedom.mjs";
import { validate, normalizePhone, checkFields, FIELD_RULES, US_STATES, composeAddress, splitAddress, parseLegacyAddress } from "../../static/js/validate.js";

installFakeDom();
const forms = await import("../../static/js/forms.js");

const cases = readFileSync(new URL("../validate_cases.txt", import.meta.url), "utf8").split("\n")
  .filter(l => l.trim() && !l.startsWith("#")).map(l => l.split("\t"));

test("every case in the shared table", () => {
  for (const [rule, value, want, phone] of cases) {
    assert.equal(validate(rule, value) === "", want === "ok", `${rule} ${JSON.stringify(value)}`);
    if (phone) assert.equal(normalizePhone(value), phone);
  }
  assert.deepEqual([...new Set(cases.map(c => c[0]))].sort(), Object.keys(FIELD_RULES).sort());
});

test("the messages and states match the server's", () => {
  const py = readFileSync(new URL("../../domain/validate.py", import.meta.url), "utf8");
  for (const [rule, [msg]] of Object.entries(FIELD_RULES)) assert.ok(py.includes(`"${rule}": ("${msg}"`), rule);
  assert.equal(US_STATES.length, 56);
  for (const s of US_STATES) assert.ok(py.includes(`"${s}"`), s);
});

test("blank passes unless required; checkFields names each box", () => {
  assert.equal(validate("zip", ""), "");
  assert.equal(validate("zip", "", true), FIELD_RULES.zip[0]);
  assert.deepEqual(Object.keys(checkFields({ phone: "555", zip: "30303" }, { phone: "phone", zip: "zip", email: "email" })), ["phone"]);
});

test("a field with a rule is marked when it is left, and a phone is tidied", () => {
  const html = forms.field({ form: "f", name: "phone", id: "f-phone", label: "Phone", rule: "phone" });
  assert.match(html, /data-rule="phone"/);
  const span = { textContent: "" };
  globalThis.document.getElementById = (id) => (id === "f-phone-err" ? span : null);
  const attrs = {};
  const el = { id: "f-phone", value: "404.555.0123", dataset: { rule: "phone", field: "phone" },
    closest: () => ({ dataset: { formKey: "f" } }), setAttribute: (k, v) => { attrs[k] = v; }, removeAttribute: (k) => { delete attrs[k]; } };
  assert.equal(forms.checkRule(el), "");
  assert.equal(el.value, "(404) 555-0123");
  assert.equal(forms.formDraft.f.phone, "(404) 555-0123");
  el.value = "555";
  assert.equal(forms.checkRule(el), FIELD_RULES.phone[0]);
  assert.equal(span.textContent, FIELD_RULES.phone[0]);
  assert.equal(attrs["aria-invalid"], "true");
  assert.equal(forms.formErrors.f.phone, FIELD_RULES.phone[0]);
  forms.clearForm("f");
});

test("addresses compose and split as the server's do", () => {
  const parts = { street: "120 Peachtree St NE", city: "Atlanta", state: "GA", zip: "30303" };
  assert.equal(composeAddress({ ...parts, state: "ga" }), "120 Peachtree St NE, Atlanta, GA 30303");
  assert.deepEqual(splitAddress("120 Peachtree St NE\nAtlanta GA 30303"), parts);
  assert.equal(splitAddress("1 Main St, Toronto, ON M5V 2T6"), null);
  assert.deepEqual(parseLegacyAddress("Lot 7, Vesta subdivision"), { street: "Lot 7, Vesta subdivision", city: "", state: "", zip: "" });
});
