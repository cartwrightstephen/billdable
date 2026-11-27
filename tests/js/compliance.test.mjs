// Jobs, Compliance: documents, what clients need, lien deadlines with the not-legal-advice note,
// and the soft gate on the pay app page (static/js/views/compliance.js). node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");
const { routeFromPath, pathFor } = await import("../../static/js/routes.js");
const { loadCompliance, complianceSendWarningHtml } = await import("../../static/js/views/compliance.js");

const reply = {
  types: [{ key: "coi", label: "Certificate of insurance (COI)", sensitive: false }, { key: "w9", label: "W-9", sensitive: true }],
  tiers: [["owner", "Hired by the owner"], ["contractor", "Hired by the general contractor"], ["lower", "Lower tier"]],
  remind_days: [30, 14, 3], today: "2026-11-27",
  docs: [{ id: "cdoc-00000001", type: "coi", type_label: "Certificate of insurance (COI)", status: "expired", expires_on: "2026-11-01", file_id: "file-1", payer: "Northline", additional_insured: true, days_left: -26 },
         { id: "cdoc-00000002", type: "w9", type_label: "W-9", status: "no_expiry", file_id: "", sensitive: true, days_left: null }],
  requirements: [{ id: "creq-00000001", type: "coi", type_label: "Certificate of insurance (COI)", payer: "Northline", status: "expired", additional_insured: true }],
  jobs: [{ job_id: "ja", job: "Pine kitchen", state: "GA", state_name: "Georgia", supported: true, statute: "O.C.G.A. § 44-14-360 et seq.", counting: "O.C.G.A. §§ 44-14-369, 1-3-1(d)(3)",
    tier: "contractor", inputs: { first_furnished_on: "2026-09-01", last_furnished_on: "2026-11-02" }, saved: {}, last_from: "job_record", waiver_clocks: [],
    waiver_note: "Georgia lien waivers don't need a notary.",
    deadlines: [{ key: "lien_claim", label: "File the claim of lien", cite: "O.C.G.A. § 44-14-361.1(a)(2)", status: "due_soon", file_by: "2027-01-29", last_day: "2027-02-01", counted: "2027-01-31", moved: true, from: "last work or materials", from_on: "2026-11-02", help: "", note: "" },
                { key: "lien_copy_owner", label: "Send the owner a copy", cite: "O.C.G.A. § 44-14-361.1(a)(2)", status: "waiting", file_by: "", note: "", help: "" }] }],
  warnings: { ja: ["Northline needs a certificate of insurance naming them as additional insured. The one on file expired Nov 1, 2026."] },
  not_legal_advice: "Not legal advice. Confirm every deadline with your attorney before you rely on it.",
};

function setUp(perms) {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner", perms });
  core.setState({ company: { name: "Pine" }, jobs: [{ id: "ja", name: "Pine kitchen", payer_id: "p1", invoices: [], payments: [], change_orders: [], closed: false }],
    payers: [{ id: "p1", name: "Northline" }], inbox: [], email_log: [], email_log_total: 0 });
  globalThis.fetch = async (url) => ({ ok: url === "/api/compliance", status: 200, json: async () => structuredClone(reply) });
}

test("the tab has its own address under Jobs", () => {
  assert.equal(pathFor("compliance"), "/jobs/compliance");
  assert.deepEqual(routeFromPath("/jobs/compliance"), { view: "compliance" });
  assert.equal(typeof reg.views.compliance, "function");
});

test("documents, requirements and lien deadlines, with the attorney note and no inline styles", async () => {
  setUp(["company", "manage_jobs"]);
  await loadCompliance(true);
  const html = reg.views.compliance();
  assert.match(html, /Company documents/);
  assert.match(html, /<span class="pill red">Expired<\/span>/);
  assert.match(html, /W-9<\/strong> <span class="pill">Private<\/span>/);
  assert.match(html, /href="\/api\/compliance\/docs\/cdoc-00000001\/file"/);
  assert.match(html, /What clients need on file/);
  assert.match(html, /Not legal advice\. Confirm every deadline with your attorney/);
  assert.match(html, /44-14-361\.1\(a\)\(2\)/);
  assert.match(html, /The statute's last day is Feb 1, 2027/);
  assert.match(html, /data-action="compliance-lien-done" data-job="ja" data-key="lien_claim"/);
  assert.doesNotMatch(html, /data-key="lien_copy_owner"/, "a step waiting on an earlier one has nothing to mark");
  assert.match(html, /data-action="compliance-add-doc"/);
  assert.doesNotMatch(html, /style=|onclick=/);
  assert.doesNotMatch(html, /packet/i);
});

test("without the permissions the page reads, with no forms", async () => {
  setUp([]);
  await loadCompliance(true);
  const html = reg.views.compliance();
  assert.match(html, /Not legal advice/);
  assert.doesNotMatch(html, /data-action="compliance-(add-doc|upload|delete-doc|add-req|lien-save|lien-done|settings)"/);
});

test("the soft gate: a warning above Approve & send, never a block", async () => {
  setUp(["company"]);
  await loadCompliance(true);
  const html = complianceSendWarningHtml({ id: "ja" });
  assert.match(html, /class="notice compliance-warning"/);
  assert.match(html, /expired Nov 1, 2026/);
  assert.match(html, /You can still send/);
  assert.equal(complianceSendWarningHtml({ id: "other" }), "");
});
