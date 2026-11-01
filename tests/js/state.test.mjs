// The page's shared state: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import * as page from "../../static/js/state.js";

test("importers see each change through the setters", () => {
  page.setData({ jobs: [] });
  page.setMe({ name: "Pat" });
  page.setLocation("job", "j1");
  assert.deepEqual(page.state, { jobs: [] });
  assert.equal(page.me.name, "Pat");
  assert.deepEqual([page.currentView, page.currentJobId, page.currentDetailId], ["job", "j1", null]);
  page.formOpen.newjob = true;
  page.closeForms();
  assert.deepEqual(page.formOpen, {});
  page.clearSession();
  assert.equal(page.state, null);
  assert.equal(page.me, null);
});

test("the account and the shell's flags moved out of app.js change through their setters (R1-03)", () => {
  page.setAccount({ helperAdmin: true, mailReady: true, readOnly: "", billingInfo: null, heldShare: 2 });
  page.setAccount({ heldShare: 0 });   // only the fields given change
  assert.deepEqual([page.helperAdmin, page.mailReady, page.heldShare], [true, true, 0]);
  page.setPending("/jobs/j1", "?new=1");
  assert.deepEqual([page.pendingPath, page.pendingSearch], ["/jobs/j1", "?new=1"]);
  page.setDrawerOpen(true);
  page.setTeamData({ users: [], invites: [] });
  assert.equal(page.drawerOpen, true);
  assert.deepEqual(page.teamData, { users: [], invites: [] });
  page.setAccount({ helperAdmin: false, mailReady: false, heldShare: 0 });
  page.setPending(null, null);
  page.setDrawerOpen(false);
  page.setTeamData(null);
});

test("the bindings cannot be reassigned from outside", () => {
  assert.throws(() => { "use strict"; page.state = {}; }, TypeError);
});
