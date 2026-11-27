// DocuSign (views/docusign.js): "Sign with DocuSign" shows beside the typed-name signature for
// everyone, and the card on Settings, Company always shows (Nov 27). Before the server has its
// keys, pressing either says so right there and offers the typed name; once connected, the real
// flow. And the words on coming back from DocuSign's signing page. node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
globalThis.requestAnimationFrame = () => 0;
const page = await import("../../static/js/state.js");
await import("../../static/js/views/index.js");
const ds = await import("../../static/js/views/docusign.js");
const { approvalHtml } = await import("../../static/js/views/payapps.js");

const inv = { id: "inv-1", number: "PA-1", status: "awaiting_approval", job_id: "j-1",
  approval: { required: [{ id: "u-ben", name: "Ben Ode", approved_at: "" }], waiting: ["Ben Ode"], stamp: "abc123" } };

test("not set up or not connected: Sign with DocuSign still shows beside the typed signature", () => {
  page.setMe({ name: "Ben", company_id: "co-p", role: "owner", email_verified: true });
  for (const status of [{ configured: false, connected: false }, { configured: true, connected: false }]) {
    ds.setDocusignStatus(status);
    ds.setDocusignNote(null);
    const html = approvalHtml(inv, "u-ben", true);
    assert.match(html, /data-action="payapp-approve"/);
    assert.match(html, /data-action="docusign-sign" data-kind="payapp"[^>]*>Sign with DocuSign</);
    assert.doesNotMatch(html, /docusign-note/, "no note until it is pressed");
    assert.doesNotMatch(approvalHtml(inv, "u-ben", false), /docusign-sign/, "only for someone who may sign");
  }
  ds.setDocusignStatus({ configured: false, connected: false });
  const card = ds.docusignCardHtml();
  assert.match(card, /<h2>DocuSign eSignature<\/h2>/);
  assert.match(card, /Sign pay apps, lien waivers and change orders with DocuSign/);
  assert.match(card, /data-action="docusign-connect">Connect DocuSign</);
  assert.doesNotMatch(card, /customer|AI Assisted/i);
});

test("not switched on yet: Sign with DocuSign says so, offers the typed name, and signs nothing", async () => {
  page.setMe({ name: "Ben", company_id: "co-p", role: "owner", email_verified: true });
  ds.setDocusignStatus({ configured: false, connected: false });
  ds.setDocusignNote(null);
  const message = "DocuSign is being switched on for your account. We'll email you when it's ready. Until then, sign with your typed name \u2014 it's legally binding.";
  dom.answers.unshift((path) => (path === "/api/docusign/sign" ? { status: 200, body: { pending: true, message, configured: false, connected: false } } : undefined));
  const el = { dataset: { kind: "payapp", id: "j-1", inv: "inv-1", stamp: "abc123" }, disabled: false };
  let went = "";
  const was = globalThis.location;
  globalThis.location = { ...(was || {}), search: "", pathname: "/", assign: (u) => { went = u; } };
  try { await ds.actions["docusign-sign"](el); } finally { globalThis.location = was; }
  assert.equal(went, "", "nowhere to go: nothing was sent to DocuSign");
  const html = approvalHtml(inv, "u-ben", true);
  assert.match(html, /class="docusign-note" role="status"/);
  assert.match(html, /being switched on for your account/);
  assert.match(html, /it&#39;s legally binding/);
  assert.match(html, /data-action="docusign-typed" data-focus="approve-inv-1-signature">Sign with your typed name</);
  assert.match(html, /data-action="payapp-approve"/, "the typed signature is still right there");
  assert.doesNotMatch(html, /Signed in DocuSign/);
  // Only under the button that was pressed.
  assert.doesNotMatch(ds.docusignButton('data-kind="co" data-id="j-1" data-co="co-1"'), /docusign-note/);
  // The typed-name offer closes the note and goes to the name box.
  let focused = false;
  dom.byId["approve-inv-1-signature"] = { focus: () => { focused = true; } };
  ds.actions["docusign-typed"]({ dataset: { focus: "approve-inv-1-signature" } });
  assert.ok(focused);
  assert.doesNotMatch(approvalHtml(inv, "u-ben", true), /docusign-note/);
  // A change order and a waiver say how to sign without DocuSign.
  ds.setDocusignNote("co|j-1|co-1|", message);
  assert.match(ds.docusignButton('data-kind="co" data-id="j-1" data-co="co-1"'), /Request signature sends your client a page where they sign with their typed name/);
  ds.setDocusignNote("waiver|j-1|inv-1|interim", message);
  assert.match(ds.docusignWaiver({ id: "j-1" }, "conditional_progress", "inv-1", false), /fill in the form, sign it and file the signed copy/);
  ds.setDocusignNote(null);
});

test("not switched on yet: Connect on the card says so instead of an error", async () => {
  page.setMe({ name: "Ben", company_id: "co-p", role: "owner", perms: ["company"], email_verified: true });
  ds.setDocusignStatus({ configured: false, connected: false });
  ds.setDocusignNote(null);
  dom.answers.unshift((path) => (path === "/api/docusign/connect" ? { status: 200, body: { pending: true, message: "DocuSign is being switched on for your account. We'll email you when it's ready.", configured: false, connected: false } } : undefined));
  await ds.actions["docusign-connect"]();
  const card = ds.docusignCardHtml();
  assert.match(card, /docusign-note/);
  assert.match(card, /being switched on for your account/);
  assert.match(card, /Connect DocuSign/);
  ds.setDocusignNote(null);
});

test("connected: Sign with DocuSign beside Sign, carrying the version shown, and pressing it goes to DocuSign", async () => {
  ds.setDocusignStatus({ configured: true, connected: true, account_name: "Pine Mechanical", environment: "demo", waiting: [] });
  const html = approvalHtml(inv, "u-ben", true);
  assert.match(html, /data-action="payapp-approve"/);
  assert.match(html, /data-action="docusign-sign" data-kind="payapp" data-id="j-1" data-inv="inv-1" data-stamp="abc123">Sign with DocuSign</);
  assert.doesNotMatch(approvalHtml(inv, "u-ben", false), /docusign-sign/, "only for someone who may sign");
  assert.match(ds.docusignWaiver({ id: "j-1" }, "conditional_progress", "inv-1", false), /data-kind="waiver" data-id="j-1" data-for="inv-1" data-which="interim" data-waiver-kind="conditional_progress"/);
  dom.answers.unshift((path) => (path === "/api/docusign/sign" ? { status: 200, body: { url: "https://demo.docusign.net/signing/abc", envelope_id: "env-9" } } : undefined));
  let went = "";
  const was = globalThis.location;
  globalThis.location = { ...(was || {}), search: "", pathname: "/", assign: (u) => { went = u; } };
  try { await ds.actions["docusign-sign"]({ dataset: { kind: "payapp", id: "j-1", inv: "inv-1", stamp: "abc123" }, disabled: false }); }
  finally { globalThis.location = was; }
  assert.equal(went, "https://demo.docusign.net/signing/abc");
  assert.doesNotMatch(approvalHtml(inv, "u-ben", true), /docusign-note/);
  const sent = dom.requests.filter(r => r.path === "/api/docusign/sign").pop();
  assert.deepEqual({ kind: sent.body.kind, job_id: sent.body.job_id, invoice_id: sent.body.invoice_id, stamp: sent.body.stamp }, { kind: "payapp", job_id: "j-1", invoice_id: "inv-1", stamp: "abc123" });
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
  page.setMe({ name: "Mo", company_id: "co-p", role: "member", perms: [], email_verified: true });
  assert.match(ds.docusignCardHtml(), /An owner of this company can connect DocuSign here/, "the card shows for everyone; Connect for owners");
});

test("coming back from DocuSign says what happened", () => {
  assert.match(ds.backFromDocusign("?docusign=signing_complete"), /Signed in DocuSign/);
  assert.match(ds.backFromDocusign("?docusign=decline"), /declined/);
  assert.match(ds.backFromDocusign("?docusign=whatever"), /didn't finish/);
  assert.equal(ds.backFromDocusign("?other=1"), "");
});
