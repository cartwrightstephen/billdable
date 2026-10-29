// The wording glossary (R6-01, docs/improvement-plan.md section 4): every plain term is on the
// page, no old one is left, a company that bills in draws reads Draw, and History pills are
// sentences. node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { setBillingStyle, billingStyle, term, termText, actionWords, ACTION_WORDS, STYLE_NOTE } from "../../static/js/terms.js";
import { nextStep, jobActions, closeBlockerActions, jobStatus, waiverHint, waiverSendForm, RECORD_KINDS } from "../../static/js/jobs.js";
import { queueHtml } from "../../static/js/today.js";
import { INVOICE_WORDS } from "../../static/js/format.js";
import { NEW, OLD } from "./glossary.mjs";

const root = new URL("../../static/", import.meta.url);
const files = ["app.js", "index.html", ...["js/", "js/views/"].flatMap(d => readdirSync(new URL(d, root)).filter(f => f.endsWith(".js")).map(f => d + f))];
// What the page says, without code comments (they may name the old words to explain a change).
const text = Object.fromEntries(files.map(f => [f, readFileSync(new URL(f, root), "utf8").split("\n").filter(l => !/^\s*\/\//.test(l)).join("\n")]));
const all = Object.values(text).join("\n");

// Code names that carry an old word but are never shown: data-action keys and the like.
// Removed from the text before OLD is checked.
const CODE = /data-[a-z-]+="[^"]*"/g;

test("every plain term from the glossary is on the page", () => {
  assert.deepEqual(NEW.filter(t => !all.includes(t)), []);
});

test("no old term from the glossary is left on the page", () => {
  const left = [];
  for (const [f, src] of Object.entries(text)) {
    const shown = src.replace(CODE, "");
    for (const re of OLD) {
      const m = shown.match(re);
      if (m) left.push(`${f}: ${m[0]}`);
    }
  }
  assert.deepEqual(left, []);
});

test("draws: the company's word replaces pay app in the app's own copy", () => {
  assert.equal(termText("Draft pay app", "draws"), "Draft draw");
  assert.equal(termText("Pay apps", "draws"), "Draws");
  assert.equal(termText("No pay apps yet. A pay app is the bill.", "draws"), "No draws yet. A draw is the bill.");
  assert.equal(termText("Draft pay app", "pay_apps"), "Draft pay app");
  assert.equal(termText("payapps and payapp-detail stay", "draws"), "payapps and payapp-detail stay");
});

test("draws: next steps, job buttons, Today and the settings note", () => {
  const job = {
    id: "j1", closed: false, work_status: "complete", invoices: [], change_orders: [], blockers: [], close_blockers: ["x"],
    close_blocker_items: [], money: { unbilled_completed_cents: 5000, outstanding_cents: 0, retainage_open_cents: 0, contract_total_cents: 10000 },
  };
  try {
    setBillingStyle("draws");
    assert.equal(billingStyle(), "draws");
    assert.equal(term("Many"), "Draws");
    assert.equal(nextStep(job), "Draft a draw for the work done and not billed.");
    const { primary } = jobActions(job);
    assert.equal(primary.label, "Draft draw");
    assert.match(primary.why, /^A draw for/);
    assert.deepEqual(closeBlockerActions(job, { kind: "unbilled" }).map(a => a.label), ["Draft draw"]);
    assert.match(queueHtml([], {}), /when a draw is ready to send/);
    assert.equal(actionWords("approve_send"), "Draw sent");
  } finally {
    setBillingStyle("pay_apps");
  }
  assert.equal(nextStep(job), "Draft a pay app for the work done and not billed.");
  assert.equal(jobActions(job).primary.label, "Draft pay app");
  assert.equal(STYLE_NOTE, "Changes the word used across the app and sets the free trial: 90 days, or 45 for draws.");
  setBillingStyle("monthly");
  assert.equal(billingStyle(), "pay_apps", "an unknown style reads pay apps");
});

test("draws: Closeout's waiver lines and the record kind read draw too (review)", () => {
  try {
    setBillingStyle("draws");
    assert.equal(waiverHint("unconditional_progress"), "You already have the money for this draw.");
    assert.match(waiverSendForm({ id: "j1", invoices: [] }, { id: "inv-1" }), /data-waiver-hint="inv-1">You already have the money for this draw\./);
    assert.equal(termText(RECORD_KINDS.pay_app), "Backup for the draw");
  } finally {
    setBillingStyle("pay_apps");
  }
  assert.equal(waiverHint("unconditional_progress"), "You already have the money for this pay app.");
  // The app's own sentences on the job page, Closeout and Today go through termText (app.js and
  // the views it imports from js/views/).
  const app = files.filter(f => f === "app.js" || f.startsWith("js/views/")).map(f => text[f]).join("\n");
  for (const s of ["Pull back this pay app…", "By pay app", "Get your first pay app out", "One lien waiver per paid pay app.", "Follow-ups per pay app"]) {
    assert.ok(app.includes(`termText("${s}`), s);
  }
  assert.ok(app.includes("${termText(b.text)}"), "the Before you can finish list");
});

test("History pills are sentences, not action codes", () => {
  assert.equal(actionWords("approve_send"), "Pay app sent");
  assert.equal(actionWords("match_payment"), "Payment recorded");
  assert.equal(actionWords("draft_replaced"), "Draft replaced");
  assert.equal(actionWords("draft_skipped"), "Draft replaced");
  assert.equal(actionWords("close_job"), "Job finished");
  assert.equal(actionWords("some_new_code"), "Some new code");
  // Every action code the server writes (domain.audit calls) has a sentence.
  const py = ["domain/__init__.py", "domain/trail.py", "server.py", "importer.py"].map(f => readFileSync(new URL(`../../${f}`, import.meta.url), "utf8")).join("\n");
  const codes = new Set([...py.matchAll(/audit\(\s*(?:state|st|s),\s*[^,]+,\s*"([a-z_]+)"/g)].map(m => m[1]));
  for (const m of py.matchAll(/audit\(\s*\n\s*state,\s*\n?\s*[^,]+,\s*\n?\s*"([a-z_]+)"/g)) codes.add(m[1]);
  assert.ok(codes.size > 30, `found the audit codes (${codes.size})`);
  assert.deepEqual([...codes].filter(c => !(c in ACTION_WORDS)), []);
});

test("statuses use the plain words", () => {
  assert.equal(INVOICE_WORDS.void, "Cancelled");
  const job = { closed: false, blocked: false, invoices: [], work_status: "complete", money: { unbilled_completed_cents: 0, retainage_open_cents: 5, outstanding_cents: 0 } };
  assert.deepEqual(jobStatus(job), ["Held back", "amber"]);
});
