// A client's card by permission: someone without "company" reads it, and only fills what is missing.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");

const client = { id: "p-1", name: "Ellison", role: "GC", client_type: "gc", approver: "Mara", ap_email: "ap@e.example", phone: "(404) 555-0100",
  packet: "Signed contract", packet_items: ["Signed contract"], rejects_when: "No waiver", job_count: 0, playbook_missing: [], usual_approval_days: 14, paid_count: 4, paid_days_avg: 30,
  waiver_not_needed: { by: "Ann", at: "2026-11-01T00:00:00+00:00", reason: "Their rule" },
  packet_suggestions: [{ id: "sug-1", text: "Notarized affidavit", source: "a rejection", at: "2026-11-01T00:00:00+00:00", status: "open" }] };
const other = { id: "p-2", name: "Oak", role: "Owner", client_type: "owner", approver: "Al", ap_email: "a@o.example", phone: "(404) 555-0101", packet_items: [], job_count: 0, playbook_missing: [] };
const draw = (user, payers) => {
  page.setMe({ name: "Pat", company_id: "co-p", role: user.role, perms: user.perms });
  core.setState({ company: { name: "Pine" }, jobs: [], payers, inbox: [], email_log: [], email_log_total: 0, paperwork_catalog: [] });
  page.setLocation("playbook");
  return reg.views.playbook();
};
const EDITS = ["save-playbook", "use-approval-days", "sug-add", "sug-dismiss", "client-waiver-needed", "merge-payer", "delete-payer"];

test("with the permission, the card can be changed", () => {
  const html = draw({ role: "member", perms: ["company"] }, [client, other]);
  for (const action of EDITS.filter(a => a !== "delete-payer")) assert.match(html, new RegExp(`data-action="${action}"`), action);
  assert.match(html, /id="payer-p-1-rejects_when"/);
});

test("without it, the card reads the same settings with no way to change them", () => {
  const html = draw({ role: "member", perms: ["record_payments"] }, [client, other]);
  for (const action of EDITS) assert.doesNotMatch(html, new RegExp(`data-action="${action}"`), action);
  assert.doesNotMatch(html, /id="payer-p-1-/, "no boxes");
  for (const words of ["Mara", "ap@e.example", "(404) 555-0100", "Signed contract", "No waiver", "Notarized affidavit", "Lien waivers not needed"]) assert.ok(html.includes(words), words);
  assert.match(html, /Suggested for the supporting documents/);
  assert.doesNotMatch(html, /Add to the supporting documents\?/);
  assert.match(html, /data-action="toggle-form" data-form="addpayer"/, "a new client can still be added");
});

test("without it, boxes show for what the client is missing and nothing else", () => {
  const html = draw({ role: "member", perms: [] }, [{ ...client, ap_email: "", phone: "", playbook_missing: ["ap_email", "phone"] }]);
  assert.match(html, /data-action="save-client-missing" data-id="p-1"/);
  assert.match(html, /id="payer-p-1-ap_email"/);
  assert.match(html, /id="payer-p-1-phone"/);
  assert.doesNotMatch(html, /id="payer-p-1-approver"/);
  assert.doesNotMatch(html, /id="payer-p-1-name"/);
});

test("Review & send's missing email doesn't need the permission", () => {
  const src = readFileSync(new URL("../../static/js/views/payapps.js", import.meta.url), "utf8");
  assert.match(src, /post\(`\/api\/payers\/\$\{id\}\/missing`, \{ ap_email: v\.ap_email \}/);
  assert.doesNotMatch(src, /\/update`/);
});
