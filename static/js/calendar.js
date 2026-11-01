// The calendar's day colours (R6-02): on a phone a day shows one dot, coloured by its most
// pressing date, so the legend can go.

import { followUpDue } from "./jobs.js";

// Most pressing first: an overdue follow-up, then a waiver, then anything else, then paid.
const RANK = { overdue: 3, waiver: 2, other: 1, paid: 0 };

export function worstKind(events) {
  let worst = "";
  for (const e of events || []) {
    const k = e.kind || "other";
    if (!worst || (RANK[k] ?? 1) > (RANK[worst] ?? 1)) worst = k;
  }
  return worst;
}

// The calendar feed as webcal://, which a phone's calendar app opens as a subscription (R6-04).
// Anything but an http(s) address gives "" and the button stays hidden.
export function webcalUrl(url) {
  const u = String(url || "").trim();
  return /^https?:\/\/[^\s"'<>]+$/i.test(u) ? u.replace(/^https?:/i, "webcal:") : "";
}

// The job's next follow-up on the in-app Calendar: the date Today raises it (followUpDue, the same
// rule as domain.follow_up_due and the calendar feed), as "YYYY-MM-DD" on the viewer's calendar,
// and whether it is already due. Null with nothing to chase.
export function followUpEvent(j, now = new Date()) {
  const f = followUpDue(j);
  if (!f) return null;
  const d = f.day;
  const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return { day, overdue: f.due <= now };
}
