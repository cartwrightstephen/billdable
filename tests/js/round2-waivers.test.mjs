// Round 2, the Waivers stream (Stephen, 10 Nov, decisions 1, 5 and 6) on the page: a waiver row for
// each part payment, no final waiver to pick while money is to come, the reason Final pay app takes
// while an earlier pay app is unpaid, and the filled-in Georgia form only where the server has it on.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const core = await import("../../static/js/app-core.js");
const { waiverRows } = await import("../../static/js/views/closeout.js");
const { filledFormLink } = await import("../../static/js/views/paperwork.js");
const { waiverSendForm, unpaidPayApps, payAppNames, finalOverrideText } = await import("../../static/js/jobs.js");

const money = { contract_total_cents: 1000000, billed_cents: 400000, collected_cents: 150000, outstanding_cents: 250000, unbilled_completed_cents: 0,
  retainage_held_cents: 0, retainage_released_cents: 0, retainage_open_cents: 0, contract_cents: 1000000, approved_co_cents: 0, pending_co_cents: 0, draft_cents: 0 };
const job = (over = {}) => ({
  id: "j1", name: "Peach clinic", payer: { name: "Jones GC", approver: "Mara", ap_email: "ap@jones.example" }, payer_id: "p1",
  work_status: "in_progress", money, records: [], change_orders: [], waiver_state: "GA", final_waiver_holds: ["the job isn't finished"],
  invoices: [{ id: "i1", number: "PA-01", status: "partial", amount_cents: 400000, waiver_due: true, waiver_sent: false, waiver_amount_cents: 150000,
    waiver_stages: { conditional: {}, unconditional: { kind: "unconditional_progress", status: "Needed" } } }],
  retainage_waivers: [],
  ...over,
});

test("a part payment's waiver is its own row, for the money that came in", () => {
  const rows = waiverRows(job());
  assert.deepEqual(rows.map(r => [r.label, r.amount, r.due, r.kind]), [["PA-01, part payment", 150000, true, "unconditional_progress"]]);
});

test("a part payment's waiver sent stays as a row when the rest comes due", () => {
  const inv = { id: "i1", number: "PA-01", status: "paid", amount_cents: 400000, waiver_due: true, waiver_sent: false, waiver_amount_cents: 250000,
    partial_waivers: [{ cents: 150000, at: "2026-11-10T12:00:00+00:00", kind: "unconditional_progress", by: "Sam" }],
    waiver_stages: { conditional: {}, unconditional: { kind: "unconditional_progress", status: "Needed" } } };
  const rows = waiverRows(job({ invoices: [inv] }));
  assert.deepEqual(rows.map(r => [r.label, r.amount, r.due]), [["PA-01, part payment", 150000, false], ["PA-01", 250000, true]]);
  // Sent and still part paid: only its history row, not a second one.
  const sentPart = { ...inv, status: "partial", waiver_sent: true, waiver_sent_at: "2026-11-10T12:00:00+00:00", waiver_kind: "unconditional_progress" };
  assert.deepEqual(waiverRows(job({ invoices: [sentPart] })).map(r => r.label), ["PA-01, part payment"]);
});

test("the final waiver types can't be picked while money is to come (decision 6)", () => {
  const held = waiverSendForm(job(), job().invoices[0]);
  assert.match(held, /value="unconditional_final" [^>]*disabled/);
  assert.match(held, /value="conditional_final" [^>]*disabled/);
  assert.doesNotMatch(held, /value="unconditional_progress" [^>]*disabled/);
  const free = waiverSendForm(job({ final_waiver_holds: [] }), job().invoices[0]);
  assert.doesNotMatch(free, /disabled/);
});

test("Final pay app names the unpaid pay apps and shows the override (decision 6)", () => {
  const j = job({ invoices: [{ number: "PA-01", status: "sent" }, { number: "PA-02", status: "partial" }, { number: "PA-03", status: "paid" }, { number: "PA-04", status: "draft" }] });
  assert.deepEqual(unpaidPayApps(j).map(i => i.number), ["PA-01", "PA-02"]);
  assert.equal(payAppNames(unpaidPayApps(j)), "PA-01 and PA-02");
  assert.equal(finalOverrideText({ by: "Sam", at: "2026-11-17T12:00:00+00:00", reason: "Owner pays at closeout", unpaid: ["PA-01"] }),
    "Final pay app with PA-01 unpaid. Overridden by Sam on Nov 17, 2026: Owner pays at closeout");
  assert.equal(finalOverrideText(null), "");
});

test("the Final pay app form asks why while an earlier pay app is unpaid", async () => {
  globalThis.requestAnimationFrame = (f) => f();
  const page = await import("../../static/js/state.js");
  const reg = await import("../../static/js/views/index.js");
  const draw = (invoices) => {
    const j = { id: "j-1", code: "OAK", name: "Oak St", address: "1 Oak St", payer: { id: "p-1", name: "Northline GC", role: "GC", client_type: "gc" },
      payer_id: "p-1", closed: false, work_status: "in_progress", blockers: [], close_blockers: [], close_blocker_items: [], invoices,
      change_orders: [], payments: [], records: [], packet: [], waiver_due: false, waiver_sent: false, started_on: "2026-11-01", retainage_pct: 0,
      money: { ...money, outstanding_cents: 0, collected_cents: 0 } };
    page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
    core.setState({ company: { name: "Pine" }, jobs: [j], payers: [j.payer], inbox: [], email_log: [], email_log_total: 0, files: [] });
    page.setLocation("job", j.id);
    page.formOpen.done = true;
    return reg.views.job();
  };
  const unpaid = draw([{ id: "i1", number: "PA-01", status: "sent", amount_cents: 400000, retainage_cents: 0 }]);
  assert.match(unpaid, /<p class="blocker">PA-01 is not paid yet\.<\/p>/);
  assert.match(unpaid, /data-field="unpaid_reason"/);
  const paid = draw([{ id: "i1", number: "PA-01", status: "paid", amount_cents: 400000, retainage_cents: 0 }]);
  assert.doesNotMatch(paid, /unpaid_reason/);
  page.formOpen.done = false;
});

test("the filled-in state form shows only where the server has it on, for the job's own state", () => {
  const ga = { name: "Georgia", fillable: true };
  const link = filledFormLink(job(), "unconditional_progress", "i1", "GA", ga);
  assert.match(link, /href="\/api\/jobs\/j1\/waiver-form\/interim\.pdf\?for=i1"/);
  assert.match(link, /Fill in the Georgia form/);
  assert.equal(filledFormLink(job(), "unconditional_progress", "i1", "GA", { ...ga, fillable: false }), "", "off on the server");
  assert.equal(filledFormLink(job(), "unconditional_progress", "i1", "TX", ga), "", "a state picked by hand isn't the job's");
  assert.equal(filledFormLink(job(), "unconditional_final", "i1", "GA", ga), "", "no final form while money is to come");
  assert.match(filledFormLink(job({ final_waiver_holds: [] }), "unconditional_final", "i1", "GA", ga), /waiver-form\/final\.pdf/);
});

test("the Client playbook has the part payment waiver setting, Yes unless the client's is off (decision 5)", async () => {
  const page = await import("../../static/js/state.js");
  const reg = await import("../../static/js/views/index.js");
  const payer = (over) => ({ id: "p-1", name: "Northline GC", role: "GC", client_type: "gc", approver: "Dee", ap_email: "ap@n.example", phone: "(404) 555-0100",
    packet_items: [], playbook_missing: [], partial_waivers: true, ...over });
  const draw = (p, me) => {
    page.setMe(me);
    core.setState({ company: { name: "Pine" }, jobs: [], payers: [p], inbox: [], email_log: [], email_log_total: 0, files: [], paperwork_catalog: [] });
    page.setLocation("playbook");
    return reg.views.playbook();
  };
  const owner = { name: "Pat", company_id: "co-p", role: "owner" };
  assert.match(draw(payer({}), owner), /<select id="payer-p-1-partial_waivers"[^>]*><option value="yes" selected>Yes<\/option><option value="no">No<\/option><\/select>/);
  assert.match(draw(payer({ partial_waivers: false }), owner), /<option value="no" selected>No<\/option>/);
  const member = { name: "Fred", company_id: "co-p", role: "member", perms: ["record_payments"] };
  assert.match(draw(payer({ partial_waivers: false }), member), /Lien waiver on each part payment<\/span><span>No</);
});
