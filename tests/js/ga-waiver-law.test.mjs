// Georgia waivers as the law reads from 1 Jan 2021 (georgia-waivers.md, Nov 18): the waiver page
// shows the 90-day affidavit notes from helper/waiver_forms.json and never the attorney's questions.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const { waiverFormHtml, setWaiverForms, recordWaiverKind } = await import("../../static/js/views/paperwork.js");

const data = JSON.parse(readFileSync(new URL("../../helper/waiver_forms.json", import.meta.url), "utf8"));
const GA = data.states.GA;

test("the Georgia notes say 90 days and the affidavit of nonpayment, not 60 days", () => {
  const notes = GA.notes.join(" ");
  assert.match(notes, /within 90 days/);
  assert.match(notes, /affidavit of nonpayment/);
  assert.match(notes, /Filing a lien does not stop it\./);
  assert.doesNotMatch(notes, /\b60\b/);
  for (const which of ["interim", "final"]) {
    const text = GA.statutory_forms[which].paragraphs.join(" ");
    assert.match(text, /90 DAYS AFTER THE DATE STATED ABOVE UNLESS YOU FILE AN AFFIDAVIT OF NONPAYMENT PRIOR TO THE EXPIRATION OF SUCH 90 DAY PERIOD\./);
    assert.doesNotMatch(text, /CLAIM OF LIEN|60 DAY/);
  }
});

test("the waiver page lists the notes and none of the attorney's questions, even if they were sent", () => {
  setWaiverForms({ states: { GA: { ...GA, statutory_forms: undefined, fillable: false } }, other: data.other });
  const html = waiverFormHtml({ id: "j1", waiver_state: "GA" }, "conditional_final");
  for (const note of GA.notes) assert.ok(html.includes(note), note);
  assert.ok(html.includes("Waiver and Release of Lien and Payment Bond Rights Upon Final Payment"));
  assert.doesNotMatch(html, /attorney:/);
});

test("a Final lien release with no type is untyped on the page too: the server asks which waiver it is", () => {
  assert.equal(recordWaiverKind({ label: "Final lien release" }), "");
  assert.equal(recordWaiverKind({ label: "Unconditional final lien release" }), "unconditional_final");
});
