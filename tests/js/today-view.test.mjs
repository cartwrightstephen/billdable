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
