// The page's colours are named tokens (G9): outside the :root blocks at the top of styles.css no
// rule writes a colour itself, so a dark palette or another accent changes only the tokens.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../../static/styles.css", import.meta.url), "utf8");

// Every :root { ... } block (the light palette, and later the dark one) is where colours live.
const outside = css.replace(/:root[^{]*\{[^}]*\}/g, "").replace(/\/\*[\s\S]*?\*\//g, "");

test("no colour is written outside the :root tokens", () => {
  const found = outside.split("\n").filter(l => !/mask-image/.test(l))
    .flatMap(l => l.match(/#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)|:\s*(white|black)\b/g) || []);
  assert.deepEqual(found, []);
});

test("every token a rule uses is defined", () => {
  const defined = new Set([...css.matchAll(/(--[\w-]+)\s*:/g)].map(m => m[1]));
  const used = new Set([...css.matchAll(/var\((--[\w-]+)/g)].map(m => m[1]));
  assert.deepEqual([...used].filter(v => !defined.has(v)), []);
});
