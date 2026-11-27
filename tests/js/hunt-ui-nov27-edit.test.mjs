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

test("a check answered at Review & send shows as checked or not needed, not as missing", async () => {
  const paperwork = await import("../../static/js/views/paperwork.js");
  const payer = { id: "p1", name: "Oak Builders" };
  const inv = { id: "d1", status: "sent", packet: [
    { id: "b", label: "Daily tickets", required: true, present: false, gate: "soft", soft_check: { answer: "checked", by: "Ana", at: "2026-11-27T12:00:00" } },
    { id: "c", label: "Time sheets", required: true, present: false, gate: "soft", soft_check: { answer: "not_needed", by: "Ana", at: "2026-11-27T12:00:00" } },
    { id: "d", label: "Progress photos", required: true, present: false, gate: "soft" },
  ] };
  const j = { ...job, payer, closed: false, invoices: [inv] };
  core.setState({ company: { name: "Pine" }, jobs: [j], payers: [payer], inbox: [], daily_records: [], files: [], email_log: [] });
  const html = paperwork.supportingDocsHtml(j, inv);
  const row = (id) => html.slice(html.indexOf(`id="packet-item-${id}"`), html.indexOf("</strong>", html.indexOf(`id="packet-item-${id}"`)));
  assert.match(row("b"), /class="mark" aria-hidden="true">✓</);
  assert.match(row("c"), /class="mark" aria-hidden="true">–</);
  assert.match(row("d"), /class="mark" aria-hidden="true">✗</);
  assert.match(html, /<span class="visually-hidden">checked<\/span>/);
  assert.match(html, /<span class="visually-hidden">not needed this time<\/span>/);
});

test("the time sheet offers a team member once, and the conversation is a multi-line box", async () => {
  const forms = await import("../../static/js/forms.js");
  st.setTeamData({ users: [{ id: "u1", name: "Alex" }, { id: "u2", name: "Sam" }] });
  st.setLocation("daily");
  core.setState({ company: { name: "Pine" }, jobs: [job], payers: [], inbox: [], daily_records: [], files: [], email_log: [] });
  daily.daily.rows.crew = 2;
  daily.daily.open.add("conversation");
  forms.formDraft.daily = { ts_pick_0: "u1", ts_pick_1: "" };
  const reg = await import("../../static/js/views/index.js");
  const html = reg.views.daily();
  const row = (n) => html.slice(html.indexOf(`id="daily-ts-pick-${n}"`), html.indexOf("</select>", html.indexOf(`id="daily-ts-pick-${n}"`)));
  assert.match(row(0), /value="u1"/);
  assert.doesNotMatch(row(1), /value="u1"/, "Alex is on row 1 already");
  assert.match(row(1), /value="u2"/);
  assert.match(html, /<textarea id="daily-conversation-text-0"/);
  daily.daily.rows.crew = 1;
  daily.daily.open.delete("conversation");
  forms.formDraft.daily = {};
  st.setTeamData(null);
});

test("Someone else puts the cursor in the name box straight away", async () => {
  const src = (await import("node:fs")).readFileSync(new URL("../../static/js/views/daily.js", import.meta.url), "utf8");
  assert.match(src, /if \(el\.value === OTHER_PERSON\) document\.getElementById\(el\.id\.replace\("-pick-", "-name-"\)\)\?\.focus\(\);/);
});
