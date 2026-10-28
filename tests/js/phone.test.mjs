// Phone layout helpers (R6-02, R6-03, R6-04): node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { isZeroMoney, KEEP_ZERO } from "../../static/js/dom.js";
import { worstKind, webcalUrl } from "../../static/js/calendar.js";
import { inBatches } from "../../static/js/net.js";
import { doneLabel, sovProgressHtml, continuationHtml } from "../../static/js/sov.js";

const css = readFileSync(new URL("../../static/styles.css", import.meta.url), "utf8");
const app = readFileSync(new URL("../../static/app.js", import.meta.url), "utf8");

test("a $0.00 money cell is hidden on a phone, except the contract total and the next step", () => {
  assert.equal(isZeroMoney("$0.00"), true);
  assert.equal(isZeroMoney(" -$0.00 "), true);
  assert.equal(isZeroMoney("$10.00"), false);
  assert.equal(isZeroMoney("$0.00 of $5.00"), false);
  assert.equal(isZeroMoney("0%"), false);
  assert.ok(KEEP_ZERO.has("Contract total") && KEEP_ZERO.has("Next"));
  assert.match(css, /table\.stack td\.zero \{ display: none; \}/);
});

test("tables stay inside the screen; a schedule of values scrolls in its own box, then becomes cards", () => {
  assert.match(css, /\.table-wrap:not\(\.sov-wrap\) \{ overflow: visible; \}/);
  assert.doesNotMatch(css, /^\s*\.table-wrap \{ overflow: visible; \}/m);
  assert.match(css, /@media \(max-width: 600px\) \{\s*table\.sov-table/);
  // Every schedule-of-values table sits in a sov-wrap.
  const j = { id: "j1", sov_view: { lines: [{ id: "l1", item: "1", description: "Tile", lump_sum: false, qty_milli: 5000, unit: "SF", done_milli: 1000, stored_cents: 0, billed_qty_milli: 0 }] } };
  assert.match(sovProgressHtml(j), /class="table-wrap sov-wrap"><table class="sov-table">/);
  assert.match(continuationHtml({ sov_lines: [{ item: "1", description: "Tile", scheduled_cents: 100, to_date_cents: 0, amount_cents: 0, unit: "SF" }] }, {}), /table-wrap sov-wrap/);
  assert.equal((app.match(/<table class="sov-table/g) || []).length, (app.match(/table-wrap sov-wrap"><table class="sov-table/g) || []).length);
});

test("a progress line's box says what goes in it", () => {
  assert.equal(doneLabel({ lump_sum: true, unit: "LS" }), "% done");
  assert.equal(doneLabel({ lump_sum: false, unit: "SF" }), "Qty done (SF)");
  const j = { id: "j1", sov_view: { lines: [{ id: "l1", item: "1", description: "Tile", lump_sum: true, qty_milli: 1000, unit: "LS", done_milli: 0, stored_cents: 0, billed_qty_milli: 0 }] } };
  assert.match(sovProgressHtml(j), /data-label="% done"/);
});

test("a calendar day's dot takes its most pressing kind", () => {
  assert.equal(worstKind([]), "");
  assert.equal(worstKind([{ kind: "paid" }]), "paid");
  assert.equal(worstKind([{ kind: "paid" }, { kind: "" }]), "other");
  assert.equal(worstKind([{ kind: "" }, { kind: "waiver" }, { kind: "paid" }]), "waiver");
  assert.equal(worstKind([{ kind: "waiver" }, { kind: "overdue" }]), "overdue");
  assert.match(css, /\.cal-dot\.overdue/);
});

test("the calendar feed opens in a phone's calendar as webcal://", () => {
  assert.equal(webcalUrl("https://app.duracash.com/api/calendar/abc.ics"), "webcal://app.duracash.com/api/calendar/abc.ics");
  assert.equal(webcalUrl("http://127.0.0.1:8765/api/calendar/abc.ics"), "webcal://127.0.0.1:8765/api/calendar/abc.ics");
  assert.equal(webcalUrl("javascript:alert(1)"), "");
  assert.equal(webcalUrl(""), "");
});

test("uploads go two or three at a time, in order, counting as they finish", async () => {
  let running = 0, most = 0;
  const seen = [];
  const out = await inBatches([5, 1, 3, 2, 4], 3, async (n) => {
    running++; most = Math.max(most, running);
    await new Promise((r) => setTimeout(r, n * 3));
    running--;
    return n * 10;
  }, (done, total) => seen.push(`${done}/${total}`));
  assert.deepEqual(out, [50, 10, 30, 20, 40]);
  assert.equal(most, 3);
  assert.deepEqual(seen, ["1/5", "2/5", "3/5", "4/5", "5/5"]);
  assert.deepEqual(await inBatches([], 3, async () => 1), []);
});

test("a link-like button grows its touch area without changing the line's height", () => {
  assert.match(css, /\.linkish::after \{ content: ""; position: absolute;/);
  assert.doesNotMatch(css, /\.linkish, \.tab/);
});

test("Money has six tabs and Settings is split into Company, Team, My account and Billing", () => {
  const money = /key: "money", label: "Money", views: (\[.*?\]) \},/.exec(app)[1];
  assert.deepEqual(JSON.parse(money).map(([, label]) => label), ["Pay apps", "Change orders", "Unpaid", "Payments", "Closeout", "Outbox"]);
  const settings = /key: "settings", label: "Settings", views: (\[.*?\]) \},/.exec(app)[1];
  assert.deepEqual(JSON.parse(settings).slice(0, 4).map(([v]) => v), ["company", "team", "account", "billing"]);
  assert.match(css, /\.tabs\.more-right \{/);
});

test("the Inbox opens on Waiting, with Take a photo before Choose files", () => {
  const inbox = app.slice(app.indexOf("  inbox() {"), app.indexOf("  help() {"));
  assert.ok(inbox.indexOf("Waiting (${items.length})") < inbox.indexOf('class="dropzone"'), "Waiting comes first");
  assert.ok(inbox.indexOf(">Take a photo<") < inbox.indexOf(">Choose files<"));
  assert.match(css, /@media \(pointer: coarse\), \(max-width: 600px\) \{\s*\.touch-only/);
});
