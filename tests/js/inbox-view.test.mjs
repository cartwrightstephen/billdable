// The Inbox's "Add all to their suggested jobs": each card's job and kind as picked before the
// tap, though every save redraws the cards. node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const inbox = await import("../../static/js/views/inbox.js");

const items = [
  { id: "i1", status: "new", suggested_job_id: "jY", kind: "invoice" },
  { id: "i2", status: "new", suggested_job_id: "jY", kind: "invoice" },
  { id: "i3", status: "new", suggested_job_id: "jY", kind: "invoice" },
];
const company = () => ({ company: { name: "Pine" }, jobs: [], payers: [], inbox: items, email_log: [], email_log_total: 0 });

test("each card is filed as picked on it, and a card set to Pick a job is skipped", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company());
  // The open cards: item 2 moved to job X as a lien waiver; item 3 set back to "Pick a job…".
  const sel = { "#inb-job-i2": { value: "jX" }, "#inb-kind-i2": { value: "waiver" }, "#inb-job-i3": { value: "" }, "#inb-kind-i3": { value: "invoice" } };
  Object.assign(dom.bySelector, sel);
  dom.requests.length = 0;
  dom.answers = [(path) => {
    if (!path.startsWith("/api/inbox/")) return undefined;
    // Each save redraws the Inbox from the saved state: the open cards show the suggestion again.
    sel["#inb-job-i2"].value = "jY"; sel["#inb-kind-i2"].value = "invoice"; sel["#inb-job-i3"].value = "jY";
    return { status: 200, body: company() };
  }];
  await inbox.actions["inbox-file-all"]();
  assert.deepEqual(dom.requests.map(r => [r.path, r.body]), [
    ["/api/inbox/i1/file", { job_id: "jY", kind: "invoice" }],
    ["/api/inbox/i2/file", { job_id: "jX", kind: "waiver" }],
  ]);
});

// Everything received (C1), the Outbox's attachments and Print (M27), and Import's file box (I1).
const comms = await import("../../static/js/views/communications.js");
const data = await import("../../static/js/views/data.js");
const { stubEl } = await import("./fakedom.mjs");
const tick = () => new Promise((r) => setTimeout(r, 0));

test("each row of Everything received says what became of it", () => {
  assert.equal(inbox.receivedStatus({ status: "new" }), "Waiting");
  assert.equal(inbox.receivedStatus({ status: "new", job_name: "Oak" }), "Waiting (suggested: Oak)");
  assert.equal(inbox.receivedStatus({ status: "filed", job_name: "Oak", kind: "waiver" }), "Filed to Oak as Lien waiver");
  assert.equal(inbox.receivedStatus({ status: "auto", job_name: "Oak" }), "Filed automatically to Oak");
  assert.equal(inbox.receivedStatus({ status: "dismissed" }), "Dismissed");
  assert.equal(inbox.receivedFilesHtml({ files: [{ id: "f1", name: "co2.pdf" }], files_gone: 1 }),
    `<div class="sub">Files: <a href="/api/files/f1" target="_blank" rel="noopener">co2.pdf</a> · 1 file no longer on the job record</div>`);
  assert.equal(inbox.receivedFilesHtml({ files: [], files_gone: 0 }), "");
});

test("the filters and sort go to the server; the default sort is left out", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  page.setLocation("inbox");
  core.setState(company());
  assert.equal(inbox.receivedQuery(), "");
  inbox.setReceivedFilter("source", "sms");
  inbox.setReceivedFilter("sort", "from");
  inbox.setReceivedFilter("bogus", "x");
  assert.equal(inbox.receivedQuery(), "source=sms&sort=from");
  dom.requests.length = 0;
  dom.answers = [(p) => (p.startsWith("/api/inbox?") ? { status: 200, body: { entries: [
    { id: "r1", source: "sms", from: "+15555550123", title: "Gate code", status: "dismissed", why: "No job name, code, address or client found.", files: [], files_gone: 0 },
  ], total: 1 } } : undefined)];
  inbox.views.inbox();
  await tick();
  assert.deepEqual(dom.requests.map(r => r.path), ["/api/inbox?offset=0&limit=50&source=sms&sort=from"]);
  const html = inbox.views.inbox();
  assert.match(html, /Everything received[\s\S]*<option value="sms" selected>Text message<\/option>/);
  assert.match(html, /<td>Dismissed<div class="sub">No job name, code, address or client found\.<\/div>[\s\S]*data-action="inbox-undo" data-item="r1"/);
  assert.doesNotMatch(html, /id="upload-job"|id="paste-text"/, "the file and paste boxes are on Import");
  assert.match(html, /class="dropzone touch-only"[\s\S]*data-capture="1">Take a photo</, "a phone keeps the camera");
  inbox.setReceivedFilter("source", ""); inbox.setReceivedFilter("sort", "");
  assert.equal(inbox.receivedQuery(), "");
});

test("an Outbox entry links its files, says when one is gone, and links the pay app PDF", () => {
  const e = { job_id: "j1", payapp_invoice_id: "inv-1", attachments: ["f1", "f-gone"], not_attached: [] };
  assert.equal(comms.attachmentsHtml(e, [{ id: "f1", name: "COI.pdf" }]),
    `<div class="sub email-files">Attached: <a href="/api/jobs/j1/payapp/inv-1.pdf" target="_blank" rel="noopener">Pay app PDF</a> · <a href="/api/files/f1" target="_blank" rel="noopener">COI.pdf</a> · A file no longer on the job record</div>`);
  assert.equal(comms.attachmentsHtml({ attachments: [] }, []), "");
});

test("Print puts one message on a sheet and marks the page printing", async () => {
  const classes = new Set();
  const sheet = { innerHTML: "" };
  const printed = [];
  document.body = stubEl({ classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c), toggle() {} } });
  dom.byId["print-sheet"] = sheet;
  window.print = () => printed.push(1);
  core.setState({ ...company(), files: [{ id: "f1", name: "COI.pdf" }] });
  dom.answers = [(p) => (p.startsWith("/api/outbox?") ? { status: 200, body: { entries: [
    { id: "e1", job_id: "j1", to: "ap@park.example", cc: ["pm@park.example"], at: "2026-10-12T09:00:00+00:00", subject: "PA-OAK-1", body: "Attached.", status: "sent", payapp_invoice_id: "inv-1", attachments: ["f1"] },
  ], total: 1 } } : undefined)];
  core.listPage("outbox", "all");
  await tick();
  comms.actions["print-email"]({ dataset: { email: "e1" } });
  assert.ok(classes.has("printing"));
  assert.equal(printed.length, 1);
  assert.match(sheet.innerHTML, /<h1>PA-OAK-1<\/h1>[\s\S]*<dt>To<\/dt><dd>ap@park\.example<\/dd><dt>Cc<\/dt><dd>pm@park\.example<\/dd>/);
  assert.match(sheet.innerHTML, /<div class="print-body">Attached\.<\/div>[\s\S]*<li>Pay app PDF<\/li><li>COI\.pdf<\/li>/);
  delete dom.byId["print-sheet"];
  delete window.print;
});

test("Import has the file box, the paste box, the payments import and the schedule picker; Export hides Everything from a member", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState({ ...company(), jobs: [{ id: "j1", name: "Oak", code: "OAK", closed: false, invoices: [], payments: [] }] });
  dom.answers = [];
  const imp = data.views.import();
  for (const part of ['class="dropzone"', 'id="paste-text"', 'id="qbo-csv"', 'id="sov-import-job"', 'data-action="import-sov"'])
    assert.ok(imp.includes(part), part);
  page.setLocation("export");
  assert.match(data.views.export(), /href="\/api\/export" download>Download everything</);
  page.setMe({ name: "Sam", company_id: "co-p", role: "member" });
  const exp = data.views.export();
  assert.doesNotMatch(exp, /\/api\/export"/);
  assert.match(exp, /An owner can download everything/);
  const row = data.sentPayAppRow({ job_id: "j1", job: "Oak", invoice_id: "inv-1", number: "PA-OAK-01", status: "paid", amount_cents: 100000, sent_at: "2026-10-01T00:00:00+00:00" });
  assert.match(row, /href="\/api\/jobs\/j1\/payapp\/inv-1\.pdf"[\s\S]*href="\/api\/jobs\/j1\/payapp\/inv-1\.csv" download>Download for QuickBooks/);
});
