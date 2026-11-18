// Georgia waivers, Nov 18 (Stephen: "Build as needed by your read of the law"): the filled-in final
// form offered with the retainage request when that is the last money (the server's
// retainage_final_holds), and the 90-day affidavit rows on the To Do. node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
globalThis.requestAnimationFrame = (f) => f();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");
const { filledFormLink, setWaiverForms } = await import("../../static/js/views/paperwork.js");
const { rowButtons, todoHtml } = await import("../../static/js/today.js");
const { todoItems } = await import("../../static/js/tasks.js");

const ga = { name: "Georgia", citation: "O.C.G.A. § 44-14-366", fillable: true, forms: {}, notes: [] };
const money = { contract_total_cents: 1000000, billed_cents: 1000000, collected_cents: 900000, outstanding_cents: 0, unbilled_completed_cents: 0,
  retainage_held_cents: 100000, retainage_released_cents: 0, retainage_open_cents: 100000, contract_cents: 1000000, approved_co_cents: 0, pending_co_cents: 0, draft_cents: 0 };
const job = (over = {}) => ({
  id: "j1", code: "PCH", name: "Peach clinic", address: "12 Peach St, Atlanta, GA 30303", payer: { id: "p1", name: "Jones GC", approver: "Mara", ap_email: "ap@jones.example" },
  payer_id: "p1", closed: false, work_status: "complete", money, records: [], change_orders: [], payments: [], waiver_state: "GA",
  final_waiver_holds: ["$1,000.00 of retainage is still held"], retainage_final_holds: [], invoices: [], retainage_waivers: [], packet: [],
  closeout_packet: [{ id: "c-fw", label: "Final lien waiver", required: true, present: false, blocker: "Final lien waiver is missing.", waiver_kind: "conditional_final", attachments: [] }],
  blockers: [], close_blockers: [], close_blocker_items: [],
  ...over,
});

test("the final form goes with the retainage request when that is the last money", () => {
  const link = filledFormLink(job(), "conditional_final", "retainage", "GA", ga);
  assert.match(link, /href="\/api\/jobs\/j1\/waiver-form\/final\.pdf\?for=retainage"/);
  assert.match(link, /Fill in the Georgia form/);
});

test("and in no other case while money is to come", () => {
  assert.equal(filledFormLink(job(), "conditional_final", "", "GA", ga), "", "not without the request");
  assert.equal(filledFormLink(job(), "conditional_final", "i1", "GA", ga), "", "not for a pay app");
  assert.equal(filledFormLink(job({ retainage_final_holds: ["$9,000.00 is still owed"] }), "conditional_final", "retainage", "GA", ga), "", "a pay app unpaid");
  assert.equal(filledFormLink(job({ retainage_final_holds: undefined }), "conditional_final", "retainage", "GA", ga), "", "an older server's page");
  assert.equal(filledFormLink(job(), "conditional_final", "retainage", "GA", { ...ga, fillable: false }), "", "off on the server");
  // The interim form for the request is as before.
  assert.match(filledFormLink(job(), "conditional_progress", "retainage", "GA", ga), /interim\.pdf\?for=retainage/);
});

test("the closeout documents' final waiver offers the form for the retainage request", () => {
  setWaiverForms({ states: { GA: ga }, other: { notes: [] } });
  const draw = (j) => {
    page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
    core.setState({ company: { name: "Pine" }, jobs: [j], payers: [j.payer], inbox: [], email_log: [], email_log_total: 0, files: [] });
    page.setLocation("packet-detail", j.id, "closeout");
    return reg.views.packetDetail();
  };
  assert.match(draw(job()), /waiver-form\/final\.pdf\?for=retainage/);
  assert.doesNotMatch(draw(job({ retainage_final_holds: ["CO 1 is not signed"] })), /waiver-form\/final\.pdf/);
});

// The server's rows (domain/gawaivers.py affidavit_rows, through domain.today_rows).
const row = (over = {}) => ({
  rank: 3.4, kind: "affidavit", job_id: "j1", job: "Peach clinic", payer: "Jones GC", label: "Affidavit of nonpayment", tone: "amber",
  text: "PA-01: the lien waiver sent Nov 18, 2026 takes effect Feb 16, 2027 unless you file an affidavit of nonpayment. Payment hasn't come in.",
  note: "File it in the property's county; copy to the owner within 7 days", button: "", path: "/jobs/j1", invoice_id: "i1", co_id: "",
  email_id: "", item_id: "", key: "affidavit:j1:i1", due: "", ball_in_court: "us", assignee_id: "", assignee_name: "", perm: "send_waivers", ...over,
});

test("an affidavit row: its words, the note, and Open (nothing in Duracash files it)", () => {
  const buttons = rowButtons(row());
  assert.doesNotMatch(buttons, /class="btn small"/, "no main button");
  assert.match(buttons, /data-action="open-job" data-id="j1"/);
  const html = todoHtml(todoItems({ today_rows: [row()] }, {}, "2027-01-17"), { total: 1, today: "2027-01-17" });
  assert.match(html, /<span class="pill amber">Affidavit of nonpayment<\/span>/);
  assert.match(html, /takes effect Feb 16, 2027 unless you file an affidavit of nonpayment\. Payment hasn't come in\./);
  assert.match(html, /\(File it in the property's county; copy to the owner within 7 days\)/);
  assert.doesNotMatch(html, /Overdue/, "due today, not late");
});

test("urgent from day 80, and the note once it is in effect", () => {
  const urgent = todoHtml(todoItems({ today_rows: [row({ tone: "red", rank: 0.8 })] }, {}, "2027-02-06"), { total: 1, today: "2027-02-06" });
  assert.match(urgent, /<span class="pill red">Affidavit of nonpayment<\/span>/);
  const note = row({ kind: "waiver_in_effect", label: "Waiver in effect", tone: "", rank: 6.8, note: "", key: "waiver_in_effect:j1:i1",
    text: "PA-01: the lien waiver sent Nov 18, 2026 is now in effect (Feb 16, 2027). Payment hasn't come in." });
  const html = todoHtml(todoItems({ today_rows: [note] }, {}, "2027-02-16"), { total: 1, today: "2027-02-16" });
  assert.match(html, /<span class="pill ">Waiver in effect<\/span>/);
  assert.match(html, /is now in effect \(Feb 16, 2027\)/);
  assert.match(rowButtons(note), /data-action="open-job"/);
});
