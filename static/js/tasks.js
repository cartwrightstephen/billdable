// The To Do list and the Calendar's Tasks block (T3, T6, G5) share one row model and one period
// filter, so both lists agree. A row is either one the server works out from the jobs
// (state.today_rows, with key, due, ball_in_court and assignee from domain/tasks.py) or a task a
// person added (state.tasks). Pure functions of the page's state, so node can test them
// (tests/js/tasks.test.mjs).

// The To Do card's periods (T3) and the Calendar's Tasks block's (T6).
export const TODO_PERIODS = [["today", "Today"], ["week", "This week"], ["month", "This month"], ["range", "Date range"]];
export const TASK_PERIODS = [["week", "This week"], ["next", "Next week"], ["month", "This month"], ["custom", "Custom"]];
export const BALL_WORDS = { us: "Us", client: "Client" };

// YYYY-MM-DD of a date in the person's own time zone.
export function localDay(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// The first and last day ({from, to}, YYYY-MM-DD, both included) of a period around `now`. A week
// runs Sunday to Saturday, as the calendar shows it. "range" and "custom" take the dates typed;
// a blank end is open.
export function periodRange(period, now = new Date(), from = "", to = "") {
  const day = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const shift = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
  if (period === "today") return { from: localDay(day), to: localDay(day) };
  if (period === "week" || period === "next") {
    const start = shift(day, -day.getDay() + (period === "next" ? 7 : 0));
    return { from: localDay(start), to: localDay(shift(start, 6)) };
  }
  if (period === "month") return { from: localDay(new Date(day.getFullYear(), day.getMonth(), 1)), to: localDay(new Date(day.getFullYear(), day.getMonth() + 1, 0)) };
  const ok = (v) => /^\d{4}-\d{2}-\d{2}$/.test(String(v || "")) ? String(v) : "";
  return { from: ok(from), to: ok(to) };
}

// Whether an item shows in the period: due inside it, or overdue (always shown until it is done),
// or with no due date at all (a task nobody dated).
export function inPeriod(item, range, today) {
  if (!item.due) return true;
  if (item.due < today && !item.done) return true;
  return (!range.from || item.due >= range.from) && (!range.to || item.due <= range.to);
}

// One list from the server's rows and the open hand-added tasks, most urgent first. A row the
// server sends with no due date is due today, the person's own today (the server's day is UTC). the server's
// order for its rows (rank), a task due today or before just after the paperwork rows (rank
// 3.5), other tasks last; then soonest due. who: "mine" keeps only what is assigned to meId.
export function todoItems(state, { who = "everyone", meId = "" } = {}, today = localDay(new Date())) {
  const jobs = Object.fromEntries((state.jobs || []).map(j => [j.id, j]));
  const rows = (state.today_rows || []).map((r, n) => ({
    source: "row", key: r.key, row: r, title: r.label, job_id: r.job_id, job: r.job, ball: r.ball_in_court || "us",
    assignee_id: r.assignee_id || "", assignee_name: r.assignee_name || "", due: r.due || today, rank: r.rank ?? 5, order: n,
  }));
  const tasks = (state.tasks || []).filter(t => t.status !== "done").map((t, n) => ({
    source: "task", key: t.id, task: t, title: t.title, job_id: t.job_id || "", job: t.job_id && jobs[t.job_id] ? jobs[t.job_id].name : "",
    ball: t.ball_in_court || "us", assignee_id: t.assignee_id || "", assignee_name: t.assignee_name || "", due: t.due || "",
    rank: t.due && t.due <= today ? 3.5 : 7, order: 1000 + n,
  }));
  return rows.concat(tasks)
    .filter(i => who !== "mine" || (meId && i.assignee_id === meId))
    .sort((a, b) => a.rank - b.rank || ((a.due || "9999") < (b.due || "9999") ? -1 : (a.due || "9999") > (b.due || "9999") ? 1 : a.order - b.order));
}

// The items in a period (todoItems, then inPeriod).
export function itemsInPeriod(items, range, today) {
  return items.filter(i => inPeriod(i, range, today));
}

// The people an item can be assigned to: the team (from /api/team), or, before it has loaded,
// the signed-in person; and whoever an item is already assigned to, so the select shows them.
export function assignees(team, me, items = []) {
  const out = (team && team.users ? team.users : me ? [me] : []).map(u => ({ id: u.id, name: u.name || u.email || "" }));
  for (const i of items) if (i.assignee_id && !out.some(p => p.id === i.assignee_id)) out.push({ id: i.assignee_id, name: i.assignee_name || "Someone" });
  return out;
}
