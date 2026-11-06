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

// ---- item 19: cut and fold ----
const routes = await import("../../static/js/routes.js");
const automations = await import("../../static/js/views/automations.js");

test("the menu: no Automations section, no Results or Payments tab, and Helper answers isn't a tab for everyone", () => {  // item 19
  const appSrc = src("app.js");
  const sections = [...appSrc.matchAll(/\{ key: "([\w-]+)", label: "([^"]+)"(, more: true)?, views: (\[.*?\]) \},/g)].map(m => [m[1], JSON.parse(m[4]).map(([v]) => v)]);
  assert.deepEqual(sections.map(([k]) => k), ["today", "jobs", "communications", "data", "money", "settings"]);
  const views = Object.fromEntries(sections);
  assert.deepEqual(views.today, ["dashboard", "calendar"]);
  assert.deepEqual(views.money, ["payapps", "cos", "collections", "waivers"]);
  assert.ok(!views.settings.includes("helper-admin"));
  assert.equal(reg.views.automations, undefined);
  assert.equal(reg.views.results, undefined);
  assert.equal(reg.views.payments, undefined);
});

test("the old addresses land on the new places: Automations on My account's Notifications, Results on the Dashboard, Payments on Pay apps", () => {  // item 19
  assert.deepEqual(routes.routeFromPath("/automations"), { view: "account", anchor: "notifications" });
  assert.deepEqual(routes.routeFromPath("/dashboard/results"), { view: "dashboard", anchor: "results" });
  assert.deepEqual(routes.routeFromPath("/today/results"), { view: "dashboard", anchor: "results" });
  assert.deepEqual(routes.routeFromPath("/money/payments"), { view: "payapps", anchor: "payments" });
});

const remindersReply = (over = {}) => ({ kinds: [{ key: "dates", label: "Important dates", help: "Dates." }, { key: "missing", label: "Missing information", help: "Missing." }],
  channels: [["app", "In the app"], ["email", "Email"], ["text", "Text"]], prefs: { dates: ["app"], missing: ["app", "email"] }, repeat_days: 3,
  notifications: [], unread: 0, can_text: false, texts_stopped: false, email_ready: true, texts_ready: false, last: null, ...over });

test("My account has the Notifications card with each person's reminder choices, saved with the page", async () => {  // item 19
  page.setMe({ id: "u1", name: "Pat", email: "pat@example.com", company_id: "co-r", role: "owner", email_verified: true });
  core.setState(fresh());
  dom.answers = [(p) => (p === "/api/reminders" ? { status: 200, body: remindersReply() } : undefined)];
  await automations.loadReminders(true);
  const html = reg.views.account();
  const card = html.split('<div class="card" id="notifications"')[1].split('<div class="save-bar">')[0];
  assert.match(card, /<h2>Notifications<\/h2>/);
  assert.match(card, /data-reminder="dates" value="app" checked/);
  assert.match(card, /data-reminder="missing" value="email" checked/);
  assert.doesNotMatch(card, /data-reminder="dates" value="email" checked/);
  assert.doesNotMatch(card, /data-action="save-reminders"/, "the page's one Save saves it");
  dom.answers = [];
});

test("fired reminders are To Do rows; one about a job that already has a row for its dates or held-up pay app is left off", async () => {  // item 19
  page.setMe({ id: "u1", name: "Pat", company_id: "co-r", role: "owner" });
  const j = (id, name) => ({ id, name, code: id.toUpperCase(), payer_id: "p1", closed: false, invoices: [], payments: [], change_orders: [], money: {} });
  core.setState({ ...fresh(), payers: [{ id: "p1", name: "GC" }], jobs: [j("a", "Oak"), j("b", "Elm")], today_rows: [row({ job_id: "a", job: "Oak", key: "new_job:a:" })] });
  const note = (id, key, job_id, job, text, over = {}) => ({ id, key, kind: key.split(":")[0], job_id, job, text, path: `/jobs/${job_id}`, at: "2026-11-05T10:00:00+00:00", read: false, done: false, ...over });
  dom.answers = [(p) => (p === "/api/reminders" ? { status: 200, body: remindersReply({ unread: 4, notifications: [
    note("n1", "co_records:co:c1", "a", "Oak", "CO-1 has work on a job record and needs a signature."),
    note("n2", "dates:a-follow", "a", "Oak", "Follow up, Nov 9."),
    note("n3", "missing:client:p1", "b", "Elm", "GC has no phone on file.", { path: "/jobs/clients" }),
    note("n4", "missing:client:p2", "b", "Elm", "Read already.", { read: true }),
  ] }) } : undefined)];
  await automations.loadReminders(true);
  const html = reg.views.dashboard();
  const todo = html.split('<section class="card action-queue"')[1].split("</section>")[0];
  assert.match(todo, /<span class="pill">Reminder<\/span><div class="next-action">CO-1 has work on a job record and needs a signature\.<\/div>/);
  assert.match(todo, /GC has no phone on file\./);
  assert.match(todo, /data-action="reminder-open" data-note="n3" data-path="\/jobs\/clients">Open<\/button>/);
  assert.match(todo, /data-action="reminder-read" data-note="n3">Mark read<\/button>/);
  assert.doesNotMatch(todo, /Follow up, Nov 9/, "Oak's own row covers its dates");
  assert.doesNotMatch(todo, /Read already/);
  dom.answers = [];
  await automations.loadReminders(true);
});

test("Results is a fold on the Dashboard once a pay app has gone out", () => {  // item 19
  page.setMe({ id: "u1", name: "Pat", company_id: "co-r", role: "owner", onboarding_dismissed: true });
  const j = { id: "a", name: "Oak", code: "OAK", payer_id: "p1", closed: false, payments: [], change_orders: [], money: {},
    invoices: [{ id: "i1", number: "PA-1", status: "sent", sent_at: "2026-10-01T00:00:00Z", amount_cents: 100 }] };
  core.setState({ ...fresh(), payers: [{ id: "p1", name: "GC" }], jobs: [j] });
  const html = reg.views.dashboard();
  assert.match(html, /<details class="card dash-results" id="results" data-more="dash-results"><summary><strong>Results<\/strong>/);
  core.setState(fresh());
  assert.doesNotMatch(reg.views.dashboard(), /id="results"/);
});

test("Pay apps has a Payments filter that lists the payments received", () => {  // item 19
  page.setMe({ id: "u1", name: "Pat", company_id: "co-r", role: "owner" });
  const j = { id: "a", name: "Oak", code: "OAK", payer_id: "p1", closed: false, change_orders: [], money: {}, blockers: [],
    invoices: [{ id: "i1", number: "PA-1", status: "paid", sent_at: "2026-10-01T00:00:00Z", amount_cents: 10000 }],
    payments: [{ id: "pay1", invoice_id: "i1", amount_cents: 10000, reference: "CHK-9", matched_at: "2026-10-09T00:00:00Z" }] };
  core.setState({ ...fresh(), payers: [{ id: "p1", name: "GC" }], jobs: [j] });
  assert.match(reg.views.payapps(), /data-action="payapp-filter" data-filter="payments" data-anchor="payments" aria-pressed="false">Payments</);
  reg.actions["payapp-filter"]({ dataset: { filter: "payments" } });
  const list = reg.views.payapps();
  assert.match(list, /<td>CHK-9<\/td>/);
  assert.match(list, /<th>Reference<\/th>/);
  reg.actions["payapp-filter"]({ dataset: { filter: "all" } });
});

test("Quantities import sits on the job's schedule card; Import has no Quantities done or Schedule of values card", () => {  // item 19
  page.setMe({ id: "u1", name: "Pat", company_id: "co-r", role: "owner" });
  const sov = { present: true, editable: false, auto: false, lines: [], totals: { scheduled_cents: 0, to_date_cents: 0, op_scheduled_cents: 0, op_to_date_cents: 0 }, op_billed_cents: 0 };
  const j = { id: "a", name: "Oak", code: "OAK", payer_id: "p1", closed: false, invoices: [], payments: [], change_orders: [], money: {}, sov_view: sov };
  core.setState({ ...fresh(), payers: [{ id: "p1", name: "GC" }], jobs: [j, { ...j, id: "b", name: "Elm", code: "ELM", sov_view: { present: false, editable: true } }] });
  const imp = reg.views.import();
  assert.doesNotMatch(imp, /id="qty-import"|id="sov-import"/);
  return import("../../static/js/views/job-sov.js").then((m) => {
    const card = m.sovCardHtml(j);
    assert.match(card, /data-action="qty-open" data-id="a">Import quantities<\/button>/);
    page.formOpen.qtyimport = "a";
    const open = m.sovCardHtml(j);
    assert.match(open, /<input type="hidden" id="qty-import-job" name="job" data-field="job"[^>]* value="a">/);
    assert.match(open, /data-action="qty-read"/);
    page.formOpen.qtyimport = false;
  });
});
