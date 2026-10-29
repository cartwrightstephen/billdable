// First-day setup hints (Oct 23 review): node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { companyGaps, companyGapsText, companyGapsHtml } from "../../static/js/setup.js";

const full = { name: "Pine", address: "2 Elm St, Decatur, GA", phone: "404-555-0100", license: "GA-123" };

test("no warning when the address, phone and licence number are all filled in", () => {
  assert.deepEqual(companyGaps(full), []);
  assert.equal(companyGapsHtml(full), "");
});

test("each blank detail is named, in the order the PDF prints them", () => {  // note 1
  const words = (co) => companyGapsText(companyGaps(co));
  assert.equal(words({ ...full, address: "  " }), "Your company address is blank on this PDF.");
  assert.equal(words({ ...full, phone: "" }), "Your company phone is blank on this PDF.");
  assert.equal(words({ ...full, license: "" }), "Your company licence number is blank on this PDF.");
  assert.equal(words({ ...full, phone: "", license: "" }), "Your company phone and licence number are blank on this PDF.");
  assert.equal(words({ name: "Pine" }), "Your company address, phone and licence number are blank on this PDF.");
  assert.equal(words(undefined), "Your company address, phone and licence number are blank on this PDF.");
});

test("Company details opens the first blank box, and only an address gap keeps the no-address class", () => {
  const noPhone = companyGapsHtml({ ...full, phone: "" });
  assert.match(noPhone, /class="notice company-gaps"/);
  assert.match(noPhone, /data-action="onb-go" data-view="company" data-focus="#co-phone">Company details<\/button>/);
  assert.match(companyGapsHtml({ ...full, license: "" }), /data-focus="#co-license"/);
  const none = companyGapsHtml({});
  assert.match(none, /class="notice company-gaps no-address"/);
  assert.match(none, /data-focus="#co-address"/);
});

test("the warning never blocks sending: it has no disabled button and no blocker", () => {
  const html = companyGapsHtml({});
  assert.doesNotMatch(html, /disabled|blocker/);
});
