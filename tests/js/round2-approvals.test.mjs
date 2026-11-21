// Stephen's answers, 18 Nov 2026: no pay app is ever sent unapproved, and approving is signing.
// Review & send shows the signing form (typed full name, title), says plainly when nobody can
// approve a client's pay apps with the way to Client playbook, and the client card says who signs.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
globalThis.requestAnimationFrame = (f) => f();
await import("../../static/js/app-core.js");
await import("../../static/js/views/index.js");
const { approvalHtml, signedText } = await import("../../static/js/views/payapps.js");
const { clientApproversText } = await import("../../static/js/views/clients.js");
const { ownersLine, approverNames } = await import("../../static/js/approvers.js");

const draft = (approval) => ({ id: "inv-1", number: "PA-3", status: "awaiting_approval", job_id: "j-1", approval });

test("the owner waiting on their own pay app signs it with a typed full name and an optional title", () => {
  const html = approvalHtml(draft({ required: [{ id: "u-own", name: "Dana Whitfield", approved_at: "" }], waiting: ["Dana Whitfield"],
    stamp: "s1", source: "owners", blocked: "", consent: "I have checked this pay app and approve it to go to the client. My typed name is my signature." }), "u-own", true);
  assert.match(html, /data-form-key="approve-inv-1"/);
  assert.match(html, /name="signature"[^>]*autocomplete="name"/);
  assert.match(html, /<label for="approve-inv-1-signature">Your full name <span class="req">\*<\/span><\/label>/);
  assert.match(html, /name="title"/);
  assert.match(html, /My typed name is my signature\./);
  assert.match(html, /data-action="payapp-approve" data-id="j-1" data-inv="inv-1" data-stamp="s1">Sign PA-3</);
  assert.doesNotMatch(html, /placeholder=/, "no grey example text (Stephen, 10 Nov, to-do 8)");
});

test("a signature reads as who signed, their title and when; someone not waiting gets no form", () => {
  const p = { id: "u-ana", name: "Ana Ruiz", signed_name: "Ana M. Ruiz", title: "Project manager", approved_at: "2026-11-18T15:00:00+00:00" };
  assert.match(signedText(p), /^Signed \/s\/ Ana M\. Ruiz, Project manager · /);
  assert.match(signedText({ ...p, title: "", signed_name: "" }), /^Signed \/s\/ Ana Ruiz · /);
  const html = approvalHtml(draft({ required: [p], waiting: [], stamp: "s1", source: "company", blocked: "" }), "u-ana", true);
  assert.match(html, /Signed \/s\/ Ana M\. Ruiz, Project manager/);
  assert.doesNotMatch(html, /payapp-approve|approve-sign/);
});

test("a client list saved empty says plainly the pay app can't go, and points to Client playbook", () => {
  const msg = "Nobody is named to approve Northline GC's pay apps, so they can't be sent. Name approvers in Client playbook.";
  const html = approvalHtml(draft({ required: [], waiting: [], stamp: "s1", source: "client", blocked: msg }), "u-own", true, true);
  assert.match(html, /class="blocker approvals-none" role="status">Nobody is named to approve Northline GC's pay apps/);
  assert.match(html, /data-action="nav" data-view="playbook">Open Client playbook</);
  assert.doesNotMatch(html, /payapp-approve/);
  // Without the client settings permission the words stay, without the button.
  assert.doesNotMatch(approvalHtml(draft({ required: [], waiting: [], source: "client", blocked: msg }), "u-own", true, false), /data-view="playbook"/);
  // Nobody at all (no owners known) points to Settings, Company.
  assert.match(approvalHtml(draft({ required: [], waiting: [], source: "owners", blocked: "Nobody can approve pay apps yet." }), "u", true, true),
    /data-view="company">Open Settings, Company</);
});

test("the client card says who signs: its own list, an empty own list, the company's list, or the owners", () => {
  const owners = [{ id: "u-own", name: "Dana Whitfield" }];
  assert.equal(clientApproversText({ payapp_approvers: [{ id: "u-ana", name: "Ana Ruiz" }] }, {}, owners), "Ana Ruiz");
  assert.match(clientApproversText({ payapp_approvers: [] }, { payapp_approvers: [{ id: "u-ben", name: "Ben Ode" }] }, owners), /^Nobody\. Its pay apps can't be sent/);
  assert.equal(clientApproversText({}, { payapp_approvers: [{ id: "u-ben", name: "Ben Ode" }] }, owners), "Ben Ode (the company's list)");
  assert.equal(clientApproversText({}, { payapp_approvers: [] }, owners), "Dana Whitfield (the owners)");
  assert.equal(ownersLine([]), "The owners");
  assert.equal(approverNames([], "every owner"), "every owner");
});
