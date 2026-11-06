// Round 1 flow fixes on the Dashboard and the shell (Nov 5 audits, round1.md items 8, 9, 11, 17,
// 19, 20): node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");
const auth = await import("../../static/js/views/auth.js");

const fresh = () => ({ company: { name: "Reyes Drywall", address: "" }, jobs: [], payers: [], inbox: [], daily_records: [], email_log: [], email_log_total: 0,
  totals: { unbilled_cents: 0, outstanding_cents: 0, retainage_cents: 0, blocked_jobs: 0 }, today_rows: [], tasks: [], closed_jobs_total: 0 });
const onboarding = (html) => (html.split('<div class="card onboarding">')[1] || "").split("</ol>\n")[0];

test("the setup checklist counts what it numbers, starts with the first job, and folds the optional steps with their count", () => {  // item 9
  page.setMe({ id: "u1", name: "Pat", company_id: "co-r", role: "owner" });
  core.setState(fresh());
  const html = reg.views.dashboard();
  const card = html.split('<div class="card onboarding">')[1].split('<div class="metrics">')[0];
  const numbered = card.split('<ol class="onb-steps">')[1].split("</ol>")[0];
  const items = numbered.match(/<li/g) || [];
  assert.match(card, new RegExp(`0 of ${items.length} steps done`), "the count is the numbered list's");
  assert.match(numbered.split("<li")[1], /Create your first job/, "the first job comes first");
  assert.doesNotMatch(numbered, /Optional/);
  assert.match(card, /<details class="onb-optional" data-more="onb-optional"><summary>Optional steps \(\d\)<\/summary>/);
});

test("the email-not-confirmed note is one plain line, not a card", () => {  // item 9
  page.setMe({ id: "u1", name: "Pat", email: "pat@example.com", company_id: "co-r", role: "owner", email_verified: false });
  page.setCheckEmail({ email: "pat@example.com", verification: "email isn't set up on this server yet" });
  const line = auth.verifyBannerHtml();
  assert.match(line, /^<p class="check-email" role="status">/);
  assert.doesNotMatch(line, /<h2|class="card/);
  assert.match(line, /Got it<\/button><\/p>$/);
  page.setCheckEmail(null);
});
