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

test("the bindings cannot be reassigned from outside", () => {
  assert.throws(() => { "use strict"; page.state = {}; }, TypeError);
});
