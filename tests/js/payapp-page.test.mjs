// The pay app page after round 1 of the Nov 5 audits: what is missing sits in Review & send above
// an Approve & send that stays on, the numbers fold under one line, a file is confirmed in one tap,
// the send asks first naming who, how much and the period, and an email that didn't go out says so
// on the page. node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom, stubEl } from "./fakedom.mjs";

const dom = installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");
const { emailStatusHtml } = await import("../../static/js/format.js");
const { rowButtons } = await import("../../static/js/today.js");
const { sendConfirmText } = await import("../../static/js/views/payapps.js");

const waiverItem = (over = {}) => ({ id: "pk-w", kind: "waiver", label: "Conditional waiver", required: true, present: false, blocker: "Conditional waiver is missing.",
  waiver_kind: "conditional_progress", accepts_kinds: ["waiver"], attachments: [], ...over });
const photosItem = (over = {}) => ({ id: "pk-p", kind: "photo", label: "Progress photos", required: true, present: true, blocker: "Progress photos are missing.",
  accepts_kinds: ["photo"], attachments: [{ record_id: "r1", name: "Porch", added_by: "Sam", added_at: "2026-11-05T12:00:00Z" }],
  confirmed_by: "Sam", confirmed_at: "2026-11-05T12:00:00Z", unread: "photo", check: { status: "unchecked", problems: ["p.jpg is a photo or a scan, so it couldn't be checked."] }, ...over });
const draft = (over = {}) => ({ id: "d1", number: "PA-PINE-01", status: "draft", amount_cents: 2_450_000, period_to: "2026-11-06", lines: [{ label: "Work", amount_cents: 2_450_000 }],
  packet: [waiverItem(), photosItem()], email_preview: { stamp: "abc123", subject: "S", body: "Hello" },
  figures: { original: 9_800_000, net_co: 0, unsigned_co: 0, contract_to_date: 9_800_000, completed: 2_450_000, retainage: 0, released: 0, earned: 2_450_000,
    previous: 0, due: 2_450_000, balance: 7_350_000, received_before: 0 }, ...over });
const job = (over = {}) => ({ id: "j1", name: "Grant Park addition", code: "GRT", payer_id: "p1", retainage_pct: 0, retainage_usual: 10,
  payer: { id: "p1", name: "Northline GC", client_type: "gc", approver: "Dev Patel", ap_email: "dev@northline.example", copy: "PE on every send", sent_before: true },
  invoices: [draft()], payments: [], change_orders: [], records: [{ id: "r1", kind: "photo", label: "Porch", file_ids: ["f1"] }], blockers: ["Conditional waiver is missing."],
  money: {}, ...over });

function show(j, inv = j.invoices[0], extra = {}) {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner", email_verified: true });
  core.setState({ company: { name: "Pine", address: "1 Main", phone: "(404) 555-0100", license: "L1" }, jobs: [j], payers: [j.payer], files: [{ id: "f1", name: "p.jpg" }],
    inbox: [], email_log: [], email_log_total: 0, ...extra });
  page.setLocation("payapp-detail", j.id, inv.id);
  return reg.views.payappDetail();
}

// askConfirm's dialog: its text is kept, and it is answered with its main button (or Cancel).
function answerDialogs(yes = true) {
  const said = [];
  const real = document.createElement;
  document.createElement = (tag) => {
    if (tag === "textarea") return real(tag);   // unesc's
    const handlers = {};
    const el = stubEl({ tag, addEventListener: (type, fn) => { handlers[type] = fn; }, close() {}, remove() {},
      showModal() { const b = made.filter(x => x.tag === "button"); (yes ? b.find(x => !x.className.includes("secondary")) : b.find(x => x.textContent === "Cancel"))?.click(); } });
    el.click = () => handlers.click && handlers.click();
    made.push(el);
    Object.defineProperty(el, "textContent", { set(v) { this._t = v; if (tag === "p") said.push(v); }, get() { return this._t; } });
    return el;
  };
  const made = [];
  return { said, restore() { document.createElement = real; } };
}

test("what is missing sits inside Review & send, above an Approve & send that stays on", () => {
  const html = show(job());
  const card = html.slice(html.indexOf('class="card send-check"'));
  const review = card.slice(0, card.indexOf('data-action="send"'));
  assert.match(review, /id="packet-item-pk-w"/, "the missing waiver is in the card, above the button");
  assert.ok(review.indexOf("packet-item-pk-w") < review.length);
  const send = html.match(/<button class="btn" data-action="send"[^>]*>/)[0];
  assert.match(send, /data-id="j1" data-reviewed="abc123"/, "the review stamp is on the button");
  assert.doesNotMatch(send, /disabled/);
  assert.equal((html.match(/id="packet-item-pk-w"/g) || []).length, 1, "each item once on the page");
  assert.equal((html.match(/id="packet-item-pk-p"/g) || []).length, 1);
  assert.ok(html.indexOf("packet-item-pk-p") > html.indexOf('data-action="send"'), "what is in waits below in Supporting documents");
});

test("the numbers fold behind one line, and This billing period is on the job page", () => {
  const j = job({ sov_view: { present: true, lines: [], period: { lines: [], draft: { id: "d1" } } } });
  const html = show(j);
  const fold = html.match(/<details class="card payapp-numbers"[^>]*>\s*<summary>([^<]*)<\/summary>/);
  assert.ok(fold, "one fold");
  assert.match(fold[1], /Show the numbers · \$24,500\.00 due · 1 line/);
  const body = html.slice(html.indexOf("payapp-numbers"));
  for (const part of ["Pay app summary", "<h2>Lines</h2>", "<h2>Approval</h2>"]) assert.ok(body.includes(part), part);
  assert.doesNotMatch(html, /This billing period/);
  assert.doesNotMatch(html, /data-action="sov-amounts"/);
  assert.match(html, /data-action="open-job" data-id="j1" data-focus="#sov-card">Change this period's amounts<\/button>/);
});

test("a GC job at 0% retainage says so on the pay app; a homeowner's doesn't", () => {
  assert.match(show(job()), /<p class="notice retainage-zero">Retainage is 0% on this job\. Northline GC usually holds 10%\./);
  assert.doesNotMatch(show(job({ retainage_usual: 0 })), /retainage-zero/);
  assert.doesNotMatch(show(job({ retainage_pct: 10 })), /retainage-zero/);
});

test("a photo the check can't read says Photo added by who, with no warning box", () => {
  const html = show(job());
  assert.match(html, /Photo added by Sam on Nov 5, 2026/);
  assert.doesNotMatch(html, /opened it and confirmed it after this warning/);
  assert.doesNotMatch(html, /<h3>Warnings<\/h3>/);
  const flagged = show(job({ invoices: [draft({ packet: [waiverItem({ attachments: [{ record_id: "r2", name: "w", unconfirmed: true }],
    check: { status: "flagged", problems: ["Amount not found: $24,500.00."] } })] })] }));
  assert.match(flagged, /Amount not found: \$24,500\.00\./);
  // On a waiver item the tap that answers which waiver it is is the look (the Nov 6 redesign).
  assert.match(flagged, /<strong>Which waiver is this\?<\/strong>/);
  assert.match(flagged, /The app couldn't tell which one this is\./, "no reading came with it: nothing is picked");
  assert.match(flagged, /data-action="packet-confirm" data-id="j1" data-item="pk-w" data-answer="conditional" data-looked="1">Conditional</);
  assert.match(flagged, /data-action="packet-confirm" data-id="j1" data-item="pk-w" data-answer="unconditional" data-looked="1">Unconditional</);
  assert.doesNotMatch(flagged, /aria-current="true">(Un)?[Cc]onditional/);
});

test("I opened it and it's right confirms in one tap, with no second dialog", async () => {
  const j = job({ invoices: [draft({ packet: [waiverItem({ attachments: [{ record_id: "r2", name: "w", unconfirmed: true }],
    check: { status: "flagged", problems: ["Amount not found: $24,500.00."] } })] })] });
  show(j);
  dom.requests.length = 0;
  dom.answers = [
    (p) => (p.includes("/packet-recheck/") ? { status: 200, body: { check_problems: ["Amount not found: $24,500.00."], check_status: "flagged" } } : undefined),
    (p) => (p.includes("/packet-confirm/") ? { status: 200, body: {} } : undefined),
    (p) => (p === "/api/state" ? { status: 200, body: { company: {}, jobs: [j], payers: [], inbox: [], email_log: [] } } : undefined),
  ];
  const d = answerDialogs(false);
  try { await reg.actions["packet-confirm"]({ dataset: { item: "pk-w", looked: "1", answer: "conditional" } }, "j1"); } finally { d.restore(); }
  assert.deepEqual(d.said, [], "no dialog");
  const sent = dom.requests.find(r => r.path === "/api/jobs/j1/packet-confirm/pk-w");
  assert.ok(sent, "confirmed");
  assert.equal(sent.body.looked, true);
  assert.equal(sent.body.answer, "conditional", "the tap says which waiver it is");
});

test("a suggestion the check flags now is shown first, not confirmed past", async () => {
  const j = job({ invoices: [draft({ packet: [waiverItem({ attachments: [{ record_id: "r2", name: "w", auto: true, unconfirmed: true }] })] })] });
  show(j);
  dom.requests.length = 0;
  dom.answers = [
    (p) => (p.includes("/packet-recheck/") ? { status: 200, body: { check_problems: ["Job name not found: Grant Park addition."], check_status: "flagged" } } : undefined),
    (p) => (p === "/api/state" ? { status: 200, body: { company: {}, jobs: [j], payers: [], inbox: [], email_log: [] } } : undefined),
  ];
  await reg.actions["packet-confirm"]({ dataset: { item: "pk-w" } }, "j1");
  assert.equal(dom.requests.filter(r => r.path.includes("/packet-confirm/")).length, 0);
});

test("a suggested photo the check can't read is confirmed with the one tap", async () => {
  const j = job({ invoices: [draft({ packet: [waiverItem({ attachments: [{ record_id: "r1", name: "p", auto: true, unconfirmed: true }] })] })] });
  show(j);
  dom.requests.length = 0;
  dom.answers = [
    (p) => (p.includes("/packet-recheck/") ? { status: 200, body: { check_problems: ["p.jpg is a photo or a scan, so it couldn't be checked."], check_status: "unchecked" } } : undefined),
    (p) => (p.includes("/packet-confirm/") ? { status: 200, body: {} } : undefined),
    (p) => (p === "/api/state" ? { status: 200, body: { company: {}, jobs: [j], payers: [], inbox: [], email_log: [] } } : undefined),
  ];
  await reg.actions["packet-confirm"]({ dataset: { item: "pk-w", answer: "unconditional" } }, "j1");
  const sent = dom.requests.find(r => r.path === "/api/jobs/j1/packet-confirm/pk-w");
  assert.equal(sent && sent.body.looked, true);
  assert.equal(sent.body.answer, "unconditional");
});

test("Approve & send asks first, naming who, how much and the period", async () => {
  const j = job({ blockers: [], invoices: [draft({ packet: [photosItem()] })] });
  const want = "Email PA-PINE-01 for $24,500.00, period to Nov 6, 2026, to Dev Patel · dev@northline.example? A copy goes to PE on every send.";
  assert.equal(sendConfirmText(j, j.invoices[0]), want);
  show(j);
  dom.requests.length = 0;
  dom.answers = [(p) => (p === "/api/jobs/j1/send" ? { status: 200, body: {} } : undefined)];
  let d = answerDialogs(false);
  try { await reg.actions.send({ dataset: { reviewed: "abc123" } }, "j1"); } finally { d.restore(); }
  assert.equal(d.said[0], want);
  assert.equal(dom.requests.filter(r => r.path === "/api/jobs/j1/send").length, 0, "Cancel sends nothing");
  d = answerDialogs(true);
  try { await reg.actions.send({ dataset: { reviewed: "abc123" } }, "j1"); } finally { d.restore(); }
  assert.deepEqual(dom.requests.find(r => r.path === "/api/jobs/j1/send").body, { reviewed: "abc123" }, "the review stamp still goes with it");
});

test("with something missing, Approve & send still asks first, then the server says why in place", async () => {
  // Round 1 hunt: the page may have been drawn before a fix made in another tab, so every send it
  // makes asks first; what can't go, the server refuses.
  const j = job();
  show(j);
  dom.requests.length = 0;
  dom.answers = [(p) => (p === "/api/jobs/j1/send" ? { status: 409, body: { error: "Pay app blocked.", blockers: ["Conditional waiver is missing."] } } : undefined)];
  const d = answerDialogs(true);
  try { await reg.actions.send({ dataset: { reviewed: "abc123", blocked: "1" } }, "j1"); } finally { d.restore(); }
  assert.equal(d.said.length, 1, "the confirm names who, how much and the period");
  assert.equal(dom.requests.filter(r => r.path === "/api/jobs/j1/send").length, 1);
});

test("a pay app marked sent whose email didn't go out says so under the pill", () => {
  const sent = draft({ status: "sent", sent_at: "2026-11-06T12:00:00Z", approved_by: "Pat" });
  const failure = { id: "e1", payapp_invoice_id: "d1", to: "dev@northline.example", status: "not sent: email delivery is not set up", job_id: "j1" };
  page.setAccount({ mailReady: false });
  let html = show(job({ invoices: [sent] }), sent, { email_failures: [failure] });
  assert.match(html, /<p class="notice email-undelivered" role="status">Marked sent · the email to dev@northline\.example was not delivered: email delivery is not set up\.<\/p>/);
  assert.doesNotMatch(html, /email-retry/);
  page.setAccount({ mailReady: true });
  html = show(job({ invoices: [sent] }), sent, { email_failures: [{ ...failure, status: "failed: 550 mailbox full" }] });
  assert.match(html, /was not delivered: 550 mailbox full\. <button class="btn small secondary" data-action="email-retry" data-email="e1">Send again<\/button>/);
  assert.doesNotMatch(show(job({ invoices: [sent] }), sent, { email_failures: [] }), /email-undelivered/);
});

test("Send again is hidden where the server has no mail set up", () => {
  page.setAccount({ mailReady: false });
  assert.doesNotMatch(emailStatusHtml({ id: "e1", status: "not sent: email delivery is not set up" }), /email-retry/);
  assert.doesNotMatch(rowButtons({ kind: "email_failed", button: "Send again", email_id: "e1", job_id: "j1" }), /email-retry/);
  page.setAccount({ mailReady: true });
  assert.match(emailStatusHtml({ id: "e1", status: "failed: 550" }), /data-action="email-retry"/);
  assert.match(rowButtons({ kind: "email_failed", button: "Send again", email_id: "e1", job_id: "j1" }), /data-action="email-retry"/);
});

test("an Activity entry carries the app's bookkeeping that came with it, folded under one line", async () => {
  const { activityAlsoHtml } = await import("../../static/js/activity.js");
  assert.equal(activityAlsoHtml({ kind: "action", action: "match_payment" }), "");
  const html = activityAlsoHtml({ kind: "action", action: "add_record", details: [
    { action: "attach_record", text: "Sam attached Porch to Photos on PA-01.", at: "2026-11-05T10:00:00Z", who: "Sam" },
    { action: "packet_confirmed", text: "Sam added Porch for Photos on PA-01.", at: "2026-11-05T10:00:01Z", who: "Sam" }] });
  assert.match(html, /<details class="activity-also"><summary>Also: Supporting documents added, Document checked<\/summary>/);
  assert.match(html, /<li>Sam attached Porch to Photos on PA-01\.<\/li>/);
});

test("the Activity log can leave out the app's own entries", async () => {
  const settings = await import("../../static/js/views/settings.js");
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner", email_verified: true });
  core.setState({ company: { name: "Pine" }, jobs: [], payers: [], inbox: [], email_log: [], email_log_total: 0 });
  dom.requests.length = 0;
  dom.answers = [(p) => (p.startsWith("/api/audit?") ? { status: 200, body: { entries: [], total: 0 } } : undefined)];
  reg.views.audit();
  assert.ok(dom.requests.some(r => r.path === "/api/audit?offset=0&limit=50"), dom.requests.map(r => r.path).join(" "));
  await reg.actions["audit-who"]({ dataset: { who: "people" } });
  assert.match(reg.views.audit(), /data-action="audit-who" data-who="people" aria-pressed="true"/);
  assert.ok(dom.requests.some(r => r.path === "/api/audit?offset=0&limit=50&who=people"), dom.requests.map(r => r.path).join(" "));
  await reg.actions["audit-who"]({ dataset: { who: "" } });
  void settings;
});

test("Delete draft says a change order billed on it comes off the bill", async () => {
  const { deleteDraftText } = await import("../../static/js/views/payapps.js");
  const plain = job({ invoices: [draft()] });
  assert.equal(deleteDraftText(plain), "Delete the open draft pay app PA-PINE-01? You can draft it again from the job page.");
  const withCo = job({ change_orders: [{ id: "co1", number: 3, status: "pending", included_in_billing: true }, { id: "co2", number: 4, status: "approved" }],
    invoices: [draft({ lines: [{ label: "Work", amount_cents: 100 }, { label: "CO 3 (unsigned)", amount_cents: 50, co_id: "co1" }] })] });
  assert.equal(deleteDraftText(withCo), "Delete the open draft pay app PA-PINE-01? CO 3 comes off the bill. You can draft it again from the job page.");
  const two = job({ change_orders: [{ id: "co1", number: 3, included_in_billing: true }, { id: "co2", number: 4, included_in_billing: true }],
    invoices: [draft({ lines: [{ co_id: "co1" }, { co_id: "co2" }] })] });
  assert.match(deleteDraftText(two), /CO 3 and CO 4 come off the bill\./);
  const actions = [];
  show(withCo);
  dom.requests.length = 0;
  dom.answers = [];
  const d = answerDialogs(false);
  try { await reg.actions["void-draft"]({ dataset: {} }, "j1"); } finally { d.restore(); }
  assert.match(d.said[0], /CO 3 comes off the bill/);
  void actions;
});
