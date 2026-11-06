// Round 1 (Nov 5 audits), the job page and its forms: who may change a job (2), the Next step and
// its button (6), the lump-sum box and dollars (10), the job page on a phone (13).
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
globalThis.requestAnimationFrame = (f) => f();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");
const jobs = await import("../../static/js/jobs.js");

const Y = new Date().getFullYear();   // summaries leave out this year
const company = () => ({ company: { name: "Pine" }, jobs: [], payers: [], inbox: [], email_log: [], email_log_total: 0, files: [] });
const money = (over = {}) => ({ contract_cents: 4850000, approved_co_cents: 0, contract_total_cents: 4850000, billed_cents: 0,
  collected_cents: 0, outstanding_cents: 0, unbilled_completed_cents: 0, retainage_held_cents: 0, retainage_released_cents: 0,
  retainage_open_cents: 0, pending_co_cents: 0, ...over });
const lump = (over = {}) => ({ id: "l1", item: "1", description: "Contract amount", lump_sum: true, unit: "LS", qty_milli: 1000, rate_cents: 4850000,
  scheduled_cents: 4850000, done_milli: 250, pct: 25, to_date_cents: 1212500, billed_cents: 1212500, billed_qty_milli: 250, ...over });
const job = (over = {}) => ({
  id: "j-1", code: "PEAC", name: "Peachtree", address: "1 Elm St", payer: { id: "p-1", name: "Northline GC", role: "GC", client_type: "gc", approver: "Sam" },
  payer_id: "p-1", closed: false, work_status: "in_progress", blockers: [], close_blockers: [], close_blocker_items: [], invoices: [], change_orders: [],
  payments: [], records: [], packet: [], waiver_due: false, waiver_sent: false, money: money(), started_on: "2026-11-01", retainage_pct: 10,
  sov_view: { present: true, auto: true, editable: true, lines: [lump()], op_bp: 0, op_billed_cents: 0, totals: { scheduled_cents: 4850000, to_date_cents: 1212500, op_scheduled_cents: 0, op_to_date_cents: 0 } },
  paperwork_view: { labels: ["Conditional waiver", "Insurance certificate"], own: false },
  ...over,
});
const member = { name: "Fred", company_id: "co-p", role: "member", perms: ["record_payments"] };
const owner = { name: "Pat", company_id: "co-p", role: "owner" };
function draw(j, me = owner) {
  page.setMe(me);
  core.setState({ ...company(), jobs: [j], payers: [j.payer] });
  page.setLocation("job", j.id);
  return reg.views.job();
}
const phone = (on) => { globalThis.matchMedia = (q) => ({ matches: on && /max-width/.test(q) }); };

// ---------- 2: manage_jobs ----------
test("a Member doesn't see Edit job, Final pay app, Bill now, Signed on paper or Withdraw", () => {
  phone(false);
  const co = { id: "co-1", number: 1, title: "Rail", amount_cents: 50000, status: "pending", included_in_billing: false };
  const j = job({ change_orders: [co] });
  const mine = draw(j, member);
  for (const hidden of ['data-form="editjob"', 'data-form="done"', 'data-action="include-co"', 'data-action="withdraw-co"', "Signed on paper"]) {
    assert.doesNotMatch(mine, new RegExp(hidden), hidden);
  }
  assert.match(mine, /data-action="request-co-signature"/, "asking for a signature stays");
  assert.match(mine, /data-form="progress"/, "progress stays");
  const owners = draw(j, owner);
  for (const shown of ['data-form="editjob"', 'data-form="done"', 'data-action="include-co"', 'data-action="withdraw-co"', "Signed on paper"]) {
    assert.match(owners, new RegExp(shown), shown);
  }
});

// ---------- 6: the Next step and its button ----------
test("the Next step names the button under it, and a step that isn't yours says whose it is", () => {
  phone(false);
  const draft = { id: "i1", number: "PA-01", status: "awaiting_approval", amount_cents: 1091250, packet: [] };
  const ready = job({ invoices: [draft] });
  const o = jobs.jobNextStep(ready, { perms: { owner: true } });
  assert.equal(o.primary.label, "Review & send");
  assert.match(o.line, /Review & send/);
  const m = jobs.jobNextStep(ready, { perms: { list: ["record_payments"] } });
  assert.equal(m.primary, null, "no main button that does something else");
  assert.match(m.line, /Review & send takes the “Send pay apps” permission\./);
  assert.ok(!m.more.some(a => a.key === "progress" && a === m.primary));
  // A new job: a Member isn't told to use Final pay app.
  const fresh = jobs.jobNextStep(job(), { perms: { list: [] } });
  assert.equal(fresh.primary.label, "Progress pay app");
  assert.doesNotMatch(fresh.line, /Final pay app/);
});

test("Before you can finish this job keeps its list but not the buttons already on the page", () => {
  phone(false);
  const sent = { id: "i1", number: "PA-01", status: "sent", amount_cents: 1000000, remaining_cents: 1000000, sent_at: "2026-11-01T10:00:00+00:00" };
  const j = job({ work_status: "complete", invoices: [sent], money: money({ outstanding_cents: 1000000, retainage_open_cents: 50000 }),
    close_blockers: ["PA-01 isn't paid.", "Retainage"], close_blocker_items: [
      { kind: "outstanding", text: "PA-01 isn't paid.", invoice_id: "i1" }, { kind: "retainage", text: "$500.00 retainage is held." }] });
  const html = draw(j);
  const card = html.slice(html.indexOf('class="card close-blockers"'));
  const cardEnd = card.indexOf("</section>");
  const inCard = card.slice(0, cardEnd);
  assert.match(inCard, /PA-01 isn/);
  assert.doesNotMatch(inCard, /Record a payment/, "it is in Get paid already");
  assert.doesNotMatch(inCard, /Record retainage paid/);
  assert.equal((html.match(/data-form="match"/g) || []).length, 1, "Record a payment once on the page");
});

// ---------- 10: the lump-sum box and dollars ----------
test("a lump-sum line's box says % done, and a one-line schedule offers Bill by dollars instead", () => {
  phone(false);
  page.formOpen.progress = true;
  const html = draw(job());
  assert.match(html, /aria-label="Contract amount: % done to date"/);
  assert.match(html, /data-label="% done to date"/);
  assert.match(html, /data-action="progress-dollars"/);
  assert.match(html, />Bill by dollars instead</);
  // Two lines: no dollar form.
  const two = job();
  two.sov_view = { ...two.sov_view, auto: false, lines: [lump({ scheduled_cents: 2000000, rate_cents: 2000000 }), lump({ id: "l2", lump_sum: false, unit: "ft", qty_milli: 100000, rate_cents: 28500, scheduled_cents: 2850000 })] };
  assert.doesNotMatch(draw(two), /progress-dollars/);
  page.formOpen.progress = false;
});

test("Bill by dollars on a one-line schedule: what is typed becomes the line's % done and its exact amount", () => {
  const j = job({ invoices: [{ id: "i0", number: "PA-01", status: "paid", amount_cents: 1091250, retainage_cents: 121250, gross_cents: 1212500 }] });
  // $12,125 billed of $48,500; this draw $10,000 → $22,125 to date, 45.6% (45.618...), billed exactly $10,000.
  const f = jobs.lumpDollars(j, "period", "10,000");
  assert.equal(f.error, "");
  assert.equal(f.done, "0.456");
  assert.equal(f.amount, "10000.00");
  const all = jobs.lumpDollars(j, "total", "48,500");
  assert.equal(all.done, "1.000");
  assert.equal(all.amount, "36375.00");
  assert.match(jobs.lumpDollars(j, "period", "50,000").error, /Enter no more than the \$36,375\.00 left/);
});

// ---------- 13: the job page on a phone ----------
test("on a phone Money, the schedule, supporting documents, job records and activity start closed with a summary line", () => {
  phone(true);
  for (const k of Object.keys(core.moreDetails)) delete core.moreDetails[k];
  const j = job({ records: [
    { id: "r1", kind: "photo", label: "Deck", file_ids: ["f1"], added_at: `${Y}-11-03T10:00:00+00:00` },
    { id: "r2", kind: "ticket", label: "Ticket 4", file_ids: ["f2"], added_at: `${Y}-11-05T10:00:00+00:00`, source: "Upload" },
  ] });
  page.setMe(owner);
  core.setState({ ...company(), jobs: [j], payers: [j.payer], files: [{ id: "f1", name: "deck.jpg" }, { id: "f2", name: "ticket.pdf" }] });
  page.setLocation("job", j.id);
  const html = reg.views.job();
  assert.match(html, /<details class="card money-file" data-more="job-money">/);
  assert.match(html, /<details class="card sov-file" id="sov-card" data-more="job-sov">\s*<summary><h2>Schedule of values<\/h2><span class="sub">1 line · 25% billed<\/span>/);
  assert.match(html, /<details class="card job-paperwork" data-more="job-docs">\s*<summary><h2[^>]*>Supporting documents for this job<\/h2><span class="sub">Conditional waiver, Insurance certificate<\/span>/);
  assert.match(html, /<details class="card job-records" data-more="job-records">\s*<summary><h2>Job records<\/h2><span class="sub">2 files · last Nov 5<\/span>/);
  assert.match(html, /<details class="card job-history" data-more="job-activity">/);
  // One row per file: the label is the link, no second line naming the file.
  assert.match(html, /<a href="\/api\/files\/f1"[^>]*>Deck<\/a>/);
  assert.doesNotMatch(html, />deck\.jpg</);
  // The fold is remembered.
  core.moreDetails["job-money"] = true;
  assert.match(reg.views.job(), /<details class="card money-file" data-more="job-money" open>/);
  delete core.moreDetails["job-money"];
  phone(false);
  assert.match(reg.views.job(), /<details class="card money-file" data-more="job-money" open>/, "desktop: open");
});

test("Activity shows 5 entries, then Show all", () => {
  phone(true);
  const j = job();
  page.setMe(owner);
  core.setState({ ...company(), jobs: [j], payers: [j.payer] });
  page.setLocation("job", j.id);
  const entries = Array.from({ length: 8 }, (_, n) => ({ kind: "audit", action: "record_progress", text: `Entry ${n}`, at: `${Y}-11-0${n + 1}T10:00:00+00:00`, who: "Pat" }));
  core.listPages.timeline = { job: j.id, entries, total: 41, loading: false };
  let html = reg.views.job();
  assert.match(html, /<summary><h2>Activity<\/h2><span class="sub">41 entries · last: [^<]+ Nov 1<\/span>/);
  assert.equal((html.match(/Entry \d/g) || []).length, 5);
  assert.match(html, /data-action="activity-all"[^>]*>Show all</);
  core.moreDetails["job-activity-all"] = true;
  html = reg.views.job();
  assert.equal((html.match(/Entry \d/g) || []).length, 8);
  delete core.moreDetails["job-activity-all"];
  phone(false);
});

// ---------- 15: New job record ----------
test("New job record: Diary, crew, weather and quantities open; the documents are chips that open their block", async () => {
  const daily = await import("../../static/js/views/daily.js");
  page.setMe(owner);
  const j = job();
  core.setState({ ...company(), jobs: [j], payers: [j.payer], daily_records: [] });
  page.setLocation("daily");
  let html = reg.views.daily();
  for (const id of ["daily-notes", "daily-crew", "daily-weather", "daily-qty-l1"]) assert.match(html, new RegExp(`id="${id}"`), id);
  assert.doesNotMatch(html, /data-daily-section="quantities"/, "quantities aren't behind a tick box");
  const chips = [...html.matchAll(/data-action="daily-chip" data-kind="([a-z_]+)"[^>]*>([^<]+)</g)].map(m => [m[1], m[2]]);
  assert.deepEqual(chips, [["delivery", "+ Delivery ticket"], ["photos", "+ Photos"], ["time_sheets", "+ Time sheet"], ["invoices", "+ Invoice"], ["inspections", "+ Inspection"], ["other", "+ Other"]]);
  assert.doesNotMatch(html, /data-record-section=/, "no document block until its chip is tapped");
  daily.actions["daily-chip"]({ dataset: { kind: "delivery" } });
  html = reg.views.daily();
  assert.match(html, /data-record-section="delivery"/);
  assert.match(html, /id="daily-delivery-supplier-0"/);
  assert.doesNotMatch(html, /data-record-section="photos"/);
  // Tapped again, it adds another ticket.
  daily.actions["daily-chip"]({ dataset: { kind: "delivery" } });
  assert.match(reg.views.daily(), /id="daily-delivery-supplier-1"/);
});

test("Take a photo takes several before Done", async () => {
  const daily = await import("../../static/js/views/daily.js");
  page.setMe(owner);
  const j = job();
  core.setState({ ...company(), jobs: [j], payers: [j.payer], daily_records: [] });
  page.setLocation("daily");
  daily.actions["daily-chip"]({ dataset: { kind: "photos" } });
  let html = reg.views.daily();
  assert.match(html, /<input type="file"[^>]*id="daily-photos_0-camera"[^>]*capture="environment" multiple/);
  daily.daily.files.photos_0 = [{ name: "deck1.jpg" }, { name: "deck2.jpg" }];
  html = reg.views.daily();
  assert.match(html, /Take another photo/);
  assert.match(html, /data-action="daily-photos-done"[^>]*>Done</);
  daily.actions["daily-photos-done"]({ dataset: { kind: "photos_0" } });
  html = reg.views.daily();
  assert.match(html, /2 photos/);
  assert.doesNotMatch(html, /Take another photo/);
});

// ---------- 16, 20: New job ----------
test("New job: required fields first with retainage beside the amount; Import contract and the schedule folded with summaries", () => {
  page.setMe(owner);
  core.setState({ ...company(), payers: [] });
  page.formOpen.newjob = true;
  page.setLocation("jobs");
  const html = reg.views.jobs();
  const at = (id) => html.indexOf(`id="${id}"`);
  for (const id of ["nj-name", "nj-street", "nj-amount", "nj-retainage", "nj-customer", "nj-client-type", "nj-approver", "nj-email", "nj-phone"]) assert.ok(at(id) > 0, id);
  const lastRequired = Math.max(...["nj-name", "nj-street", "nj-amount", "nj-customer", "nj-client-type", "nj-approver", "nj-email", "nj-phone"].map(at));
  for (const id of ["nj-start", "nj-end", "nj-contract", "nj-note"]) assert.ok(at(id) > lastRequired, `${id} comes after the required fields`);
  assert.ok(at("nj-retainage") < at("nj-start"), "retainage is with the money, before the optional fields");
  assert.match(html, /<details class="contract-fold" data-more="nj-import">\s*<summary>Import contract <span class="sub">[^<]+<\/span><\/summary>/);
  assert.match(html, /<details class="nj-sov" data-more="nj-sov">\s*<summary>Schedule of values <span class="sub">One lump-sum line for the contract amount<\/span><\/summary>/);
  assert.ok(html.indexOf('data-action="create-job"') > html.indexOf('data-more="nj-sov"'));
  page.formOpen.newjob = false;
});

test("New job: a job of the same name is asked about, with Open it and Keep as a new job", async () => {
  const views = await import("../../static/js/views/jobs.js");
  const box = { hidden: true, innerHTML: "" };
  const before = globalThis.document.querySelector;
  globalThis.document.querySelector = (sel) => (sel === '[data-same-job="newjob"]' ? box : null);
  assert.ok(views.showSameJob({ same_job: { id: "j-1", name: "Peachtree", typed: "peachtree" } }));
  assert.equal(box.hidden, false);
  assert.match(box.innerHTML, /data-action="open-job" data-id="j-1">Open Peachtree</);
  assert.match(box.innerHTML, /data-action="nj-same-job-keep">Keep as a new job</);
  assert.equal(views.showSameJob({}), false);
  globalThis.document.querySelector = before;
});

// ---------- 18: the client playbook card ----------
test("a client's card has three sections and a full-width Save", () => {
  page.setMe(owner);
  const p = { id: "p-1", name: "Northline GC", role: "GC", client_type: "gc", approver: "Sam", ap_email: "ap@n.example", phone: "(404) 555-0100",
    follow_up_days: 5, usual_approval_days: 21, payment_terms_days: 30, packet_items: ["Conditional waiver"], copy: "pm@n.example", rejects_when: "Missing documents." };
  core.setState({ ...company(), payers: [p], paperwork_catalog: [{ key: "waiver", label: "Conditional waiver" }] });
  page.setLocation("playbook");
  const html = reg.views.playbook();
  const order = ["<h3>Contact and billing</h3>", "Supporting documents", "Follow-ups and copies"].map(t => html.indexOf(t));
  assert.ok(order.every(n => n > 0) && order[0] < order[1] && order[1] < order[2], String(order));
  const contact = html.slice(order[0], order[1]);
  for (const name of ["name", "client_type", "approver", "ap_email", "phone", "payapp_due_day", "payment_terms_days"]) assert.match(contact, new RegExp(`data-field="${name}"`), name);
  assert.match(html, /<details class="playbook-section" data-more="pb-docs-p-1">\s*<summary><h3>Supporting documents<\/h3><span class="sub">Conditional waiver<\/span><\/summary>/);
  assert.match(html, /<details class="playbook-section" data-more="pb-follow-p-1">\s*<summary><h3>Follow-ups and copies<\/h3><span class="sub">Every 5 days · CC pm@n\.example<\/span><\/summary>/);
  const follow = html.slice(order[2]);
  for (const name of ["follow_up_days", "usual_approval_days", "copy", "escalation_email"]) assert.match(follow, new RegExp(`data-field="${name}"`), name);
  assert.match(html, /<button type="button" class="btn full-width" data-action="save-playbook" data-id="p-1">Save client<\/button>/);
});
