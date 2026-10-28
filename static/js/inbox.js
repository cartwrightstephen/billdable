// The Inbox card and the paste box (R5-05, R5-06, R5-07): which jobs to offer an item, the line
// that names them, and what a paste looks like. Pure functions over escDeep'd state, so node can
// test them (tests/js/inbox.test.mjs).

// Jobs for the card's select: the item's candidates first, in the server's order, then the rest.
export function jobsInOrder(openJobs, candidates = []) {
  const first = candidates.map(id => openJobs.find(j => j.id === id)).filter(Boolean);
  return [...first, ...openJobs.filter(j => !candidates.includes(j.id))];
}

// The jobs to offer as one-tap choices when nothing was suggested: the tied ones, else the top
// three candidates. Closed or missing jobs drop out.
export function choiceJobs(item, openJobs) {
  const ids = (item.candidates || []).slice(0, item.tied > 1 ? item.tied : 3);
  return ids.map(id => openJobs.find(j => j.id === id)).filter(Boolean);
}

const orList = (names) => names.length < 3 ? names.join(" or ") : `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;

// "Could be Mill Creek Clubhouse or Vesta Phase 2 (both Harmon & Vale jobs). Pick one."
export function choiceLine(jobs, payers = []) {
  if (!jobs.length) return "";
  if (jobs.length === 1) return `Could be ${jobs[0].name}. Pick it or another job.`;
  const payerIds = new Set(jobs.map(j => j.payer_id));
  const payer = payerIds.size === 1 ? payers.find(p => p.id === jobs[0].payer_id) : null;
  const same = payer ? ` (${jobs.length === 2 ? "both" : "all"} ${payer.name} jobs)` : "";
  return `Could be ${orList(jobs.map(j => j.name))}${same}. Pick one.`;
}

// A line that starts a day in pasted notes, like importer.leading_date: 10/12, Oct 12,
// 2026-10-12, after a "-" or a weekday. "3/4 inch" and "1/2 bath" are sizes, not dates.
const DAY = "(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\\.?,?\\s+)?";
const DATE_START = new RegExp(`^\\s*(?:[-*•]\\s*)?${DAY}(?:\\d{4}-\\d{1,2}-\\d{1,2}\\b|(\\d{1,2})/(\\d{1,2})(?:/\\d{2,4})?(?![\\d"/])(?!\\s*(?:in|inch|inches|ft|foot|feet|bath|lb|lbs|ton|yd|sf|mm|cm)\\b)|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\\.?\\s+\\d{1,2}(?:st|nd|rd|th)?\\b(?!\\s+(?:guys|men|crew|days|hours)\\b))`, "i");

export function dateLines(text) {
  return String(text || "").split(/\r?\n/).filter(line => {
    const m = DATE_START.exec(line);
    return m && !(m[1] && +m[1] > 12);
  }).length;
}

const HEADER = /^\s*(from|subject|to|date|sent|cc):\s*\S/i;

// What a paste looks like (R5-06): a text thread when lines start "[date] Name:", notes when two
// or more lines start with a date, an email when a From:/Subject: block sits in its first 10
// lines, else "Something else". `hint` is shown under the box when the guess set the select.
export function pasteGuess(text) {
  const lines = String(text || "").split(/\r?\n/);
  // An exported chat: "[10/21/26, 3:58 PM] Luis Ortega: Whitfield is done".
  if (lines.filter(l => /^\s*\[[^\]]{4,40}\]\s*[^:\]]{1,40}:/.test(l)).length >= 2) return { source: "sms", hint: "Looks like a text thread." };
  const days = dateLines(text);
  if (days >= 2) return { source: "notes", hint: `Looks like notes from ${days} days. One daily record per date.` };
  const head = lines.slice(0, 10).filter(l => HEADER.test(l)).map(l => HEADER.exec(l)[1].toLowerCase());
  if (head.length >= 2 && (head.includes("from") || head.includes("subject"))) return { source: "email", hint: "Looks like an email. The sender and subject are read from it." };
  return { source: "paste", hint: "" };
}
