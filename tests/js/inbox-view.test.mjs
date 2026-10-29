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
