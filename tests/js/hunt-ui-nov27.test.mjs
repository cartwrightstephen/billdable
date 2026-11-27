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

test("a diary entry's label shows its day as the page writes dates, not 2026-11-27", async () => {
  const { recordFace } = await import("../../static/js/jobs.js");
  assert.equal(recordFace({ kind: "daily", label: "Diary entry 2026-11-27" }).label, "Diary entry Nov 27, 2026");
  assert.equal(recordFace({ kind: "daily", label: "Diary entry 2026-11-27: 3 EA sink" }).label, "Diary entry Nov 27, 2026: 3 EA sink");
  assert.equal(recordFace({ kind: "note", label: "Diary entry 2026-11-27" }).label, "Diary entry 2026-11-27");
});

test("up to tablet width, boxes, fold titles, the money strip and Log out are 44 px, after the forms' own rules", () => {
  const css = readFileSync(new URL("../../static/styles.css", import.meta.url), "utf8");
  const at = css.lastIndexOf("@media (pointer: coarse), (max-width: 1024px) {");
  assert.ok(at > css.indexOf(".form-inline input, .form-inline textarea, .form-inline select"), "comes after the 34 px box rules");
  const block = css.slice(at, css.indexOf("\n}", at));
  assert.match(block, /#app input:not\(\[type=checkbox\]\):not\(\[type=radio\]\):not\(\[type=file\]\), #app select \{ min-height: 44px; \}/);
  assert.match(block, /#app details:not\(\.info-pop\) > summary \{ min-height: 44px;/);
  assert.match(block, /\.money-strip \.strip-item \{ display: inline-flex; align-items: center; gap: 0\.3em; min-height: 44px; \}/);
  assert.match(block, /\.user-meta \.reset-btn \{ min-height: 44px;/);
});

test("the retainage percent boxes on a draft and by line carry their % inside the box", () => {
  const src = readFileSync(new URL("../../static/js/views/job-payapp-core.js", import.meta.url), "utf8");
  assert.match(src, /label: "Retainage this period", suffix: "%"/);
  assert.doesNotMatch(src, /Retainage this period %/);
  assert.match(src, /<span class="with-suffix inline"><input id="lr-\$\{l\.id\}"[^]*?<span class="suffix" aria-hidden="true">%<\/span><\/span>/);
});
