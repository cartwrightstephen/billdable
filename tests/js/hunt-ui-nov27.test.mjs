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

test("Results says 1 pay app, not 1 pay apps", () => {
  const src = readFileSync(new URL("../../static/js/views/today.js", import.meta.url), "utf8");
  assert.match(src, /const apps = \(n, tail = ""\) => `\$\{n\} \$\{termText\(n === 1 \? "pay app" : "pay apps"\)\}\$\{tail\}`;/);
  assert.doesNotMatch(src, /\$\{m\.pay_apps_paid\} \$\{termText\("pay apps"\)\}/);
  assert.doesNotMatch(src, /\$\{m\.pay_apps_sent\} \$\{termText\("pay apps/);
});

test("the lien Done on day, reminder days and Which job? boxes take the app's box style", () => {
  const css = readFileSync(new URL("../../static/styles.css", import.meta.url), "utf8");
  assert.match(css, /\.compliance-done input, \[data-form-key="csettings"\] input, \.upload-job select \{\n  padding: 6px 8px; border: 1px solid var\(--field\); border-radius: 6px; font: inherit; color: var\(--ink\); background: var\(--field-bg\);/);
});

test("My account says Accent color, in the US spelling the rest of the app uses", () => {
  const src = readFileSync(new URL("../../static/js/views/settings.js", import.meta.url), "utf8");
  assert.match(src, /label: "Accent color"/);
  assert.doesNotMatch(src, /"Accent colour"/);
});

test("a link in a table shown as cards is 44 px tall", () => {
  const css = readFileSync(new URL("../../static/styles.css", import.meta.url), "utf8");
  assert.match(css, /@media \(max-width: 800px\) \{\n  #view table\.stack td > a, #view tr\.action-item td > a \{ display: inline-flex; align-items: center; min-height: 44px; \}/);
});

test("Earlier pay apps by % complete: the % is inside each line's box", () => {
  const src = readFileSync(new URL("../../static/js/views/job-payapp-core.js", import.meta.url), "utf8");
  assert.match(src, /\$\{earlierLinesAs === "pct" \? `<span class="with-suffix inline">` : ""\}<input id="ep-line-/);
});

test("the browser tab says Edit job record while a saved one is being changed", () => {
  const app = readFileSync(new URL("../../static/app.js", import.meta.url), "utf8");
  assert.match(app, /if \(currentView === "daily" && daily\.editing\) return "Edit job record · Duracash";/);
});

test("a pay app waiting on a signature says so, not Ready to send", async () => {
  const { invoicePill } = await import("../../static/js/format.js");
  const { jobStatus } = await import("../../static/js/jobs.js");
  const waiting = { status: "awaiting_approval", approval: { waiting: ["Dana Whitfield"] } };
  const signed = { status: "awaiting_approval", approval: { waiting: [] } };
  assert.match(invoicePill(waiting), />Waiting for signature</);
  assert.match(invoicePill(signed), />Ready to send</);
  assert.match(invoicePill({ status: "draft", approval: { waiting: ["Dana"] } }), />Draft</);
  assert.deepEqual(jobStatus({ invoices: [waiting], blockers: [], packet: [] }), ["Waiting for signature", "copper"]);
});

test("the Progress pay app page's running figure: work and stored to date, less what pay apps sent billed", async () => {
  const { progressEstimate, progressEstimateHtml } = await import("../../static/js/sov.js");
  const lines = [
    { id: "a", qty_milli: 24000, rate_cents: 145000, scheduled_cents: 3480000, done_milli: 0, sent_cents: 0 },
    { id: "b", qty_milli: 1000, rate_cents: 1273000, scheduled_cents: 1273000, lump_sum: true, done_milli: 0, sent_cents: 0 },
    { id: "c", qty_milli: 1000, rate_cents: 350000, scheduled_cents: 350000, lump_sum: true, done_milli: 1000, sent_cents: 350000 },
  ];
  // 12 of 24 at $1,450 and $1,500 stored on a lump sum at 0%: the case company's PA-SORR-02, $18,900.
  assert.equal(progressEstimate(lines, { a: { done: "12" }, b: { done: "0", stored: "1,500" }, c: { done: "100" } }), 1890000);
  assert.equal(progressEstimate(lines, { a: { done: "24" } }), 3480000, "a line done in full bills its scheduled value");
  assert.equal(progressEstimate(lines, { a: { done: "x" } }), null);
  assert.match(progressEstimateHtml({ sov_view: {} }, 1890000), /This pay app: about \$18,900\.00, before retainage\./);
});

test("Who hired you: short choices a phone shows whole, the full words under the box", () => {
  const src = readFileSync(new URL("../../static/js/views/compliance.js", import.meta.url), "utf8");
  assert.match(src, /options: \[\["", "From the client's type"\]\]\.concat\(tiers\.map\(\(\[k, w\]\) => \[k, SHORT_TIER\(w\)\]\)\)/);
  const SHORT_TIER = (words) => String(words).replace(/\s*\([^)]*\)\s*$/, "");
  assert.equal(SHORT_TIER("Hired by the owner (you are the prime contractor)"), "Hired by the owner");
  assert.equal(SHORT_TIER("Public project"), "Public project");
});

test("a lump sum billed in full reads billed 100%, not 100.0%", () => {
  const src = readFileSync(new URL("../../static/js/sov.js", import.meta.url), "utf8");
  assert.match(src, /`billed \$\{l\.lump_sum \? \+\(l\.billed_qty_milli \/ 10\)\.toFixed\(1\) \+ "%"/);
});
