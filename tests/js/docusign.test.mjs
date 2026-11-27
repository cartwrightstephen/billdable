// DocuSign (views/docusign.js): "Sign with DocuSign" shows beside the typed-name signature only
// once DocuSign is set up on the server and connected for the company; the card on Settings,
// Company; and the words on coming back from DocuSign's signing page. node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const page = await import("../../static/js/state.js");
await import("../../static/js/views/index.js");
const ds = await import("../../static/js/views/docusign.js");
const { approvalHtml } = await import("../../static/js/views/payapps.js");

const inv = { id: "inv-1", number: "PA-1", status: "awaiting_approval", job_id: "j-1",
  approval: { required: [{ id: "u-ben", name: "Ben Ode", approved_at: "" }], waiting: ["Ben Ode"], stamp: "abc123" } };

test("not set up or not connected: the typed signature alone, as before", () => {
  page.setMe({ name: "Ben", company_id: "co-p", role: "owner", email_verified: true });
  for (const status of [{ configured: false, connected: false }, { configured: true, connected: false }]) {
    ds.setDocusignStatus(status);
    const html = approvalHtml(inv, "u-ben", true);
    assert.match(html, /data-action="payapp-approve"/);
    assert.doesNotMatch(html, /docusign-sign/);
  }
  ds.setDocusignStatus({ configured: false, connected: false });
  assert.equal(ds.docusignCardHtml(), "", "no card while the server has no DocuSign keys");
});

test("connected: Sign with DocuSign beside Sign, carrying the version shown", () => {
  ds.setDocusignStatus({ configured: true, connected: true, account_name: "Pine Mechanical", environment: "demo", waiting: [] });
  const html = approvalHtml(inv, "u-ben", true);
  assert.match(html, /data-action="payapp-approve"/);
  assert.match(html, /data-action="docusign-sign" data-kind="payapp" data-id="j-1" data-inv="inv-1" data-stamp="abc123">Sign with DocuSign</);
  assert.doesNotMatch(approvalHtml(inv, "u-ben", false), /docusign-sign/, "only for someone who may sign");
  assert.match(ds.docusignWaiver({ id: "j-1" }, "conditional_progress", "inv-1", false), /data-kind="waiver" data-id="j-1" data-for="inv-1" data-which="interim" data-waiver-kind="conditional_progress"/);
});

test("the Settings card says what is connected and what is waiting", () => {
  ds.setDocusignStatus({ configured: true, connected: true, account_name: "Pine Mechanical", environment: "demo", connected_at: "2026-11-27T10:00:00+00:00",
    connected_by: "Pat", co_client_signs: false, waiting: [{ id: "env-1", kind: "co", label: "CO 2 - Beam", created_at: "2026-11-27T10:00:00+00:00", user_name: "Pat" }] });
  const html = ds.docusignCardHtml();
  assert.match(html, /Connected/);
  assert.match(html, /Pine Mechanical/);
  assert.match(html, /Demo account/);
  assert.match(html, /data-action="docusign-check" data-envelope="env-1"/);
  ds.setDocusignStatus({ configured: true, connected: false });
  assert.match(ds.docusignCardHtml(), /data-action="docusign-connect"/);
});

test("coming back from DocuSign says what happened", () => {
  assert.match(ds.backFromDocusign("?docusign=signing_complete"), /Signed in DocuSign/);
  assert.match(ds.backFromDocusign("?docusign=decline"), /declined/);
  assert.match(ds.backFromDocusign("?docusign=whatever"), /didn't finish/);
  assert.equal(ds.backFromDocusign("?other=1"), "");
});
