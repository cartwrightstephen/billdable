// Client helpers that need no browser: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { centsFrom, percentFrom, dollarsText, plainNumber } from "../../static/js/format.js";
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
