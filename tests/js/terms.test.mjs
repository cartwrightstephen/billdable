// The wording glossary (R6-01, docs/improvement-plan.md section 4): every plain term is on the
// page, no old one is left, a company that bills in draws reads Draw, and History pills are
// sentences. node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { setBillingStyle, billingStyle, term, termText, actionWords, ACTION_WORDS, STYLE_NOTE } from "../../static/js/terms.js";
import { nextStep, jobActions, closeBlockerActions, jobStatus } from "../../static/js/jobs.js";
import { queueHtml } from "../../static/js/today.js";
import { INVOICE_WORDS } from "../../static/js/format.js";

const root = new URL("../../static/", import.meta.url);
const files = ["app.js", "index.html", ...readdirSync(new URL("js/", root)).filter(f => f.endsWith(".js")).map(f => `js/${f}`)];
// What the page says, without code comments (they may name the old words to explain a change).
const text = Object.fromEntries(files.map(f => [f, readFileSync(new URL(f, root), "utf8").split("\n").filter(l => !/^\s*\/\//.test(l)).join("\n")]));
const all = Object.values(text).join("\n");

// The glossary's plain terms, as the page writes them.
const NEW = [
  "Paperwork", "Open paperwork", "Paperwork for ", "Needs paperwork", "Get CO ", "I have this…", "Marked as in hand",
  "Send draft for their review", "Customer agreed to the draft", "Customer wants changes", "Customer settings",
  "Save to customer settings", "Customer sent a pay app back? Note why.", "Who pays you?", "Who signs off on your bills",
  "Email for pay apps", "Record a payment", "Payments received", "Finish job", "Ready to finish the job.", "<h2>Money</h2>",
  "Signed change orders", "Unsigned change orders", "Bill now (send waits for signature)", "Held back, still owed",
  "Delete draft", "Activity log", "Unpaid", "Closeout", "Waiting (", "Also email (CC)", "Paperwork needed with each pay app",
  "They send pay apps back when", "Days they usually take to pay", "Days between follow-ups", "Total done so far $",
  "Anything to note? (optional)", "sent by ", "Notes (kept on the job record)", "Recommended for owners.",
  "Homeowner: their email. GC: their accounts-payable address.",
];

// The words they replace. Each is a label, button, heading or sentence the page must not show;
// the trade terms stay only in help sentences (the Help page's "Paperwork (packet)" and the like).
const OLD = [
  /\bPackets\b/, /Open packet/, /Clear \$?\{?\w*\}? ?blockers?/i, /Mark present/i, /Marked present/, /pencil copy\b(?!\))/i,
  /Payer agreed/, /Payer wants changes/, /Playbook|(the|to|payer) playbook/, /Payer sent a pay app back/, /Match payment/, /Payments matched/,
  /[Cc]lose for (the )?record/, /Closed for the record/, /Money file/i, /Approved COs/, /Pending COs/, /Put on next pay app/,
  /Retainage open/, /Void draft/, />Audit</, /"Audit"/, /"Collections"/, />Collections</, /File to job record/, /To file \(/,
  /Completed to date/, /Completion notes/, /Notes for the job record/, /Owners should turn this on/, /"AP email"/, /AP email \(/,
  /"Approver"/, />Approver</, /Approve send/, /Draft skipped/, /\bthe payer\b/, /\bPayer\b(?!s)/,
];

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

test("History pills are sentences, not action codes", () => {
  assert.equal(actionWords("approve_send"), "Pay app sent");
  assert.equal(actionWords("match_payment"), "Payment recorded");
  assert.equal(actionWords("draft_replaced"), "Draft replaced");
  assert.equal(actionWords("draft_skipped"), "Draft replaced");
  assert.equal(actionWords("close_job"), "Job finished");
  assert.equal(actionWords("some_new_code"), "Some new code");
  // Every action code the server writes (domain.audit calls) has a sentence.
  const py = ["domain.py", "server.py", "importer.py"].map(f => readFileSync(new URL(`../../${f}`, import.meta.url), "utf8")).join("\n");
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
