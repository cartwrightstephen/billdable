// Paperwork, lien waivers and retainage on the page (Stephen's Oct 29 review): the job's Retainage
// card, Closeout's one row per pay app and per release, the waiver page's checks, and Before you
// send's list of files. node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");
const { retainageCardHtml } = await import("../../static/js/views/job-money.js");
const { waiverRows } = await import("../../static/js/views/closeout.js");
const { beforeYouSendHtml, recordWaiverKind, setWaiverForms } = await import("../../static/js/views/paperwork.js");
const { retainageFacts, daysAgoText, waiverStageText } = await import("../../static/js/jobs.js");

const money = { contract_total_cents: 1000000, billed_cents: 900000, collected_cents: 900000, outstanding_cents: 0, unbilled_completed_cents: 0,
  retainage_held_cents: 100000, retainage_released_cents: 40000, retainage_open_cents: 60000, contract_cents: 1000000,
  approved_co_cents: 0, pending_co_cents: 0, short_paid_cents: 0 };
const job = (over = {}) => ({
  id: "j1", name: "Pine porch", address: "5 Pine St, Atlanta, GA 30307", payer: { name: "Lee GC", approver: "Dee", ap_email: "ap@lee.example", follow_up_days: 7 },
  payer_id: "p1", retainage_pct: 10, work_status: "complete", completed_at: "2026-10-01T12:00:00+00:00", money, records: [], change_orders: [],
  invoices: [
    { id: "i1", number: "PA-1", status: "paid", amount_cents: 450000, retainage_cents: 50000, waiver_due: true, waiver_sent: true, waiver_kind: "unconditional_progress", waiver_sent_at: "2026-10-05T12:00:00+00:00" },
    { id: "i2", number: "PA-2", status: "paid", amount_cents: 450000, retainage_cents: 50000, waiver_due: true, waiver_sent: false,
      waiver_stages: { conditional: { kind: "conditional_final", status: "Sent" }, unconditional: { kind: "unconditional_progress", status: "Needed" } } },
    { id: "i3", number: "PA-3", status: "void", amount_cents: 1, retainage_cents: 5 },
  ],
  retainage_chases: 2, last_retainage_chase_at: "2026-10-20T12:00:00+00:00",
  retainage_payments: [{ amount_cents: 40000, reference: "CHK-9", at: "2026-10-22T12:00:00+00:00", recorded_by: "Sam" }],
  retainage_waivers: [{ id: "rw-1", amount_cents: 40000, at: "2026-10-22T12:00:00+00:00", kind: "unconditional_progress", waiver_due: true, waiver_sent: false, on_file: false }],
  closeout_packet: [], closeout_missing: [],
  ...over,
});

test("the Retainage card has the rate, the money, the asks and the releases", () => {
  core.setState({ company: { name: "Pine" }, jobs: [job()], payers: [], files: [] });
  const now = new Date("2026-10-30T12:00:00Z");
  const f = retainageFacts(job(), now);
  assert.deepEqual([f.pct, f.asked, f.doneDays, f.askedDays, f.payApps.map(i => i.number)], [10, 2, 29, 10, ["PA-1", "PA-2"]]);
  const html = retainageCardHtml(job(), now);
  for (const want of ["Retainage held", "$1,000.00", "Retainage released", "$400.00", "Still held", "$600.00", "29 days ago", "Times asked",
    "Next ask allowed", "PA-1</a> · $500.00", "CHK-9", "Sam", "waiver due"]) assert.ok(html.includes(want), want);
  assert.equal(retainageCardHtml(job({ retainage_pct: 0, money: { ...money, retainage_held_cents: 0 } })), "");
  assert.equal(daysAgoText(1), "1 day ago");
});

test("Closeout has a row for each pay app and each retainage release", () => {
  const rows = waiverRows(job());
  assert.deepEqual(rows.map(r => [r.label, r.due, r.kind]), [["PA-1", false, "unconditional_progress"], ["PA-2", true, "unconditional_progress"],
    ["Retainage released Oct 22, 2026", true, "unconditional_progress"]]);
  core.setState({ company: { name: "Pine" }, jobs: [job()], payers: [], files: [] });
  const html = reg.views.waivers();
  assert.match(html, /<th>Pay app<\/th>/);
  assert.match(html, /<th>Times asked<\/th>/);
  assert.equal((html.match(/Choose type and send/g) || []).length, 2);
});

test("the waiver page asks for the signed form before it sends, and says what the form must show", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  setWaiverForms({ states: { GA: { name: "Georgia", citation: "O.C.G.A. § 44-14-366", forms: { unconditional_progress: "Interim Waiver and Release Upon Payment" }, notes: [] } }, other: { notes: ["No state form."] } });
  core.setState({ company: { name: "Pine", legal_name: "Pine Builders LLC" }, jobs: [{ ...job(), waiver_state: "GA" }], payers: [], files: [] });
  const view = reg.views["waiver-detail"];
  page.setLocation("waiver-detail", "j1");
  const html = view();
  assert.match(html, /Add the signed unconditional waiver to the job record, then send\./);
  assert.match(html, /data-waiver-kind="unconditional_progress"/);
  assert.match(html, /data-action="waiver" data-id="j1" data-inv="i2" disabled>/);
  for (const want of ["Pine Builders LLC", "Amount covered: $4,500.00", "Not released: the $500.00 retainage held on PA-2", "O.C.G.A. § 44-14-366",
    "Interim Waiver and Release Upon Payment", "Conditional, final payment: Sent"]) assert.ok(html.includes(want), want);
  assert.equal(waiverStageText({ kind: "", status: "After payment" }), "After payment");
});

test("Before you send lists each file with Leave out or Put back, and warns of an unconditional waiver", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  const records = [{ id: "r1", kind: "photo", label: "Porch", file_ids: ["f1", "f2"] }, { id: "r2", kind: "waiver", label: "Unconditional waiver", file_ids: ["f3"] },
    { id: "r3", kind: "supplier_invoice", label: "Lumber", file_ids: ["f4"] }];
  const files = [["f1", "a.jpg"], ["f2", "b.jpg"], ["f3", "w.pdf"], ["f4", "l.pdf"]].map(([id, name]) => ({ id, name }));
  const inv = { id: "d1", number: "PA-3", status: "awaiting_approval", excluded_file_ids: ["f2"], extra_record_ids: [],
    packet: [{ label: "Photos", attachments: [{ record_id: "r1" }] }, { label: "Conditional waiver", waiver_kind: "conditional_progress", attachments: [{ record_id: "r2" }] }] };
  const j = job({ records, invoices: [inv] });
  core.setState({ company: { name: "Pine" }, jobs: [j], payers: [], files });
  const html = beforeYouSendHtml(j, inv);
  assert.match(html, /a\.jpg<\/a>[\s\S]*data-op="exclude_file" data-target="f1">Leave out/);
  assert.match(html, /b\.jpg<\/a>[\s\S]*left out[\s\S]*data-op="include_file" data-target="f2">Put back/);
  assert.match(html, /<option value="r3">Lumber<\/option>/);
  assert.match(html, /An unconditional waiver is attached: Unconditional waiver/);
  assert.match(html, /data-action="send-override"/);
  assert.match(html, /What Lee GC needs to pay/);
  assert.equal(recordWaiverKind({ label: "Unconditional final waiver" }), "unconditional_final");
});
