// Round 1, after the three parts were merged: New job's Retainage % follows the client, the client
// card's Usual retainage %, an imported schedule saved with the job, and a lump sum's % done to date.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom, stubEl } from "./fakedom.mjs";

const dom = installFakeDom();
globalThis.requestAnimationFrame = (f) => f();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const forms = await import("../../static/js/forms.js");
const reg = await import("../../static/js/views/index.js");
const jobs = await import("../../static/js/jobs.js");
const jobsView = await import("../../static/js/views/jobs.js");
const daily = await import("../../static/js/views/daily.js");

const owner = { id: "u1", name: "Pat", company_id: "co-p", role: "owner" };
const member = { id: "u2", name: "Fred", company_id: "co-p", role: "member", perms: ["record_payments"] };
const gc = { id: "p-1", name: "Northline GC", role: "GC", client_type: "gc", approver: "Sam", ap_email: "ap@northline.example", phone: "(404) 555-0100",
  playbook_missing: [], retainage_default: 10, follow_up_days: 7 };
const company = (over = {}) => ({ company: { name: "Pine" }, jobs: [], payers: [gc], inbox: [], email_log: [], email_log_total: 0, files: [], daily_records: [], ...over });
const newJobBox = (html) => (html.match(/<input id="nj-retainage"[^>]*>/) || [""])[0];

function newJob(draft) {
  page.setMe(owner);
  core.setState(company());
  forms.clearForm("newjob");
  if (draft) Object.assign((forms.formDraft.newjob ||= {}), draft);
  page.formOpen.newjob = true;
  page.setLocation("jobs");
  return reg.views.jobs();
}

test("the retainage a new job starts at: the client's usual, else its type's", () => {
  assert.equal(jobs.newJobRetainage(null, ""), null);
  for (const [kind, pct] of [["gc", 10], ["public", 10], ["sub", 10], ["owner", 0], ["homeowner", 0]]) assert.equal(jobs.newJobRetainage(null, kind), pct, kind);
  assert.equal(jobs.newJobRetainage({ retainage_default: 10 }), 10);
  assert.equal(jobs.newJobRetainage({ retainage_pct: 5, retainage_default: 5 }, "gc"), 5, "the playbook's own figure wins");
  assert.equal(jobs.newJobRetainage({ retainage_pct: 0, retainage_default: 0 }, "gc"), 0, "a usual 0 is 0");
  assert.equal(jobs.newJobRetainage({ retainage_default: 10 }, "homeowner"), 0, "a type picked here, for a client saved without one");
});

test("New job prefills Retainage % from the client picked, and keeps what was typed, a 0 too", () => {
  assert.match(newJobBox(newJob()), /value=""/, "no client yet: blank");
  assert.match(newJobBox(newJob({ client_mode: "existing", customer: "Northline GC" })), /value="10"/);
  assert.match(newJobBox(newJob({ client_mode: "new", customer: "Lee Home", client_type: "homeowner" })), /value="0"/);
  assert.match(newJobBox(newJob({ client_mode: "new", customer: "City of Decatur", client_type: "public" })), /value="10"/);
  // A figure left over from an earlier client follows the new one until the person types.
  assert.match(newJobBox(newJob({ client_mode: "existing", customer: "Northline GC", retainage_pct: "0" })), /value="10"/);
  assert.match(newJobBox(newJob({ client_mode: "existing", customer: "Northline GC", retainage_pct: "0", retainage_touched: true })), /value="0"/);
  // In place, as the client box changes (prefillRetainage).
  const box = stubEl({ value: "" });
  const form = stubEl({ querySelector: (sel) => ({ '[data-field="retainage_pct"]': box, '[data-field="customer"]': { value: "Northline GC" }, '[data-field="client_type"]': null })[sel] || null });
  dom.bySelector['[data-form-key="newjob"]'] = form;
  forms.formDraft.newjob = {};
  jobsView.prefillRetainage();
  assert.equal(box.value, "10");
  box.value = "0"; forms.formDraft.newjob.retainage_touched = true;
  jobsView.prefillRetainage();
  assert.equal(box.value, "0", "typed: left alone");
  delete dom.bySelector['[data-form-key="newjob"]'];
  page.formOpen.newjob = false;
  forms.clearForm("newjob");
});

// The New job form's boxes as formValues reads them, then Save job; returns what was posted.
async function saveNewJob(values) {
  page.setMe(member);
  core.setState(company());
  page.formOpen.newjob = true;
  forms.formDraft.newjob = { client_mode: "existing" };
  dom.all['[data-form-key="newjob"] [data-field]'] = Object.entries(values).map(([k, v]) => stubEl({ dataset: { field: k }, value: v }));
  dom.requests.length = 0;
  dom.answers.push((path) => (path === "/api/jobs" ? { status: 200, body: { ok: true, jobs: [] } } : undefined));
  await jobsView.actions["create-job"]();
  dom.answers.pop();
  delete dom.all['[data-form-key="newjob"] [data-field]'];
  return dom.requests.filter(r => r.path === "/api/jobs" && r.method === "POST").map(r => r.body);
}
const JOB_BOXES = { name: "Oak kitchen", street: "9 Oak St", city: "Atlanta", state: "GA", zip: "30307", customer: "Northline GC", contract_cents: "48,500" };

test("Save job sends a typed 0 as 0, and a blank box blank so the server takes the client's usual", async () => {
  let [body] = await saveNewJob({ ...JOB_BOXES, retainage_pct: "0" });
  assert.equal(body.retainage_pct, 0);
  [body] = await saveNewJob({ ...JOB_BOXES, retainage_pct: "" });
  assert.equal(body.retainage_pct, "");
  [body] = await saveNewJob({ ...JOB_BOXES, retainage_pct: "10" });
  assert.equal(body.retainage_pct, 10);
});

test("an imported schedule goes with the job itself, its total as the contract when ticked: no second request a Member is refused", async () => {
  const sov = { lines: [{ item: "1", description: "Framing", quantity: "1", unit: "LS", rate: "30000" }, { item: "2", description: "Drywall", quantity: "1", unit: "LS", rate: "20000" }], total_cents: 5_000_000 };
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (path, init) => (path === "/api/contract/read"
    ? { ok: true, status: 200, json: async () => ({ ok: true, fields: {}, notes: [], sov }) } : realFetch(path, init));
  dom.byId["contract-text-newjob"] = stubEl({ value: "Contract. Schedule of values..." });
  await jobsView.actions["contract-paste"]({ dataset: { for: "newjob" } });
  globalThis.fetch = realFetch;
  dom.byId["nj-sov-total"] = stubEl({ checked: true });
  const [body] = await saveNewJob({ ...JOB_BOXES, contract_cents: "48,500", retainage_pct: "10" });
  delete dom.byId["nj-sov-total"];
  delete dom.byId["contract-text-newjob"];
  assert.equal(body.contract_cents, 5_000_000, "the schedule's total is the contract amount");
  assert.deepEqual(body.sov_lines, sov.lines);
  assert.equal(dom.requests.filter(r => /\/sov$/.test(r.path)).length, 0, "no separate schedule request");
});

test("the client card asks Usual retainage % in Contact and billing, and the read-only card shows it", () => {
  page.setMe(owner);
  core.setState(company({ payers: [{ ...gc, retainage_pct: 5, retainage_default: 5 }] }));
  core.moreDetails[`payer-card-${gc.id}`] = true;
  page.setLocation("playbook");
  let html = reg.views.playbook();
  const contact = html.slice(html.indexOf("<h3>Contact and billing</h3>"), html.indexOf("Supporting documents</"));
  assert.match(contact, /<input id="[^"]*retainage_pct[^"]*"[^>]*data-field="retainage_pct"[^>]*value="5"/);
  assert.match(contact, /Usual retainage %/);
  core.setState(company());
  html = reg.views.playbook();
  assert.match(html, /data-field="retainage_pct"[^>]*value=""[^>]*placeholder="10"|data-field="retainage_pct"[^>]*placeholder="10"[^>]*value=""/, "blank: goes by the type, shown as the placeholder");
  page.setMe(member);
  html = reg.views.playbook();
  assert.doesNotMatch(html, /data-field="retainage_pct"/);
  assert.match(html, /<span class="label">Usual retainage %<\/span><span>10%<\/span>/);
});

test("the client card's Save sends Usual retainage %, and a figure past 20 is refused in place", async () => {
  page.setMe(owner);
  core.setState(company());
  const key = `payer-${gc.id}`;
  const boxes = (pct) => { dom.all[`[data-form-key="${key}"] [data-field]`] = Object.entries({ name: "Northline GC", client_type: "gc", approver: "Sam", ap_email: "ap@northline.example", phone: "(404) 555-0100", retainage_pct: pct })
    .map(([k, v]) => stubEl({ dataset: { field: k }, value: v })); };
  dom.answers.push((path) => (path === `/api/payers/${gc.id}/update` ? { status: 200, body: { ok: true } } : undefined));
  dom.requests.length = 0;
  boxes("5");
  await reg.actions["save-playbook"]({ dataset: {} }, gc.id);
  const sent = dom.requests.find(r => r.path === `/api/payers/${gc.id}/update`);
  assert.equal(sent.body.retainage_pct, "5");
  dom.requests.length = 0;
  boxes("25");
  await reg.actions["save-playbook"]({ dataset: {} }, gc.id);
  assert.equal(dom.requests.filter(r => r.path === `/api/payers/${gc.id}/update`).length, 0);
  assert.match(forms.formErrors[key].retainage_pct, /0 to 20 percent/);
  dom.answers.pop();
  delete dom.all[`[data-form-key="${key}"] [data-field]`];
  forms.clearForm(key);
});

test("New job record asks a lump sum's % done to date, and today's quantity on the other lines", () => {
  const lump = { id: "l1", item: "1", description: "Contract amount", lump_sum: true, unit: "LS", qty_milli: 1000, done_milli: 250 };
  const unit = { id: "l2", item: "2", description: "Conduit", lump_sum: false, unit: "LF", qty_milli: 400000, done_milli: 120000 };
  const job = (lines) => ({ id: "j-1", name: "Peachtree", code: "PEAC", payer_id: "p-1", payer: gc, closed: false, work_status: "in_progress", invoices: [], payments: [],
    change_orders: [], records: [], blockers: [], close_blockers: [], money: { contract_cents: 4850000 }, sov_view: { present: true, lines } });
  page.setMe(owner);
  core.setState(company({ jobs: [job([lump])] }));
  page.setLocation("daily");
  let html = reg.views.daily();
  assert.match(html, /<h3>Work done to date<\/h3>/);
  assert.match(html, /aria-label="Contract amount: % done to date"/);
  assert.doesNotMatch(html, /% done today/);
  core.setState(company({ jobs: [job([lump, unit])] }));
  html = reg.views.daily();
  assert.match(html, /<h3>Quantities put in place today<\/h3>/);
  assert.match(html, /data-label="% done to date"/);
  assert.match(html, /aria-label="Conduit: LF put in place today"/);
  const body = daily.dailyBody({ notes: "", qty_l1: "60", qty_l2: "40" }, job([lump, unit]), new Set());
  assert.deepEqual(body.sections.quantities, [{ line_id: "l1", qty: "60", to_date: true }, { line_id: "l2", qty: "40" }]);
});
