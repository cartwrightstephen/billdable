// The Inbox card's job choices and the paste box's guess (R5-05, R5-06): node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { jobsInOrder, choiceJobs, choiceLine, dateLines, pasteGuess } from "../../static/js/inbox.js";

const jobs = [
  { id: "j1", name: "Oak Street kitchen", payer_id: "p1" },
  { id: "j2", name: "Mill Creek Clubhouse", payer_id: "p2" },
  { id: "j3", name: "Vesta Phase 2", payer_id: "p2" },
];
const payers = [{ id: "p1", name: "Lee Homeowner" }, { id: "p2", name: "Harmon &amp; Vale" }];

test("the candidates come first in the job select", () => {
  assert.deepEqual(jobsInOrder(jobs, ["j3", "j2"]).map(j => j.id), ["j3", "j2", "j1"]);
  assert.deepEqual(jobsInOrder(jobs, ["gone"]).map(j => j.id), ["j1", "j2", "j3"]);
});

test("a tie offers the tied jobs, named with their shared customer", () => {
  const item = { suggested_job_id: null, candidates: ["j2", "j3", "j1"], tied: 2 };
  const choices = choiceJobs(item, jobs);
  assert.deepEqual(choices.map(j => j.id), ["j2", "j3"]);
  assert.equal(choiceLine(choices, payers), "Could be Mill Creek Clubhouse or Vesta Phase 2 (both Harmon &amp; Vale jobs). Pick one.");
  assert.equal(choiceLine([jobs[0], jobs[1]], payers), "Could be Oak Street kitchen or Mill Creek Clubhouse. Pick one.");
  assert.equal(choiceLine([], payers), "");
});

test("lines that start a day are counted; sizes and crew counts are not", () => {
  assert.equal(dateLines("10/12 framing\nOct 13 rough-in\n- 2026-10-14 tile\nMon 10/15 grout"), 4);
  assert.equal(dateLines("3/4 inch pipe\n1/2 bath demo\nMay 2 guys out sick\n13/4 nothing"), 0);
});

test("a paste picks Notes, an email, a text thread or Something else", () => {
  assert.deepEqual(pasteGuess("10/12 Framing done, 4 crew\n10/13 Inspector passed rough-in"),
    { source: "notes", hint: "Looks like notes from 2 days. One daily record per date." });
  assert.equal(pasteGuess("FYI\n---------- Forwarded message ---------\nFrom: Kim <ap@park.example>\nSubject: Backup\n\nhi").source, "email");
  assert.equal(pasteGuess("[10/21/26, 3:58 PM] Luis: done\n[10/21/26, 3:59 PM] Nadia: thanks").source, "sms");
  assert.deepEqual(pasteGuess("10/12 one date only\nTo: Mike"), { source: "paste", hint: "" });
});
