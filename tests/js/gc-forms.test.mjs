// The client's own forms: the client card's section and the pay app page's card (views/gcforms.js).
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const gc = await import("../../static/js/views/gcforms.js");
core.setPage({ render() {} });

const view = {
  payer_id: "payer-1", send: "both", admin: false,
  send_modes: { ours: "Duracash's pay app PDF only", theirs: "The client's own form only", both: "Both: the client's form and Duracash's PDF" },
  form_types: { payapp: "Pay app (cover page)", continuation: "Continuation sheet" },
  money_formats: { dollars: "$1,234.56", plain: "1234.56" }, date_formats: { us: "11/27/2026" },
  sources: [{ key: "current_due", label: "8. Current payment due", kind: "money", group: "Summary" },
    { key: "line.description", label: "Line: description of work", kind: "text", group: "Continuation line (pick the row)" },
    { key: "text", label: "Fixed text (type it)", kind: "text", group: "Other" }],
  templates: [{ id: "gcf-0123456789", form_type: "payapp", label: "Pay app (cover page)", version: 2, file_name: "park.pdf", added_by: "Pat",
    fillable: true, pages: [{ width: 612, height: 792 }], notes: [], money_format: "dollars", date_format: "us",
    fields: [{ name: "Due<img src=x>", kind: "text", page: 1 }, { name: "Row1Desc", kind: "text", page: 1 }, { name: "Sig", kind: "signature", page: 1 }],
    mapping: [{ field: "Due<img src=x>", source: "current_due" }, { field: "Row1Desc", source: "line.description", row: 1, guessed: true }], placed: [] }],
  history: [],
};
dom.answers.push((path) => (path === "/api/payers/payer-1/client-forms" ? { status: 200, body: view } : undefined));
dom.answers.push((path) => (path.endsWith("/client-forms") && path.startsWith("/api/jobs/") ? { status: 200, body: {
  send: "theirs", send_label: "The client's own form only", goes: true,
  forms: [{ form_type: "payapp", label: "Pay app (cover page)", version: 2, url: "/api/jobs/j1/payapp/i1/client-form/payapp.pdf", problems: ["AppNo (Contract number) has no value."] }],
  waivers: [{ form_type: "conditional_progress", label: "Conditional waiver on progress payment", version: 1, url: "/api/jobs/j1/payapp/i1/client-form/conditional_progress.pdf" }] } } : undefined));

const tick = () => new Promise(r => setTimeout(r, 0));
page.setMe({ name: "Pat", company_id: "co-p", role: "owner", perms: ["company"] });
core.setState({ company: { name: "Pine" }, jobs: [], payers: [{ id: "payer-1", name: "Park GC" }], inbox: [], email_log: [], email_log_total: 0 });

test("the card loads the client's forms and escapes what a PDF names its fields", async () => {
  const p = { id: "payer-1", name: "Park GC" };
  assert.match(gc.clientFormsHtml(p, true, true), /Loading/);
  await tick();
  const html = gc.clientFormsHtml(p, true, true);
  assert.match(html, /Pay app \(cover page\)<\/strong> <span class="sub">version 2 · fillable, 3 fields, 2 matched/);
  assert.match(html, /data-action="cf-upload" data-id="payer-1"/);
  assert.match(html, /<option value="both" selected>/);
  assert.match(html, /client-forms\/gcf-0123456789\/sample\.pdf\?grid=1/);
  gc.actions["cf-edit"]({ dataset: { tpl: "gcf-0123456789" } });
  const open = gc.clientFormsHtml(p, true, true);
  assert.ok(!open.includes("<img src=x>"), "a field's name is text, never markup");
  assert.match(open, /Due&lt;img src=x&gt;/);
  assert.match(open, /Left for the person signing/);
  assert.match(open, /Guessed from its name/);
  assert.match(open, /data-cf-key="row" value="1"/);
  // Without the permission: the forms read, nothing to change.
  const read = gc.clientFormsHtml(p, true, false);
  assert.doesNotMatch(read, /data-action="cf-(upload|edit|retire|send)"/);
});

test("Save matches sends each field's value, row and text", async () => {
  dom.requests.length = 0;
  dom.answers.unshift((path, init) => (init.method === "POST" && path.endsWith("/match") ? { status: 200, body: { jobs: [], payers: [], client_forms: view } } : undefined));
  await gc.actions["cf-save"]({ dataset: { tpl: "gcf-0123456789" } }, "payer-1");
  const sent = dom.requests.find(r => r.method === "POST");
  assert.equal(sent.path, "/api/payers/payer-1/client-forms/gcf-0123456789/match");
  assert.deepEqual(sent.body.mapping, [{ field: "Due<img src=x>", source: "current_due" }, { field: "Row1Desc", source: "line.description", row: 1 }]);
  assert.deepEqual(sent.body.placed, []);
  assert.equal(sent.body.money_format, "dollars");
});

test("placed values go in points from the top left", () => {
  const body = gc.matchBody({ mapping: {}, placed: [{ source: "current_due", page: "1", x: 144, y: 36, size: "9", align: "right" }], money_format: "plain", date_format: "us" });
  assert.deepEqual(body.placed[0], { source: "current_due", row: undefined, text: undefined, optional: false, page: 1, x: 144, y: 36, size: 9, align: "right" });
});

test("the pay app page shows the preview link, what stops it and the waivers to print", async () => {
  const j = { id: "j1", payer: { name: "Park GC" } }, inv = { id: "i1", status: "draft" };
  assert.equal(gc.payappFormsHtml(j, inv), "");
  await tick();
  const html = gc.payappFormsHtml(j, inv);
  assert.match(html, /On Park GC's own forms/);
  assert.match(html, /carries the client&#39;s own form only/);
  assert.match(html, /href="\/api\/jobs\/j1\/payapp\/i1\/client-form\/payapp\.pdf"[^>]*>Preview it filled in/);
  assert.match(html, /AppNo \(Contract number\) has no value/);
  assert.match(html, /Conditional waiver on progress payment/);
});
