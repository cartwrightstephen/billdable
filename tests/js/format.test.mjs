// Client helpers that need no browser: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { centsFrom, percentFrom, dollarsText, plainNumber, esc, unesc, escDeep, money, words, emailStatusHtml, emailOutcome } from "../../static/js/format.js";
import { sovNum } from "../../static/js/sov.js";

test("centsFrom reads dollars the way people type them", () => {
  assert.equal(centsFrom("$12,500.50").cents, 1250050);
  assert.equal(centsFrom("48,500").cents, 4850000);
  assert.equal(centsFrom(" 48500.5 ").cents, 4850050);
  assert.equal(centsFrom(".75").cents, 75);
  assert.equal(centsFrom("0.10").cents, 10);
  assert.equal(centsFrom("-5").cents, -500);
  assert.equal(centsFrom("1,250,000.07").cents, 125000007, "no floating-point drift");
});

test("centsFrom refuses what is not an amount", () => {
  for (const bad of ["abc", "", "   ", "12.345", "1.2.3", "12a", "$", "--5"]) {
    const r = centsFrom(bad);
    assert.ok(r.error, `"${bad}" should be refused`);
    assert.equal(r.cents, null);
  }
});

test("dollarsText writes what centsFrom reads back", () => {
  assert.equal(dollarsText(4850000), "$48,500.00");
  assert.equal(dollarsText(-1250), "-$12.50");
  assert.equal(dollarsText(null), "");
  for (const c of [0, 1, 99, 4850000, 125000007]) assert.equal(centsFrom(dollarsText(c)).cents, c);
});

test("percentFrom takes one decimal", () => {
  assert.equal(percentFrom("5").value, 5);
  assert.equal(percentFrom("7.5 %").value, 7.5);
  assert.ok(percentFrom("7.25").error);
  assert.ok(percentFrom("ten").error);
  assert.ok(percentFrom("-5").error);
});

test("the schedule of values strips the same characters", () => {
  assert.equal(plainNumber("$1,200 %"), "1200");
  assert.equal(sovNum("$1,200.5"), 1200.5);
  assert.ok(Number.isNaN(sovNum("abc")));
});

test("unesc gives back what esc wrote, without a page", () => {
  for (const raw of [`Oak "St" <b>&co</b>`, "O'Brien & Sons", "&lt; stays literal", "plain"]) assert.equal(unesc(esc(raw)), raw);
  assert.equal(unesc("&amp;lt;"), "&lt;", "one pass: decoded once, like the browser");
  assert.equal(unesc("&#233;t&#xE9;"), "été");
  assert.equal(unesc("&bogus; & alone"), "&bogus; & alone");
});

test("escDeep escapes every string inside, and only strings", () => {
  const out = escDeep({ name: "<i>", n: 5, list: ["a&b", null], nested: { ok: true, s: '"' } });
  assert.deepEqual(out, { name: "&lt;i&gt;", n: 5, list: ["a&amp;b", null], nested: { ok: true, s: "&quot;" } });
});

test("money and words", () => {
  assert.equal(money(4850000), "$48,500.00");
  assert.equal(money(-1250), "-$12.50");
  assert.equal(money(null), "$0.00");
  assert.equal(words("in_progress"), "In progress");
  assert.equal(words("awaiting_<b>"), "Awaiting &lt;b&gt;", "words() output goes into HTML, so it is escaped");
});

test("an email sent with a copy address refused says so, with no Send again", () => {
  const e = { id: "eml-1", to: "ap@gc.com", status: "sent", delivered_at: "2026-10-29T10:00:00+00:00", refused: ["pm@gc.con (550)"] };
  const html = emailStatusHtml(e);
  assert.match(html, /Not delivered to the copy address pm@gc\.con \(550\)/);
  assert.doesNotMatch(html, /Send again/);
  assert.match(emailOutcome([e]), /Emailed ap@gc\.com\. Not delivered to the copy address pm@gc\.con \(550\)\./);
  assert.equal(emailOutcome([{ to: "ap@gc.com", status: "sent" }]), "Emailed ap@gc.com.");
});
