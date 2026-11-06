// A job's Activity, one entry per person action (round 1, item 14): the app's own bookkeeping that
// came with it (the server's details: a file put on a supporting document, the check's warning, the
// confirm, a suggestion) folds under one line. The activity log keeps every row.
import { actionWords } from "./terms.js";

export function activityAlsoHtml(e) {
  const details = (e && e.details) || [];
  if (!details.length) return "";
  const names = [...new Set(details.map(d => actionWords(d.action)))].join(", ");
  return `<details class="activity-also"><summary>Also: ${names}</summary><ul class="plain-list">${details.map(d => `<li>${d.text}</li>`).join("")}</ul></details>`;
}
