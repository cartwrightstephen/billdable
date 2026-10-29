// Page addresses: every view and detail page has its own path, so refresh, Back and shared links work.

import { unesc } from "./format.js";

// A page's tab: a detail page's list, Retainage, which is part of the Closeout tab (R6-01), and
// Paperwork, which is a column of Pay apps (R6-03).
export const DETAIL_PARENT = { job: "jobs", "payapp-detail": "payapps", "packet-detail": "payapps", packets: "payapps", "co-detail": "cos", "waiver-detail": "waivers", retainage: "waivers" };

export const VIEW_PATHS = {
  dashboard: "/today", results: "/today/results", calendar: "/today/calendar", daily: "/today/daily",
  jobs: "/jobs", history: "/jobs/history", inbox: "/inbox",
  payapps: "/money", packets: "/money/packets", cos: "/money/change-orders", collections: "/money/collections",
  payments: "/money/payments", waivers: "/money/waivers", retainage: "/money/retainage", outbox: "/money/outbox",
  company: "/settings/company", team: "/settings/team", account: "/settings/account", billing: "/settings/billing",
  playbook: "/settings/playbook", audit: "/settings/audit", help: "/settings/help", "helper-admin": "/settings/helper-answers",
};

// Older addresses that still open a page: /settings was one page before it split (R6-03).
export const PATH_ALIASES = { "/settings": "company" };

export const DETAIL_PATHS = { "payapp-detail": "/money/pay-app", "packet-detail": "/money/packet", "co-detail": "/money/change-order", "waiver-detail": "/money/waiver" };

export function pathFor(view, jobId, detailId) {
  const seg = (x) => encodeURIComponent(unesc(String(x)));
  if (view === "job" && jobId) return `/jobs/${seg(jobId)}`;
  if (DETAIL_PATHS[view] && jobId) return [DETAIL_PATHS[view], seg(jobId)].concat(detailId ? [seg(detailId)] : []).join("/");
  return VIEW_PATHS[view] || "/today";
}

// A refusal's way out (the server's GateError `action`, like Undo's "Open the paperwork for
// PA-OAK-3"): a link to a detail page, or null for anything else.
export function refusalLink(action) {
  if (!action || typeof action !== "object" || !DETAIL_PATHS[action.view] || !action.job_id || !action.label) return null;
  const detailId = action.detail ? String(action.detail) : null;
  return { label: String(action.label), view: action.view, jobId: String(action.job_id), detailId, href: pathFor(action.view, action.job_id, detailId) };
}

export function routeFromPath(path) {
  let parts;
  try { parts = path.split("/").filter(Boolean).map(decodeURIComponent); } catch (_) { return null; }
  if (!parts.length) return { view: "dashboard" };
  const joined = "/" + parts.join("/");
  for (const [v, p] of Object.entries(VIEW_PATHS)) if (p === joined) return { view: v };
  if (PATH_ALIASES[joined]) return { view: PATH_ALIASES[joined] };
  if (parts[0] === "jobs" && parts.length === 2) return { view: "job", jobId: parts[1] };
  for (const [v, p] of Object.entries(DETAIL_PATHS)) {
    const base = p.split("/").filter(Boolean);
    if (parts.length >= 3 && parts.length <= 4 && parts[0] === base[0] && parts[1] === base[1]) return { view: v, jobId: parts[2], detailId: parts[3] || null };
  }
  return null;
}
