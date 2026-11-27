// Nov 27 UI hunt: Edit on a job record opened straight after the page loads, before the team
// list has come, kept a team member on the time sheet as "Someone else" with a typed name.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
globalThis.requestAnimationFrame ||= (fn) => fn();
const core = await import("../../static/js/app-core.js");
const st = await import("../../static/js/state.js");
const daily = await import("../../static/js/views/daily.js");

const job = { id: "j1", name: "Oak job", payer_id: "p1", invoices: [], payments: [], change_orders: [], blockers: [], close_blockers: [],
  records: [{ id: "rs", kind: "ticket", ticket_type: "time_sheet", label: "Time sheet: 1 crew, 8 h", daily_record_id: "dr1",
    crew: [{ name: "Alex Hartline", hours_milli: 8000, member_id: "u1" }, { name: "Jose", hours_milli: 4000 }] }] };
const dr = { id: "dr1", job_id: "j1", at: "2026-11-27T12:00:00+00:00", notes: "Drywall" };

test("before the team list comes, a member on a time sheet stays that member", () => {
  globalThis.fetch = () => new Promise(() => {});   // the team list never arrives in this test
  st.setTeamData(null);
  core.setState({ company: { name: "Pine" }, jobs: [job], payers: [], inbox: [], daily_records: [dr], files: [], email_log: [] });
  const p = daily.editPrefill(job, dr);
  assert.equal(p.draft.ts_pick_0, "u1");
  assert.equal(p.draft.ts_pick_1, "__other");
  assert.equal(p.draft.ts_name_1, "Jose");
  daily.daily.rows.crew = 2;
  // Saved before the list comes: the same member, by the saved name.
  assert.deepEqual(daily.crewRows(p.draft, []), [{ name: "Alex Hartline", hours: "8", member_id: "u1" }, { name: "Jose", hours: "4" }]);
  daily.daily.rows.crew = 1;
});

test("with the team list here, a member no longer on the team comes back as Someone else", () => {
  st.setTeamData({ users: [{ id: "u2", name: "Sam" }] });
  const p = daily.editPrefill(job, dr);
  assert.equal(p.draft.ts_pick_0, "__other");
  assert.equal(p.draft.ts_name_0, "Alex Hartline");
  st.setTeamData(null);
});
