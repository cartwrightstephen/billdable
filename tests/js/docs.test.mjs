// The docs drawn from the code (R6-11): the redrawn wireframes use the glossary's plain words and
// the app's own tabs, and every punch-list id in docs/client-ready-tuning.md maps to an item of
// docs/improvement-plan.md that the plan's Progress records. node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { NEW, OLD } from "./glossary.mjs";

const repo = new URL("../../", import.meta.url);
const read = (path) => readFileSync(new URL(path, repo), "utf8");

// The boards redrawn from the code after Round 6 (R6-11) and in the final pass.
const BOARDS = ["Job", "PhoneJob", "Money", "PaymentsImport", "Inbox", "Settings", "PayerPage",
  "Today", "PhoneToday", "Packet", "PhonePacket", "SendCheck", "Playbook"];
// Every board in the folder: the final pass brought the others' words up to the app too.
const ALL = readdirSync(new URL("docs/wireframes/", repo)).filter(f => f.endsWith(".dc.html")).map(f => f.replace(".dc.html", ""));

// A board as a person reads it: no styles, links or the canvas's script, entities decoded. The
// tags stay, so ">Audit<" style checks still work; labels in placeholder and aria-label stay too.
function shown(name) {
  return read(`docs/wireframes/${name}.dc.html`)
    .replace(/<script[\s\S]*?<\/script>/g, "").replace(/<helmet>[\s\S]*?<\/helmet>/g, "").replace(/<title>[\s\S]*?<\/title>/, "")
    .replace(/\s(style|href|src)="[^"]*"/g, "")
    .replace(/&#10;/g, "\n").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

// What the app's pages say, as terms.test.mjs reads them.
const staticDir = new URL("static/", repo);
const appFiles = ["app.js", "index.html", ...["js/", "js/views/"].flatMap(d => readdirSync(new URL(d, staticDir)).filter(f => f.endsWith(".js")).map(f => d + f))];
const appText = appFiles.map(f => readFileSync(new URL(f, staticDir), "utf8")).join("\n");

test("no wireframe uses an old word from the glossary", () => {
  const left = [];
  for (const name of ALL) {
    const text = shown(name);
    for (const re of [...OLD, /\bAudit\b/, /Team &(amp;)? account/, /Money file/, /\bTo file\b/])
      if (re.test(text)) left.push(`${name}: ${text.match(re)[0]}`);
  }
  assert.deepEqual(left, []);
});

test("the redrawn wireframes use the plain words the screens use, and the app has each of them", () => {
  const want = {
    Job: ["Held back, still owed", "Signed change orders", "Unsigned change orders", "Received", "Done, not billed",
      "Client sent a pay app back? Note why.", "Save to the client playbook", "Before you can finish this job", "Record a payment",
      "Bill now (send waits for signature)", "Total done so far $", "Signed on paper…", "Take a photo", "More ("],
    PhoneJob: ["Client sent a pay app back? Note why.", "Before you can finish this job", "Take a photo", "Add a file", "Signed on paper…"],
    Money: ["Paperwork", "Unpaid", "Closeout", "Not chosen yet", "Which type?", "Held back, still owed", "Open closeout paperwork"],
    PaymentsImport: ["Record the ticked payments", "Pick a pay app…", "Download for QuickBooks", "Read payments from QuickBooks"],
    Inbox: ["Waiting (", "Other job", "Change type (", "Add to job", "Read as payments (", "Everything received", "Came in by", "Filed automatically to "],
    Settings: ["Activity log", "Use an authenticator app instead", "Sending and email", "Permission profiles", "Delete everything", "Appearance"],
    PayerPage: ["Approve for payment", "Payment expected by", "Something not right?", "Send a question", "Mark as received"],
    Today: ["To Do", "Needs paperwork", "Spoke to them…", "Done, not billed", "Held back, still owed",
      "Can't send yet", "Open and recent"],
    PhoneToday: ["To Do", "Done, not billed", "Held back, still owed"],
    Packet: ["Paperwork for ", "Required items", "Take a photo", "Choose file", "I have this…", "Not needed for this client", "Also blocking send"],
    PhonePacket: ["Paperwork for ", "Take a photo", "I have this…", "Also blocking send"],
    SendCheck: ["Before you send", "Also email (CC)", "Period to", "Your company address is blank on this PDF.", "Save email", "Open paperwork"],
    Playbook: ["Client playbook", "Add client", "Add to the paperwork?", "Add to paperwork", "Save client", "Same client as another one?",
      "Days they usually take to pay", "Also email from the third follow-up"],
  };
  const missing = [];
  for (const [name, phrases] of Object.entries(want)) {
    const text = shown(name);
    for (const p of phrases) {
      if (!text.includes(p)) missing.push(`${name} lacks "${p}"`);
      // Drawn from the code: the page that board shows says it too.
      if (!(name === "PayerPage" ? read("payer_page.py") : appText).includes(p)) missing.push(`the app lacks "${p}"`);
    }
  }
  assert.deepEqual(missing, []);
});

test("the Job board uses every glossary term its screen shows", () => {
  const job = shown("Job");
  const onJobPage = ["Client sent a pay app back? Note why.", "Save to the client playbook", "Record a payment", "Signed change orders",
    "Unsigned change orders", "Held back, still owed", "Total done so far $", "Anything to note? (optional)", "Bill now (send waits for signature)", "sent by "];
  assert.deepEqual(onJobPage.filter(t => !NEW.includes(t)), [], "each is a glossary term");
  assert.deepEqual(onJobPage.filter(t => !job.includes(t)), []);
});

// The tabs of one section in static/app.js's SECTIONS, as they read.
function appTabs(key) {
  const line = read("static/app.js").split("\n").find(l => l.includes(`{ key: "${key}",`));
  return [...line.matchAll(/\["[\w-]+", "([^"]+)"\]/g)].map(m => m[1]);
}

function boardTabs(name, label) {
  const nav = read(`docs/wireframes/${name}.dc.html`).match(new RegExp(`<nav aria-label="${label}"[^>]*>([\\s\\S]*?)</nav>`))[1];
  return [...nav.matchAll(/<a [^>]*>([^<]+)<\/a>/g)].map(m => m[1].replace(/&amp;/g, "&"));
}

test("the Money, Import and Settings boards have the app's tabs, in the app's order", () => {
  assert.deepEqual(boardTabs("Money", "Money"), appTabs("money"));
  assert.deepEqual(boardTabs("PaymentsImport", "Import/Export"), appTabs("data"));
  // Helper answers is a founders' page: not drawn.
  assert.deepEqual(boardTabs("Settings", "Settings"), appTabs("settings").filter(t => t !== "Helper answers"));
  assert.deepEqual(boardTabs("Job", "Jobs"), appTabs("jobs"));
});

test("the canvas lists every board in the folder, with the redrawn boards' sizes", () => {
  const canvas = JSON.parse(read("docs/wireframes/canvas.json"));
  const files = readdirSync(new URL("docs/wireframes/", repo)).filter(f => f.endsWith(".dc.html")).sort();
  assert.deepEqual(Object.keys(canvas.boards).sort(), files);
  assert.deepEqual([...canvas.order].sort(), files);
  for (const name of BOARDS) {
    const props = JSON.parse(read(`docs/wireframes/${name}.dc.html`).match(/data-props='([^']+)'/)[1]).$preview;
    const b = canvas.boards[`${name}.dc.html`];
    assert.deepEqual([b.w, b.h], [props.width, props.height], name);
  }
  // No two boards overlap.
  const boxes = Object.entries(canvas.boards);
  for (const [a, p] of boxes) for (const [b, q] of boxes) {
    if (a >= b) continue;
    const apart = p.x + p.w <= q.x || q.x + q.w <= p.x || p.y + p.h <= q.y || q.y + q.h <= p.y;
    assert.ok(apart, `${a} overlaps ${b}`);
  }
});

test("every punch-list id maps to an item of the improvement plan that is recorded", () => {
  const punch = read("docs/client-ready-tuning.md");
  const plan = read("docs/improvement-plan.md");
  const marks = new Map();
  for (const m of punch.matchAll(/^### ([A-E]\d+)\. .*\n\n> (.*)$/gm)) marks.set(m[1], m[2]);
  for (const m of punch.matchAll(/^- \*\*(F\d)\. .* ((?:Absorbed by|Deferred) .*)$/gm)) marks.set(m[1], m[2]);
  // A7 + B12 + C8 + D8 + E9 + F9.
  assert.equal(marks.size, 53);
  // The plan's appendix says the same.
  const appendix = plan.split("## Appendix")[1].split("\n---")[0];
  const mapped = new Map([...appendix.matchAll(/\b([A-F]\d+) ((?:R\d-\d\d(?:, )?)+|deferred)/g)].map(m => [m[1], m[2]]));
  const progress = plan.split("\n## Progress")[1];
  const problems = [];
  for (const [id, mark] of marks) {
    const items = [...mark.matchAll(/R\d-\d\d/g)].map(m => m[0]);
    if (!items.length) {
      if (!/^Deferred/.test(mark) || mapped.get(id) !== "deferred") problems.push(`${id}: no R-item and not deferred in the plan`);
      continue;
    }
    if (items.join(", ") !== mapped.get(id)) problems.push(`${id}: ${items.join(", ")} here, ${mapped.get(id)} in the plan's appendix`);
    for (const r of items) {
      if (!plan.includes(`| **${r}** |`)) problems.push(`${id}: ${r} is not a row of the plan`);
      if (!new RegExp(`^${r} (done|partly done)`, "m").test(progress)) problems.push(`${id}: ${r} has no Progress line`);
    }
  }
  assert.deepEqual(problems, []);
  assert.match(punch, /Retired Oct 29, 2026 \(R6-11\)/);
});
