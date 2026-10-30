// A finished job opened from Job history is fetched whole. Only the server saying it isn't there
// (404) makes it "not found"; a dropped connection or a server error is tried again.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");

const company = () => ({ company: { name: "Pine" }, jobs: [], payers: [], inbox: [], email_log: [], email_log_total: 0 });

test("a failed fetch doesn't mark the job missing; it is tried again on the next visit", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company());
  page.setLocation("job", "j-old");
  dom.answers = [() => "offline"];
  await core.loadJobDetail("j-old");
  assert.equal(core.missingJobs.has("j-old"), false);
  assert.doesNotMatch(reg.views.job(), /Job not found/);
  assert.match(reg.views.job(), /Couldn't load this job/);
  dom.answers = [() => ({ status: 503, body: { error: "Busy." } })];
  await core.loadJobDetail("j-old");
  assert.equal(core.missingJobs.has("j-old"), false);
  // Going to the job again asks again.
  assert.equal(core.jobDetailFailed, "j-old");
  core.nav("job", "j-old");
  assert.equal(core.jobDetailFailed, null);
});

test("a job the server says isn't there is not found", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company());
  page.setLocation("job", "j-gone");
  dom.answers = [() => ({ status: 404, body: { error: "Job not found." } })];
  await core.loadJobDetail("j-gone");
  assert.equal(core.missingJobs.has("j-gone"), true);
  assert.match(reg.views.job(), /Job not found/);
});
