// Delivery status per recipient with Resend to this recipient, Revise with its earlier versions,
// and the predicted pay date on the pay app, the Dashboard's Money owed and the Client playbook
// (Siteline comparison, items 5 and 6). node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");
const { deliveryHtml, recipientRows } = await import("../../static/js/views/communications.js");
const { owedRows } = await import("../../static/js/views/today.js");
const { payStatsHtml } = await import("../../static/js/views/clients.js");

const sentEmail = (over = {}) => ({ id: "e1", job_id: "j1", kind: "pay_app", at: "2026-11-02T12:00:00Z", to: "ap@gc.example", cc: ["pm@gc.example"],
  status: "sent", delivered_at: "2026-11-02T12:00:05Z", payapp_invoice_id: "i1", subject: "Pay application PA-1",
  recipients: [{ email: "ap@gc.example", role: "to", status: "opened", sent_at: "2026-11-02T12:00:05Z", opened_at: "2026-11-03T09:00:00Z" },
    { email: "pm@gc.example", role: "cc", status: "bounced", sent_at: "2026-11-02T12:00:05Z", bounced_at: "2026-11-02T12:01:00Z", reason: "550 no such user" }], ...over });
const sent = (over = {}) => ({ id: "i1", number: "PA-GRT-01", status: "sent", amount_cents: 1_000_000, sent_at: "2026-11-02T12:00:00Z", lines: [], packet: [],
  predicted_pay: { on: "2026-12-10", basis: "history", days: 38, text: "Expected about Dec 10 · this client usually pays in 38 days", overdue_days: 0 }, ...over });
const job = (over = {}) => ({ id: "j1", name: "Grant Park addition", code: "GRT", payer_id: "p1",
  payer: { id: "p1", name: "Northline GC", approver: "Dev", ap_email: "ap@gc.example", sent_before: true },
  invoices: [sent()], payments: [], change_orders: [], records: [], blockers: [], money: {}, ...over });

function show(j, perms = null) {
  page.setMe({ name: "Pat", company_id: "co-p", role: perms ? "member" : "owner", email_verified: true, ...(perms ? { perms } : {}) });
  page.setAccount({ mailReady: true });
  core.setState({ company: { name: "Pine" }, jobs: [j], payers: [j.payer], files: [], inbox: [], email_log: [], email_log_total: 0 });
  page.setLocation("payapp-detail", j.id, j.invoices[0].id);
  return reg.views.payappDetail();
}

test("each address on an email says what became of it, with Resend to this recipient", () => {
  show(job());
  const html = deliveryHtml(sentEmail());
  assert.match(html, /ap@gc\.example<\/span>\s*<span class="pill green">Opened<\/span>/);
  assert.match(html, /pm@gc\.example <span class="sub">\(copy\)<\/span><\/span>\s*<span class="pill red">Bounced<\/span>.*550 no such user/s);
  assert.equal((html.match(/data-action="email-resend"/g) || []).length, 2);
  assert.match(html, /data-email="e1" data-to="pm@gc\.example">Resend to this recipient/);
});

test("an email from before per-address lines reads from its own status", () => {
  const rows = recipientRows(sentEmail({ recipients: undefined, status: "bounced: Mailbox full", refused: [] }));
  assert.deepEqual(rows.map(r => [r.email, r.status]), [["ap@gc.example", "bounced"], ["pm@gc.example", "sent"]]);
  const refused = recipientRows(sentEmail({ recipients: undefined, refused: ["pm@gc.example (550 no)"] }));
  assert.deepEqual(refused.map(r => r.status), ["sent", "failed"]);
});

test("Resend is for those who may send that kind, never on the demo or one still going out", () => {
  show(job(), ["record_payments"]);
  assert.doesNotMatch(deliveryHtml(sentEmail()), /email-resend/, "a member who can't send pay apps");
  assert.match(deliveryHtml(sentEmail({ kind: "follow_up" })), /email-resend/, "follow-ups are anyone's");
  show(job());
  assert.doesNotMatch(deliveryHtml(sentEmail({ status: "sending" })), /email-resend/);
  assert.doesNotMatch(deliveryHtml(sentEmail({ moot: "PA-GRT-01 is paid." })), /email-resend/);
  assert.doesNotMatch(deliveryHtml(sentEmail({ resend_moot: "PA-GRT-01 is paid." })), /email-resend/);
  page.setAccount({ mailReady: false });
  assert.doesNotMatch(deliveryHtml(sentEmail()), /email-resend/);
});

test("the pay app page shows its emails, when to expect the money, and Revise", () => {
  const html = show(job({ invoices: [sent({ emails: [sentEmail()] })] }));
  assert.match(html, /<h2>Emails<\/h2>/);
  assert.match(html, /<strong>Pay app<\/strong> · /);
  assert.match(html, /class="sub predicted-pay">Expected about Dec 10 · this client usually pays in 38 days<\/p>/);
  assert.match(html, /data-form="revise-i1"[^>]*>Revise this pay app…/);
  const late = show(job({ invoices: [sent({ predicted_pay: { ...sent().predicted_pay, overdue_days: 4 } })] }));
  assert.match(late, /4 days past expected/);
  assert.doesNotMatch(show(job(), ["record_payments"]), /Revise this pay app/, "Revise needs the pull-back permission");
});

test("a revised pay app says so, and its earlier version opens with its PDF and figures", () => {
  const rev = { n: 1, at: "2026-11-05T10:00:00Z", by: "Pat", reason: "Wrong quantity on line 3", sent_at: "2026-11-02T12:00:00Z", amount_cents: 1_000_000,
    file_id: "file-abc", lines: [{ label: "Work", amount_cents: 1_000_000 }],
    figures: { original: 9_000_000, net_co: 0, contract_to_date: 9_000_000, completed: 1_000_000, retainage: 0, earned: 1_000_000, previous: 0, due: 1_000_000, balance: 8_000_000 } };
  const html = show(job({ invoices: [sent({ status: "draft", sent_at: null, revised_at: "2026-11-05T10:00:00Z", revisions: [rev], predicted_pay: undefined })] }));
  assert.match(html, /Back to a draft for revision Nov 5, 2026/);
  assert.match(html, /<h2>Earlier versions<\/h2>/);
  assert.match(html, /Revision 1 · sent Nov 2, 2026 · \$10,000\.00 · back to draft Nov 5, 2026 by Pat/);
  assert.match(html, /href="\/api\/files\/file-abc"[^>]*>Open the PDF as sent/);
  assert.match(html, /Why: Wrong quantity on line 3/);
  assert.doesNotMatch(html, /Revise this pay app/, "a draft isn't revised; it is changed");
});

// Integration (Nov 27): a revised draft already in QuickBooks says its invoice there is updated later.
test("a revised draft already in QuickBooks says that invoice is updated when it goes again", () => {
  const draft = (over) => job({ invoices: [sent({ status: "draft", sent_at: null, revised_at: "2026-11-05T10:00:00Z", revisions: [], predicted_pay: undefined, ...over })] });
  assert.match(show(draft({ qbo: { invoice_id: "130", doc_number: "PA-GRT-01", total_cents: 1_000_000 } })),
    /QuickBooks invoice will be updated when you send it again: invoice PA-GRT-01 still shows \$10,000\.00/);
  assert.doesNotMatch(show(draft({})), /QuickBooks invoice will be updated/, "not in QuickBooks: nothing to say");
});

test("Money owed lists open pay apps soonest expected first, past-expected ones flagged", () => {
  const a = job({ id: "ja", name: "A job", invoices: [sent({ id: "ia", number: "PA-A-01", predicted_pay: { on: "2026-12-20", text: "Expected about Dec 20 · x", overdue_days: 0 } })] });
  const b = job({ id: "jb", name: "B job", invoices: [sent({ id: "ib", number: "PA-B-01", predicted_pay: { on: "2026-11-20", text: "Expected about Nov 20 · y", overdue_days: 7 } })] });
  assert.deepEqual(owedRows([a, b]).map(r => r.i.number), ["PA-B-01", "PA-A-01"]);
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState({ company: { name: "Pine" }, jobs: [a, b], payers: [a.payer], files: [], inbox: [], email_log: [], email_log_total: 0, tasks: [], today_rows: [],
    totals: { unbilled_cents: 0, outstanding_cents: 2_000_000, retainage_cents: 0 } });
  page.setLocation("dashboard");
  const html = reg.views.dashboard();
  assert.match(html, /<h2>Money owed<\/h2>/);
  assert.match(html, /1 past expected/);
  assert.ok(html.indexOf("PA-B-01") < html.indexOf("PA-A-01"));
  assert.match(html, /7 days past expected/);
});

test("the playbook shows days to paid and the on-time rate", () => {
  assert.equal(payStatsHtml({}), "");
  assert.match(payStatsHtml({ pay_stats: { paid: 3, median_days: 38, average_days: 36, on_time: 1, judged: 2, on_time_pct: 50 } }),
    /Days to paid: median 38 days, average 36 days \(3 paid\)\. On time 1 of 2 with a due date \(50%\)\. New pay apps to them are expected about 38 days after they go\./);
});

// Integration (Nov 27): Send again and Resend to this recipient show only to those who may send
// that kind (format.js maySendAgain, as the server's profiles.may_send_again).
test("Send again and Resend to this recipient hide from those who can't send that kind", async () => {
  const { emailStatusHtml, maySendAgain } = await import("../../static/js/format.js");
  const { rowButtons } = await import("../../static/js/today.js");
  const failed = sentEmail({ status: "failed: timeout", recipients: [] });
  show(job(), ["record_payments"]);
  assert.equal(maySendAgain(failed), false);
  assert.doesNotMatch(emailStatusHtml(failed), /email-retry/);
  assert.doesNotMatch(deliveryHtml(sentEmail()), /email-resend/);
  assert.doesNotMatch(rowButtons({ kind: "email_failed", email_id: "e1", email_kind: "pay_app", button: "Send again", job_id: "j1" }, core.perms()), /email-retry/);
  assert.equal(maySendAgain({ kind: "follow_up" }), true, "a follow-up is anyone's");
  show(job(), ["send_payapps"]);
  assert.match(emailStatusHtml(failed), /data-action="email-retry"/);
  assert.match(deliveryHtml(sentEmail()), /email-resend/);
  assert.match(rowButtons({ kind: "email_failed", email_id: "e1", email_kind: "pay_app", button: "Send again", job_id: "j1" }, core.perms()), /email-retry/);
  assert.equal(maySendAgain({ kind: "waiver" }), false);
});
