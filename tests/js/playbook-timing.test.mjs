// Customer settings: "Use N days" is only offered for a number the server takes (1 to 365,
// domain.update_payer). node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const settings = await import("../../static/js/views/settings.js");
const reg = await import("../../static/js/views/index.js");

const company = (payer) => ({ company: { name: "Pine" }, jobs: [], payers: [{ id: "p1", name: "Oak Builders", role: "GC", usual_approval_days: 14, ...payer }],
  inbox: [], email_log: [], email_log_total: 0 });

test("a customer who pays the day a pay app is sent isn't offered 'Use 0 days'", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  page.setLocation("playbook");
  core.setState(company({ paid_count: 2, paid_days_avg: 0 }));
  const html = reg.views.playbook();
  assert.doesNotMatch(html, /data-action="use-approval-days"/);
  assert.doesNotMatch(html, /That matches your setting/);
  assert.match(html, /Your setting says 14\./);
});

test("an average the server takes is still offered", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company({ paid_count: 3, paid_days_avg: 30.4 }));
  assert.match(reg.views.playbook(), /data-days="30">Use 30 days<\/button>/);
  core.setState(company({ paid_count: 3, paid_days_avg: 0.6 }));
  assert.match(reg.views.playbook(), /data-days="1">Use 1 day/);
});
