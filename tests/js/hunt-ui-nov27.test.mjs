// Nov 27 UI hunt: fixes found walking every page at desktop, iPad and phone widths.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { focusSignature, findBySignature } from "../../static/js/dom.js";

test("a redraw finds a focused button without an id again by what it does", () => {
  const chip = { tagName: "BUTTON", dataset: { action: "today-filter", kind: "week" } };
  const sig = focusSignature(chip);
  assert.deepEqual(sig, { tag: "button", data: { action: "today-filter", kind: "week" } });
  let asked = "";
  const root = { querySelector(sel) { asked = sel; return "found"; } };
  assert.equal(findBySignature(root, sig), "found");
  assert.equal(asked, 'button[data-action="today-filter"][data-kind="week"]');
});

test("camelCase data names and quotes are written back as a valid selector", () => {
  const el = { tagName: "BUTTON", dataset: { action: "nav", jobId: 'job "x"' } };
  let asked = "";
  findBySignature({ querySelector(sel) { asked = sel; return null; } }, focusSignature(el));
  assert.equal(asked, 'button[data-action="nav"][data-job-id="job \\"x\\""]');
});

test("a control without a data-action has no signature: nothing to find it by", () => {
  assert.equal(focusSignature({ tagName: "A", dataset: {} }), null);
  assert.equal(focusSignature(null), null);
  assert.equal(findBySignature({ querySelector() { return "x"; } }, null), null);
});

test("the shell puts the focus back by signature when the focused button had no id", () => {
  const app = readFileSync(new URL("../../static/app.js", import.meta.url), "utf8");
  assert.match(app, /const keepSig = !keep && was && app\.contains\(was\) \? focusSignature\(was\) : null;/);
  assert.match(app, /findBySignature\(app, keepSig\)\?\.focus\(\{ preventScroll: true \}\)/);
});

test("Remove on a job record row keeps the focus in the list", () => {
  const src = readFileSync(new URL("../../static/js/views/daily.js", import.meta.url), "utf8");
  const body = src.slice(src.indexOf('"daily-row-remove": (el)'), src.indexOf('"daily-chip": (el)'));
  assert.match(body, /q\(n\) \|\| q\(n - 1\)/);
  assert.match(body, /data-action="daily-more"\]\[data-kind="crew"\]/);
});
