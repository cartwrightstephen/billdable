// Today: the overview with what needs you and the setup checklist, Results, Calendar and the
// Daily record.
import { esc, unesc, escDeep, money, toDate, fmtDate } from "../format.js";
import { isBlocked, nextStep, statusPill, defaultJobId } from "../jobs.js";
import { pathFor } from "../routes.js";
import { readJson, setNetBanner, postJson } from "../net.js";
import { worstKind, followUpEvent } from "../calendar.js";
import { todayQueue, queueHtml, anyPayAppSent, billedThisMonth } from "../today.js";
import { termText } from "../terms.js";
import { state, me, currentView, formOpen, setMe, queueExpanded, setQueueExpanded } from "../state.js";
import { post, toast, render, DEMO_COMPANY_ID, emptyState, nav } from "../app-core.js";
import { qboStatus } from "./money.js";
import { newAddressButton } from "./inbox.js";
import { trialBannerHtml } from "./settings.js";

// The Daily record starts on the job last used on this device (R2-07).
const DAILY_JOB_KEY = "duracash-daily-job";
function lastDailyJob() {
  try { return localStorage.getItem(DAILY_JOB_KEY) || ""; } catch (_) { return ""; }
}
function rememberDailyJob(id) {
  try { localStorage.setItem(DAILY_JOB_KEY, unesc(id)); } catch (_) { /* private window: the only open job still comes first */ }
}
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

// Hide the setup checklist, or show it again (from Help). It is kept on the person's own
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
  try {
    const res = await fetch(`/api/metrics?period=${period}`);
    if (!res.ok) return;
    metricsData = { period, data: escDeep(await readJson(res)) };
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
    { text: termText("Create your first job: contract amount, customer and where pay apps go."), done: jobs.length > 0,
      label: "New job", attrs: `data-action="onb-newjob"`, always: true },
    { text: "Put the contract and paperwork on its record: drop files on the job, or bring emails and texts in through the Inbox.", done: jobs.some(hasPaperwork),
      label: "Add files", attrs: goJob(hasPaperwork, ".dropzone"), needsJob: true },
    { text: "Set up a schedule of values to bill line by line (paste it from your estimate).", optional: true, done: jobs.some(j => j.sov_view && j.sov_view.present),
      label: "Open the job", attrs: goJob(j => !(j.sov_view && j.sov_view.editable) || j.sov_view.present, "#sov-card"), needsJob: true },
    { text: termText("Record progress, or mark the job done. Duracash drafts the pay app and lists the paperwork the customer needs."), done: invoices.length > 0,
      label: "Open the job", attrs: goJob(billed, ".next-card"), needsJob: true },
    { text: "Add the missing paperwork, then Approve & send.", done: invoices.some(i => i.sent_at),
      label: draft ? "Review & send" : "Open the job",
      attrs: draft ? `data-action="open-payapp" data-job="${draftJob.id}" data-inv="${draft.id}" data-focus=".send-check"` : goJob(billed, ".next-card"), needsJob: true },
    { text: "Already been paid on some jobs? Import the payments from QuickBooks.", optional: true, done: jobs.some(j => (j.payments || []).length),
      label: "Import payments", attrs: `data-action="onb-go" data-view="payments" data-focus="#qbo-csv"`, needsJob: true },
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
    <p class="sub">${done} of ${required.length} steps done. Steps marked optional don't count. You can bring this back from Help.</p>
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
    const queue = todayQueue(state);
    const hero = t.unbilled_cents > 0 ? "unbilled" : t.outstanding_cents > 0 ? "outstanding" : "";
    const sortHead = (key, label) => `<th data-action="sort" data-sort="${key}" aria-sort="${dashSort === key ? (dashSortDir > 0 ? "ascending" : "descending") : "none"}">${label} ${dashSort === key ? (dashSortDir > 0 ? "↑" : "↓") : ""}</th>`;
    const stage = (key, label) => `<button class="stage ${dashFilter === key ? "active" : ""}" data-action="filter" data-filter="${key}" aria-pressed="${dashFilter === key}">${label}</button>`;
    return `
      <h1>Today</h1>
      ${trialBannerHtml()}
      ${onboardingHtml()}
      ${!state.jobs.length && !state.closed_jobs_total && (me.onboarding_dismissed || isDemo) ? emptyState("No jobs yet", "Create the first job. Its record holds notes and files until a pay app needs them.", `<button class="btn" data-action="onb-newjob">New job</button>`) : ""}
      ${isDemo && !helpDismissed && state.jobs.length ? `
        <div class="help-card">
          <button class="help-dismiss" data-action="dismiss-help" aria-label="Hide this tip">×</button>
          <strong>Try the demo:</strong> open a job, add its missing paperwork, Review & send, record the payment, send the waiver, then finish the job.
        </div>` : ""}
      ${state.jobs.length ? `
      <section class="card action-queue" aria-labelledby="needs-action">
        <h2 id="needs-action">Needs you${queue.length ? ` <span class="count-badge">${queue.length}</span>` : ""}</h2>
        ${queueHtml(queue, { expanded: queueExpanded, anySent: anyPayAppSent(state) })}
      </section>` : ""}
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
            <th>Customer</th>
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
      </div>` : emptyState("No jobs in this view", "Pick another filter above.")}` : ""}
    `;
  },
  results() {
    const f = (key, label) => `<button class="${metricsPeriod === key ? "btn" : "btn secondary"} small" data-action="metrics-period" data-period="${key}" aria-pressed="${metricsPeriod === key}">${label}</button>`;
    const head = `<h1>Results</h1>
      <p class="sub">${termText("What Duracash recorded for your company, counted from your own pay apps and payments. Nothing here is estimated.")}</p>
      <div class="filter-bar" role="group" aria-label="Period">${f("90", "Last 90 days")}${f("all", "All time")}
        <button class="btn small secondary" data-action="metrics-email">Email me last month's summary</button></div>`;
    if (!metricsData || metricsData.period !== metricsPeriod) return head + `<p class="sub">Loading…</p>`;
    const m = metricsData.data;
    const days = (v) => v === null || v === undefined ? "—" : `${v} day${v === 1 ? "" : "s"}`;
    return head + `
      <div class="metrics">
        <div class="metric"><div class="k">${termText("Work done to pay app sent")}</div><div class="v">${days(m.done_to_sent_median_days)}</div><div class="sub row-sub">median, ${m.done_to_sent_jobs} job${m.done_to_sent_jobs === 1 ? "" : "s"}</div></div>
        <div class="metric"><div class="k">${termText("Pay app sent to paid")}</div><div class="v">${days(m.sent_to_paid_median_days)}</div><div class="sub row-sub">median; average ${days(m.sent_to_paid_average_days)}; ${m.pay_apps_paid} paid</div></div>
        <div class="metric"><div class="k">${termText("Follow-ups per pay app")}</div><div class="v">${m.follow_ups_per_pay_app ?? "—"}</div><div class="sub row-sub">${m.follow_ups} follow-up${m.follow_ups === 1 ? "" : "s"}, ${m.pay_apps_sent} ${termText("pay apps sent")}</div></div>
        <div class="metric"><div class="k">Sent back by the customer</div><div class="v">${m.sent_back}</div><div class="sub row-sub">reasons saved to customer settings and draft changes</div></div>
      </div>
      <div class="metrics">
        <div class="metric"><div class="k">Billed</div><div class="v">${money(m.billed_cents)}</div><div class="sub row-sub">${m.pay_apps_sent} ${termText("pay apps")}</div></div>
        <div class="metric"><div class="k">Paid</div><div class="v">${money(m.paid_cents)}</div><div class="sub row-sub">${m.pay_apps_paid} ${termText("pay apps")}</div></div>
        <div class="metric"><div class="k">Change orders billed</div><div class="v">${money(m.change_orders_billed_cents)}</div><div class="sub row-sub">signed, all time</div></div>
        <div class="metric"><div class="k">Held-back money received</div><div class="v">${money(m.retainage_collected_cents)}</div><div class="sub row-sub">all time</div></div>
      </div>
      <div class="card"><h2>Done but not billed</h2>
        ${m.waiting_to_bill.length ? m.waiting_to_bill.map(w => `<div class="list-item row-link"><a href="${pathFor("job", w.job_id)}" data-action="open-job" data-id="${w.job_id}">${w.job}</a><span>${days(w.days)} since done</span></div>`).join("") : `<p class="sub">Every finished job has a pay app out.</p>`}
      </div>
      ${m.by_payer.length ? `<div class="table-wrap"><table>
        <tr><th>Customer</th><th>Average days from sent to paid</th><th>Pay apps paid</th></tr>
        ${m.by_payer.map(r => `<tr><td>${r.payer}</td><td class="num">${r.average_days}</td><td class="num">${r.paid}</td></tr>`).join("")}
      </table></div>` : ""}
    `;
  },
  daily() {
    const openJobs = state.jobs.filter(j => !j.closed);
    const pick = defaultJobId(openJobs, esc(lastDailyJob()));
    const records = state.daily_records || [];
    return `
      <h1>Daily record</h1>
      <p class="sub">What happened on site today, saved on the job record. ${termText("Add it to a pay app's paperwork later.")}</p>
      <div class="card">
        <h2>New record</h2>
        ${openJobs.length ? `<div class="form-grid">
          <div><label>Job</label><select id="daily-job"><option value="">Pick a job…</option>${openJobs.map(j => `<option value="${j.id}"${j.id === pick ? " selected" : ""}>${j.name}</option>`).join("")}</select></div>
          <div class="span-2"><label>What happened today</label><textarea id="daily-notes" rows="3" placeholder="Framing done on the east wall, 4 crew, inspector booked for Thursday"></textarea></div>
        </div>
        <button class="btn" data-action="daily-save">Save to job record</button>` : `<p class="sub">Create a job first; daily records are saved on a job.</p>`}
      </div>
      <div class="card">
        <h2>Recent records</h2>
        ${records.length ? records.slice().reverse().map(r => {
          const j = state.jobs.find(x => x.id === r.job_id);
          return `<div class="list-item"><strong>${fmtDate(r.at)}</strong> · ${j ? `<a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a>` : "Deleted job"}<div>${r.notes}</div></div>`;
        }).join("") : `<p class="sub">No daily records yet.</p>`}
      </div>
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
    const thisWeek = Object.entries(eventsByDay).filter(([d]) => {
      const diff = (toDate(d) - toDate(todayKey)) / 86400000;
      return diff >= -1 && diff <= 7;
    }).sort(([a], [b]) => (a < b ? -1 : 1)).flatMap(([d, evs]) => evs.map(e => ({ ...e, date: d })));
    const selected = calSelectedDay ? (eventsByDay[calSelectedDay] || []) : [];
    const dayCard = calSelectedDay ? `
      <div class="card day-detail" id="day-detail">
        <div class="top"><h2>${fmtDate(calSelectedDay)}</h2><button class="btn small secondary" data-action="cal-day" data-day="">Close</button></div>
        ${selected.length ? selected.map(e => `<div class="list-item">${e.label} ${e.jobId ? `<button class="btn small secondary" data-action="open-job" data-id="${e.jobId}">Open job</button>` : ""}</div>`).join("") : `<p class="sub">Nothing on this day.</p>`}
      </div>` : "";
    return `
      <h1>Calendar</h1>
      <p class="sub">Follow-ups, expected approvals, payments, and closeouts.</p>
      <p class="notice" id="cal-not-legal">These are your contract and follow-up dates, not legal deadlines. Check lien and prompt-pay deadlines with your attorney.</p>
      <div class="filter-bar">
        <label for="cal-filter" class="inline-label">Job</label>
        <select id="cal-filter" data-filter-for="calendar">
          <option value="all">All jobs</option>
          ${state.jobs.map(j => `<option value="${j.id}" ${calJobFilter === j.id ? "selected" : ""}>${j.name}</option>`).join("")}
        </select>
      </div>
      ${thisWeek.length ? `<h2>This week</h2><div class="due-strip week-list">${thisWeek.slice(0, 8).map(e => `<button class="due-item ${e.kind === "overdue" ? "overdue" : ""}" data-action="cal-day" data-day="${e.date}">${fmtDate(e.date).replace(/, \d{4}$/, "")} · ${e.label}</button>`).join("")}</div>` : ""}
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
        <p><a class="btn" id="cal-webcal" href="/today/calendar" hidden>Add to my phone's calendar</a></p>
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
  "metrics-email": async () => {
    const r = await postJson("/api/metrics/summary-email", {});
    toast(r.ok ? r.data.message : (r.data.error || "That didn't work."));
  },
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
  "daily-save": async () => {
    const notes = document.querySelector("#daily-notes")?.value || "";
    const job = document.querySelector("#daily-job")?.value;
    if (!job) { toast("Pick a job."); document.querySelector("#daily-job")?.focus(); return; }
    if (!notes.trim()) { toast("Write what happened today."); document.querySelector("#daily-notes")?.focus(); return; }
    const r = await post(`/api/jobs/${job}/daily-record`, { notes });
    if (r && r.ok) rememberDailyJob(job);
  },
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
  "dismiss-help": () => { helpDismissed = true; try { localStorage.setItem("duracash-help-dismissed", "1"); } catch (_) { /* hidden for this visit */ } render(); },
  "filter": (el) => { dashFilter = el.dataset.filter; render(); },
  "sort": (el) => {
    if (dashSort === el.dataset.sort) dashSortDir *= -1;
    else { dashSort = el.dataset.sort; dashSortDir = 1; }
    render();
  },
};
