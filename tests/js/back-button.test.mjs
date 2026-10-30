// The in-app Back button (G3): it names the page the person came from and goes back to it; opened
// from a link or a refresh, it goes to the page's fixed parent. node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");

test("Back names the page before and goes back; without one it goes to the parent", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState({ company: { name: "Pine" }, jobs: [], payers: [], inbox: [], email_log: [], email_log_total: 0 });
  core.setPage({ pageName: (e) => ({ dashboard: "Dashboard", job: "Oak job", payapps: "Pay apps" })[e.view] || "" });
  core.applyLocation();   // arrived from outside: the fixed parent
  assert.match(core.backButton("jobs", "Jobs"), /data-action="nav" data-view="jobs">← Jobs</);
  core.nav("dashboard");
  core.nav("job", "j1");
  assert.match(core.backButton("jobs", "Jobs"), /data-action="back">← Dashboard</);
  core.nav("payapp-detail", "j1", "inv1");
  assert.match(core.backButton("job", "Oak job", "j1"), /data-action="back">← Oak job</);
  core.nav("job", "j1", null, { fromHistory: true });
  assert.match(core.backButton("jobs", "Jobs"), /← Dashboard</);
  core.setPage({ pageName: () => "" });
});
