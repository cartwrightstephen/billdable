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

const today = await import("../../static/js/today.js");
const tasks = await import("../../static/js/tasks.js");
const row = (over = {}) => ({ rank: 5, kind: "new_job", job_id: "j1", job: "Porch", label: "Not billed yet", tone: "copper", text: "Porch has no pay app yet.",
  button: "Progress pay app", key: "new_job:j1:", due: "2026-10-30", ball_in_court: "us", assignee_id: "", ...over });

test("a one-person team's To Do rows have no Assignee select or due box; a team of two has both", () => {  // item 8
  const items = tasks.todoItems({ today_rows: [row()] });
  const solo = today.todoHtml(items, { people: [{ id: "u1", name: "Pat" }], today: "2026-10-28" });
  assert.doesNotMatch(solo, /todo-assign|todo-due-date|<th>Assignee<\/th>|<th>Due<\/th>/);
  const team = today.todoHtml(items, { people: [{ id: "u1", name: "Pat" }, { id: "u2", name: "Mo" }], today: "2026-10-28" });
  assert.match(team, /<th>Assignee<\/th><th>Due<\/th>/);
  assert.match(team, /class="todo-assign"/);
  assert.match(team, /class="todo-due-date"/);
});

test("Add task: no Job select with no open job, no Assignee on a one-person team, Ball in court as two buttons", () => {  // item 8
  page.setMe({ id: "u1", name: "Pat", company_id: "co-r", role: "owner" });
  page.setTeamData({ users: [{ id: "u1", name: "Pat" }] });
  core.setState({ ...fresh(), tasks: [{ id: "t1", title: "Call", status: "open" }] });
  page.formOpen.task = true;
  const html = reg.views.dashboard();
  assert.doesNotMatch(html, /id="task-job"/);
  assert.doesNotMatch(html, /id="task-assignee"/);
  assert.match(html, /data-action="seg-pick" data-for="task-ball" data-value="us" aria-pressed="true">Us</);
  assert.doesNotMatch(html, /<select id="task-ball"/);
  page.formOpen.task = false;
  page.setTeamData(null);
});

test("a job filter shows only when there are two jobs or more to pick between", () => {  // item 8
  page.setMe({ id: "u1", name: "Pat", company_id: "co-r", role: "owner" });
  const j = (id) => ({ id, name: `Job ${id}`, code: id.toUpperCase(), payer_id: "p1", closed: false, invoices: [], payments: [], change_orders: [], money: {} });
  core.setState({ ...fresh(), payers: [{ id: "p1", name: "GC" }], jobs: [j("a")] });
  for (const [view, id] of [["calendar", "cal-filter"], ["outbox", "outbox-filter"], ["audit", "audit-filter"], ["export", "sent-filter"]]) {
    assert.doesNotMatch(reg.views[view](), new RegExp(`id="${id}"`), `${view}: one job, no filter`);
  }
  core.setState({ ...fresh(), payers: [{ id: "p1", name: "GC" }], jobs: [j("a"), j("b")] });
  for (const [view, id] of [["calendar", "cal-filter"], ["outbox", "outbox-filter"], ["audit", "audit-filter"], ["export", "sent-filter"]]) {
    assert.match(reg.views[view](), new RegExp(`id="${id}"`), `${view}: two jobs, a filter`);
  }
});

import { readFileSync } from "node:fs";
const src = (f) => readFileSync(new URL(`../../static/${f}`, import.meta.url), "utf8");

test("accessibility: (i) icons are out of the Tab order and describe their field; the closed phone drawer is inert", () => {  // item 11
  const coreSrc = src("js/app-core.js");
  assert.match(coreSrc, /icon\.tabIndex = -1;/);
  assert.doesNotMatch(coreSrc, /icon\.tabIndex = 0;/);
  assert.match(coreSrc, /setAttribute\("aria-description", /);
  const appSrc = src("app.js");
  assert.match(appSrc, /rail\.inert = !drawerOpen && phoneLayout\.matches;/);
});

test("accessibility: the ? badge clears a sticky Save bar, a disabled button stays readable, and a toast leaves the tab row alone", () => {  // item 11, small
  const css = src("styles.css");
  assert.match(css, /body:has\(\.save-bar\) \.ai-badge \{ bottom: \d+px; \}/);
  assert.doesNotMatch(css, /\.btn:disabled \{ opacity: 0\.45;/);
  assert.match(css, /\.btn:disabled \{ opacity: 1; background: var\(--field-bg\); color: var\(--muted\);/);
  const toast = /\n\.toast \{([^}]*)\}/.exec(css)[1];
  assert.match(toast, /bottom: /);
  assert.doesNotMatch(toast, /top: 16px/);
  assert.doesNotMatch(css, /\.toast \{ top: 68px; \}/);
});

test("the Dashboard on a phone: To Do rows are pill, sentence and one button; tiles 2x2; one line per job", () => {  // item 17
  page.setMe({ id: "u1", name: "Pat", company_id: "co-hartline", role: "owner", onboarding_dismissed: true });
  const j = { id: "a", name: "Turner", code: "TUR", payer_id: "p1", closed: false, invoices: [], payments: [], change_orders: [],
    money: { contract_total_cents: 100, billed_cents: 0, outstanding_cents: 5000, unbilled_completed_cents: 0, retainage_open_cents: 0 } };
  core.setState({ ...fresh(), payers: [{ id: "p1", name: "GC" }], jobs: [j], today_rows: [row({ job_id: "a", job: "Turner" })] });
  const html = reg.views.dashboard();
  assert.match(html, /<div class="metrics dash-tiles">/);
  assert.match(html, /<table class="sortable dash-jobs">/);
  assert.match(html, /<td class="dj-keep dj-name"><a href="\/jobs\/a"/);
  assert.match(html, /<td class="num dj-keep">\$50\.00<\/td>/);
  assert.match(html, /<td class="todo-job">/);
  assert.match(html, /<td class="todo-ball">/);
  const css = src("styles.css");
  const phone = css.slice(css.indexOf("/* == round 1: the Dashboard on a phone"));
  assert.match(phone, /\.metrics\.dash-tiles \{ grid-template-columns: 1fr 1fr; \}/);
  assert.match(phone, /table\.todo-table td\.todo-job, table\.todo-table td\.todo-ball \{ display: none; \}/);
  assert.match(phone, /table\.todo-table \.action-buttons \.btn\.secondary:not\(:only-child\) \{ display: none; \}/);
  assert.match(phone, /table\.dash-jobs td:not\(\.dj-keep\) \{ display: none; \}/);
});
