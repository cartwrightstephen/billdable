// The To Do list's and the Calendar Tasks block's shared filter (T3, T6, G5): node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { periodRange, inPeriod, todoItems, itemsInPeriod, assignees, localDay } from "../../static/js/tasks.js";

// Wednesday, October 28, 2026, mid-morning where the person is.
const NOW = new Date(2026, 9, 28, 10, 0);

test("periods: a week runs Sunday to Saturday; next week, the month and a typed range", () => {
  assert.deepEqual(periodRange("today", NOW), { from: "2026-10-28", to: "2026-10-28" });
  assert.deepEqual(periodRange("week", NOW), { from: "2026-10-25", to: "2026-10-31" });
  assert.deepEqual(periodRange("next", NOW), { from: "2026-11-01", to: "2026-11-07" });
  assert.deepEqual(periodRange("month", NOW), { from: "2026-10-01", to: "2026-10-31" });
  // A Sunday and a Saturday are the edges of their own week.
  assert.deepEqual(periodRange("week", new Date(2026, 10, 1)), { from: "2026-11-01", to: "2026-11-07" });
  assert.deepEqual(periodRange("week", new Date(2026, 9, 31, 23, 59)), { from: "2026-10-25", to: "2026-10-31" });
  // Across a year end.
  assert.deepEqual(periodRange("next", new Date(2026, 11, 30)), { from: "2027-01-03", to: "2027-01-09" });
  assert.deepEqual(periodRange("range", NOW, "2026-11-01", "2026-11-15"), { from: "2026-11-01", to: "2026-11-15" });
  assert.deepEqual(periodRange("custom", NOW, "soon", ""), { from: "", to: "" });
  assert.equal(localDay(new Date(2026, 0, 5)), "2026-01-05");
});

test("an overdue item always shows; one with no date shows; a done one past due doesn't", () => {
  const week = periodRange("week", NOW);
  assert.equal(inPeriod({ due: "2026-10-01" }, week, "2026-10-28"), true);
  assert.equal(inPeriod({ due: "2026-10-01", done: true }, week, "2026-10-28"), false);
  assert.equal(inPeriod({ due: "" }, week, "2026-10-28"), true);
  assert.equal(inPeriod({ due: "2026-10-31" }, week, "2026-10-28"), true);
  assert.equal(inPeriod({ due: "2026-11-01" }, week, "2026-10-28"), false);
  assert.equal(inPeriod({ due: "2026-11-01" }, periodRange("next", NOW), "2026-10-28"), true);
  assert.equal(inPeriod({ due: "2027-01-01" }, { from: "2026-11-01", to: "" }, "2026-10-28"), true, "an open end");
});

const state = {
  jobs: [{ id: "j1", name: "Porch" }],
  today_rows: [
    { key: "ready:j1:i1", kind: "ready", label: "Ready to send", job_id: "j1", job: "Porch", due: "2026-10-30", ball_in_court: "us", assignee_id: "u1" },
    { key: "follow_up:j1:i0", kind: "follow_up", label: "Follow up", job_id: "j1", job: "Porch", due: "2026-10-20", ball_in_court: "client", assignee_id: "" },
  ],
  tasks: [
    { id: "task-a", title: "Order tile", job_id: "j1", status: "open", due: "2026-10-30", assignee_id: "u2", assignee_name: "Mo" },
    { id: "task-b", title: "Old one", status: "done", due: "2026-10-01", assignee_id: "u1" },
    { id: "task-c", title: "Someday", status: "open", due: "", assignee_id: "u1" },
  ],
};

test("one list: the server's rows most urgent first, a task due by today among them, other tasks last", () => {
  const ranked = { ...state, today_rows: [{ ...state.today_rows[0], rank: 1 }, { ...state.today_rows[1], rank: 4 }] };
  assert.deepEqual(todoItems(ranked, {}, "2026-10-28").map(i => i.key), ["ready:j1:i1", "follow_up:j1:i0", "task-a", "task-c"]);
  assert.deepEqual(todoItems(ranked, {}, "2026-10-30").map(i => i.key), ["ready:j1:i1", "task-a", "follow_up:j1:i0", "task-c"]);
  // Rows of one rank (none sent here) go soonest due first.
  const items = todoItems(state, {}, "2026-10-28");
  assert.deepEqual(items.map(i => i.key), ["follow_up:j1:i0", "ready:j1:i1", "task-a", "task-c"]);
  assert.equal(items[2].job, "Porch");
  assert.equal(items[0].ball, "client");
  assert.deepEqual(todoItems(state, { who: "mine", meId: "u1" }).map(i => i.key), ["ready:j1:i1", "task-c"]);
  assert.deepEqual(todoItems(state, { who: "mine", meId: "" }), []);
  assert.deepEqual(itemsInPeriod(items, periodRange("today", NOW), "2026-10-28").map(i => i.key), ["follow_up:j1:i0", "task-c"]);
});

test("the assignee list is the team, or me before it loads, plus anyone already assigned", () => {
  assert.deepEqual(assignees(null, { id: "u1", name: "Pat" }, todoItems(state)), [{ id: "u1", name: "Pat" }, { id: "u2", name: "Mo" }]);
  assert.deepEqual(assignees({ users: [{ id: "u1", name: "Pat" }, { id: "u3", name: "", email: "x@y.example" }] }, null, []),
    [{ id: "u1", name: "Pat" }, { id: "u3", name: "x@y.example" }]);
});
