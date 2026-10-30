// Money > Unpaid: each job has a closed "Recent contact (N)" list of the last emails, calls and client
// page visits, newest first as the server sends them (Stephen's Oct 29 review, M11). node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");
const { recentContactHtml } = await import("../../static/js/views/collections.js");

const contact = [
  { at: "2026-10-20T15:00:00+00:00", kind: "call", label: "Call", text: "Spoke to Sam: Check goes out Friday", by: "Pat", status: "" },
  { at: "2026-10-18T15:00:00+00:00", kind: "email", label: "Follow-up emailed", text: "Follow-up: PA-01", by: "Pat", status: "Bounced" },
];
const job = { id: "ja", name: "Pine kitchen", code: "PINE", payer_id: "p1", payer: { approver: "Sam", ap_email: "ap@x.example" }, closed: false,
  invoices: [{ id: "i1", number: "PA-01", status: "sent", sent_at: "2026-10-01T10:00:00+00:00", amount_cents: 100_000, remaining_cents: 100_000 }],
  payments: [], change_orders: [], money: { outstanding_cents: 100_000 }, status_label: "Sent", recent_contact: contact, contact_total: 7 };

test("the Unpaid row has a closed Recent contact list, newest first, with a way to the job's history", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState({ company: { name: "Pine" }, jobs: [job], payers: [], inbox: [], email_log: [], email_log_total: 0 });
  const html = reg.views.collections();
  assert.match(html, /<details class="recent-contact"><summary>Recent contact \(7\)<\/summary>/);
  assert.ok(html.indexOf("Check goes out Friday") < html.indexOf("Follow-up emailed"), "newest first");
  assert.match(html, /Follow-up emailed<\/strong> · Bounced/);
  assert.match(html, /data-focus="\.job-history">See all on the job</);
  assert.doesNotMatch(html, /<details[^>]* open/);
  assert.doesNotMatch(html, /style=|onclick=/);
  assert.equal(recentContactHtml({ ...job, recent_contact: [] }), "", "nothing to show, no list");
});
