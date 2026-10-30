// Today's Calendar and Daily record pages, drawn from the company's state. node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const today = await import("../../static/js/views/today.js");
const reg = await import("../../static/js/views/index.js");

const payer = { id: "p1", name: "Oak Builders", follow_up_days: 7, usual_approval_days: 14 };
const job = (id, code, invoices, over = {}) => ({ id, name: `${code} job`, code, payer_id: "p1", closed: false, invoices, payments: [], money: { unbilled_completed_cents: 0 }, ...over });
const company = (jobs) => ({ company: { name: "Pine" }, jobs, payers: [payer], inbox: [], daily_records: [], email_log: [], email_log_total: 0 });

function dayCard(day) {
  reg.actions["cal-day"]({ dataset: { day } });
  const html = reg.views.calendar();
  const card = html.split('id="day-detail"')[1] || "";
  return card.split('<div class="cal-header">')[0];
}

test("the calendar's follow-up is the next one due: after the last follow-up, and for a part-paid pay app too", () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  core.setState(company([
    job("j1", "OAK", [{ id: "d1", number: "PA-OAK-1", status: "sent", sent_at: "2026-10-01T12:00:00", last_follow_up_at: "2026-10-08T12:00:00" }]),
    job("j2", "ELM", [{ id: "d2", number: "PA-ELM-1", status: "partial", sent_at: "2026-10-02T12:00:00" }]),
  ]));
  assert.match(dayCard("2026-10-15"), /OAK follow-up/);
  assert.doesNotMatch(dayCard("2026-10-08"), /OAK follow-up/);
  assert.match(dayCard("2026-10-09"), /ELM follow-up/);
  // The date noted after a call ("Spoke to them") moves it, as on Today and the job page.
  core.setState(company([job("j1", "OAK", [{ id: "d1", number: "PA-OAK-1", status: "sent", sent_at: "2026-10-01T12:00:00" }],
    { expected_payment_on: "2026-10-20", contact_noted_at: "2026-10-05T09:00:00" })]));
  assert.match(dayCard("2026-10-20"), /OAK follow-up/);
  assert.doesNotMatch(dayCard("2026-10-08"), /OAK follow-up/);
});

test("after a save, the Daily record's job stays on the job just used", async () => {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner" });
  const state = company([job("jB", "BIRCH", []), job("jC", "CEDAR", [])]);
  core.setState(state);
  localStorage.setItem("duracash-daily-job", "jB");
  dom.bySelector["#daily-notes"] = { value: "Framing done" };
  dom.bySelector["#daily-job"] = { value: "jC" };
  dom.answers = [(p) => (p === "/api/jobs/jC/daily-record" ? { status: 200, body: state } : undefined)];
  let drawn = "";
  core.setPage({ render: () => { drawn = reg.views.daily(); } });
  await reg.actions["daily-save"]();
  assert.match(drawn, /<option value="jC" selected>/);
  assert.doesNotMatch(drawn, /<option value="jB" selected>/);
  core.setPage({ render() {} });
});

test("the Calendar's Tasks block lists To Do items and the calendar's dates in the period, overdue ones too", () => {  // T6
  page.setMe({ id: "u1", name: "Pat", company_id: "co-p", role: "owner" });
  const day = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
  core.setState({ ...company([job("j1", "OAK", [])]),
    today_rows: [{ key: "ready:j1:i1", kind: "ready", label: "Ready to send", job_id: "j1", job: "OAK job", due: day(-3), ball_in_court: "us", assignee_id: "" }],
    tasks: [{ id: "task-a", title: "Order tile", status: "open", due: day(40), assignee_id: "u1", assignee_name: "Pat" },
      { id: "task-b", title: "Call inspector", status: "open", due: "", assignee_id: "" }] });
  const html = reg.views.calendar();
  const block = html.split('<section class="tasks-block"')[1].split("</section>")[0];
  assert.match(block, /<h2 id="tasks-head">Tasks<\/h2>/);
  assert.match(block, /data-action="tasks-period" data-period="week" aria-pressed="true">This week</);
  assert.match(block, /class="due-item overdue" data-action="cal-day"[^>]*>[^<]*Ready to send · OAK job</);
  assert.match(block, /No date · Call inspector/);
  assert.doesNotMatch(block, /Order tile/, "40 days out is not this week");
  reg.actions["tasks-period"]({ dataset: { period: "custom" } });
  const custom = reg.views.calendar().split('<section class="tasks-block"')[1].split("</section>")[0];
  assert.match(custom, /id="tasks-from" data-period-from="tasks"/);
  assert.match(custom, /Order tile · Pat/, "a custom range with no dates is open-ended");
  reg.actions["tasks-period"]({ dataset: { period: "week" } });
});
