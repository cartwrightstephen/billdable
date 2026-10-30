// What every view shares: talking to the server (post, load, the full job and the paged lists),
// going to a page (nav), redrawing (render, patch), toasts and refusals, and the (i) help.
import { unesc, escDeep, emailOutcome } from "./format.js";
import { mayTake } from "./jobs.js";
import { pathFor, routeFromPath, refusalLink, refusalLinkData, refusalToastAction } from "./routes.js";
import { resetTrail, stepTrail, previousPage } from "./trail.js";
import { FIELD_HELP, NAV_HELP, helpFor } from "./help.js";
import { readJson, setNetBanner } from "./net.js";
import { labelTables } from "./dom.js";
import { refuseForm, formErrors, clearForm, clearAllForms } from "./forms.js";
import { setBillingStyle, termText } from "./terms.js";
import { state, me, currentView, currentJobId, currentDetailId, formOpen, setData, clearSession, onSessionEnd, setLocation, closeForms, pendingPath, pendingSearch, setPending, setCameraAsk, setTeamData, setDrawerOpen, setQueueExpanded, idleMinutes, setSignedOutNote } from "./state.js";
import { startIdleWatch, stopIdleWatch, idleWatching } from "./idle.js";
import { applyAppearance } from "./theme.js";

// The shell's render and boot live in app.js and the landing page in views/auth.js; app.js hands
// them over at start (setPage). A view can't import app.js: index.html loads it as /app.js?v=...,
// so an import would load a second copy of the page. pageName({view, jobId, detailId}) is what
// the menu calls a page, for the Back button.
const page = { render() {}, renderLanding() {}, boot() {}, pageName() { return ""; } };
export function setPage(fns) { Object.assign(page, fns); }
export function render() { page.render(); }
export function renderLanding() { page.renderLanding(); }
export function boot(checkLink) { return page.boot(checkLink); }

let actionEl = null;      // the button whose action is running: a refusal is shown under it
export function setActionEl(el) { actionEl = el; }

// Run a tapped button's action: ACTIONS[name](el, el.dataset.id, event). While it is still
// waiting on the server, another tap on the same button (same action and data) does nothing, so
// a double tap on Record doesn't record the payment twice. One that finishes at once isn't held.
const runningActions = new Set();
export function runAction(run, el, e) {
  const key = JSON.stringify(Object.entries(el.dataset || {}).sort());
  if (runningActions.has(key)) return undefined;
  setActionEl(el);
  const out = run(el, el.dataset.id, e);
  if (!out || typeof out.then !== "function") return out;
  runningActions.add(key);
  return Promise.resolve(out).finally(() => runningActions.delete(key));
}
let lastToastAt = 0;
// /api/state carries open jobs in full and recently closed jobs as summaries (P2-6). A job opened
// from history is fetched whole and kept here until the next change; long lists are paged.
export let jobDetails = {};          // job id -> the full job from /api/jobs/<id>
export let jobDetailLoading = null;  // the id being fetched
export const missingJobs = new Set();  // ids the server said aren't there (404): "Job not found"
// The id whose fetch failed (offline, a server error): not "not found", just not loaded. It is
// asked for again on the next visit to it (nav) or the next state from the server.
export let jobDetailFailed = null;
export function clearJobDetails() { jobDetails = {}; }
export let listPages = {};           // outbox | audit | history -> {job, entries, total, loading, stale}
const LIST_URLS = { outbox: "/api/outbox?", audit: "/api/audit?", history: "/api/jobs?status=closed&", timeline: "/api/timeline?" };
const LIST_VIEWS = { timeline: "job" };   // the view that shows a list, where it isn't the list's own name
let toastTimer = null;
export const DEMO_COMPANY_ID = "co-hartline";

let helpTip = null;
function showHelpTip(icon) {
  if (!helpTip) {
    helpTip = document.createElement("div");
    helpTip.id = "help-tip";
    helpTip.className = "help-tip";
    helpTip.setAttribute("role", "tooltip");
    document.body.appendChild(helpTip);
  }
  helpTip.textContent = icon.dataset.help;
  helpTip.style.display = "block";
  const r = icon.getBoundingClientRect();
  const w = helpTip.offsetWidth, h = helpTip.offsetHeight;
  let left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), window.innerWidth - w - 8);
  let top = r.bottom + 8;
  if (top + h > window.innerHeight - 8) top = r.top - h - 8;
  helpTip.style.left = left + "px";
  helpTip.style.top = Math.max(8, top) + "px";
  icon.setAttribute("aria-describedby", "help-tip");
}
export function hideHelpTip() { if (helpTip) helpTip.style.display = "none"; }

function addInfoIcon(labelEl, text, hostEl) {
  if (!text || labelEl.querySelector(".info")) return;
  const icon = document.createElement("span");
  icon.className = "info";
  icon.textContent = "i";
  icon.tabIndex = 0;
  icon.setAttribute("role", "button");
  icon.setAttribute("aria-label", "What is this?");
  icon.dataset.help = text;
  icon.addEventListener("mouseenter", () => showHelpTip(icon));
  icon.addEventListener("mouseleave", hideHelpTip);
  icon.addEventListener("focus", () => showHelpTip(icon));
  icon.addEventListener("blur", hideHelpTip);
  icon.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); showHelpTip(icon); });
  icon.addEventListener("keydown", (e) => { if (e.key === "Escape") { hideHelpTip(); icon.blur(); } });
  labelEl.appendChild(icon);
  (hostEl || labelEl).classList.add("has-info");
}

// The (i) beside each label, found by the label's data-help key (a field()), else its text.
// A touch screen shows no icons (styles.css); each card instead ends with one "What do these
// mean?" list of the help in it (R3-10).
function decorateHelp(root) {
  const lists = new Map();  // card -> [[term, help], ...]
  root.querySelectorAll(".money-row .label, .metric .k, th, label").forEach(el => {
    if (el.closest(".auth-card")) return;
    const host = el.closest(".money-row, .metric, th, .form-grid > div") || el;
    const text = termText((el.dataset.help && FIELD_HELP[el.dataset.help]) || helpFor(el.textContent));
    addInfoIcon(el, text, host);
    const card = text && el.closest(".card, .table-wrap, .metrics");
    if (!card) return;
    const term = el.textContent.replace(/[↑↓*]/g, "").trim();
    const list = lists.get(card) || [];
    if (term && !list.some(([t]) => t === term)) list.push([term, text]);
    lists.set(card, list);
  });
  lists.forEach((list, card) => {
    const placed = card.classList.contains("card") ? card.querySelector(":scope > .help-list") : card.nextElementSibling?.classList.contains("help-list");
    if (placed) return;  // drawn already
    const box = document.createElement("details");
    box.className = "help-list";
    const summary = document.createElement("summary");
    summary.textContent = "What do these mean?";
    const dl = document.createElement("dl");
    for (const [term, text] of list) {
      const dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = term;
      dd.textContent = text;
      dl.append(dt, dd);
    }
    box.append(summary, dl);
    // A card holds its list; a table or a row of tiles has it just after.
    if (card.classList.contains("card")) card.append(box);
    else card.after(box);
  });
  root.querySelectorAll(".nav button[data-section]").forEach(b => addInfoIcon(b, termText(NAV_HELP[b.dataset.section]), b));
}

// action: { label, run } adds a button to the toast, like Undo after Dismiss (R5-05); the toast
// then stays up long enough to reach it.
export function toast(msg, action = null) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    document.body.appendChild(el);
  }
  el.textContent = msg;
  if (action) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "toast-action";
    btn.textContent = action.label;
    btn.addEventListener("click", () => { hideToast(); action.run(); });
    el.append(" ", btn);
  }
  el.style.display = "block";
  lastToastAt = Date.now();
  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, action ? 8000 : Math.min(9000, Math.max(2200, String(msg).length * 50)));
}
function hideToast() { const el = document.getElementById("toast"); if (el) el.style.display = "none"; }

// Called once signed in: open the page in the address bar (or the one asked for while signed out).
// The home-screen shortcuts (manifest.webmanifest, R6-04) add ?camera=1 to the Inbox and ?new=1
// to Jobs. A page can't open the camera by itself, so Take a photo is put under the thumb.
export function applyLocation() {
  const r = routeFromPath(pendingPath || location.pathname) || { view: "dashboard" };
  const ask = new URLSearchParams(pendingSearch ?? location.search);
  setPending(null, null);
  if (r.view === "jobs" && ask.get("new") === "1") formOpen.newjob = true;
  if (r.view === "inbox" && ask.get("camera") === "1") setCameraAsk(true);
  setLocation(r.view, r.jobId, r.detailId);
  resetTrail(currentView, currentJobId, currentDetailId);   // arrived from outside the app
  history.replaceState(null, "", pathFor(currentView, currentJobId, currentDetailId));
}

export async function load() {
  let res;
  try {
    res = await fetch("/api/state");
  } catch (_) {
    setNetBanner(true);
    return;
  }
  if (res.status === 401) {
    signedOut(await readJson(res));
    return;
  }
  if (!res.ok) {
    setNetBanner(true);
    return;
  }
  setNetBanner(false);
  setState(await readJson(res));
  // Signed in: the idle count starts (G2), and the person's own theme applies (G9).
  if (me && !idleWatching()) startIdleWatch(idleMinutes);
  // The sample company is one account for every visitor: its look stays in each browser.
  if (me && me.company_id !== DEMO_COMPANY_ID) applyAppearance(me.appearance);
  render();
}

// The server answered 401: the session ended (signed out elsewhere, or idle, G2). Sign in shows why.
function signedOut(data) {
  if (data && data.idle) setSignedOutNote(String(data.error || data.message || ""));
  clearSession();
  renderLanding();
}
onSessionEnd(stopIdleWatch);

function withPayer(j) {
  if (!j.payer) j.payer = (state.payers || []).find(p => p.id === j.payer_id) || { name: "—" };
  return j;
}

let stateVersion = 0;  // counts state replacements, so a job detail that was loading can tell it is stale

export function setState(data) {
  stateVersion++;
  setData(escDeep(data));
  setBillingStyle(state.company && state.company.billing_style);
  state.jobs.forEach(withPayer);
  Object.values(jobDetails).forEach(placeJob);
  Object.values(listPages).forEach(p => { p.stale = true; });
  jobDetailFailed = null;   // the server answered: a job that didn't load is asked for again
}

// Signed out: the jobs fetched whole, the paged lists and what was typed belong to that company.
// stateVersion moves on, so a job still loading for it is dropped when it arrives.
onSessionEnd(() => {
  resetTrail(null);
  stateVersion++;
  jobDetails = {};
  jobDetailLoading = null;
  jobDetailFailed = null;
  missingJobs.clear();
  listPages = {};
  actionEl = null;
  clearAllForms();
  for (const k of Object.keys(moreDetails)) delete moreDetails[k];
});

function placeJob(full) {
  const i = state.jobs.findIndex(j => j.id === full.id);
  if (i >= 0) state.jobs[i] = full; else state.jobs.push(full);
}

export async function loadJobDetail(id) {
  jobDetailLoading = id;
  if (jobDetailFailed === id) jobDetailFailed = null;
  const version = stateVersion;
  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(id)}`);
    // A change saved while this was loading brought newer state: this answer is older, so drop it.
    if (version !== stateVersion) { jobDetailLoading = null; if (currentJobId === id) render(); return; }
    if (res.ok) { const full = withPayer(escDeep((await readJson(res)).job)); jobDetails[id] = full; placeJob(full); }
    else if (res.status === 404) missingJobs.add(id);
    else jobDetailFailed = id;
  } catch (_) { setNetBanner(true); jobDetailFailed = id; }
  jobDetailLoading = null;
  if (currentJobId === id) render();
}

// A paged list for a view; fetched on first use, refreshed in place after changes.
export function listPage(kind, job = "all") {
  const p = listPages[kind];
  if (p && p.job === job) {
    if (p.stale && !p.loading) loadListPage(kind, job, 0, Math.max(50, p.entries.length));
    return p.entries.length || !p.loading ? p : null;
  }
  listPages[kind] = { job, entries: [], total: 0, loading: true };
  loadListPage(kind, job, 0, 50);
  return null;
}

export async function loadListPage(kind, job, offset, limit) {
  const p = listPages[kind];
  p.loading = true;
  try {
    const res = await fetch(`${LIST_URLS[kind]}offset=${offset}&limit=${limit}${job !== "all" ? "&job=" + encodeURIComponent(unesc(job)) : ""}`);
    if (!res.ok) { p.loading = false; return; }
    const data = escDeep(await readJson(res));
    if (listPages[kind] !== p) return;
    const rows = (data.entries || data.jobs || []).map(r => (kind === "history" ? withPayer(r) : r));
    Object.assign(p, { entries: offset ? p.entries.concat(rows) : rows, total: data.total, loading: false, stale: false });
  } catch (_) { setNetBanner(true); p.loading = false; return; }
  if (currentView === (LIST_VIEWS[kind] || kind)) render();
}

// opts.formKey: the form this came from: what was typed is forgotten once it saves, and the
// server's field errors show under its inputs. opts.close: the formOpen key to close once saved.
// A refusal (409) changes nothing, so the page is not redrawn: its reasons show in a card under
// the button that was tapped (opts.anchor, else the one being handled), not in a toast.
// opts.ownRefusal: the caller tells the refusal itself (the helper, in its own panel).
export async function post(path, body = {}, opts = {}) {
  const anchor = opts.anchor || actionEl;
  const emailsBefore = state ? state.email_log_total || 0 : 0;
  jobDetails = {};
  let res;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (_) {
    setNetBanner(true);
    toast("Not saved: can't reach Duracash.");
    return;
  }
  setNetBanner(false);
  const data = await readJson(res);
  if (res.status === 401) {
    signedOut(data);
    return;
  }
  if (res.status === 409) {
    const fields = data.fields && typeof data.fields === "object" ? data.fields : null;
    if (opts.formKey && fields && Object.keys(fields).length) refuseForm(opts.formKey, fields);
    else if (!opts.ownRefusal) showRefusal(anchor, data);
    return { ok: false, status: 409, data };
  }
  if (!res.ok) {
    toast((data.error || "Request failed") + (data.request_id ? ` Reference: ${data.request_id}.` : ""));
  } else {
    if (!opts.quiet) toast("Saved");
    if (opts.formKey) clearForm(opts.formKey);
    if (opts.close) formOpen[opts.close] = false;
  }
  // A change answers with the company's updated state (the same shape as /api/state): use it
  // rather than asking again. Anything else (an error) reloads, so the page shows what is true.
  if (res.ok && data && Array.isArray(data.jobs)) { setState(data); render(); }
  else await load();
  const added = state ? (state.email_log_total || 0) - emailsBefore : 0;
  if (res.ok && added > 0) toast(emailOutcome(state.email_log.slice(-added)));
  return { ok: res.ok, status: res.status, data };
}

// Why an action was refused, in a card right under the button that was tapped (or the row or
// bar it sits in), scrolled into view, with a link to the page that fixes it when the server
// names one. Without a button on the page it falls back to a toast.
function showRefusal(anchor, data) {
  document.querySelectorAll(".action-error").forEach(c => c.remove());
  const message = String(data.error || "That wasn't done.");
  const lines = (Array.isArray(data.blockers) ? data.blockers : []).map(String).filter(b => b && b !== message);
  const link = refusalLink(data.action);
  if (anchor && !anchor.isConnected && anchor.dataset && anchor.dataset.action) {
    const sel = `[data-action="${CSS.escape(anchor.dataset.action)}"]${anchor.dataset.id ? `[data-id="${CSS.escape(anchor.dataset.id)}"]` : ""}`;
    anchor = document.querySelector(sel);
  }
  if (!anchor || !anchor.isConnected) {
    toast([message, ...lines].join(" "), refusalToastAction(link, nav));
    return;
  }
  const card = document.createElement("div");
  card.className = "form-error action-error";
  card.setAttribute("role", "alert");
  const head = document.createElement("strong");
  head.textContent = message;
  card.append(head);
  if (lines.length) {
    const ul = document.createElement("ul");
    lines.forEach(l => { const li = document.createElement("li"); li.textContent = l; ul.append(li); });
    card.append(ul);
  }
  if (link) {
    const a = document.createElement("a");
    a.className = "btn small secondary action-error-link";
    a.href = link.href;
    Object.assign(a.dataset, refusalLinkData(link));
    a.textContent = link.label;
    card.append(a);
  }
  const host = anchor.closest(".action-item, .next-card, .more-actions, .form-inline, .form-row, .list-item") || anchor;
  host.insertAdjacentElement("afterend", card);
  card.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// opts.fromHistory: Back/Forward already changed the address; opts.replace: don't add a history entry.
export function nav(view, jobId = null, detailId = null, opts = {}) {
  if (view === "team") setTeamData(null);  // fresh team list each time the page opens
  jobDetailFailed = null;   // a job that didn't load is asked for again
  const moved = view !== currentView || jobId !== currentJobId || detailId !== currentDetailId;
  setLocation(view, jobId, detailId);
  stepTrail(view, jobId, detailId, opts.fromHistory ? "history" : opts.replace ? "replace" : "push");
  closeForms();
  clearAllForms();
  setDrawerOpen(false);
  setQueueExpanded(false);
  if (!opts.fromHistory) {
    const url = pathFor(view, jobId, detailId);
    if (url !== location.pathname) history[opts.replace ? "replaceState" : "pushState"](null, "", url);
  }
  // A toast from the action that led here stays; an older one would cover the new page.
  if (Date.now() - lastToastAt > 1500) hideToast();
  render();
  if (moved) {
    window.scrollTo(0, 0);
    document.getElementById("main")?.focus({ preventScroll: true });
    // Retainage is the second half of the Closeout tab: open it there.
    if (view === "retainage") document.getElementById("closeout-retainage")?.scrollIntoView({ block: "start" });
  }
}

export function toggleForm(key) {
  formOpen[key] = !formOpen[key];
  if (!formOpen[key]) {   // Cancel drops what was typed
    clearForm(key);
    if (key === "progress") clearForm("sovprogress");   // the line-by-line form opens from the same button
  }
  render();
}

// Back (G3): to the page the person came from in the app, named as the menu names it, through
// the browser's own history; opened from a link or after a refresh, to the page's fixed parent
// (view, label, jobId).
export function backButton(view, label, jobId = null) {
  const prev = previousPage();
  const name = prev && page.pageName(prev);
  if (name) return `<div class="top"><button class="back" data-action="back">← ${name}</button></div>`;
  return `<div class="top"><button class="back" data-action="nav" data-view="${view}"${jobId ? ` data-id="${jobId}"` : ""}>← ${label}</button></div>`;
}

// Every field gets an accessible name: its <label> when there is one, else its placeholder.
let fieldSeq = 0;
function a11yPass(root) {
  root.querySelectorAll("label:not([for])").forEach(l => {
    if (l.querySelector("input, select, textarea")) return;
    const next = l.nextElementSibling;
    const field = next && next.matches("input, select, textarea") ? next
      : (l.parentElement && l.parentElement.matches(".form-grid > div, .field") ? l.parentElement.querySelector("input, select, textarea") : null);
    if (!field) return;
    if (!field.id) field.id = `fld-${++fieldSeq}`;
    l.htmlFor = field.id;
  });
  root.querySelectorAll("input:not([type=hidden]):not([type=checkbox]), select, textarea").forEach(f => {
    if (f.getAttribute("aria-label") || (f.id && root.querySelector(`label[for="${CSS.escape(f.id)}"]`))) return;
    const name = f.getAttribute("placeholder") || f.closest(".money-row")?.querySelector(".label")?.textContent;
    if (name) f.setAttribute("aria-label", name.trim());
  });
}

// An empty list: its title and, when there is one, the button to fill it (G6: no sentence about
// the app). body is only for a not-found page's reason.
export function emptyState(title, body = "", action = "") {
  return `<div class="empty-state"><strong>${title}</strong>${body ? `<p>${body}</p>` : ""}${action}</div>`;
}

// What the signed-in person may do (S6): the permissions of their profile, from /api/auth/me.
// An older page without them (a session from before profiles) falls back to the role.
export function perms() {
  return { owner: !me || me.role === "owner", list: me && Array.isArray(me.perms) ? me.perms : undefined };
}

// Whether the signed-in person holds a permission (S6), for pages that hide what they can't do.
export function can(perm) {
  if (!me) return false;
  return Array.isArray(me.perms) ? me.perms.includes(perm) : me.role === "owner";
}

export function may(action) { return mayTake(action, perms()); }

// Redraw one part of the page (a card, a list) in place, without redrawing the view around it.
export function patch(selector, html) {
  const el = document.querySelector(selector);
  if (!el) return null;
  el.innerHTML = html;
  finishHtml(el);
  return el;
}

// What every freshly drawn piece of the page gets: phone-friendly tables, (i) help, field names.
export function finishHtml(root) {
  labelTables(root);
  decorateHelp(root);
  a11yPass(root);
}
export const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

// "More details (optional)" stays as the person left it across re-renders, and opens when
// one of its fields has an error.
export const moreDetails = {};
export function moreOpen(key) {
  const errs = formErrors[key] || {};
  return !!moreDetails[key] || ["contract_number", "retainage_pct", "phone", "approver", "ap_email", "note"].some(k => errs[k]);
}
