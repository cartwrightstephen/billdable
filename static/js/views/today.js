// Dashboard: the overview with the To Do list and the setup checklist, Results, and Calendar
// with its Tasks block. The Daily record is in daily.js.
import { unesc, esc, escDeep, money, toDate, fmtDate } from "../format.js";
import { isBlocked, nextStep, statusPill } from "../jobs.js";
import { pathFor } from "../routes.js";
import { readJson, setNetBanner, postJson } from "../net.js";
import { TODO_PERIODS, TASK_PERIODS, BALL_WORDS, localDay, periodRange, todoItems, itemsInPeriod, assignees } from "../tasks.js";
import { field, formValues, refuseForm } from "../forms.js";
import { worstKind, followUpEvent } from "../calendar.js";
import { todoHtml, anyPayAppSent, billedThisMonth } from "../today.js";
import { termText } from "../terms.js";
import { state, me, currentView, formOpen, setMe, queueExpanded, setQueueExpanded, onSessionEnd, teamData, setTeamData } from "../state.js";
import { toast, render, DEMO_COMPANY_ID, emptyState, nav, post } from "../app-core.js";
import { qboStatus } from "./payments.js";
import { newAddressButton } from "./inbox.js";

export let metricsData = null;   // {period, data} from /api/metrics
export let metricsPeriod = "90";
let calYear = new Date().getFullYear();
let calMonth = new Date().getMonth();
let calSelectedDay = null;
let helpDismissed = (() => { try { return localStorage.getItem("duracash-help-dismissed") === "1"; } catch (_) { return false; } })();
let dashFilter = "all";
let dashSort = "name";
let dashSortDir = 1;
let calJobFilter = "all";
export function setCalJobFilter(value) { calJobFilter = value; }

// The To Do list's filters (T3): Mine or Everyone, and a period (This week first); the Calendar's
// Tasks block has its own period (T6). Both use static/js/tasks.js, so they agree.
let todoWho = "everyone";
let todoPeriod = "week";
const todoRange = { from: "", to: "" };
let tasksPeriod = "week";
const tasksRange = { from: "", to: "" };
let teamAsked = false;

// Signed out: Results' figures, the calendar's job filter and the team are that company's.
onSessionEnd(() => { metricsData = null; calJobFilter = "all"; calSelectedDay = null; teamAsked = false; todoWho = "everyone"; });

// The team, for the assignee selects: asked for once, when a list that needs it is drawn.
async function loadTeamOnce() {
  if (teamAsked || teamData) return;
  teamAsked = true;
  try {
    const res = await fetch("/api/team");
    if (!res.ok) return;
    setTeamData(escDeep(await readJson(res)));
  } catch (_) { setNetBanner(true); return; }
  if (currentView === "dashboard" || currentView === "calendar") render();
}

const periodButtons = (periods, current, action) => periods.map(([k, label]) =>
  `<button class="${current === k ? "btn" : "btn secondary"} small" data-action="${action}" data-period="${k}" aria-pressed="${current === k}">${label}</button>`).join("");
const rangeInputs = (which, range) => `<span class="period-range">
    <label class="inline-label" for="${which}-from">From</label><input type="date" id="${which}-from" data-period-from="${which}" value="${esc(range.from)}">
    <label class="inline-label" for="${which}-to">To</label><input type="date" id="${which}-to" data-period-to="${which}" value="${esc(range.to)}">
  </span>`;

// Add a task by hand (G5): what needs doing, and optionally the job, who, when and whose court.
function taskFormHtml(people) {
  if (!formOpen.task) return "";
  const open = state.jobs.filter(j => !j.closed);
  return `<div class="card task-form" data-form-key="task">
    <div class="form-grid">
      ${field({ form: "task", name: "title", id: "task-title", label: "What needs doing", required: true, wide: true, placeholder: "Order the tile for the Oak Street bath" })}
      ${field({ form: "task", name: "job_id", id: "task-job", label: "Job", options: [["", "No job"], ...open.map(j => [j.id, unesc(j.name)])] })}
      ${field({ form: "task", name: "assignee_id", id: "task-assignee", label: "Assignee", options: [["", "Unassigned"], ...people.map(p => [p.id, unesc(p.name)])], value: me ? me.id : "" })}
      ${field({ form: "task", name: "due", id: "task-due", label: "Due", type: "date" })}
      ${field({ form: "task", name: "ball_in_court", id: "task-ball", label: "Ball in court", options: Object.entries(BALL_WORDS) })}
    </div>
    <div class="form-inline"><button class="btn" data-action="task-add">Add task</button><button class="btn secondary" data-action="task-cancel">Cancel</button></div>
  </div>`;
}

// The To Do card (T3).
function todoCardHtml() {
  loadTeamOnce();
  const today = localDay(new Date());
  const everyone = todoItems(state);
  const items = todoWho === "mine" ? todoItems(state, { who: "mine", meId: me ? me.id : "" }) : everyone;
  const shown = itemsInPeriod(items, periodRange(todoPeriod, new Date(), todoRange.from, todoRange.to), today);
  const people = assignees(teamData, me, everyone);
  const who = (k, label) => `<button class="${todoWho === k ? "btn" : "btn secondary"} small" data-action="todo-who" data-who="${k}" aria-pressed="${todoWho === k}">${label}</button>`;
  return `<section class="card action-queue" aria-labelledby="needs-action">
        <div class="top"><h2 id="needs-action">To Do${shown.length ? ` <span class="count-badge">${shown.length}</span>` : ""}</h2>
          <button class="btn small secondary" data-action="task-new">Add task</button></div>
        <div class="filter-bar todo-filters">
          <span role="group" aria-label="Whose">${who("mine", "Mine")}${who("everyone", "Everyone")}</span>
          <span role="group" aria-label="Due">${periodButtons(TODO_PERIODS, todoPeriod, "todo-period")}</span>
          ${todoPeriod === "range" ? rangeInputs("todo", todoRange) : ""}
        </div>
        ${taskFormHtml(people)}
        ${todoHtml(shown, { expanded: queueExpanded, anySent: anyPayAppSent(state), total: everyone.length, people, today })}
      </section>`;
}

// The Calendar's Tasks block (T6): the To Do items and the calendar's dates in a period, soonest
// first. Each opens its day; an item on a job opens the job too.
function tasksBlockHtml(eventsByDay) {
  loadTeamOnce();
  const today = localDay(new Date());
  const range = periodRange(tasksPeriod, new Date(), tasksRange.from, tasksRange.to);
  const people = assignees(teamData, me, todoItems(state));
  const nameOf = (i) => i.assignee_id ? (people.find(p => p.id === i.assignee_id) || {}).name || i.assignee_name : "";
  const items = itemsInPeriod(todoItems(state), range, today)
    .filter(i => calJobFilter === "all" || i.job_id === calJobFilter)
    .map(i => ({ date: i.due, label: i.source === "row" ? i.row.label : i.title, job: i.job, jobId: i.job_id, who: nameOf(i), overdue: !!i.due && i.due < today }));
  const events = Object.entries(eventsByDay)
    .filter(([d]) => (!range.from || d >= range.from) && (!range.to || d <= range.to))
    .flatMap(([d, evs]) => evs.map(e => ({ date: d, label: e.label, jobId: e.jobId, job: "", who: "", overdue: e.kind === "overdue" })));
  const all = items.concat(events).sort((a, b) => (a.date || "9999") < (b.date || "9999") ? -1 : (a.date || "9999") > (b.date || "9999") ? 1 : 0);
  return `<section class="tasks-block" aria-labelledby="tasks-head">
      <div class="top"><h2 id="tasks-head">Tasks</h2><button class="btn small secondary" data-action="task-new">Add task</button></div>
      <div class="filter-bar" role="group" aria-label="Period">${periodButtons(TASK_PERIODS, tasksPeriod, "tasks-period")}${tasksPeriod === "custom" ? rangeInputs("tasks", tasksRange) : ""}</div>
      ${taskFormHtml(people)}
      ${all.length ? `<div class="due-strip week-list">${all.map(e => {
        const text = `${e.date ? fmtDate(e.date).replace(/, \d{4}$/, "") : "No date"} · ${e.label}${e.job ? ` · ${e.job}` : ""}${e.who ? ` · ${e.who}` : ""}`;
        const opens = e.date ? `data-action="cal-day" data-day="${e.date}"` : e.jobId ? `data-action="open-job" data-id="${e.jobId}"` : "";
        return opens ? `<button class="due-item${e.overdue ? " overdue" : ""}" ${opens}>${text}</button>` : `<span class="due-item">${text}</span>`;
      }).join("")}</div>` : `<p class="sub">Nothing in this period.</p>`}
    </section>`;
}

// Saves an assignee picked in a To Do row (G5): a row the jobs make by its key, a task by its id.
async function assignFrom(sel) {
  const value = sel.value;
  const key = sel.dataset.key;
  const r = sel.dataset.todoAssign === "task"
    ? await post(`/api/tasks/${encodeURIComponent(key)}`, { assignee_id: value }, { quiet: true, anchor: sel })
    : await post("/api/tasks/assign", { key, assignee_id: value }, { quiet: true, anchor: sel });
  if (r && r.ok) toast(value ? "Assigned." : "Unassigned.");
}

document.addEventListener("change", (e) => {
  const el = e.target;
  if (!el || !el.dataset) return;
  if (el.dataset.todoAssign) { assignFrom(el); return; }
  const which = el.dataset.periodFrom || el.dataset.periodTo;
  if (!which) return;
  const range = which === "todo" ? todoRange : tasksRange;
  range[el.dataset.periodFrom ? "from" : "to"] = el.value;
  setQueueExpanded(false);
  render();
});

// Hide the setup checklist, or show it again (from My account). It is kept on the person's own
// account, so a teammate's choice doesn't change it.
async function setChecklist(show) {
  const r = await postJson("/api/onboarding/dismiss", show ? { show: true } : {});
  if (!r.ok || !r.data.user) { toast(r.data.error || "That didn't work."); return false; }
  setMe(escDeep(r.data.user));
  render();
  return true;
}

export async function loadMetrics() {
  const period = metricsPeriod;
  const asked = me;
  try {
    const res = await fetch(`/api/metrics?period=${period}`);
    if (!res.ok) return;
    const data = await readJson(res);
    if (me !== asked) return;   // signed out while this was asked: the last company's figures
    metricsData = { period, data: escDeep(data) };
  } catch (_) { setNetBanner(true); return; }
  if (currentView === "results") render();
}

// The setup checklist on Today (R2-03). Each step's button goes to where that step is done:
// the first job still lacking it, scrolled to the part to use (data-focus). Hidden per person.
function onboardingHtml() {
  if (!state || (me && me.onboarding_dismissed) || (me && me.company_id === DEMO_COMPANY_ID)) return "";
  const jobs = state.jobs;
  const open = jobs.filter(j => !j.closed);
  const invoices = jobs.flatMap(j => j.invoices);
  // A note typed in doesn't count as paperwork: a file, or something brought in through the Inbox, does.
  const hasPaperwork = (j) => (j.records || []).some(r => (r.file_ids || []).length || r.source);
  const lacking = (has) => open.find(j => !has(j)) || open[0];
  const goJob = (has, focus) => { const j = lacking(has); return j ? `data-action="onb-go" data-view="job" data-id="${j.id}" data-focus="${focus}"` : ""; };
  const billed = (j) => j.invoices.some(i => i.status !== "void" && i.status !== "replaced");
  const draftJob = open.find(j => j.invoices.some(i => ["draft", "awaiting_approval"].includes(i.status)));
  const draft = draftJob && draftJob.invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
  const steps = [
    { text: termText("Add your company address, phone and licence number (they print on every pay app)."), done: !!String(state.company.address || "").trim(),
      label: "Company details", attrs: `data-action="onb-go" data-view="company" data-focus="#co-address"`, always: true },
    { text: termText("Create your first job: contract amount, client and where pay apps go."), done: jobs.length > 0,
      label: "New job", attrs: `data-action="onb-newjob"`, always: true },
    { text: "Put the contract and paperwork on its record: drop files on the job, or bring emails and texts in through the Inbox.", done: jobs.some(hasPaperwork),
      label: "Add files", attrs: goJob(hasPaperwork, ".dropzone"), needsJob: true },
    { text: "Set up a schedule of values to bill line by line (paste it from your estimate).", optional: true, done: jobs.some(j => j.sov_view && j.sov_view.present),
      label: "Open the job", attrs: goJob(j => !(j.sov_view && j.sov_view.editable) || j.sov_view.present, "#sov-card"), needsJob: true },
    { text: termText("Record progress, or mark the job done. Duracash drafts the pay app and lists the paperwork the client needs."), done: invoices.length > 0,
      label: "Open the job", attrs: goJob(billed, ".next-card"), needsJob: true },
    { text: "Add the missing paperwork, then Approve & send.", done: invoices.some(i => i.sent_at),
      label: draft ? "Review & send" : "Open the job",
      attrs: draft ? `data-action="open-payapp" data-job="${draftJob.id}" data-inv="${draft.id}" data-focus=".send-check"` : goJob(billed, ".next-card"), needsJob: true },
    { text: "Already been paid on some jobs? Import the payments from QuickBooks.", optional: true, done: jobs.some(j => (j.payments || []).length),
      label: "Import payments", attrs: `data-action="onb-go" data-view="import" data-focus="#qbo-csv"`, needsJob: true },
    { text: "Connect QuickBooks.", optional: true, done: !!(qboStatus && qboStatus.connected), hidden: !!(qboStatus && !qboStatus.loading && !qboStatus.configured),
      label: "QuickBooks", attrs: `data-action="onb-go" data-view="payments"` },
  ].filter(st => !st.hidden);
  const required = steps.filter(st => !st.optional);
  // Its job is the first pay app: once one has gone out, it is done, whatever else is left.
  if (required.every(st => st.done) || anyPayAppSent(state)) return "";
  const done = required.filter(st => st.done).length;
  const next = steps.find(st => !st.optional && !st.done);
  const button = (st) => st.done || !st.attrs || (st.needsJob && !jobs.length) ? ""
    : ` <button class="btn small${st === next ? "" : " secondary"}" ${st.attrs}>${st.label}</button>`;
  return `<div class="card onboarding">
    <button class="help-dismiss" data-action="onb-dismiss" title="Hide this" aria-label="Hide the setup checklist">×</button>
    <h2>${termText("Get your first pay app out")}</h2>
    <p class="sub">${done} of ${required.length} steps done. Steps marked optional don't count. You can bring this back from Settings, My account.</p>
    <ol class="onb-steps">${steps.map(st => `<li class="${st.done ? "done" : st === next ? "next" : ""}">${st.done ? "✓ " : ""}${st.optional ? "Optional: " : ""}${st.text}${st === next || st.optional || st.always ? button(st) : ""}</li>`).join("")}</ol>
  </div>`;
}

function filteredJobs() {
  const now = new Date();
  const thisMonth = (d) => d && new Date(d).getMonth() === now.getMonth() && new Date(d).getFullYear() === now.getFullYear();
  let jobs = [...state.jobs];
  if (dashFilter === "all") {
    // all open + completed this month
    jobs = jobs.filter(j => !j.closed || thisMonth(j.completed_at) || thisMonth(j.closed_at));
  } else if (dashFilter === "complete") {
    jobs = jobs.filter(j => j.work_status === "complete" && thisMonth(j.completed_at));
  } else if (dashFilter === "sent") {
    jobs = jobs.filter(j => j.invoices.some(i => ["sent", "approved", "partial"].includes(i.status)));
  } else if (dashFilter === "blocked") jobs = jobs.filter(j => isBlocked(j) && !j.closed);
  else if (dashFilter === "draft") jobs = jobs.filter(j => j.invoices.some(i => ["draft", "awaiting_approval"].includes(i.status)));
  else if (dashFilter === "waiver") jobs = jobs.filter(j => j.waiver_due && !j.waiver_sent);
  const key = dashSort;
  jobs.sort((a, b) => {
    let va, vb;
    if (key === "name") { va = a.name; vb = b.name; }
    else if (key === "outstanding") { va = a.money.outstanding_cents; vb = b.money.outstanding_cents; }
    else if (key === "unbilled") { va = a.money.unbilled_completed_cents; vb = b.money.unbilled_completed_cents; }
    else if (key === "contract") { va = a.money.contract_total_cents; vb = b.money.contract_total_cents; }
    else if (key === "billed") { va = a.money.billed_cents; vb = b.money.billed_cents; }
    else if (key === "collected") { va = a.money.collected_cents; vb = b.money.collected_cents; }
    else if (key === "retainage") { va = a.money.retainage_open_cents; vb = b.money.retainage_open_cents; }
    else { va = a.name; vb = b.name; }
    if (va < vb) return -1 * dashSortDir;
    if (va > vb) return 1 * dashSortDir;
    return 0;
  });
  return jobs;
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  dashboard() {
    const t = state.totals;
    const jobs = filteredJobs();
    const isDemo = me && me.company_id === DEMO_COMPANY_ID;
    const open = state.jobs.filter(j => !j.closed);
    const hero = t.unbilled_cents > 0 ? "unbilled" : t.outstanding_cents > 0 ? "outstanding" : "";
    const sortHead = (key, label) => `<th data-action="sort" data-sort="${key}" aria-sort="${dashSort === key ? (dashSortDir > 0 ? "ascending" : "descending") : "none"}">${label} ${dashSort === key ? (dashSortDir > 0 ? "↑" : "↓") : ""}</th>`;
    const stage = (key, label) => `<button class="stage ${dashFilter === key ? "active" : ""}" data-action="filter" data-filter="${key}" aria-pressed="${dashFilter === key}">${label}</button>`;
    return `
      <h1>Overview</h1>
      ${onboardingHtml()}
      ${!state.jobs.length && !state.closed_jobs_total && (me.onboarding_dismissed || isDemo) ? emptyState("No jobs yet", "", `<button class="btn" data-action="onb-newjob">New job</button>`) : ""}
      ${isDemo && !helpDismissed && state.jobs.length ? `
        <div class="help-card">
          <button class="help-dismiss" data-action="dismiss-help" aria-label="Hide this tip">×</button>
          <strong>Try the demo:</strong> open a job, add its missing paperwork, Review & send, record the payment, send the waiver, then finish the job.
        </div>` : ""}
      ${state.jobs.length || (state.tasks || []).length ? todoCardHtml() : ""}
      <div class="metrics">
        <div class="metric ${hero === "unbilled" ? "highlight" : ""}"><div class="k">Done, not billed</div><div class="v">${money(t.unbilled_cents)}</div></div>
        <div class="metric ${hero === "outstanding" ? "highlight" : ""}"><div class="k">Outstanding</div><div class="v">${money(t.outstanding_cents)}</div></div>
        <div class="metric"><div class="k">Held back, still owed</div><div class="v">${money(t.retainage_cents)}</div></div>
        <div class="metric"><div class="k">Billed this month</div><div class="v">${money(billedThisMonth(state.jobs))}</div></div>
      </div>
      <p class="sub">${open.length} active job${open.length === 1 ? "" : "s"}${t.blocked_jobs ? ` · ${t.blocked_jobs} can't be sent yet` : ""}</p>
      ${state.jobs.length ? `
      <div class="stages" role="group" aria-label="Show jobs">
        ${stage("all", "Open and recent")}${stage("draft", "Ready to send")}${stage("blocked", "Can't send yet")}${stage("sent", "Unpaid")}${stage("waiver", "Waiver due")}${stage("complete", "Completed this month")}
      </div>
      ${jobs.length ? `<div class="table-wrap">
        <table class="sortable">
          <tr>
            ${sortHead("name", "Job")}
            <th>Client</th>
            <th>Status</th>
            ${sortHead("contract", "Contract total")}
            ${sortHead("billed", "Billed")}
            ${sortHead("outstanding", "Outstanding")}
            ${sortHead("unbilled", "Unbilled")}
            ${sortHead("retainage", "Held back")}
            <th>Next</th>
          </tr>
          ${jobs.map(j => {
            const m = j.money;
            return `<tr>
              <td><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a></td>
              <td>${j.payer.name}</td>
              <td>${statusPill(j)}</td>
              <td class="num">${money(m.contract_total_cents)}</td>
              <td class="num">${money(m.billed_cents)}</td>
              <td class="num">${money(m.outstanding_cents)}</td>
              <td class="num">${money(m.unbilled_completed_cents)}</td>
              <td class="num">${money(m.retainage_open_cents)}</td>
              <td class="next-action">${nextStep(j)}</td>
            </tr>`;
          }).join("")}
        </table>
      </div>` : emptyState("No jobs in this view")}` : ""}
    `;
  },
  results() {
    const f = (key, label) => `<button class="${metricsPeriod === key ? "btn" : "btn secondary"} small" data-action="metrics-period" data-period="${key}" aria-pressed="${metricsPeriod === key}">${label}</button>`;
    const head = `<h1>Results</h1>
      <div class="filter-bar" role="group" aria-label="Period">${f("90", "Last 90 days")}${f("all", "All time")}</div>`;
    if (!metricsData || metricsData.period !== metricsPeriod) return head + `<p class="sub">Loading…</p>`;
    const m = metricsData.data;
    const days = (v) => v === null || v === undefined ? "—" : `${v} day${v === 1 ? "" : "s"}`;
    const num = (v) => v === null || v === undefined ? "—" : String(v);
    return head + `
      <div class="metrics">
        <div class="metric"><div class="k">${termText("Days from Job Completion to Pay App Sent")}</div><div class="v">${num(m.done_to_sent_median_days)}</div><div class="sub row-sub">median, ${m.done_to_sent_jobs} job${m.done_to_sent_jobs === 1 ? "" : "s"}</div></div>
        <div class="metric"><div class="k">${termText("Days from Pay App Sent to Paid")}</div><div class="v">${num(m.sent_to_paid_median_days)}</div><div class="sub row-sub">median; average ${num(m.sent_to_paid_average_days)}; ${m.pay_apps_paid} paid</div></div>
        <div class="metric"><div class="k">${termText("Follow-ups per pay app")}</div><div class="v">${m.follow_ups_per_pay_app ?? "—"}</div><div class="sub row-sub">${m.follow_ups} follow-up${m.follow_ups === 1 ? "" : "s"}, ${m.pay_apps_sent} ${termText("pay apps sent")}</div></div>
        <div class="metric"><div class="k">${termText("Pay app rejections by Clients")}</div><div class="v">${m.sent_back}</div><div class="sub row-sub">of ${m.pay_apps_sent} ${termText("pay apps sent")}</div></div>
      </div>
      <div class="metrics">
        <div class="metric"><div class="k">Billed</div><div class="v">${money(m.billed_cents)}</div><div class="sub row-sub">${m.pay_apps_sent} ${termText("pay apps")}</div></div>
        <div class="metric"><div class="k">Paid</div><div class="v">${money(m.paid_cents)}</div><div class="sub row-sub">${m.pay_apps_paid} ${termText("pay apps")}</div></div>
        <div class="metric"><div class="k">Change orders billed</div><div class="v">${money(m.change_orders_billed_cents)}</div><div class="sub row-sub">signed, all time</div></div>
        <div class="metric"><div class="k">Held-back money received</div><div class="v">${money(m.retainage_collected_cents)}</div><div class="sub row-sub">all time</div></div>
      </div>
      <div class="card"><h2>Jobs completed with outstanding billings</h2>
        ${m.waiting_to_bill.length ? m.waiting_to_bill.map(w => `<div class="list-item row-link"><a href="${pathFor("job", w.job_id)}" data-action="open-job" data-id="${w.job_id}">${w.job}</a><span>${days(w.days)} since done</span></div>`).join("") : `<p class="sub">None.</p>`}
      </div>
      ${m.by_payer.length ? `<div class="table-wrap"><table>
        <tr><th>Client</th><th>Average days from sent to paid</th><th>Pay apps paid</th></tr>
        ${m.by_payer.map(r => `<tr><td>${r.payer}</td><td class="num">${r.average_days}</td><td class="num">${r.paid}</td></tr>`).join("")}
      </table></div>` : ""}
    `;
  },
  calendar() {
    const first = new Date(calYear, calMonth, 1);
    const startDow = first.getDay();
    const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
    const eventsByDay = {};
    const localDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const add = (iso, label, kind = "", jobId = "") => {
      const d = toDate(iso);
      if (!d) return;
      const key = localDay(d);
      (eventsByDay[key] = eventsByDay[key] || []).push({ label, kind, jobId });
    };
    const today = new Date();
    const todayKey = localDay(today);
    state.jobs.filter(j => calJobFilter === "all" || j.id === calJobFilter).forEach(j => {
      if (j.completed_at) add(j.completed_at, `${j.code} work complete`, "paid", j.id);
      // One follow-up per job, on the day Today raises it (followUpEvent).
      const follow = followUpEvent(j, today);
      if (follow) add(follow.day, `${j.code} follow-up`, follow.overdue ? "overdue" : "", j.id);
      j.invoices.forEach(inv => {
        if (inv.sent_at) add(inv.sent_at, `${inv.number} sent`, "", j.id);
        if (inv.status === "sent" && inv.sent_at) {
          const appr = toDate(inv.sent_at);
          appr.setDate(appr.getDate() + (j.payer.usual_approval_days || 14));
          add(localDay(appr), `${j.code} expected approval`, "", j.id);
        }
      });
      j.payments.forEach(p => add(p.matched_at, `${j.code} payment received`, "paid", j.id));
      if (j.waiver_sent_at) add(j.waiver_sent_at, `${j.code} waiver sent`, "waiver", j.id);
      if (j.waiver_due && !j.waiver_sent) add(todayKey, `${j.code} waiver ready`, "waiver", j.id);
      if (j.closed_at) add(j.closed_at, `${j.code} finished`, "paid", j.id);
      const dueDay = Number(unesc(String(j.payer.payapp_due_day || "")));
      if (dueDay && !j.closed && (j.work_status !== "complete" || j.money.unbilled_completed_cents > 0))
        add(localDay(new Date(calYear, calMonth, Math.min(dueDay, daysInMonth))), `${j.code} ${termText("pay app due to")} ${j.payer.name}`, "", j.id);
    });
    const cells = [];
    for (let i = 0; i < startDow; i++) cells.push({ other: true });
    for (let d = 1; d <= daysInMonth; d++) {
      const ds = `${calYear}-${String(calMonth + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
      cells.push({ day: d, ds, events: eventsByDay[ds] || [], today: ds === todayKey });
    }
    while (cells.length % 7) cells.push({ other: true });
    const monthName = first.toLocaleString("en-US", { month: "long" });
    const selected = calSelectedDay ? (eventsByDay[calSelectedDay] || []) : [];
    const dayCard = calSelectedDay ? `
      <div class="card day-detail" id="day-detail">
        <div class="top"><h2>${fmtDate(calSelectedDay)}</h2><button class="btn small secondary" data-action="cal-day" data-day="">Close</button></div>
        ${selected.length ? selected.map(e => `<div class="list-item">${e.label} ${e.jobId ? `<button class="btn small secondary" data-action="open-job" data-id="${e.jobId}">Open job</button>` : ""}</div>`).join("") : `<p class="sub">Nothing on this day.</p>`}
      </div>` : "";
    return `
      <h1>Calendar</h1>
      <p class="notice" id="cal-not-legal">These are your contract and follow-up dates, not legal deadlines. Check lien and prompt-pay deadlines with your attorney.</p>
      <div class="filter-bar">
        <label for="cal-filter" class="inline-label">Job</label>
        <select id="cal-filter" data-filter-for="calendar">
          <option value="all">All jobs</option>
          ${state.jobs.map(j => `<option value="${j.id}" ${calJobFilter === j.id ? "selected" : ""}>${j.name}</option>`).join("")}
        </select>
      </div>
      ${tasksBlockHtml(eventsByDay)}
      ${dayCard}
      <div class="cal-header">
        <div>
          <button class="btn secondary small" data-action="cal-prev" aria-label="Previous month">←</button>
          <strong class="cal-month" aria-live="polite">${monthName} ${calYear}</strong>
          <button class="btn secondary small" data-action="cal-next" aria-label="Next month">→</button>
        </div>
        <button class="btn secondary small" data-action="cal-today">Today</button>
      </div>
      <div class="legend">
        <span><i class="lg-paid"></i> Complete / paid</span>
        <span><i class="lg-waiver"></i> Waiver</span>
        <span><i class="lg-overdue"></i> Overdue</span>
        <span><i class="lg-other"></i> Other</span>
      </div>
      <div class="cal-grid" role="grid" aria-label="${monthName} ${calYear}">
        ${["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map(d => `<div class="cal-dow" role="columnheader">${d}</div>`).join("")}
        ${cells.map(c => c.other
          ? `<div class="cal-day other" aria-hidden="true"></div>`
          : `<button class="cal-day ${c.today ? "today" : ""} ${c.ds === calSelectedDay ? "selected" : ""}" data-action="cal-day" data-day="${c.ds}" aria-label="${fmtDate(c.ds)}${c.events.length ? `, ${c.events.length} item${c.events.length === 1 ? "" : "s"}` : ""}">
               <span class="num">${c.day}</span>
               ${(() => {
                 const evs = c.events;
                 if (evs.length <= 3) return evs.map(e => `<span class="cal-event ${e.kind}">${e.label}</span>`).join("");
                 const cats = {};
                 evs.forEach(e => { const k = e.kind || "other"; cats[k] = (cats[k] || 0) + 1; });
                 const labels = { paid: "complete / paid", waiver: "waivers", overdue: "overdue", other: "other" };
                 return Object.entries(cats).map(([k, n]) => `<span class="cal-event ${k}">${n} ${labels[k] || k}</span>`).join("");
               })()}
               ${c.events.length ? `<span class="cal-dot ${worstKind(c.events)}" aria-hidden="true">${c.events.length}</span>` : ""}
             </button>`
        ).join("")}
      </div>
      <details class="card subscribe">
        <summary><strong>Put these dates on your phone (Google Calendar, Apple, Outlook)</strong></summary>
        <p><a class="btn" id="cal-webcal" href="/dashboard/calendar" hidden>Add to my phone's calendar</a></p>
        <p class="sub">On an iPhone or a Mac that button adds them in one tap. Or copy this private address. In Google Calendar on a computer: Other calendars, +, From URL, paste, Add calendar. It then shows on your phone. Calendar apps refresh subscribed calendars on their own schedule, often every few hours, so new dates are not instant.</p>
        <div class="copy-row"><code id="cal-url">Loading…</code><button class="btn small secondary" data-action="copy" data-target="cal-url">Copy</button>${newAddressButton("calendar")}</div>
        <p class="sub">Anyone with this address can see the dates and job names. ${me && me.role === "owner" ? "Make a new address if it was shared by mistake." : "If it was shared by mistake, an owner can make a new one."}</p>
      </details>
    `;
  },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in app.js.
export const actions = {
  "metrics-period": (el) => { metricsPeriod = el.dataset.period; metricsData = null; render(); },
  "onb-newjob": () => { nav("jobs"); formOpen.newjob = true; render(); document.getElementById("nj-name")?.focus(); },
  "onb-job": () => { const j = state.jobs.find(x => !x.closed) || state.jobs[0]; if (j) nav("job", j.id); },
  // A setup step: open its page and bring the part to use into view.
  "onb-go": (el) => {
    nav(el.dataset.view, el.dataset.id || null);
    const sel = el.dataset.focus;
    // nav() has drawn the page, so the part is there now: focus it straight away rather than a
    // frame later, when anything typed in between would have gone to the page.
    const target = sel && document.querySelector(sel);
    if (!target) return;
    // A box folded away (the payments import) opens first, or it can't be seen or focused.
    const folded = target.closest("details");
    if (folded) folded.open = true;
    target.scrollIntoView({ block: "center" });
    if (target.matches("input, select, textarea, [tabindex]")) target.focus({ preventScroll: true });
  },
  "onb-dismiss": () => setChecklist(false),
  "onb-show": async () => { if (await setChecklist(true)) nav("dashboard"); },
  "cal-prev": () => { calMonth--; if (calMonth < 0) { calMonth = 11; calYear--; } calSelectedDay = null; render(); },
  "cal-next": () => { calMonth++; if (calMonth > 11) { calMonth = 0; calYear++; } calSelectedDay = null; render(); },
  "cal-today": () => { const n = new Date(); calYear = n.getFullYear(); calMonth = n.getMonth(); calSelectedDay = null; render(); },
  "cal-day": (el) => {
    calSelectedDay = el.dataset.day || null;
    if (calSelectedDay) { const d = toDate(calSelectedDay); calYear = d.getFullYear(); calMonth = d.getMonth(); }
    render();
    document.getElementById("day-detail")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  },
  "queue-more": () => { setQueueExpanded(true); render(); },
  "todo-who": (el) => { todoWho = el.dataset.who; setQueueExpanded(false); render(); },
  "todo-period": (el) => { todoPeriod = el.dataset.period; setQueueExpanded(false); render(); if (todoPeriod === "range") document.getElementById("todo-from")?.focus(); },
  "tasks-period": (el) => { tasksPeriod = el.dataset.period; render(); if (tasksPeriod === "custom") document.getElementById("tasks-from")?.focus(); },
  "task-new": () => { formOpen.task = true; render(); document.getElementById("task-title")?.focus(); },
  "task-cancel": () => { formOpen.task = false; render(); },
  "task-add": async () => {
    const body = formValues("task");
    if (!body.title) { refuseForm("task", { title: "Enter what needs doing." }); return; }
    const r = await post("/api/tasks", body, { formKey: "task", close: "task" });
    if (r && r.ok) toast("Task added.");
  },
  "task-done": async (el, id) => { const r = await post(`/api/tasks/${encodeURIComponent(id)}/done`, { done: true }, { quiet: true }); if (r && r.ok) toast("Done."); },
  "dismiss-help": () => { helpDismissed = true; try { localStorage.setItem("duracash-help-dismissed", "1"); } catch (_) { /* hidden for this visit */ } render(); },
  "filter": (el) => { dashFilter = el.dataset.filter; render(); },
  "sort": (el) => {
    if (dashSort === el.dataset.sort) dashSortDir *= -1;
    else { dashSort = el.dataset.sort; dashSortDir = 1; }
    render();
  },
};
