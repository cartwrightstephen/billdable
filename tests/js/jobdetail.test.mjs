// A finished job opened from Job history is fetched whole. Only the server saying it isn't there
// (404) makes it "not found"; a dropped connection or a server error is tried again.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");

const company = () => ({ company: { name: "Pine" }, jobs: [], payers: [], inbox: [], email_log: [], email_log_total: 0 });

test("a failed fetch doesn't mark the job missing; it is tried again on the next visit", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company());
  page.setLocation("job", "j-old");
  dom.answers = [() => "offline"];
  await core.loadJobDetail("j-old");
  assert.equal(core.missingJobs.has("j-old"), false);
  assert.doesNotMatch(reg.views.job(), /Job not found/);
  assert.match(reg.views.job(), /Couldn't load this job/);
  dom.answers = [() => ({ status: 503, body: { error: "Busy." } })];
  await core.loadJobDetail("j-old");
  assert.equal(core.missingJobs.has("j-old"), false);
  // Going to the job again asks again.
  assert.equal(core.jobDetailFailed, "j-old");
  core.nav("job", "j-old");
  assert.equal(core.jobDetailFailed, null);
});

test("a job the server says isn't there is not found", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company());
  page.setLocation("job", "j-gone");
  dom.answers = [() => ({ status: 404, body: { error: "Job not found." } })];
  await core.loadJobDetail("j-gone");
  assert.equal(core.missingJobs.has("j-gone"), true);
  assert.match(reg.views.job(), /Job not found/);
});

// ---------- jobs-clients (Stephen's Oct 29 review) ----------
const money = (over = {}) => ({ contract_cents: 1000000, approved_co_cents: 0, contract_total_cents: 1000000, billed_cents: 400000,
  collected_cents: 400000, outstanding_cents: 0, unbilled_completed_cents: 0, retainage_held_cents: 0, retainage_released_cents: 0,
  retainage_open_cents: 0, pending_co_cents: 0, ...over });
const fullJob = (over = {}) => ({
  id: "j-1", code: "OAK", name: "Oak Street", address: "418 Oak St, Decatur, GA 30030", payer: { id: "p-1", name: "Ellison", role: "Owner", client_type: "homeowner" },
  payer_id: "p-1", closed: false, work_status: "in_progress", blockers: [], close_blockers: [], close_blocker_items: [], invoices: [], change_orders: [],
  payments: [], records: [], packet: [], waiver_due: false, waiver_sent: false, money: money(), started_on: "2026-03-02",
  stage: { pct_billed: 40, steps: [{ key: "started", label: "Started", state: "done" }, { key: "in_progress", label: "Work in progress", state: "current" }, { key: "finished", label: "Finished", state: "todo" }] },
  key_dates: [{ key: "started", label: "Started", date: "2026-03-02" }, { key: "cutoff", label: "Pay app due by", date: "2026-11-25" }],
  address_parts: { street: "418 Oak St", city: "Decatur", state: "GA", zip: "30030" },
  ...over,
});

test("the job page: where it stands, and one Next step card that lists every blocker (J6, J7)", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  const blocked = fullJob({ blocked: true, blockers: ["Signed contract", "Daily tickets"], invoices: [{ id: "i1", number: "PA-1", status: "awaiting_approval", amount_cents: 100000 }] });
  core.setState({ ...company(), jobs: [blocked], payers: [blocked.payer] });
  page.setLocation("job", "j-1");
  const html = reg.views.job();
  assert.match(html, /Where this job stands/);
  assert.match(html, /aria-current="step">Work in progress, 40% billed/);
  assert.match(html, /Pay app due by<\/dt><dd>Nov 25, 2026/);
  assert.match(html, /Homeowner/, "the client's type beside its name");
  assert.equal((html.match(/class="next-card/g) || []).length, 1);
  assert.match(html, /<strong>Before it can go:<\/strong>\s*<ul class="blocker-list"><li>Signed contract<\/li><li>Daily tickets<\/li><\/ul>/);
  assert.doesNotMatch(html, /blockers-card/);
  assert.match(html, /<h2>Activity<\/h2>/);
});

test("a finished job is its job file: Download job file, Add more work, no working forms (J9)", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  const done = fullJob({ closed: true, closed_at: "2026-09-01T10:00:00+00:00", work_status: "complete",
    invoices: [{ id: "i1", number: "PA-1", status: "paid", amount_cents: 400000, sent_at: "2026-05-01T10:00:00+00:00" }] });
  core.setState({ ...company(), jobs: [done], payers: [done.payer] });
  page.setLocation("job", "j-1");
  const html = reg.views.job();
  assert.match(html, /Job file/);
  assert.match(html, /href="\/api\/jobs\/j-1\/archive\/job-file\.zip" download>Download job file/);
  assert.match(html, /Add more work/);
  assert.match(html, /href="\/api\/jobs\/j-1\/payapp\/i1\.pdf"/);
  assert.doesNotMatch(html, /Reopen/);
  assert.doesNotMatch(html, /Edit job|next-card|Take a photo/);
  page.setMe({ name: "Sam", company_id: "co-p", role: "member", perms: ["record_payments"] });
  assert.doesNotMatch(reg.views.job(), /Add more work/, "only with Finish and reopen jobs, as the server");
  page.setMe({ name: "Lee", company_id: "co-p", role: "member", perms: ["close_jobs"] });
  assert.match(reg.views.job(), /Add more work/, "a member whose profile may finish and reopen jobs");
});

test("Edit job: the address in four boxes, an older one-line address all in Street (J1, J8)", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  const old = fullJob({ address: "418 Oak Street, Decatur", address_parts: { street: "418 Oak Street, Decatur", city: "", state: "", zip: "" }, started_on: "" });
  core.setState({ ...company(), jobs: [old], payers: [old.payer] });
  page.setLocation("job", "j-1");
  page.formOpen.editjob = true;
  try {
    const html = reg.views.job();
    assert.match(html, /id="ej-street"[^>]*value="418 Oak Street, Decatur"/);
    assert.match(html, /id="ej-city"[^>]*value=""/);
    assert.match(html, /<select id="ej-state"[^>]*>(?:(?!<\/select>)[\s\S])*<option value="GA">GA<\/option>/);
    assert.match(html, /id="ej-zip"[^>]*data-rule="zip"/);
    assert.match(html, /id="ej-start"[^>]*type="date" value=""/, "an older job's unknown start date stays blank, not today");
  } finally { page.formOpen.editjob = false; }
});

test("New job: the four address boxes, Client type only for a new client, the start date at today (J1, J2, J8)", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  const saved = { id: "p-1", name: "Ellison", role: "GC", client_type: "gc" };
  core.setState({ ...company(), jobs: [], payers: [saved] });
  page.setLocation("jobs");
  page.formOpen.newjob = true;
  const forms = await import("../../static/js/forms.js");
  try {
    let html = reg.views.jobs();
    for (const box of ["nj-street", "nj-city", "nj-state", "nj-zip", "nj-start"]) assert.match(html, new RegExp(`id="${box}"`));
    assert.match(html, /<div data-payer-slot="client_type">/);
    assert.match(html, /Import contract/);
    const today = new Date();
    const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
    assert.match(html, new RegExp(`id="nj-start"[^>]*value="${iso}"`));
    forms.formDraft.newjob = { customer: "ellison" };
    html = reg.views.jobs();
    assert.match(html, /<div data-payer-slot="client_type" hidden>/, "a saved client keeps its own type");
    assert.match(html, /General contractor\. Pay apps go to/);
  } finally { page.formOpen.newjob = false; delete forms.formDraft.newjob; }
});

test("the job form's own checks name each box, as the server's (G1, J1)", async () => {
  const { jobFieldErrors } = await import("../../static/js/views/jobs.js");
  assert.deepEqual(jobFieldErrors({ name: "Oak", street: "1 Main St", city: "Atlanta", state: "GA", zip: "30303", contract_number: "SC-1" }), {});
  const errors = jobFieldErrors({ name: "", street: "", city: "Atlanta", state: "ZZ", zip: "3030", contract_number: "SC*1" });
  assert.deepEqual(Object.keys(errors).sort(), ["contract_number", "name", "state", "street", "zip"]);
  assert.match(errors.zip, /5-digit ZIP/);
});

test("Edit job checks only the boxes that changed (review C4)", async () => {
  const { jobFieldErrors, savedJobFields } = await import("../../static/js/views/jobs.js");
  const j = { name: "Porch", contract_number: "PO*4500; Rev 2", address_parts: { street: "Lot 4 behind the mill", city: "", state: "", zip: "" } };
  const saved = savedJobFields(j);
  const v = { ...saved, retainage_pct: "5" };
  assert.deepEqual(Object.keys(jobFieldErrors(v)).sort(), ["city", "contract_number", "state", "zip"], "without saved, all checked");
  assert.deepEqual(jobFieldErrors(v, saved), {}, "left as they were: nothing to fix");
  assert.deepEqual(Object.keys(jobFieldErrors({ ...v, street: "Lot 5 behind the mill" }, saved)).sort(), ["city", "state", "zip"]);
  assert.deepEqual(Object.keys(jobFieldErrors({ ...v, contract_number: "PO*4501" }, saved)), ["contract_number"]);
  assert.deepEqual(jobFieldErrors({ ...v, contract_number: "SC_2026_014" }, saved), {});
});

test("an imported schedule that doesn't add up to the amount says by how much and offers its total (review C6)", async () => {
  const { sovTotalChoice } = await import("../../static/js/views/jobs.js");
  const { formDraft } = await import("../../static/js/forms.js");
  const r = { sov: { total_cents: 4_500_000, lines: [{}, {}] } };
  formDraft.newjob = { contract_cents: "50,000" };
  const html = sovTotalChoice(r);
  assert.match(html, /The schedule adds up to \$45,000\.00; the contract amount is \$50,000\.00, \$5,000\.00 less\./);
  assert.match(html, /id="nj-sov-total"/);
  assert.match(html, /Use the schedule total as the contract amount/);
  assert.doesNotMatch(html, /style=/);
  assert.match(sovTotalChoice({ ...r, useSovTotal: true }), /id="nj-sov-total" checked/);
  formDraft.newjob = { contract_cents: "45,000" };
  assert.equal(sovTotalChoice(r), "", "adds up: nothing to say");
  formDraft.newjob = { contract_cents: "50,000" };
  assert.equal(sovTotalChoice({ ...r, useSov: false }), "", "schedule unticked");
  delete formDraft.newjob;
});

test("Client playbook: the paperwork as checkboxes and Other, the client type, and Add client's import (S11, J2, S12, S13)", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  const p = { id: "p-1", name: "Ellison", role: "GC", client_type: "sub", approver: "Mara", ap_email: "ap@e.example",
    packet: "Signed contract, Certified payroll", packet_items: ["Signed contract", "Certified payroll"], job_count: 0 };
  const paperwork_catalog = [{ key: "contract", label: "Signed contract", closeout: false }, { key: "insurance", label: "Insurance certificate", closeout: false },
    { key: "punch", label: "Punch list sign-off", closeout: true }];
  core.setState({ ...company(), jobs: [], payers: [p], paperwork_catalog });
  page.setLocation("playbook");
  page.formOpen.addpayer = true;
  try {
    const html = reg.views.playbook();
    assert.match(html, /Another subcontractor \(you are a lower tier\)/);
    assert.match(html, /data-packet-choice="Signed contract" checked/);
    assert.doesNotMatch(html, /data-packet-choice="Insurance certificate" checked/);
    assert.match(html, /id="payer-p-1-pw-other"[^>]*value="Certified payroll"/);
    assert.match(html, /Nothing needed/);
    assert.match(html, /<h4>At closeout<\/h4><label[^>]*><input type="checkbox"[^>]*data-packet-choice="Punch list sign-off"/, "the catalog's closeout items, from the server");
    assert.doesNotMatch(html, /name="packet"/, "no free-text paperwork box");
    assert.match(html, /<h2>Add client<\/h2>\s*<div class="contract-import" data-contract-import="addpayer">/);
  } finally { page.formOpen.addpayer = false; }
});
