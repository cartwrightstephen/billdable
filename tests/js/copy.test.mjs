// Stephen's Oct 29 review: no notes about the app ("flavor text") come back, and the people who pay
// a contractor are Clients, never customers, in what the page says. helper/answers.json is checked
// by tests/test_helper_answers.py. node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";

const root = new URL("../../static/", import.meta.url);
const files = ["app.js", "index.html", ...["js/", "js/views/"].flatMap(d => readdirSync(new URL(d, root)).filter(f => f.endsWith(".js")).map(f => d + f))];
// What the page says: the code without comments.
const text = Object.fromEntries(files.map(f => [f, readFileSync(new URL(f, root), "utf8").split("\n").filter(l => !/^\s*\/\//.test(l)).join("\n")]));

// Each line Stephen asked to remove, and the other notes like them (G6, T2, T4, T5, T7, T8, T10,
// M12, S4, S5, S14, C2).
const GONE = [
  "Nothing here is estimated", "What Duracash recorded for your company", "Email me last month's summary", "metrics-email",
  "Every finished job has a pay app out", "Done but not billed", "Work done to pay app sent", "\"Pay app sent to paid\"", "Sent back by the",
  "Billing isn't live", "sets the free trial", "STYLE_NOTE", "How you bill", "Owners manage the team", "Every action is in the Activity log",
  "Every action: what was drafted", "The chain of records checks out", "One lien waiver per paid pay app", "Duracash never writes",
  "What happened today", "Write what happened today", "They wait below for you", "Notes split at every line",
  "Customer settings", "Payer playbook", "Go to Today", "<h1>Today</h1>", "<h1>Help</h1>", "Needs you", "<h2>This week</h2>",
];

test("the notes Stephen asked to remove don't come back", () => {
  const left = [];
  for (const [f, src] of Object.entries(text)) for (const phrase of GONE) if (src.includes(phrase)) left.push(`${f}: ${phrase}`);
  assert.deepEqual(left, []);
  const server = readFileSync(new URL("../../server.py", import.meta.url), "utf8");
  assert.doesNotMatch(server, /summary-email|_p_summary_email/, "the summary email has no route");
  assert.match(server, /"Enter the diary entry\."/);
});

// "customer" as a word the page shows: not inside a name (nj-customer, new_customer,
// sameCustomerBox, .customer) and not a quoted key ("customer", data-field="customer").
const CUSTOMER = /(?<![\w$.\-/])[Cc]ustomers?(?:'s)?(?![\w\-$])/g;

test("no customer in what the page says: they are Clients", () => {
  const left = [];
  for (const [f, src] of Object.entries(text)) {
    for (const m of src.matchAll(CUSTOMER)) {
      const before = src.slice(Math.max(0, m.index - 12), m.index), after = src.slice(m.index + m[0].length, m.index + m[0].length + 2);
      if (/["']$/.test(before) && after[0] === before[before.length - 1]) continue;   // a quoted key
      if (/(?:const|let|!|\(|\{ )\s*$/.test(before) || /^\s*[):;=,]/.test(after)) continue;   // a variable or a key
      left.push(`${f}: …${src.slice(Math.max(0, m.index - 30), m.index + 20).replace(/\s+/g, " ")}…`);
    }
  }
  assert.deepEqual(left, []);
});

test("the Results cards and the Diary use Stephen's words", () => {
  const today = text["js/views/today.js"], daily = text["js/views/daily.js"];
  for (const s of ["Days from Job Completion to Pay App Sent", "Days from Pay App Sent to Paid", "Pay app rejections by Clients", "Jobs completed with outstanding billings"]) {
    assert.ok(today.includes(s), s);
  }
  assert.match(daily, /<label>Diary<\/label>/);
  assert.match(daily, /"Write the diary entry\."/);
  assert.match(text["js/views/clients.js"], /<h1>Client playbook<\/h1>/);
});
