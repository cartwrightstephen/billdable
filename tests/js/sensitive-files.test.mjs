// Security review, Nov 26 (H2, L1): payroll, time sheets, lien waivers and contracts are shown
// without a link to someone without "See payroll and sensitive documents"; the job file zip too.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
globalThis.requestAnimationFrame = (f) => f();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const record = await import("../../static/js/views/job-record.js");
const comms = await import("../../static/js/views/communications.js");

const owner = { name: "Pat", company_id: "co-p", role: "owner" };
const field = { name: "Bob", company_id: "co-p", role: "member", perms: ["record_payments"] };
const office = { ...field, perms: ["record_payments", "sensitive_docs"] };
const files = [{ id: "f-pay", name: "wh-347.pdf", sensitive: true }, { id: "f-photo", name: "wall.jpg" }];
function as(me) {
  page.setMe(me);
  core.setState({ company: { name: "Pine" }, jobs: [], payers: [], inbox: [], email_log: [], files });
}

test("a sensitive file is a name without a link to someone without the permission", () => {
  as(field);
  assert.equal(core.mayOpenFile("f-pay"), false);
  assert.equal(core.mayOpenFile("f-photo"), true);
  const html = core.fileLink("f-pay", "wh-347.pdf");
  assert.doesNotMatch(html, /href=/);
  assert.match(html, /wh-347\.pdf <span class="sub">\(private\)<\/span>/);
  assert.match(core.fileLink("f-photo", "wall.jpg"), /<a href="\/api\/files\/f-photo" target="_blank" rel="noopener">wall\.jpg<\/a>/);
  assert.doesNotMatch(record.recordFiles({ file_ids: ["f-pay", "f-photo"] }), /api\/files\/f-pay/);
  assert.doesNotMatch(comms.attachmentsHtml({ attachments: ["f-pay"] }), /href=/);
});

test("an owner, or a profile given it, opens them", () => {
  for (const me of [owner, office]) {
    as(me);
    assert.equal(core.mayOpenFile("f-pay"), true);
    assert.match(core.fileLink("f-pay", "wh-347.pdf", "file-link"), /<a class="file-link" href="\/api\/files\/f-pay"/);
    assert.match(record.recordFiles({ file_ids: ["f-pay"] }), /href="\/api\/files\/f-pay"/);
  }
});
