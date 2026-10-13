// Billdable's browser app, loaded as an ES module (no bundler). Shared helpers live in
// static/js/; the views, which share the page's state, stay here.
import { esc, unesc, escDeep, money, toDate, fmtDate, fmtDateTime, words, INVOICE_WORDS, invoicePill, companyLine, emailOutcome, emailStatusHtml, helperFormat } from "./js/format.js";
import { RECORD_KINDS, SOURCE_LABELS, isBlocked, nextStep, jobStatus, statusPill, coSignature, WAIVER_KINDS, waiverKindLabel, waiverSendForm } from "./js/jobs.js";
import { sovNum, sovMilli, sovCents, sovExtend, sovMarkup, fmtQty, blankSovLine, sovProgressHtml, sheetColumns, continuationHtml } from "./js/sov.js";
import { DETAIL_PARENT, VIEW_PATHS, DETAIL_PATHS, pathFor, routeFromPath } from "./js/routes.js";
import { FIELD_HELP, NAV_HELP, helpFor } from "./js/help.js";
import { readJson, setNetBanner, postJson, readTokenFromUrl } from "./js/net.js";
import { labelTables, moreButton, askConfirm } from "./js/dom.js";

let state = null;
let me = null;
let authMode = "signup";
let dataNotice = false;   // server says data may be erased (no persistent disk confirmed yet)
let authError = "";
let authErrorField = "";  // "password" | "terms" when the error belongs to one field
let jobFormErrors = {};   // new-job or edit-job field id -> message
let drawerOpen = false;   // the phone menu
let pendingPath = null;   // a page address opened while signed out, opened after sign-in
let lastToastAt = 0;
let loginCode = null;     // {email, password} while the sign-in asks for a two-step code
let twoStepSetup = null;  // {secret, uri} while setting up two-step sign-in
let recoveryCodes = null; // shown once, right after two-step sign-in is turned on
let metricsData = null;   // {period, data} from /api/metrics
let metricsPeriod = "90";
let paymentImport = null;  // {rows, warnings} proposed from a QuickBooks CSV or the QuickBooks connection
let qboStatus = null;      // /api/qbo/status: configured, connected, environment (escaped)
let qboReconcile = null;   // /api/qbo/reconcile: {rows, invoices}
const RECON_WORDS = { missing: "Not in QuickBooks", amount: "Amount differs", paid_in_qbo: "Paid in QuickBooks", open_in_qbo: "Open in QuickBooks" };
let currentView = "dashboard";
let currentJobId = null;
let currentDetailId = null; // for packet, co, payapp, waiver detail
let aiOpen = false;
let calYear = new Date().getFullYear();
let calMonth = new Date().getMonth();
let calSelectedDay = null;
let helpDismissed = localStorage.getItem("billdable-help-dismissed") === "1";
let dashFilter = "all";
let dashSort = "name";
let dashSortDir = 1;
let payappFilter = "all";
let outboxJobFilter = "all";
// /api/state carries open jobs in full and recently closed jobs as summaries (P2-6). A job opened
// from history is fetched whole and kept here until the next change; long lists are paged.
let jobDetails = {};          // job id -> the full job from /api/jobs/<id>
let jobDetailLoading = null;  // the id being fetched
const missingJobs = new Set();
let listPages = {};           // outbox | audit | history -> {job, entries, total, loading, stale}
const FILE_ACCEPT = document.getElementById("file-input")?.getAttribute("accept") || "";
const LIST_URLS = { outbox: "/api/outbox?", audit: "/api/audit?", history: "/api/jobs?status=closed&" };
let auditJobFilter = "all";
let coPreselectJob = null;
let formOpen = {};
let toastTimer = null;
const DEMO_COMPANY_ID = "co-hartline";
let calJobFilter = "all";
let importSettings = null;
let sovDraft = null; // schedule-of-values editor: { jobId, lines: [{id,item,description,quantity,unit,rate}], op_pct, warnings } (raw text, not escaped)

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
function hideHelpTip() { if (helpTip) helpTip.style.display = "none"; }

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

function decorateHelp(root) {
  root.querySelectorAll(".money-row .label, .metric .k, th, label").forEach(el => {
    if (el.closest(".auth-card")) return;
    const host = el.closest(".money-row, .metric, th, .form-grid > div") || el;
    addInfoIcon(el, helpFor(el.textContent), host);
  });
  root.querySelectorAll(".nav button[data-section]").forEach(b => addInfoIcon(b, NAV_HELP[b.dataset.section], b));
}

function toast(msg) {
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
  el.style.display = "block";
  lastToastAt = Date.now();
  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, Math.min(9000, Math.max(2200, String(msg).length * 50)));
}
function hideToast() { const el = document.getElementById("toast"); if (el) el.style.display = "none"; }

// ---------- navigation: five destinations with tabs; every page has its own address ----------
const SECTIONS = [
  { key: "today", label: "Today", views: [["dashboard", "Overview"], ["results", "Results"], ["calendar", "Calendar"], ["daily", "Daily record"]] },
  { key: "jobs", label: "Jobs", views: [["jobs", "Open jobs"], ["history", "Job history"]] },
  { key: "inbox", label: "Inbox", views: [["inbox", "Inbox"]] },
  { key: "money", label: "Money", views: [["payapps", "Pay apps"], ["packets", "Packets"], ["cos", "Change orders"], ["collections", "Collections"], ["payments", "Payments"], ["waivers", "Waivers"], ["retainage", "Retainage"], ["outbox", "Outbox"]] },
  { key: "settings", label: "Settings", views: [["account", "Team & account"], ["playbook", "Payer playbook"], ["audit", "Audit"], ["help", "Help"], ["helper-admin", "Helper answers"]] },
];

function sectionOf(view) {
  const v = DETAIL_PARENT[view] || view;
  return SECTIONS.find(s => s.views.some(([k]) => k === v)) || SECTIONS[0];
}
function tabLabel(view) {
  for (const s of SECTIONS) for (const [k, label] of s.views) if (k === view) return label;
  return "";
}
function pageTitle() {
  if (!state) return "Billdable";
  const j = currentJobId && state.jobs.find(x => x.id === currentJobId);
  const name = currentView === "job" && j ? unesc(j.name) : DETAIL_PARENT[currentView] && j ? `${tabLabel(DETAIL_PARENT[currentView])}: ${unesc(j.name)}` : (tabLabel(currentView) || sectionOf(currentView).label);
  return `${name} · Billdable`;
}
// Called once signed in: open the page in the address bar (or the one asked for while signed out).
function applyLocation() {
  const r = routeFromPath(pendingPath || location.pathname) || { view: "dashboard" };
  pendingPath = null;
  currentView = r.view;
  currentJobId = r.jobId || null;
  currentDetailId = r.detailId || null;
  history.replaceState(null, "", pathFor(currentView, currentJobId, currentDetailId));
}

async function load() {
  let res;
  try {
    res = await fetch("/api/state");
  } catch (_) {
    setNetBanner(true);
    return;
  }
  if (res.status === 401) {
    me = null;
    state = null;
    renderLanding();
    return;
  }
  if (!res.ok) {
    setNetBanner(true);
    return;
  }
  setNetBanner(false);
  setState(await readJson(res));
  render();
}

function withPayer(j) {
  if (!j.payer) j.payer = (state.payers || []).find(p => p.id === j.payer_id) || { name: "—" };
  return j;
}

function setState(data) {
  state = escDeep(data);
  state.jobs.forEach(withPayer);
  Object.values(jobDetails).forEach(placeJob);
  Object.values(listPages).forEach(p => { p.stale = true; });
}

function placeJob(full) {
  const i = state.jobs.findIndex(j => j.id === full.id);
  if (i >= 0) state.jobs[i] = full; else state.jobs.push(full);
}

async function loadJobDetail(id) {
  jobDetailLoading = id;
  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(id)}`);
    if (res.ok) { const full = withPayer(escDeep((await readJson(res)).job)); jobDetails[id] = full; placeJob(full); }
    else missingJobs.add(id);
  } catch (_) { setNetBanner(true); missingJobs.add(id); }
  jobDetailLoading = null;
  if (currentJobId === id) render();
}

// A paged list for a view; fetched on first use, refreshed in place after changes.
function listPage(kind, job = "all") {
  const p = listPages[kind];
  if (p && p.job === job) {
    if (p.stale && !p.loading) loadListPage(kind, job, 0, Math.max(50, p.entries.length));
    return p.entries.length || !p.loading ? p : null;
  }
  listPages[kind] = { job, entries: [], total: 0, loading: true };
  loadListPage(kind, job, 0, 50);
  return null;
}

async function loadListPage(kind, job, offset, limit) {
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
  if (currentView === kind) render();
}

async function boot(checkLink = true) {
  let linkMessage = null;
  if (checkLink) {
    const t = readTokenFromUrl();
    if (t) {
      linkMessage = await handleTokenLink(t);
      if (tokenScreen) return;   // reset or invite form is showing
    }
  }
  let res;
  try {
    res = await fetch("/api/auth/me");
  } catch (_) {
    setNetBanner(true);
    renderLanding();
    return;
  }
  if (res.ok) {
    const data = await readJson(res);
    me = escDeep(data.user);
    helperAdmin = data.helper_admin === true;
    mailReady = data.mail_ready === true;
    readOnly = typeof data.read_only === "string" ? esc(data.read_only) : "";
    applyLocation();
    await load();
  } else {
    dataNotice = (await readJson(res)).data_notice === true;
    if (location.pathname !== "/" && routeFromPath(location.pathname)) { pendingPath = location.pathname; authMode = "login"; }
    renderLanding();
  }
  setAiVisible(!!me);
  if (linkMessage) toast(linkMessage);
}

function setAiVisible(on) {
  const badge = document.getElementById("ai-badge");
  const panel = document.getElementById("ai-panel");
  if (badge) badge.style.display = on ? "flex" : "none";
  if (panel && !on) panel.classList.remove("open");
}

function renderLanding() {
  document.title = "Billdable";
  if (!authError) authErrorField = "";
  setAiVisible(false);
  const app = document.getElementById("app");
  const signup = authMode === "signup";
  app.innerHTML = `
    <div class="landing">
      ${dataNotice ? `<p class="notice land-notice" role="status">Demo: data may be erased. Don't enter real job or customer data yet.</p>` : ""}
      <header class="land-bar">
        <div class="brand">Billdable</div>
        <div class="land-bar-actions">
          <button class="btn secondary small" id="bar-signin">Sign in</button>
          <button class="btn small" id="bar-start">Create account</button>
        </div>
      </header>
      <section class="land-hero">
        <div class="land-copy">
          <p class="eyebrow">For small GCs and remodelers</p>
          <h1>You finish the work.<br>Billdable gets it billed and collected.</h1>
          <p class="lede">Works beside QuickBooks. Catches the missing packet, the unsigned change order, and the retainage nobody chased. You approve every send.</p>
          <div class="land-points">
            <div><strong>Same-week invoice</strong><span>From “job’s done” to a draft you can send.</span></div>
            <div><strong>Packet gate</strong><span>Nothing leaves with a missing waiver or photo.</span></div>
            <div><strong>Money file</strong><span>Contract, COs, billed, collected, retainage — one place.</span></div>
          </div>
        </div>
        <div class="auth-card land-form" id="signup">
          <h2>${signup ? "Create your company" : "Sign in"}</h2>
          <p class="sub">${signup ? "Your jobs stay in your company space. Passwords need at least 10 characters." : pendingPath ? "Sign in to open that page." : "Back to your jobs, packets, and playbook."}</p>
          ${!signup && loginCode ? `<form id="auth-form">
            <label for="login-code">Code from your authenticator app</label>
            <input id="login-code" name="code" required autocomplete="one-time-code" inputmode="numeric" maxlength="12" placeholder="123456" />
            <p class="sub">Lost your phone? Type one of your recovery codes instead.</p>
            <button class="btn" type="submit">Sign in</button>
            ${authError ? `<div class="auth-error" role="alert">${esc(authError)}</div>` : ""}
            <div class="auth-toggle"><button type="button" id="code-back">Back</button></div>
          </form>` : `<form id="auth-form">
            ${signup ? `<label>Your name</label><input name="name" required autocomplete="name" />` : ""}
            ${signup ? `<label>Company name</label><input name="company_name" required />` : ""}
            <label>Email</label>
            <input name="email" type="email" required autocomplete="username" />
            <label>Password</label>
            <input name="password" type="password" required autocomplete="${signup ? "new-password" : "current-password"}"${authErrorField === "password" ? ' aria-invalid="true" aria-describedby="auth-field-error"' : ""} />
            ${authErrorField === "password" ? `<span class="field-error" id="auth-field-error">${esc(authError)}</span>` : ""}
            ${signup ? `<label class="terms-check"><input type="checkbox" name="accept_terms" required${authErrorField === "terms" ? ' aria-invalid="true" aria-describedby="auth-field-error"' : ""}> I agree to the <a href="/terms" target="_blank" rel="noopener">Terms of Service</a> and the <a href="/privacy" target="_blank" rel="noopener">Privacy Policy</a>.</label>` : ""}
            ${authErrorField === "terms" ? `<span class="field-error" id="auth-field-error">${esc(authError)}</span>` : ""}
            <button class="btn" type="submit">${signup ? "Create account" : "Sign in"}</button>
            ${authError && !authErrorField ? `<div class="auth-error" role="alert">${esc(authError)}</div>` : ""}
          </form>`}
          ${signup ? "" : `<div class="auth-toggle"><button type="button" id="auth-forgot">Forgot your password?</button></div>`}
          <div class="auth-toggle">
            ${signup ? "Already have an account?" : "New company?"}
            <button type="button" id="auth-switch">${signup ? "Sign in" : "Create one"}</button>
          </div>
        </div>
      </section>
      <section class="land-how">
        <h2>How it works</h2>
        <div class="land-steps">
          <div class="card"><span class="step">1</span><h3>Mark the job done</h3><p>Notes and evidence land in the binder. The money file already knows the contract.</p></div>
          <div class="card"><span class="step">2</span><h3>Clear the packet</h3><p>Billdable names the blocker — missing waiver, unsigned CO, thin photos — before anything is sent.</p></div>
          <div class="card"><span class="step">3</span><h3>Approve and collect</h3><p>You approve the send. Payments match. Retainage and closeout stay on the calendar.</p></div>
        </div>
      </section>
      <section class="land-price">
        <div class="card">
          <p class="eyebrow">Planned pricing</p>
          <h2>$299<span>/mo</span></h2>
          <p class="sub">Planned price for companies between $750k and $2.5M. No percent of collections. Billing is not live yet.</p>
          <button class="btn" id="price-start">Create your company</button>
        </div>
      </section>
      <footer class="land-foot">Billdable works beside QuickBooks and never changes your books: it reads payments from a QuickBooks export today, and a direct QuickBooks connection is coming. It does not replace your books or your field app.
        <div class="land-legal"><a href="/terms">Terms of Service</a> · <a href="/privacy">Privacy Policy</a></div></footer>
    </div>
  `;
  document.getElementById("auth-form").addEventListener("submit", onAuthSubmit);
  document.getElementById("code-back")?.addEventListener("click", () => { loginCode = null; authError = ""; renderLanding(); });
  document.getElementById("login-code")?.focus();
  document.getElementById("auth-switch").addEventListener("click", () => {
    authMode = signup ? "login" : "signup";
    authError = "";
    renderLanding();
  });
  const toSignup = () => { authMode = "signup"; authError = ""; renderLanding(); };
  const toSignin = () => { authMode = "login"; authError = ""; renderLanding(); };
  document.getElementById("bar-start").addEventListener("click", toSignup);
  document.getElementById("price-start").addEventListener("click", toSignup);
  document.getElementById("bar-signin").addEventListener("click", toSignin);
  document.getElementById("auth-forgot")?.addEventListener("click", () => renderForgot());
}

async function onAuthSubmit(e) {
  e.preventDefault();
  const form = e.target;
  authErrorField = "";
  if (authMode === "signup" && form.password.value.length < 10) {
    authError = "Password must be at least 10 characters.";
    authErrorField = "password";
    renderLanding();
    document.querySelector('#auth-form input[name="password"]')?.focus();
    return;
  }
  const body = loginCode && authMode === "login"
    ? { ...loginCode, code: form.code.value }
    : { email: form.email.value, password: form.password.value };
  if (authMode === "signup") {
    body.name = form.name.value;
    body.company_name = form.company_name.value;
    body.accept_terms = form.accept_terms.checked;
    if (!body.accept_terms) { authError = "To create an account, accept the Terms of Service and the Privacy Policy."; authErrorField = "terms"; renderLanding(); return; }
  }
  let res;
  try {
    res = await fetch(authMode === "signup" ? "/api/auth/signup" : "/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (_) {
    setNetBanner(true);
    return;
  }
  const data = await readJson(res);
  if (!res.ok) {
    if (data.needs_code && authMode === "login") {
      authError = loginCode ? (data.error || "That code didn't work.") : "";
      loginCode = { email: body.email, password: body.password };
    } else {
      authError = data.error || "Could not sign in.";
    }
    renderLanding();
    return;
  }
  loginCode = null;
  me = escDeep(data.user);
  authError = "";
  helperLog.length = 0;
  try {
    const who = await fetch("/api/auth/me");
    if (who.ok) {
      const info = await readJson(who);
      helperAdmin = info.helper_admin === true;
      mailReady = info.mail_ready === true;
      readOnly = typeof info.read_only === "string" ? esc(info.read_only) : "";
    }
  } catch (_) { helperAdmin = false; }
  applyLocation();
  await load();
  setAiVisible(true);
}

async function logout() {
  await fetch("/api/auth/logout", { method: "POST" });
  me = null;
  state = null;
  twoStepSetup = null; recoveryCodes = null; loginCode = null;
  helperAdmin = false;
  teamData = null;
  lastInvite = null;
  adminCompanies = null;
  helperLog.length = 0;   // never show one person's questions to the next
  document.getElementById("ai-replies").innerHTML = "";
  document.getElementById("ai-chips").innerHTML = "";
  aiOpen = false;
  document.getElementById("ai-panel").classList.remove("open");
  authMode = "signup";
  setAiVisible(false);
  drawerOpen = false;
  history.replaceState(null, "", "/");
  renderLanding();
}

async function post(path, body = {}) {
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
    toast("Not saved: can't reach Billdable.");
    return;
  }
  setNetBanner(false);
  const data = await readJson(res);
  if (res.status === 401) {
    me = null;
    state = null;
    renderLanding();
    return;
  }
  if (res.status === 409) {
    const lines = data.blockers || [data.error];
    toast((data.error && lines[0] !== data.error ? data.error + " " : "Blocked: ") + lines.join("; "));
  } else if (!res.ok) {
    toast((data.error || "Request failed") + (data.request_id ? ` Reference: ${data.request_id}.` : ""));
  } else {
    toast("Saved");
  }
  await load();
  const added = state ? (state.email_log_total || 0) - emailsBefore : 0;
  if (res.ok && added > 0) toast(emailOutcome(state.email_log.slice(-added)));
  return { ok: res.ok, status: res.status, data };
}

// opts.fromHistory: Back/Forward already changed the address; opts.replace: don't add a history entry.
function nav(view, jobId = null, detailId = null, opts = {}) {
  if (view === "account") teamData = null;  // fresh team list each time the page opens
  const moved = view !== currentView || jobId !== currentJobId || detailId !== currentDetailId;
  currentView = view;
  currentJobId = jobId;
  currentDetailId = detailId;
  formOpen = {};
  jobFormErrors = {};
  drawerOpen = false;
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
  }
}

function toggleForm(key) {
  formOpen[key] = !formOpen[key];
  if (key === "newjob" || key === "editjob") jobFormErrors = {};
  render();
}

function backButton(view, label, jobId = null) {
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

function emptyState(title, body, action = "") {
  return `<div class="empty-state"><strong>${title}</strong><p>${body}</p>${action}</div>`;
}

// Change orders are approved by a signature: online through a link the payer opens,
// or a signed copy on the job record (P2-3). There is no bare "approve" any more.
function coSignControls(j, co, extra = "") {
  if (co.status !== "pending" || j.closed) return "";
  const key = `cosign-${co.id}`;
  const records = j.records || [];
  return `<div class="form-inline">
      <button class="btn small" data-action="request-co-signature" data-id="${j.id}" data-co="${co.id}">${co.sign_token ? "Send the signing link again" : "Request signature"}</button>
      <button class="btn small secondary" data-action="toggle-form" data-form="${key}" aria-expanded="${!!formOpen[key]}">Signed on paper…</button>
      <button class="btn small secondary" data-action="withdraw-co" data-id="${j.id}" data-co="${co.id}">Withdraw</button>
      ${extra}
    </div>
    ${co.sign_token ? `<p class="sub">Signing page: <a href="/s/${me.company_id}.${co.sign_token}" target="_blank" rel="noopener">open it</a>${co.signature_requested_at ? ` · requested ${fmtDate(co.signature_requested_at)}` : ""}</p>` : ""}
    ${formOpen[key] ? (records.length
      ? `<div class="form-inline"><label for="cosign-rec-${co.id}" class="inline-label">Signed copy</label><select id="cosign-rec-${co.id}">${records.map(r => `<option value="${r.id}">${r.label}</option>`).join("")}</select><button class="btn small" data-action="approve-co" data-id="${j.id}" data-co="${co.id}">Save as signed</button></div>`
      : `<p class="sub">Add the signed copy to the <button class="linkish" data-action="open-job" data-id="${j.id}">job record</button> first (a photo or PDF), then pick it here.</p>`) : ""}`;
}

// The job page shows ONE main button: the next step. Everything else that applies sits in a
// secondary row, each with its explanation as a tooltip (it used to be a paragraph beside it).
function jobActions(j) {
  if (j.closed) return { primary: null, more: [] };
  const m = j.money;
  const blocked = isBlocked(j);
  const draft = j.invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
  const pencilOut = draft && draft.pencil_status === "sent";
  const waitingPay = j.invoices.some(i => ["sent", "approved", "partial"].includes(i.status));
  const canClose = !(j.close_blockers || []).length;
  const isDemo = me && me.company_id === DEMO_COMPANY_ID;
  const all = [];
  const add = (key, label, attrs, why) => all.push({ key, label, attrs, why });
  if (blocked) add("clear", `Clear ${j.blockers.length} blocker${j.blockers.length === 1 ? "" : "s"}`, `data-action="open-packet" data-job="${j.id}"${draft ? ` data-inv="${draft.id}"` : ""}`, "Open the packet and attach what is missing.");
  if (draft) add("send", "Approve & send", `data-action="send" data-id="${j.id}" ${blocked ? "disabled" : ""}`, blocked ? "Unlocks when the blockers are cleared." : "You approve; Billdable emails the pay app, its PDF and the packet.");
  if (j.waiver_due && !j.waiver_sent) add("waiver", "Send waiver", `data-action="waiver" data-id="${j.id}"`, "Emails the payer the newest lien waiver on the job record. Drop your signed waiver there first.");
  if (waitingPay) add("match", "Match payment", `data-action="toggle-form" data-form="match"`, "Record money received. Paid in full opens the waiver step.");
  if (j.work_status === "complete" && !draft && m.unbilled_completed_cents > 0) add("draft", "Draft pay app", `data-action="draft" data-id="${j.id}"`, "A pay app for the unbilled completed work.");
  if (j.work_status !== "complete") add("progress", "Progress pay app", `data-action="toggle-form" data-form="progress"`, "Bill completed-to-date without marking the job done.");
  if (j.work_status !== "complete") add("done", "Mark done", `data-action="toggle-form" data-form="done"`, "Record completion and draft the final pay app.");
  if (canClose) add("close", "Close for record", `data-action="close" data-id="${j.id}"`, "Moves the settled job to Job history.");
  if (m.retainage_open_cents > 0) add("retainage", "Ask for retainage", `data-action="retainage" data-id="${j.id}"`, "Emails the request once the closeout packet is complete.");
  if (draft && !pencilOut) add("pencil", "Send pencil copy", `data-action="pencil" data-id="${j.id}"`, "Send the draft so the payer agrees quantities first.");
  if (pencilOut) add("pencil-ok", "Payer agreed to pencil", `data-action="pencil-agreed" data-id="${j.id}"`, "Record that the payer agreed to the pencil copy.");
  if (pencilOut) add("pencil-no", "Payer wants changes", `data-action="toggle-form" data-form="pencilchanges"`, "Record what the payer wants changed.");
  if (waitingPay) add("follow", "Follow up", `data-action="follow-up" data-id="${j.id}"`, "Email a follow-up, no sooner than the payer's playbook allows.");
  if (m.retainage_open_cents > 0) add("retpay", "Record retainage paid", `data-action="toggle-form" data-form="retpay"`, "Record retainage the payer released.");
  add("co", "Add change order", `data-action="goto-co" data-id="${j.id}"`, "Create a change order for this job.");
  if (draft) add("packet", "Open packet", `data-action="open-packet" data-job="${j.id}" data-inv="${draft.id}"`, "Attach records to the pay app's packet.");
  if (draft) add("void", "Void draft", `data-action="void-draft" data-id="${j.id}"`, "Throw away the open draft. Its work goes back to unbilled.");
  if (isDemo) add("qbo", "Import QBO sample", `data-action="import-qbo" data-id="${j.id}"`, "Demo only: a stand-in for a QuickBooks payment read.");
  const order = ["clear", "send", "waiver", "match", "draft", "progress", "close", "retainage"];
  const primary = order.map(k => all.find(a => a.key === k)).find(Boolean) || null;
  return { primary, more: all.filter(a => a !== primary) };
}

function render() {
  if (!state) return;
  hideHelpTip();
  const cj = currentJobId && state.jobs.find(j => j.id === currentJobId);
  if (currentJobId && (!cj || cj.summary) && !jobDetails[currentJobId] && !missingJobs.has(currentJobId) && jobDetailLoading !== currentJobId) loadJobDetail(currentJobId);
  const app = document.getElementById("app");
  const company = state.company;
  const section = sectionOf(currentView);
  const tabView = DETAIL_PARENT[currentView] || currentView;
  const inboxCount = newInboxCount();
  const tabs = section.views.filter(([v]) => v !== "helper-admin" || helperAdmin);
  app.innerHTML = `
    <a class="skip-link" href="#main">Skip to content</a>
    <div class="shell ${drawerOpen ? "drawer-open" : ""}">
      <header class="topbar">
        <button class="menu-btn" id="menu-btn" aria-label="Menu" aria-controls="rail" aria-expanded="${drawerOpen}">☰</button>
        <div class="brand">Billdable</div>
        <span class="topbar-section">${section.label}</span>
      </header>
      <aside class="rail" id="rail">
        <div class="brand">Billdable</div>
        <div class="company"><strong>${company.name}</strong>${companyLine(company) ? `<br>${companyLine(company)}` : ""}</div>
        <nav class="nav" aria-label="Main">
          ${SECTIONS.map(s => `<button class="${s.key === section.key ? "active" : ""}" data-section="${s.key}" ${s.key === section.key ? 'aria-current="page"' : ""}>${s.label}${s.key === "inbox" && inboxCount ? ` <span class="count" aria-label="${inboxCount} new">${inboxCount}</span>` : ""}</button>`).join("")}
        </nav>
        <div class="user-meta">
          ${me ? me.name : ""}<br>${me ? me.email : ""}
          <button class="reset-btn" id="logout">Log out</button>
        </div>
      </aside>
      <div class="scrim" id="scrim" aria-hidden="true"></div>
      <main class="main" id="main" tabindex="-1">
        ${tabs.length > 1 ? `<nav class="tabs" aria-label="${section.label}">${tabs.map(([v, label]) => `<button class="tab ${v === tabView ? "active" : ""}" data-tab="${v}" ${v === tabView ? 'aria-current="page"' : ""} title="${esc(NAV_HELP[v] || "")}">${label}</button>`).join("")}</nav>` : ""}
        <div id="view"></div>
      </main>
    </div>
  `;
  document.querySelectorAll(".nav button[data-section]").forEach(b =>
    b.addEventListener("click", () => nav(SECTIONS.find(s => s.key === b.dataset.section).views[0][0]))
  );
  document.querySelectorAll(".tabs button[data-tab]").forEach(b => b.addEventListener("click", () => nav(b.dataset.tab)));
  document.getElementById("menu-btn").addEventListener("click", () => { drawerOpen = !drawerOpen; render(); if (drawerOpen) document.querySelector(".nav button")?.focus(); });
  document.getElementById("scrim").addEventListener("click", () => { drawerOpen = false; render(); });
  document.getElementById("logout").addEventListener("click", logout);
  document.getElementById("view").innerHTML = verifyBannerHtml() + (views[currentView] ? views[currentView]() : emptyState("Page not found", "There is nothing at this address.", `<button class="btn" data-action="nav" data-view="dashboard">Go to Today</button>`));
  document.getElementById("reset")?.addEventListener("click", resetData);
  document.title = pageTitle();
  bindActions();
  labelTables(document.getElementById("app"));
  decorateHelp(document.getElementById("app"));
  a11yPass(document.getElementById("app"));
  if (["inbox", "calendar", "outbox"].includes(currentView)) loadImportSettings();
  if (currentView === "results" && (!metricsData || metricsData.period !== metricsPeriod)) loadMetrics();
  if (currentView === "payments" && !qboStatus) loadQboStatus();
  if (sovDraft) updateSovTotals();
  if (currentView === "helper-admin") { loadHelperReview(); loadAdminCompanies(); }
  updateAiContext();
}

async function resetData() {
  if (me && me.company_id === DEMO_COMPANY_ID) {
    if (!(await askConfirm("Reset the demo account to its starting data?", "Reset the demo", true))) return;
    await post("/api/reset");
    return;
  }
  formOpen.reset = !formOpen.reset;
  render();
  document.getElementById("reset-confirm")?.focus();
}

function bindActions() {
  document.querySelectorAll("[data-action]").forEach((el) => {
    el.addEventListener("click", async (e) => {
      e.preventDefault();
      const a = el.dataset.action;
      const id = el.dataset.id;
      if (a === "email-retry") e.stopPropagation();
      if (a === "resend-verify") {
        const r = await postJson("/api/auth/resend-verify", {});
        toast(r.ok ? r.data.message : (r.data.error || "That didn't work."));
        return;
      }
      if (a === "change-password") {
        const r = await postJson("/api/auth/password", { current: document.getElementById("pw-current").value, new: document.getElementById("pw-new").value });
        toast(r.ok ? r.data.message : (r.data.error || "That didn't work."));
        if (r.ok) render();
        return;
      }
      if (a === "team-invite") {
        const email = document.getElementById("invite-email").value;
        const data = await teamPost("/api/team/invite", { email, role: document.getElementById("invite-role").value });
        if (data) { lastInvite = { link: data.invite_link || (location.origin + data.invite_path), emailed: data.emailed, email_status: data.email_status, email }; render(); }
        return;
      }
      if (a === "team-role") { await teamPost(`/api/team/${el.dataset.user}/role`, { role: el.dataset.role }, "Role changed."); return; }
      if (a === "team-remove") {
        if (!(await askConfirm(`Remove ${unesc(el.dataset.name)} from the team? They are signed out right away.`, "Remove", true))) return;
        await teamPost(`/api/team/${el.dataset.user}/remove`, {}, "Removed and signed out. The inbound email, text and calendar addresses changed too: update any forwarding rule or calendar subscription.");
        return;
      }
      if (a === "team-revoke") { await teamPost(`/api/team/invites/${el.dataset.invite}/revoke`, {}, "Invite revoked."); return; }
      if (a === "delete-account") {
        if (!(await askConfirm("Delete the company, every job and file, and every user? This can't be undone here.", "Delete everything", true))) return;
        const r = await postJson("/api/account/delete", { password: document.getElementById("del-password").value, confirm: document.getElementById("del-confirm").value });
        if (!r.ok) { toast(r.data.error || "Nothing was deleted."); return; }
        me = null; state = null; teamData = null; lastInvite = null;
        renderLanding();
        toast(r.data.message);
        return;
      }
      if (a === "reset-confirm") {
        const r = await post("/api/reset", { confirm: document.getElementById("reset-confirm")?.value || "" });
        if (r && r.ok) { formOpen.reset = false; render(); }
        return;
      }
      if (a === "2fa-begin") {
        const r = await postJson("/api/auth/2fa/begin", {});
        if (!r.ok) { toast(r.data.error || "That didn't work."); return; }
        twoStepSetup = escDeep(r.data); render(); document.getElementById("2fa-code")?.focus(); return;
      }
      if (a === "2fa-enable") {
        const r = await postJson("/api/auth/2fa/enable", { code: document.getElementById("2fa-code")?.value || "" });
        if (!r.ok) { toast(r.data.error || "That didn't work."); return; }
        twoStepSetup = null; recoveryCodes = escDeep(r.data.recovery_codes); me.two_step = true; render(); return;
      }
      if (a === "2fa-done") { recoveryCodes = null; render(); return; }
      if (a === "2fa-disable") {
        const r = await postJson("/api/auth/2fa/disable", { password: document.getElementById("2fa-off-pw")?.value || "", code: document.getElementById("2fa-off-code")?.value || "" });
        toast(r.ok ? r.data.message : (r.data.error || "That didn't work."));
        if (r.ok) { me.two_step = false; render(); }
        return;
      }
      if (a === "qbo-read") {
        const text = document.getElementById("qbo-csv")?.value || "";
        if (!text.trim()) { toast("Paste the CSV first."); return; }
        const r = await postJson("/api/import/payments-csv", { csv: text });
        if (!r.ok) { toast(r.data.error || "Couldn't read it."); return; }
        paymentImport = escDeep(r.data); render(); return;
      }
      if (a === "qbo-apply") {
        const picks = [...document.querySelectorAll("[data-qbo-row]:checked")].map(cb => paymentImport.rows[+cb.dataset.qboRow])
          .map(row => ({ job_id: row.job_id, invoice_id: row.invoice_id, amount_cents: row.amount_cents, reference: unesc(row.ref), date: row.date, qbo_id: row.qbo_id ? unesc(row.qbo_id) : undefined }));
        if (!picks.length) { toast("Tick the payments to record."); return; }
        const r = await postJson("/api/import/payments-apply", { payments: picks });
        if (!r.ok) { toast(r.data.error || "Nothing was recorded."); return; }
        const done = r.data.import_results.filter(x => x.ok).length, failed = r.data.import_results.filter(x => !x.ok);
        setState(r.data); paymentImport = null;
        toast(`${done} payment${done === 1 ? "" : "s"} recorded.${failed.length ? " Not recorded: " + failed.map(f => `${f.reference}: ${f.error}`).join("; ") : ""}`);
        render(); return;
      }
      if (a === "qbo-cancel") { paymentImport = null; render(); return; }
      if (a === "qbo-connect") {
        const r = await postJson("/api/qbo/connect", {});
        if (!r.ok) { toast(r.data.error || "Couldn't start the QuickBooks connection."); return; }
        if (typeof r.data.url === "string" && r.data.url.startsWith("https://")) location.assign(r.data.url);
        return;
      }
      if (a === "qbo-sync") {
        toast("Reading payments from QuickBooks…");
        const r = await postJson("/api/qbo/payments", { days: 90 });
        if (!r.ok) { toast(r.data.error || "Couldn't read QuickBooks."); qboStatus = null; render(); return; }
        paymentImport = escDeep(r.data); render(); return;
      }
      if (a === "qbo-reconcile") {
        try {
          const res = await fetch("/api/qbo/reconcile");
          const data = await readJson(res);
          if (!res.ok) { toast(data.error || "Couldn't read QuickBooks."); qboStatus = null; render(); return; }
          qboReconcile = escDeep(data);
        } catch (_) { setNetBanner(true); return; }
        render(); return;
      }
      if (a === "qbo-disconnect") {
        if (!(await askConfirm("Disconnect QuickBooks? Payments already recorded stay. An owner can connect it again any time.", "Disconnect"))) return;
        const r = await postJson("/api/qbo/disconnect", {});
        if (r.ok) { qboStatus = escDeep(r.data); qboReconcile = null; }
        toast(r.ok ? "QuickBooks is disconnected." : (r.data.error || "That didn't work."));
        render(); return;
      }
      if (a === "metrics-period") { metricsPeriod = el.dataset.period; metricsData = null; render(); return; }
      if (a === "metrics-email") {
        const r = await postJson("/api/metrics/summary-email", {});
        toast(r.ok ? r.data.message : (r.data.error || "That didn't work."));
        return;
      }
      if (a === "nav") { nav(el.dataset.view, el.dataset.id || null, el.dataset.detail || null); return; }
      if (a === "onb-newjob") { nav("jobs"); formOpen.newjob = true; render(); document.getElementById("nj-name")?.focus(); return; }
      if (a === "list-more") { const k = el.dataset.kind, pg = listPages[k]; if (pg && !pg.loading) loadListPage(k, pg.job, pg.entries.length, 50); return; }
      if (a === "onb-job") { const j = state.jobs.find(x => !x.closed) || state.jobs[0]; if (j) nav("job", j.id); return; }
      if (a === "onb-dismiss") { await post("/api/onboarding/dismiss"); return; }
      if (a === "draft") await post(`/api/jobs/${id}/draft`);
      else if (a === "sov-start") { sovStartEdit(state.jobs.find(x => x.id === id)); render(); }
      else if (a === "sov-add") { sovDraft.lines.push(blankSovLine()); render(); document.querySelectorAll(".sov-desc")[sovDraft.lines.length - 1]?.focus(); }
      else if (a === "sov-remove") { sovDraft.lines.splice(+el.dataset.row, 1); if (!sovDraft.lines.length) sovDraft.lines.push(blankSovLine()); render(); }
      else if (a === "sov-read") await sovReadPaste();
      else if (a === "sov-cancel") { sovDraft = null; render(); }
      else if (a === "sov-save") await sovSave(id, false);
      else if (a === "sov-save-contract") await sovSave(id, true);
      else if (a === "sov-progress") await sovProgress(id);
      else if (a === "email-retry") await post(`/api/outbox/${el.dataset.email}/retry`);
      else if (a === "send") await post(`/api/jobs/${id}/send`);
      else if (a === "progress") {
        const dollars = parseFloat(document.querySelector("#progress-amt")?.value || "0");
        const notes = document.querySelector("#progress-notes")?.value || "";
        if (!(dollars > 0)) { toast("Enter completed-to-date in dollars."); return; }
        formOpen.progress = false;
        await post(`/api/jobs/${id}/progress`, { completed_cents: Math.round(dollars * 100), notes });
      }
      else if (a === "done") {
        const notes = document.querySelector("#done-notes")?.value || "";
        await post(`/api/jobs/${id}/done`, { notes });
        formOpen.done = false;
      }
      else if (a === "resolve") {
        const box = document.getElementById(`resolve-reason-${el.dataset.item}`);
        const reason = (box?.value || "").trim();
        if (!reason) { toast("Say why it's present, or attach a job record instead."); box?.focus(); return; }
        await post(`/api/jobs/${id}/resolve/${el.dataset.item}`, { reason });
      }
      else if (a === "waiver") {
        const kind = document.getElementById(`waiver-kind-${el.dataset.inv || "job"}`)?.value;
        await post(`/api/jobs/${id}/waiver`, { ...(el.dataset.inv ? { invoice_id: el.dataset.inv } : {}), ...(kind ? { kind } : {}) });
      }
      else if (a === "retainage") await post(`/api/jobs/${id}/retainage`);
      else if (a === "retainage-paid") {
        const dollars = parseFloat(document.querySelector("#retpay-amt")?.value || "0");
        const reference = document.querySelector("#retpay-ref")?.value || "";
        const cents = Math.round(dollars * 100);
        if (!(cents > 0)) { toast("Enter the retainage amount received."); return; }
        formOpen.retpay = false;
        await post(`/api/jobs/${id}/retainage-paid`, { amount_cents: cents, reference });
      }
      else if (a === "include-co") await post(`/api/jobs/${id}/include-co/${el.dataset.co}`);
      else if (a === "pencil") await post(`/api/jobs/${id}/pencil`);
      else if (a === "pencil-agreed") await post(`/api/jobs/${id}/pencil-response`, { agreed: true });
      else if (a === "pencil-changes") {
        const note = (document.getElementById("pencil-note")?.value || "").trim();
        if (!note) { toast("Say what the payer wants changed."); document.getElementById("pencil-note")?.focus(); return; }
        formOpen.pencilchanges = false;
        await post(`/api/jobs/${id}/pencil-response`, { agreed: false, note });
      }
      else if (a === "pick-files") {
        const input = document.getElementById("file-input");
        if (input) {
          input.dataset.job = el.dataset.job || "";
          // "Take a photo" opens the rear camera directly; "choose files" offers every source.
          if (el.dataset.capture) { input.setAttribute("capture", "environment"); input.setAttribute("accept", "image/*"); }
          else { input.removeAttribute("capture"); input.setAttribute("accept", FILE_ACCEPT); }
          input.click();
        }
      }
      else if (a === "import-text") {
        const text = document.querySelector("#paste-text")?.value || "";
        const source = document.querySelector("#paste-source")?.value || "paste";
        const jobId = el.dataset.job || "";
        if (!text.trim()) { toast("Paste some text first."); return; }
        const ok = await postImport("/api/import/text" + (jobId ? "?file=1" : ""), { text, source, job_id: jobId });
        if (ok) { const box = document.querySelector("#paste-text"); if (box) box.value = ""; }
      }
      else if (a === "inbox-file") {
        const itemId = el.dataset.item;
        const jobId = document.querySelector(`#inb-job-${itemId}`)?.value || "";
        const kind = document.querySelector(`#inb-kind-${itemId}`)?.value || "";
        if (!jobId) { toast("Pick a job for this item."); return; }
        await post(`/api/inbox/${itemId}/file`, { job_id: jobId, kind });
      }
      else if (a === "inbox-file-all") {
        const items = (state.inbox || []).filter(i => i.status === "new" && i.suggested_job_id);
        for (const i of items) {
          const select = document.querySelector(`#inb-job-${i.id}`);
          const jobId = select ? select.value : i.suggested_job_id;   // a row set back to "Pick a job" is skipped
          if (!jobId) continue;
          const kind = document.querySelector(`#inb-kind-${i.id}`)?.value || i.kind;
          await post(`/api/inbox/${i.id}/file`, { job_id: jobId, kind });
        }
      }
      else if (a === "inbox-dismiss") await post(`/api/inbox/${el.dataset.item}/dismiss`);
      else if (a === "inbox-mark-done") {
        const r = await post(`/api/inbox/${el.dataset.item}/mark-done`);
        const item = r && r.ok && (state.inbox || []).find(x => x.id === el.dataset.item);
        if (item) nav("job", item.filed_job_id);
      }
      else if (a === "inbox-undo") await post(`/api/inbox/${el.dataset.item}/undo`);
      else if (a === "copy") {
        const text = document.getElementById(el.dataset.target)?.textContent || "";
        try { await navigator.clipboard.writeText(text); toast("Copied"); }
        catch (_) { toast("Copy blocked by the browser. Select the text and copy it."); }
      }
      else if (a === "rotate-token") {
        if (!(await askConfirm("Make a new address? The old one stops working right away.", "Make a new address"))) return;
        await fetch("/api/import/rotate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: el.dataset.kind }) });
        importSettings = null;
        render();
      }
      else if (a === "withdraw-co") {
        if (!(await askConfirm("Withdraw this change order? Use this when the customer will not sign it.", "Withdraw", true))) return;
        await post(`/api/jobs/${id}/withdraw-co/${el.dataset.co}`);
      }
      else if (a === "void-draft") {
        if (!(await askConfirm("Void the open draft pay app? Its work goes back to unbilled.", "Void the draft", true))) return;
        await post(`/api/jobs/${id}/void-draft`);
      }
      else if (a === "follow-up") await post(`/api/jobs/${id}/follow-up`);
      else if (a === "match") {
        const dollars = parseFloat(document.querySelector("#match-amt")?.value || "0");
        const ref = document.querySelector("#match-ref")?.value || "";
        const invoiceId = document.querySelector("#match-inv")?.value || "";
        const cents = Math.round(dollars * 100);
        if (!(cents > 0)) { toast("Enter the payment amount in dollars."); return; }
        await post(`/api/jobs/${id}/match-payment`, { amount_cents: cents, reference: ref, invoice_id: invoiceId });
      }
      else if (a === "add-co") {
        const title = (document.querySelector("#co-title")?.value || "").trim();
        const dollars = parseFloat(document.querySelector("#co-amt")?.value || "0");
        const cents = Math.round(dollars * 100);
        if (!title) { toast("Give the change order a title."); return; }
        if (!(cents > 0)) { toast("Enter the change order amount."); return; }
        const description = (document.querySelector("#co-desc")?.value || "").trim();
        await post(`/api/jobs/${id}/add-co`, { title, amount_cents: cents, description });
        nav("co-detail", id);
      }
      else if (a === "goto-co") { coPreselectJob = id; nav("cos"); }
      else if (a === "add-co-from-list") {
        const jobId = document.querySelector("#new-co-job")?.value;
        const title = (document.querySelector("#co-title")?.value || "").trim();
        const dollars = parseFloat(document.querySelector("#co-amt")?.value || "0");
        const cents = Math.round(dollars * 100);
        if (!jobId) { toast("Pick a job."); return; }
        if (!title) { toast("Give the change order a title."); return; }
        if (!(cents > 0)) { toast("Enter the change order amount."); return; }
        const description = (document.querySelector("#co-desc")?.value || "").trim();
        const r = await post(`/api/jobs/${jobId}/add-co`, { title, amount_cents: cents, description });
        if (r && r.ok) toast(`Change order created on ${unesc((state.jobs.find(j => j.id === jobId) || {}).name || "the job")}. It stays unbilled until signed.`);
      }
      else if (a === "goto-packet") nav("packet-detail", id);
      else if (a === "open-packet") nav("packet-detail", el.dataset.job, el.dataset.inv);
      else if (a === "open-co") nav("co-detail", el.dataset.job, el.dataset.co);
      else if (a === "open-payapp") nav("payapp-detail", el.dataset.job, el.dataset.inv);
      else if (a === "open-waiver") nav("waiver-detail", id);
      else if (a === "daily-save") {
        const notes = document.querySelector("#daily-notes")?.value || "";
        const job = document.querySelector("#daily-job")?.value;
        if (job && notes) await post(`/api/jobs/${job}/daily-record`, { notes });
      }
      else if (a === "attach") {
        const name = document.querySelector("#attach-name")?.value || "attachment.pdf";
        await post(`/api/jobs/${id}/attach/${el.dataset.item}`, { name });
      }
      else if (a === "import-qbo") await post(`/api/jobs/${id}/import-qbo`);
      else if (a === "close") await post(`/api/jobs/${id}/close`);
      else if (a === "cal-prev") { calMonth--; if (calMonth < 0) { calMonth = 11; calYear--; } calSelectedDay = null; render(); }
      else if (a === "cal-next") { calMonth++; if (calMonth > 11) { calMonth = 0; calYear++; } calSelectedDay = null; render(); }
      else if (a === "cal-today") { const n = new Date(); calYear = n.getFullYear(); calMonth = n.getMonth(); calSelectedDay = null; render(); }
      else if (a === "cal-day") {
        calSelectedDay = el.dataset.day || null;
        if (calSelectedDay) { const d = toDate(calSelectedDay); calYear = d.getFullYear(); calMonth = d.getMonth(); }
        render();
        document.getElementById("day-detail")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
      else if (a === "approve-co") {
        const recordId = document.getElementById(`cosign-rec-${el.dataset.co}`)?.value || "";
        const r = await post(`/api/jobs/${id}/approve-co/${el.dataset.co}`, { record_id: recordId });
        if (r && r.ok) { formOpen[`cosign-${el.dataset.co}`] = false; render(); }
      }
      else if (a === "request-co-signature") await post(`/api/jobs/${id}/request-co-signature/${el.dataset.co}`);
      else if (a === "add-evidence") {
        const label = document.querySelector("#ev-label")?.value;
        if (label) await post(`/api/jobs/${id}/add-evidence`, { label, kind: "note" });
      }
      else if (a === "learn") {
        const reason = document.querySelector("#learn-reason")?.value;
        if (reason) await post(`/api/jobs/${id}/learn-rejection`, { reason });
      }
      else if (a === "add-record") {
        const label = document.querySelector("#rec-label")?.value.trim();
        const note = document.querySelector("#rec-note")?.value.trim() || "";
        const kind = document.querySelector("#rec-kind")?.value || "note";
        if (!label) { toast("A record needs a label."); return; }
        await post(`/api/jobs/${id}/add-record`, { label, note, kind });
      }
      else if (a === "attach-record") {
        const recordId = document.querySelector(`#attach-rec-${el.dataset.item}`)?.value;
        if (!recordId) { toast("Pick a job record to attach."); return; }
        await post(`/api/jobs/${id}/attach-record/${el.dataset.item}`, { record_id: recordId });
      }
      else if (a === "open-job") nav("job", id);
      else if (a === "create-job") {
        const name = document.querySelector("#nj-name")?.value.trim() || "";
        const address = document.querySelector("#nj-address")?.value.trim() || "";
        const customer = document.querySelector("#nj-customer")?.value.trim() || "";
        const approver = document.querySelector("#nj-approver")?.value.trim() || "";
        const apEmail = document.querySelector("#nj-email")?.value.trim() || "";
        const phone = document.querySelector("#nj-phone")?.value.trim() || "";
        const role = document.querySelector("#nj-role")?.value || "Owner";
        const contractNumber = document.querySelector("#nj-contract")?.value.trim() || "";
        const packetNotes = document.querySelector("#nj-packet")?.value.trim() || "";
        const dollars = parseFloat(document.querySelector("#nj-amount")?.value || "0");
        const retainage = parseInt(document.querySelector("#nj-retainage")?.value || "0", 10);
        const errors = {};
        if (!name) errors["nj-name"] = "Enter the job name.";
        if (!address) errors["nj-address"] = "Enter the job address.";
        if (!customer) errors["nj-customer"] = "Enter who pays this job.";
        if (!approver) errors["nj-approver"] = "Enter who approves the pay app.";
        if (!apEmail || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(apEmail)) errors["nj-email"] = "Enter a real AP email, like ap@builder.com.";
        if (!(dollars > 0)) errors["nj-amount"] = "Enter the contract amount in dollars.";
        if (retainage < 0 || retainage > 20) errors["nj-retainage"] = "Use a number from 0 to 20.";
        if (Object.keys(errors).length) {
          jobFormErrors = errors;
          render();
          document.getElementById(Object.keys(errors)[0])?.focus();
          return;
        }
        jobFormErrors = {};
        let res;
        try {
          res = await fetch("/api/jobs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name, address, customer, approver,
            ap_email: apEmail, phone, role,
            contract_number: contractNumber,
            packet_notes: packetNotes,
            contract_cents: Math.round(dollars * 100),
            retainage_pct: retainage,
          }),
          });
        } catch (_) {
          setNetBanner(true);
          toast("Not saved: can't reach Billdable.");
          return;
        }
        const data = await readJson(res);
        if (!res.ok) {
          jobFormErrors = { form: (data.blockers || [data.error || "Could not create the job."]).join(" ") };
          render();
          return;
        }
        formOpen.newjob = false;
        setState(data);
        toast("Job saved");
        const made = state.jobs[state.jobs.length - 1];
        if (made) nav("job", made.id); else render();
      }
      else if (a === "save-job") {
        const val = (fid) => (document.getElementById(fid)?.value || "").trim();
        const body = { name: val("ej-name"), address: val("ej-address"), contract_number: val("ej-contract") };
        const errors = {};
        if (!body.name) errors["ej-name"] = "Enter the job name.";
        if (!body.address) errors["ej-address"] = "Enter the job address.";
        if (document.getElementById("ej-amount")) {
          const dollars = parseFloat(val("ej-amount") || "0");
          if (!(dollars > 0)) errors["ej-amount"] = "Enter the contract amount in dollars.";
          body.contract_cents = Math.round(dollars * 100);
        }
        if (document.getElementById("ej-retainage")) {
          const pct = parseInt(val("ej-retainage") || "0", 10);
          if (!(pct >= 0 && pct <= 20)) errors["ej-retainage"] = "Use a number from 0 to 20.";
          body.retainage_pct = pct;
        }
        if (Object.keys(errors).length) { jobFormErrors = errors; render(); document.getElementById(Object.keys(errors)[0])?.focus(); return; }
        const r = await post(`/api/jobs/${id}/edit`, body);
        if (r && r.ok) { formOpen.editjob = false; jobFormErrors = {}; render(); }
      }
      else if (a === "delete-job") {
        if (!(await askConfirm(`Delete ${unesc(el.dataset.name || "this job")}? It has no pay apps, so nothing billed is lost. Its record and notes go too.`, "Delete the job", true))) return;
        const r = await post(`/api/jobs/${id}/delete`);
        if (r && r.ok) nav("jobs");
      }
      else if (a === "toggle-form") toggleForm(el.dataset.form);
      else if (a === "dismiss-help") { helpDismissed = true; localStorage.setItem("billdable-help-dismissed", "1"); render(); }
      else if (a === "filter") { dashFilter = el.dataset.filter; render(); }
      else if (a === "sort") {
        if (dashSort === el.dataset.sort) dashSortDir *= -1;
        else { dashSort = el.dataset.sort; dashSortDir = 1; }
        render();
      }
      else if (a === "payapp-filter") { payappFilter = el.dataset.filter; render(); }
      else if (a === "sug-add" || a === "sug-dismiss") await post(`/api/payers/${id}/suggestion/${el.dataset.sug}/${a === "sug-add" ? "add" : "dismiss"}`);
      else if (a === "save-playbook") {
        const form = el.closest("form");
        const updates = {};
        new FormData(form).forEach((v, k) => { if (v !== "") updates[k] = isNaN(v) ? v : Number(v); });
        await post(`/api/payers/${id}/update`, updates);
      }
    });
  });
  // Filter dropdowns listen for "change" only. A click handler that re-renders
  // destroys the open dropdown, which is why mouse selection used to fail.
  document.querySelectorAll("select[data-filter-for]").forEach(s => {
    s.addEventListener("change", () => {
      if (s.dataset.filterFor === "outbox") outboxJobFilter = s.value;
      else if (s.dataset.filterFor === "audit") auditJobFilter = s.value;
      else if (s.dataset.filterFor === "calendar") calJobFilter = s.value;
      render();
    });
  });
}

function newInboxCount() {
  return (state && state.inbox ? state.inbox.filter(i => i.status === "new").length : 0);
}

const MAX_UPLOAD_MB = 15;

async function uploadFiles(fileList, jobId = "", fileNow = false) {
  const files = [...(fileList || [])];
  if (!files.length) return;
  let ok = 0;
  const failed = [];
  for (const f of files) {
    if (f.size > MAX_UPLOAD_MB * 1024 * 1024) { failed.push(`${f.name}: larger than ${MAX_UPLOAD_MB} MB`); continue; }
    const name = f.name && f.name !== "image.png" ? f.name : `Screenshot ${new Date().toISOString().slice(0, 16).replace("T", " ").replace(":", ".")}.png`;
    const query = jobId ? `?job=${encodeURIComponent(jobId)}${fileNow ? "&file=1" : ""}` : "";
    try {
      const res = await fetch(`/api/import/file${query}`, {
        method: "POST",
        headers: { "Content-Type": f.type || "application/octet-stream", "X-Filename": encodeURIComponent(name) },
        body: f,
      });
      const data = await readJson(res);
      if (res.status === 401) { me = null; state = null; renderLanding(); return; }
      if (res.ok) { ok++; setState(data); setNetBanner(false); }
      else failed.push(data.error || `${name}: upload failed`);
    } catch (_) {
      setNetBanner(true);
      failed.push(`${name}: can't reach Billdable`);
    }
  }
  const where = fileNow ? "added to the job record" : "in the Inbox";
  toast([ok ? `${ok} file${ok === 1 ? "" : "s"} ${where}.` : "", failed.join("; ")].filter(Boolean).join(" "));
  render();
}

async function postImport(path, body) {
  let res;
  try {
    res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch (_) { setNetBanner(true); toast("Not saved: can't reach Billdable."); return false; }
  const data = await readJson(res);
  if (!res.ok) { toast(data.error || "Import failed."); return false; }
  setState(data);
  toast(body.job_id ? "Added to the job record." : "Added to the Inbox.");
  render();
  return true;
}

async function loadMetrics() {
  const period = metricsPeriod;
  try {
    const res = await fetch(`/api/metrics?period=${period}`);
    if (!res.ok) return;
    metricsData = { period, data: escDeep(await readJson(res)) };
  } catch (_) { setNetBanner(true); return; }
  if (currentView === "results") render();
}

async function loadQboStatus() {
  qboStatus = { loading: true };
  try {
    const res = await fetch("/api/qbo/status");
    if (!res.ok) { qboStatus = null; return; }
    qboStatus = escDeep(await readJson(res));
  } catch (_) { qboStatus = null; setNetBanner(true); return; }
  if (currentView === "payments") render();
}

async function loadImportSettings() {
  if (importSettings) { fillImportSettings(); return; }
  try {
    const res = await fetch("/api/import/settings");
    if (res.ok) { importSettings = escDeep(await readJson(res)); fillImportSettings(); }
  } catch (_) { /* the cards keep their "loading" text; the banner covers outages */ }
}

function fillImportSettings() {
  const s = importSettings;
  if (!s) return;
  const set = (id, value) => { const el = document.getElementById(id); if (el) el.innerHTML = value; };
  const isDemo = me && me.company_id === DEMO_COMPANY_ID;
  set("outbox-mode", isDemo
    ? "This is the demo account: nothing is emailed from it."
    : s.mail_configured
      ? `Approved emails are sent from <strong>${s.mail_from}</strong> with your company name, and replies go to the person who approved them. Up to ${s.mail_daily_limit} a day.`
      : "Email delivery is not set up on this server yet, so nothing here has left Billdable. Once it is, use Send again on any row.");
  set("email-webhook", s.email_webhook);
  set("sms-webhook", s.sms_webhook);
  set("cal-url", s.calendar_url);
  set("email-address", s.email_address ? `Forward or BCC job email to <strong>${s.email_address}</strong>.` : "No forwarding address is set up yet (an email provider has to be connected first; see steps).");
  set("sms-number", s.sms_number ? `Text photos and notes to <strong>${s.sms_number}</strong>.` : "No texting number is set up yet (a Twilio number has to be connected first; see steps).");
}

function sovStartEdit(j) {
  const v = j.sov_view || {};
  const base = (v.lines || []).filter(l => !l.co_id);
  sovDraft = {
    jobId: j.id,
    lines: base.length
      ? base.map(l => ({ id: l.id, item: unesc(l.item), description: unesc(l.description), quantity: String(l.qty_milli / 1000), unit: unesc(l.unit), rate: (l.rate_cents / 100).toFixed(2) }))
      : [blankSovLine(), blankSovLine(), blankSovLine()],
    op_pct: v.op_bp ? String(v.op_bp / 100) : "",
    warnings: [],
  };
}

function sovFilledLines() {
  return sovDraft.lines.filter(l => [l.description, l.quantity, l.rate].some(x => String(x || "").trim()));
}

function sovDraftTotals() {
  let base = 0, bad = 0;
  const amounts = sovDraft.lines.map(l => {
    const filled = [l.description, l.quantity, l.rate].some(x => String(x || "").trim());
    if (!filled) return null;
    const q = String(l.quantity || "").trim() ? sovMilli(l.quantity) : 1000;
    const r = sovCents(l.rate);
    if (!(q > 0) || !(r > 0) || !String(l.description || "").trim()) { bad++; return NaN; }
    const a = sovExtend(q, r);
    base += a;
    return a;
  });
  const bpRaw = String(sovDraft.op_pct || "").trim();
  const bp = bpRaw ? Math.round(sovNum(bpRaw) * 100) : 0;
  const op = isNaN(bp) ? 0 : sovMarkup(base, bp);
  return { amounts, base, bp, op, total: base + op, bad, badBp: isNaN(bp) || bp < 0 || bp > 5000 };
}

function updateSovTotals() {
  if (!sovDraft) return;
  const j = state.jobs.find(x => x.id === sovDraft.jobId);
  if (!j) return;
  const t = sovDraftTotals();
  t.amounts.forEach((a, i) => {
    const el = document.getElementById(`sov-amt-${i}`);
    if (el) el.textContent = a === null ? "" : isNaN(a) ? "check" : money(a);
  });
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set("sov-sum-lines", money(t.base));
  set("sov-sum-op", t.badBp ? "check the percent" : money(t.op));
  set("sov-sum-total", money(t.total));
  const diff = t.total - j.contract_cents;
  set("sov-sum-diff", diff === 0 ? "matches the contract" : `${diff > 0 ? "+" : "−"}${money(Math.abs(diff))} vs the contract`);
  const d = document.getElementById("sov-sum-diff");
  if (d) d.className = diff === 0 ? "ok" : "blocker";
  const alt = document.querySelector('[data-action="sov-save-contract"]');
  if (alt) {
    alt.hidden = diff === 0 || t.base === 0;
    alt.textContent = `Save and change the contract to ${money(t.total)}`;
  }
}

function sovEditorHtml(j) {
  const rows = sovDraft.lines.map((l, i) => `
    <tr>
      <td><input class="sov-in sov-item" data-sov-row="${i}" data-sov-field="item" value="${esc(l.item)}" placeholder="${i + 1}"></td>
      <td><input class="sov-in sov-desc" data-sov-row="${i}" data-sov-field="description" value="${esc(l.description)}" placeholder="Rough-in wiring"></td>
      <td><input class="sov-in sov-num" data-sov-row="${i}" data-sov-field="quantity" value="${esc(l.quantity)}" placeholder="1" inputmode="decimal"></td>
      <td><input class="sov-in sov-unit" data-sov-row="${i}" data-sov-field="unit" value="${esc(l.unit)}" placeholder="LS"></td>
      <td><input class="sov-in sov-num" data-sov-row="${i}" data-sov-field="rate" value="${esc(l.rate)}" placeholder="0.00" inputmode="decimal"></td>
      <td class="num" id="sov-amt-${i}"></td>
      <td><button class="btn small secondary" data-action="sov-remove" data-row="${i}" title="Remove line">×</button></td>
    </tr>`).join("");
  return `
    <p class="sub">Each line is a quantity at a unit rate; a lump sum is quantity 1 (unit LS) at its amount. Overhead and profit goes on top of the base lines. Approved change orders add their own lines.</p>
    <div class="sov-paste">
      <textarea id="sov-paste" rows="3" placeholder="Paste rows from Excel, Google Sheets, a CSV or a PDF: description, quantity, unit, unit rate, or a lump-sum amount. A header row is optional; total rows are skipped."></textarea>
      <button class="btn small" data-action="sov-read">Read pasted rows</button>
    </div>
    ${sovDraft.warnings.length ? `<div class="sov-warn">${sovDraft.warnings.map(w => `<div>${esc(w)}</div>`).join("")}</div>` : ""}
    <div class="table-wrap"><table class="sov-table">
      <tr><th>Item</th><th>Description</th><th>Qty</th><th>Unit</th><th>Unit rate $</th><th>Scheduled value</th><th></th></tr>
      ${rows}
    </table></div>
    <div class="form-inline">
      <button class="btn small secondary" data-action="sov-add">Add line</button>
      <label for="sov-op" class="sov-op-label">Overhead and profit</label>
      <input id="sov-op" class="sov-in sov-num" data-sov-field="op_pct" value="${esc(sovDraft.op_pct)}" placeholder="0" inputmode="decimal"> %
    </div>
    <div class="sov-sums">
      <div class="money-row"><span class="label">Schedule lines</span><span id="sov-sum-lines"></span></div>
      <div class="money-row"><span class="label">Overhead and profit</span><span id="sov-sum-op"></span></div>
      <div class="money-row"><span class="label">Schedule total</span><span><strong id="sov-sum-total"></strong> · <span id="sov-sum-diff"></span></span></div>
      <div class="money-row"><span class="label">Contract</span><span>${money(j.contract_cents)}</span></div>
    </div>
    <div class="form-inline">
      <button class="btn" data-action="sov-save" data-id="${j.id}">Save schedule</button>
      <button class="btn secondary" data-action="sov-save-contract" data-id="${j.id}" hidden>Save and change the contract</button>
      <button class="btn secondary" data-action="sov-cancel">Cancel</button>
    </div>`;
}

function sovCardHtml(j) {
  const v = j.sov_view || {};
  if (sovDraft && sovDraft.jobId === j.id) {
    return `<div class="card" id="sov-card"><h2>Schedule of values</h2>${sovEditorHtml(j)}</div>`;
  }
  if (!v.present) {
    if (!v.editable) return "";
    return `<div class="card" id="sov-card">
      <h2>Schedule of values</h2>
      <p class="sub">Bill line by line, the way subs bill: quantity × unit rate plus overhead and profit. Each pay app then shows what was done on each line this period. Optional; without it, progress is entered in dollars. Set it before the first pay app.</p>
      <button class="btn secondary" data-action="sov-start" data-id="${j.id}">Set up schedule of values</button>
    </div>`;
  }
  const t = v.totals;
  const rows = v.lines.map(l => `
    <tr>
      <td>${l.item}</td>
      <td>${l.description}${l.co_id ? ' <span class="pill green">CO</span>' : ""}</td>
      <td class="num">${l.lump_sum ? "" : fmtQty(l.qty_milli) + " " + l.unit}</td>
      <td class="num">${l.lump_sum ? "LS" : money(l.rate_cents)}</td>
      <td class="num">${money(l.scheduled_cents)}</td>
      <td class="num">${l.lump_sum ? l.pct + "%" : fmtQty(l.done_milli)}${l.stored_cents ? `<div class="sub">+ ${money(l.stored_cents)} stored</div>` : ""}</td>
      <td class="num">${money(l.to_date_cents)}</td>
      <td class="num">${money(l.billed_cents)}</td>
      <td class="num">${money(l.scheduled_cents - l.to_date_cents)}</td>
    </tr>`).join("");
  return `<div class="card" id="sov-card">
    <h2>Schedule of values</h2>
    <div class="table-wrap"><table class="sov-table">
      <tr><th>Item</th><th>Description</th><th>Qty</th><th>Unit rate</th><th>Scheduled value</th><th>Done to date</th><th>Earned to date</th><th>Billed</th><th>Balance to finish</th></tr>
      ${rows}
      ${v.op_bp ? `<tr><td></td><td>Overhead and profit ${v.op_bp / 100}%</td><td></td><td></td><td class="num">${money(t.op_scheduled_cents)}</td><td></td><td class="num">${money(t.op_to_date_cents)}</td><td class="num">${money(v.op_billed_cents)}</td><td class="num">${money(t.op_scheduled_cents - t.op_to_date_cents)}</td></tr>` : ""}
      <tr class="sov-total"><td></td><td>Total</td><td></td><td></td><td class="num">${money(t.scheduled_cents)}</td><td></td><td class="num">${money(t.to_date_cents)}</td><td class="num">${money(v.lines.reduce((a, l) => a + l.billed_cents, 0) + v.op_billed_cents)}</td><td class="num">${money(t.scheduled_cents - t.to_date_cents)}</td></tr>
    </table></div>
    ${v.editable ? `<button class="btn small secondary" data-action="sov-start" data-id="${j.id}">Edit schedule</button>` : `<p class="sub">Fixed once the first pay app exists. Scope changes go through change orders, which add their own lines when approved.</p>`}
  </div>`;
}

async function sovReadPaste() {
  const box = document.getElementById("sov-paste");
  if (!box || !sovDraft) return;
  const text = box.value;
  if (!text.trim()) { toast("Paste rows into the box first."); return; }
  let res;
  try {
    res = await fetch("/api/sov/parse", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
  } catch (_) { setNetBanner(true); toast("Can't reach Billdable."); return; }
  const data = await readJson(res);
  if (!res.ok) { toast(data.error || "Couldn't read the rows."); return; }
  const kept = sovFilledLines();
  sovDraft.lines = kept.concat((data.lines || []).map(l => ({ id: "", item: l.item, description: l.description, quantity: l.quantity, unit: l.unit, rate: l.rate })));
  if (!sovDraft.lines.length) sovDraft.lines = [blankSovLine()];
  if (data.op_pct && !String(sovDraft.op_pct || "").trim()) sovDraft.op_pct = data.op_pct;
  sovDraft.warnings = [`Read ${(data.lines || []).length} line${(data.lines || []).length === 1 ? "" : "s"}. Check them below, then save.`].concat(data.warnings || []);
  render();
}

async function sovSave(jobId, updateContract) {
  const t = sovDraftTotals();
  if (t.bad) { toast("Some lines need a description, a quantity above zero and a rate above zero."); return; }
  const j = state.jobs.find(x => x.id === jobId);
  if (updateContract && !(await askConfirm(`Change the contract amount from ${money(j.contract_cents)} to ${money(t.total)} to match the schedule?`, "Change the contract"))) return;
  const lines = sovFilledLines().map(l => ({ id: l.id || undefined, item: l.item, description: l.description, quantity: l.quantity, unit: l.unit, rate: l.rate }));
  const r = await post(`/api/jobs/${jobId}/sov`, { lines, op_pct: sovDraft.op_pct, update_contract: !!updateContract });
  if (r && r.ok) { sovDraft = null; render(); }
}

async function sovProgress(jobId) {
  const done = {};
  document.querySelectorAll("[data-sovp]").forEach(el => {
    if (el.disabled) return;
    const raw = el.value.trim();
    if (!raw) return;
    if (el.dataset.lump === "1") {
      const pct = sovNum(raw);
      done[el.dataset.sovp] = isNaN(pct) ? raw : (Math.round(pct * 10) / 1000).toFixed(3);
    } else done[el.dataset.sovp] = raw;
  });
  const stored = {};
  document.querySelectorAll("[data-sovs]").forEach(el => {
    if (el.disabled) return;
    const raw = el.value.trim();
    const line = (state.jobs.find(j => j.id === jobId)?.sov_view?.lines || []).find(l => l.id === el.dataset.sovs);
    const before = line && line.stored_cents ? (line.stored_cents / 100).toFixed(2) : "";
    if (raw !== before) stored[el.dataset.sovs] = raw || "0";
  });
  const notes = document.querySelector("#progress-notes")?.value || "";
  const r = await post(`/api/jobs/${jobId}/sov-progress`, { done, stored, notes });
  if (r && r.ok) { formOpen.progress = false; render(); }
}

document.addEventListener("input", (e) => {
  const el = e.target;
  if (!sovDraft || !el || !el.dataset || !el.dataset.sovField) return;
  if (el.dataset.sovField === "op_pct") sovDraft.op_pct = el.value;
  else if (sovDraft.lines[+el.dataset.sovRow]) sovDraft.lines[+el.dataset.sovRow][el.dataset.sovField] = el.value;
  updateSovTotals();
});
document.addEventListener("paste", (e) => {
  if (e.target && e.target.id === "sov-paste") setTimeout(sovReadPaste, 0);
});

// ---------- accounts: reset, verify, invite links; Team & account ----------
// Links in emails carry a one-time token after "#", which browsers never send to a server.
let mailReady = false;
let readOnly = "";        // why this company can't be changed (the read-only demo, or a founder's hold), from /api/auth/me
let teamData = null;      // { users, invites } from /api/team (escaped)
let lastInvite = null;    // { link, emailed, email_status } after inviting
let tokenScreen = null;   // { kind: "reset" | "invite", token, info }

async function handleTokenLink(t) {
  if (t.kind === "verify") {
    const r = await postJson("/api/auth/verify", { token: t.token });
    return r.ok ? "Your email is confirmed. Thanks." : (r.data.error || "That link didn't work.");
  }
  if (t.kind === "invite") {
    const r = await postJson("/api/auth/invite-info", { token: t.token });
    tokenScreen = { kind: "invite", token: t.token, info: r.ok ? escDeep(r.data) : null, error: r.ok ? "" : (r.data.error || "") };
  } else {
    tokenScreen = { kind: "reset", token: t.token, error: "" };
  }
  renderTokenScreen();
  return null;
}

function renderTokenScreen() {
  setAiVisible(false);
  const s = tokenScreen;
  const app = document.getElementById("app");
  let inner;
  if (s.kind === "invite" && !s.info) {
    inner = `<h2>Invite not found</h2><p class="sub">${esc(s.error || "This invite is invalid or has expired. Ask for a new one.")}</p>
      <button class="btn" id="token-done">Go to sign in</button>`;
  } else if (s.kind === "invite") {
    inner = `<h2>Join ${s.info.company_name}</h2>
      <p class="sub">${s.info.invited_by ? s.info.invited_by + " invited you" : "You were invited"} as ${s.info.role}. Your sign-in email is <strong>${s.info.email}</strong>.</p>
      <form id="token-form">
        <label>Your name</label><input name="name" required autocomplete="name">
        <label>Choose a password (at least 10 characters)</label><input name="password" type="password" required autocomplete="new-password" minlength="10">
        <label class="terms-check"><input type="checkbox" name="accept_terms" required> I agree to the <a href="/terms" target="_blank" rel="noopener">Terms of Service</a> and the <a href="/privacy" target="_blank" rel="noopener">Privacy Policy</a>.</label>
        <button class="btn" type="submit">Join the team</button>
        ${s.error ? `<div class="auth-error">${esc(s.error)}</div>` : ""}
      </form>`;
  } else {
    inner = `<h2>Choose a new password</h2>
      <p class="sub">At least 10 characters. Every other device is signed out when you save it.</p>
      <form id="token-form">
        <label>New password</label><input name="password" type="password" required autocomplete="new-password" minlength="10">
        <button class="btn" type="submit">Save and sign in</button>
        ${s.error ? `<div class="auth-error">${esc(s.error)}</div>` : ""}
      </form>`;
  }
  app.innerHTML = `<div class="landing"><header class="land-bar"><div class="brand">Billdable</div></header>
    <section class="land-hero"><div class="auth-card land-form" id="token-card">${inner}</div></section></div>`;
  document.getElementById("token-done")?.addEventListener("click", () => { tokenScreen = null; authMode = "login"; renderLanding(); });
  document.getElementById("token-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    if (f.password.value.length < 10) { s.error = "Password must be at least 10 characters."; renderTokenScreen(); return; }
    if (s.kind === "invite" && !f.accept_terms.checked) { s.error = "To join, accept the Terms of Service and the Privacy Policy."; renderTokenScreen(); return; }
    const r = s.kind === "invite"
      ? await postJson("/api/auth/accept-invite", { token: s.token, name: f.name.value, password: f.password.value, accept_terms: true })
      : await postJson("/api/auth/reset", { token: s.token, password: f.password.value });
    if (!r.ok) { s.error = r.data.error || "That didn't work."; renderTokenScreen(); return; }
    tokenScreen = null;
    await boot(false);
    toast(s.kind === "invite" ? "Welcome to the team." : "Password changed. You're signed in.");
  });
}

function renderForgot(message = "", error = "") {
  const card = document.getElementById("signup");
  if (!card) return;
  card.innerHTML = `<h2>Reset your password</h2>
    <p class="sub">We'll email a link that works once, for one hour.</p>
    <form id="forgot-form">
      <label>Email</label><input name="email" type="email" required autocomplete="username">
      <button class="btn" type="submit">Send the link</button>
      ${message ? `<div class="sub">${esc(message)}</div>` : ""}${error ? `<div class="auth-error">${esc(error)}</div>` : ""}
    </form>
    <div class="auth-toggle"><button type="button" id="forgot-back">Back to sign in</button></div>`;
  document.getElementById("forgot-back").addEventListener("click", () => { authMode = "login"; renderLanding(); });
  document.getElementById("forgot-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = await postJson("/api/auth/forgot", { email: e.target.email.value });
    if (r.ok) renderForgot(r.data.message || "");
    else renderForgot("", r.data.error || "That didn't work.");
  });
}

async function loadTeam() {
  try {
    const res = await fetch("/api/team");
    if (!res.ok) return;
    teamData = escDeep(await readJson(res));
  } catch (_) { setNetBanner(true); return; }
  if (currentView === "account") render();
}

async function teamPost(path, body, okMessage) {
  const r = await postJson(path, body);
  if (!r.ok) { toast(r.data.error || "That didn't work."); return null; }
  if (r.data.users) teamData = escDeep({ users: r.data.users, invites: r.data.invites || [] });
  if (okMessage) toast(okMessage);
  render();
  return r.data;
}

function verifyBannerHtml() {
  if (readOnly) return `<div class="verify-banner read-only-banner" role="status">${readOnly}</div>`;
  if (!mailReady || !me || me.email_verified || me.company_id === DEMO_COMPANY_ID) return "";
  return `<div class="verify-banner">Confirm your email (${me.email}) so Billdable can send pay apps for you; replies come back to it.
    <button class="linkish" data-action="resend-verify">Send the link again</button></div>`;
}

function accountView() {
  if (!teamData) loadTeam();
  const owner = me.role === "owner";
  const isDemo = me.company_id === DEMO_COMPANY_ID;
  const users = teamData ? teamData.users : [];
  const invites = teamData ? teamData.invites : [];
  const verified = me.email_verified
    ? `<span class="pill green">confirmed</span>`
    : `<span class="pill amber">not confirmed</span> ${mailReady ? `<button class="btn small secondary" data-action="resend-verify">Send the link again</button>` : `<span class="sub">Confirming needs email set up on the server.</span>`}`;
  return `
    <h1>Team & account</h1>
    <div class="card">
      <h2>You</h2>
      <div class="money-row"><span class="label">Name</span><span>${me.name}</span></div>
      <div class="money-row"><span class="label">Email</span><span>${me.email} ${isDemo ? "" : verified}</span></div>
      <div class="money-row"><span class="label">Role</span><span>${words(me.role)}</span></div>
      ${isDemo ? "" : `<div class="form-inline">
        <input id="pw-current" type="password" placeholder="Current password" autocomplete="current-password">
        <input id="pw-new" type="password" placeholder="New password (10+ characters)" autocomplete="new-password">
        <button class="btn small" data-action="change-password">Change password</button>
      </div>`}
    </div>
    <div class="card">
      <h2>Team</h2>
      <p class="sub">Owners manage the team, inbound addresses, reset and deletion. Members do everything else. Every action is in the Audit with the person's name.</p>
      <div class="table-wrap"><table>
        <tr><th>Name</th><th>Email</th><th>Role</th><th>Joined</th><th></th></tr>
        ${users.map(u => `<tr><td>${u.name}${u.id === me.id ? " (you)" : ""}</td><td>${u.email}</td><td>${words(u.role)}</td><td>${fmtDate(u.created_at)}</td>
          <td>${owner && !isDemo && u.id !== me.id ? `
            <button class="btn small secondary" data-action="team-role" data-user="${u.id}" data-role="${u.role === "owner" ? "member" : "owner"}">Make ${u.role === "owner" ? "member" : "owner"}</button>
            <button class="btn small secondary" data-action="team-remove" data-user="${u.id}" data-name="${u.name}">Remove</button>` : ""}</td></tr>`).join("")}
      </table></div>
      ${owner && !isDemo ? `
        <h3>Invite someone</h3>
        <div class="form-inline">
          <input id="invite-email" type="email" placeholder="Their email" class="grow">
          <select id="invite-role"><option value="member">Member</option><option value="owner">Owner</option></select>
          <button class="btn small" data-action="team-invite">Invite</button>
        </div>
        ${lastInvite ? `<div class="invite-result">${lastInvite.emailed ? `Invite emailed to ${esc(lastInvite.email)}.` : `Not emailed (${esc(lastInvite.email_status)}). Send them this link yourself; it works once, for 7 days:`}
          <div class="copy-row"><code id="invite-link">${esc(lastInvite.link)}</code><button class="btn small secondary" data-action="copy" data-target="invite-link">Copy</button></div></div>` : ""}
        ${invites.length ? `<h3>Open invites</h3>${invites.map(i => `<div class="list-item">${i.email} · ${i.role} · invited by ${i.invited_by} · expires ${fmtDate(i.expires_at)}
          <button class="btn small secondary" data-action="team-revoke" data-invite="${i.id}">Revoke</button></div>`).join("")}` : ""}` : ""}
    </div>
    ${isDemo ? "" : `<div class="card" id="two-step">
      <h2>Two-step sign-in</h2>
      ${recoveryCodes ? `<div class="invite-result" role="status"><strong>Two-step sign-in is on.</strong> Save these recovery codes somewhere safe, such as your password manager. Each one signs you in once if you lose your phone. They are not shown again.
        <div class="copy-row"><code id="recovery-codes">${recoveryCodes.join("  ")}</code><button class="btn small secondary" data-action="copy" data-target="recovery-codes">Copy</button><button class="btn small" data-action="2fa-done">I saved them</button></div></div>`
      : me.two_step ? `<p class="sub"><span class="pill green">On</span> Signing in asks for a code from your authenticator app.</p>
        <details><summary>Turn it off</summary><div class="form-inline">
          <input id="2fa-off-pw" type="password" placeholder="Your password" autocomplete="current-password">
          <input id="2fa-off-code" placeholder="Code from the app" autocomplete="one-time-code" inputmode="numeric">
          <button class="btn small secondary" data-action="2fa-disable">Turn off</button></div></details>`
      : twoStepSetup ? `<ol class="steps">
          <li>In an authenticator app (Google Authenticator, 1Password, Authy, Microsoft Authenticator), add an account and choose to enter a key. ${/Mobi|Android|iPhone/.test(navigator.userAgent) ? `Or <a href="${esc(unesc(twoStepSetup.uri))}">open it in the app</a>.` : ""}</li>
          <li>Key: <code id="2fa-key">${twoStepSetup.secret.match(/.{1,4}/g).join(" ")}</code> <button class="btn small secondary" data-action="copy" data-target="2fa-key">Copy</button></li>
          <li>Type the 6-digit code the app shows: <span class="form-inline"><input id="2fa-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="123456"><button class="btn small" data-action="2fa-enable">Turn on</button></span></li>
        </ol>`
      : `<p class="sub"><span class="pill">Off</span> Add a code from your phone to every sign-in, so a stolen password isn't enough. Owners should turn this on before billing goes live.</p>
        <button class="btn small" data-action="2fa-begin">Set up two-step sign-in</button>`}
    </div>`}
    <div class="card">
      <h2>Your data</h2>
      <p class="sub">Download everything in your company as one file, or start over.</p>
      <div class="form-inline">
        <a class="btn small secondary" href="/api/export" download>Export data</a>
        ${owner ? `<button class="btn small secondary" id="reset" aria-expanded="${!!formOpen.reset}">${isDemo ? "Reset the demo" : "Reset data"}</button>` : ""}
      </div>
      ${formOpen.reset && !isDemo ? `<div class="form-inline">
        <label for="reset-confirm" class="inline-label">Reset deletes every job, payer and record; a snapshot is kept on the server. Type the company name to confirm:</label>
        <input id="reset-confirm" placeholder="${state.company.name}" class="grow">
        <button class="btn small danger" data-action="reset-confirm">Reset everything</button></div>` : ""}
    </div>
    ${owner && !isDemo ? `
    <div class="card danger-zone">
      <h2>Delete the company</h2>
      <p class="sub">Deletes every job, file, payer and record, and every user on this team. Export your data first if you want a copy. Server backups that already hold it expire on their own (the newest 14 are kept by default).</p>
      <div class="form-inline">
        <input id="del-password" type="password" placeholder="Your password" autocomplete="current-password">
        <input id="del-confirm" placeholder="Type the company name: ${state.company.name}" class="grow">
        <button class="btn small danger" data-action="delete-account">Delete everything</button>
      </div>
    </div>` : ""}
  `;
}

function trialBannerHtml() {
  const t = state.trial;
  if (!t || !t.tracked) return "";
  const day = (iso) => new Date(unesc(iso)).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  if (t.state === "active") {
    return `<div class="trial-banner">Free trial: nothing to pay until the first pay app you send through Billdable is paid, or until ${day(t.ends_by)}${t.extended ? " (extended because a pay app is still unpaid)" : ""}. Billing isn't live yet.</div>`;
  }
  return `<div class="trial-banner">Your free trial ended on ${day(t.ended_at)}${t.reason === "paid" ? `, when ${t.first_paid} on ${t.first_paid_job} was paid` : ""}. Billing isn't live yet, so nothing changes for now.</div>`;
}

function onboardingHtml() {
  if (!state || state.company.onboarding_dismissed || (me && me.company_id === DEMO_COMPANY_ID)) return "";
  const jobs = state.jobs;
  const invoices = jobs.flatMap(j => j.invoices);
  const steps = [
    ["Create your first job: contract amount, payer, approver, AP email, retainage.", jobs.length > 0, "onb-newjob", "New job"],
    ["Put the contract and paperwork on its record: drop files on the job, or bring emails and texts in through the Inbox.", jobs.some(j => (j.records || []).length), "onb-job", "Open the job"],
    ["Optional: set up a schedule of values to bill line by line (paste it from your estimate).", jobs.some(j => j.sov_view && j.sov_view.present), "onb-job", "Open the job"],
    ["Record progress, or mark the job done. Billdable drafts the pay app and lists the packet the payer requires.", invoices.length > 0, "onb-job", "Open the job"],
    ["Clear the packet, then Approve & send.", invoices.some(i => i.sent_at), "onb-job", "Open the job"],
  ];
  if (steps[4][1]) return "";
  const OPTIONAL = 2;  // the schedule of values is optional, so it doesn't count toward "done"
  const required = steps.filter((s, i) => i !== OPTIONAL);
  const done = required.filter(s => s[1]).length;
  const next = steps.findIndex((s, i) => i !== OPTIONAL && !s[1]);
  return `<div class="card onboarding">
    <button class="help-dismiss" data-action="onb-dismiss" title="Hide this">×</button>
    <h2>Get your first pay app out</h2>
    <p class="sub">${done} of ${required.length} steps done (step 3 is optional). The Help page explains every term.</p>
    <ol class="onb-steps">${steps.map((s, i) => `<li class="${s[1] ? "done" : i === next ? "next" : ""}">${s[1] ? "✓ " : ""}${s[0]}
      ${i === next && (i === 0 || jobs.length) ? ` <button class="btn small" data-action="${s[2]}">${s[3]}</button>` : ""}</li>`).join("")}</ol>
  </div>`;
}

let adminCompanies = null;
async function loadAdminCompanies() {
  try {
    const res = await fetch("/api/admin/companies");
    if (!res.ok) return;
    adminCompanies = escDeep((await readJson(res)).companies);
  } catch (_) { return; }
  const el = document.getElementById("admin-companies");
  if (el) el.innerHTML = adminCompaniesHtml();
}

function adminCompaniesHtml() {
  if (!adminCompanies) return "Loading…";
  const trialText = (t) => !t.tracked ? "not tracked (signed up before trials)" : t.state === "active"
    ? `active until ${fmtDate(t.ends_by)}${t.extended ? " (extended)" : ""}` : `ended ${fmtDate(t.ended_at)}${t.reason === "paid" ? ` (${t.first_paid} paid)` : " (time)"}`;
  return `<div class="table-wrap"><table>
    <tr><th>Company</th><th>Id</th><th>Signed up</th><th>Users</th><th>Jobs</th><th>Pay apps sent</th><th>Trial</th></tr>
    ${adminCompanies.filter(c => !c.demo).map(c => `<tr><td>${c.name}</td><td><code>${c.company_id || ""}</code></td><td>${fmtDate(c.signed_up)}</td><td>${c.users}</td><td>${c.jobs}</td><td>${c.pay_apps_sent}</td><td>${trialText(c.trial)}</td></tr>`).join("")}
  </table></div>`;
}

function recordFiles(r) {
  return (r.file_ids || []).map(fid => {
    const f = (state.files || []).find(x => x.id === fid);
    return f ? `<a href="/api/files/${f.id}" target="_blank" rel="noopener">${f.name}</a>` : "";
  }).filter(Boolean).join(" · ");
}

function inboxItemCard(i) {
  const openJobs = state.jobs.filter(j => !j.closed);
  const files = (i.file_ids || []).map(fid => (state.files || []).find(x => x.id === fid)).filter(Boolean);
  const title = i.subject || (files[0] && files[0].name) || (i.text || "").split("\n")[0].slice(0, 80) || "Untitled";
  const preview = (i.text || "").slice(0, 600);
  const kinds = Object.entries(RECORD_KINDS).map(([k, v]) => `<option value="${k}" ${i.kind === k ? "selected" : ""}>${v}</option>`).join("");
  const jobs = `<option value="">Pick a job…</option>` + openJobs.map(j => `<option value="${j.id}" ${i.suggested_job_id === j.id ? "selected" : ""}>${j.name} (${j.code})</option>`).join("");
  return `<div class="inbox-item">
    <div class="inbox-head"><span class="pill">${SOURCE_LABELS[i.source] || i.source}</span> <strong>${title}</strong>
      <span class="sub">${i.from ? "from " + i.from + " · " : ""}${fmtDate(i.sent_at || i.received_at)}${i.note_date ? " · dated " + fmtDate(i.note_date) : ""}</span></div>
    ${preview ? `<details><summary>${preview.slice(0, 140)}${preview.length > 140 ? "…" : ""}</summary><pre class="inbox-text">${preview}</pre></details>` : ""}
    ${files.length ? `<div class="sub">Files: ${files.map(f => `<a href="/api/files/${f.id}" target="_blank" rel="noopener">${f.name}</a>`).join(" · ")}</div>` : ""}
    ${(i.amounts || []).length || (i.dates || []).length ? `<div class="sub">Found: ${(i.amounts || []).map(a => money(a)).join(", ")}${(i.amounts || []).length && (i.dates || []).length ? " · " : ""}${(i.dates || []).join(", ")}</div>` : ""}
    ${(i.skipped_attachments || []).length ? `<div class="field-error">${i.skipped_attachments.join(" ")}</div>` : ""}
    <div class="sub">${i.suggested_job_id ? "Suggested because it " + i.match_reason : i.match_reason}</div>
    ${i.says_done_job_id ? `<div class="notice says-done">This says the job is done. <button class="btn small" data-action="inbox-mark-done" data-item="${i.id}">Mark ${(state.jobs.find(j => j.id === i.says_done_job_id) || {}).name || "the job"} done and draft the pay app</button></div>` : ""}
    <div class="form-inline">
      <select id="inb-job-${i.id}" aria-label="Job">${jobs}</select>
      <select id="inb-kind-${i.id}" aria-label="Kind">${kinds}</select>
      <button class="btn small" data-action="inbox-file" data-item="${i.id}">File to job record</button>
      <button class="btn small secondary" data-action="inbox-dismiss" data-item="${i.id}">Dismiss</button>
    </div>
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
    jobs = jobs.filter(j => j.invoices.some(i => i.status === "sent"));
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

function billedThisMonth() {
  const now = new Date();
  let total = 0;
  state.jobs.forEach(j => j.invoices.forEach(inv => {
    if (inv.sent_at && new Date(inv.sent_at).getMonth() === now.getMonth() && new Date(inv.sent_at).getFullYear() === now.getFullYear()) {
      total += inv.amount_cents;
    }
  }));
  return total;
}

let helperReview = null;
let helperDraft = null;   // the answer being written or edited
const HELPER_VIEWS = ["inbox", "dashboard", "daily", "jobs", "calendar", "payapps", "packets", "cos", "collections", "payments", "waivers", "retainage", "history", "playbook", "outbox", "audit", "help"];

async function loadHelperReview() {
  const body = document.getElementById("helper-admin-body");
  if (!body) return;
  try {
    const res = await fetch("/api/helper/review");
    const data = await readJson(res);
    if (!res.ok) { body.textContent = data.error || "Could not load."; return; }
    helperReview = escDeep(data);
  } catch (_) { setNetBanner(true); return; }
  renderHelperAdminBody();
}

function helperEditor() {
  const d = helperDraft || {};
  const handlers = (helperReview && helperReview.handlers) || [];
  const navAction = (d.actions || []).find(a => a.type === "nav");
  return `<div class="card" id="helper-editor">
    <h2>${d.id ? "Edit answer" : "New answer"}</h2>
    <div class="form-grid">
      <div class="span-2"><label for="ha-questions">Example questions, one per line</label><textarea id="ha-questions" rows="4">${esc((d.questions || []).join("\n"))}</textarea></div>
      <div class="span-2"><label for="ha-answer">Answer</label><textarea id="ha-answer" rows="6" placeholder="Plain words. Start a line with - for a bullet.">${esc(d.answer || "")}</textarea></div>
      <div><label for="ha-category">Category</label><input id="ha-category" value="${esc(d.category || "General")}"></div>
      <div><label for="ha-keywords">Keywords, comma separated</label><input id="ha-keywords" value="${esc((d.keywords || []).join(", "))}"></div>
      <div><label for="ha-handler">Add live data from the company's jobs</label><select id="ha-handler"><option value="">None</option>${handlers.map(h => `<option ${unesc(h) === d.handler ? "selected" : ""}>${h}</option>`).join("")}</select></div>
      <div><label for="ha-nav">Button to open a page</label><select id="ha-nav"><option value="">None</option>${HELPER_VIEWS.map(v => `<option value="${v}" ${navAction && navAction.view === v ? "selected" : ""}>${v}</option>`).join("")}</select></div>
      <div class="span-2"><label for="ha-follow">Suggested next questions, one per line</label><textarea id="ha-follow" rows="2">${esc((d.follow_ups || []).join("\n"))}</textarea></div>
    </div>
    <div class="form-inline"><button class="btn" id="ha-save">Save answer</button><button class="btn secondary" id="ha-cancel">Cancel</button>
    <span class="sub">Saved answers work right away for every company. Same id as a shipped answer replaces it.</span></div>
  </div>`;
}

function renderHelperAdminBody() {
  const body = document.getElementById("helper-admin-body");
  if (!body || !helperReview) return;
  const r = helperReview;
  const s = r.stats;
  body.innerHTML = `
    <div class="metrics">
      <div class="metric"><div class="k">Questions asked</div><div class="v">${s.questions}</div></div>
      <div class="metric"><div class="k">Answered</div><div class="v">${s.answered}</div></div>
      <div class="metric"><div class="k">Marked helpful</div><div class="v">${s.helpful}</div></div>
      <div class="metric"><div class="k">Marked not helpful</div><div class="v">${s.unhelpful}</div></div>
    </div>
    ${helperDraft ? helperEditor() : `<button class="btn" id="ha-new">Write a new answer</button>`}
    <div class="card"><h2>Questions without a good answer (${r.open.length})</h2>
      ${r.open.length ? r.open.map((g, i) => `<div class="list-item"><strong>${g.examples[0]}</strong> <span class="sub">asked ${g.count}×${g.unhelpful ? `, ${g.unhelpful} marked not helpful` : ""}${g.top_guess ? `, closest answer: ${g.top_guess}` : ""}</span>
        ${g.examples.length > 1 ? `<div class="sub">Also: ${g.examples.slice(1).join(" · ")}</div>` : ""}
        <div class="form-inline"><button class="btn small" data-ha-answer="${i}">Write an answer</button>${g.top_guess ? `<button class="btn small secondary" data-ha-extend="${i}">Add to "${g.top_guess}"</button>` : ""}<button class="btn small secondary" data-ha-resolve="${i}">Not needed</button></div></div>`).join("")
        : emptyState("Nothing open", "Every question so far found an answer people found helpful.")}
    </div>
    <details class="card"><summary><strong>All answers (${r.answers.length})</strong></summary>
      ${r.answers.map((a, i) => `<div class="list-item"><strong>${a.questions[0]}</strong> <span class="sub">${a.category} · ${a.id} · ${a.origin}${a.handler ? " · live: " + a.handler : ""}</span> <button class="btn small secondary" data-ha-edit="${i}">Edit</button></div>`).join("")}
    </details>`;
  const raw = unesc;   // values below are decoded for the editor, which escapes them again
  const rawDeep = (o) => typeof o === "string" ? raw(o) : Array.isArray(o) ? o.map(rawDeep) : (o && typeof o === "object") ? Object.fromEntries(Object.entries(o).map(([k, v]) => [k, rawDeep(v)])) : o;
  body.querySelector("#ha-new")?.addEventListener("click", () => { helperDraft = { questions: [] }; renderHelperAdminBody(); });
  body.querySelectorAll("[data-ha-answer]").forEach(b => b.addEventListener("click", () => {
    helperDraft = { questions: rawDeep(r.open[Number(b.dataset.haAnswer)].examples) }; renderHelperAdminBody();
    document.getElementById("helper-editor")?.scrollIntoView({ behavior: "smooth" });
  }));
  body.querySelectorAll("[data-ha-extend]").forEach(b => b.addEventListener("click", () => {
    const g = r.open[Number(b.dataset.haExtend)];
    const base = r.answers.find(a => a.id === g.top_guess);
    if (!base) return;
    helperDraft = rawDeep({ ...base, questions: [...base.questions, ...g.examples] }); renderHelperAdminBody();
    document.getElementById("helper-editor")?.scrollIntoView({ behavior: "smooth" });
  }));
  body.querySelectorAll("[data-ha-edit]").forEach(b => b.addEventListener("click", () => {
    helperDraft = rawDeep(r.answers[Number(b.dataset.haEdit)]); renderHelperAdminBody();
    document.getElementById("helper-editor")?.scrollIntoView({ behavior: "smooth" });
  }));
  body.querySelectorAll("[data-ha-resolve]").forEach(b => b.addEventListener("click", async () => {
    const g = r.open[Number(b.dataset.haResolve)];
    const res = await fetch("/api/helper/resolve", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ normalized: raw(g.normalized) }) });
    if (res.ok) { helperReview = escDeep(await readJson(res)); renderHelperAdminBody(); }
  }));
  body.querySelector("#ha-cancel")?.addEventListener("click", () => { helperDraft = null; renderHelperAdminBody(); });
  body.querySelector("#ha-save")?.addEventListener("click", async () => {
    const lines = (id) => (document.getElementById(id).value || "").split("\n").map(x => x.trim()).filter(Boolean);
    const navView = document.getElementById("ha-nav").value;
    const payload = {
      id: helperDraft && helperDraft.id ? helperDraft.id : "",
      questions: lines("ha-questions"),
      answer: document.getElementById("ha-answer").value,
      category: document.getElementById("ha-category").value,
      keywords: document.getElementById("ha-keywords").value.split(",").map(x => x.trim()).filter(Boolean),
      handler: document.getElementById("ha-handler").value,
      needs_job: ["draft_followup", "job_status"].includes(document.getElementById("ha-handler").value),
      actions: navView ? [{ type: "nav", view: navView, label: "Open " + navView.charAt(0).toUpperCase() + navView.slice(1) }] : [],
      follow_ups: lines("ha-follow"),
    };
    const res = await fetch("/api/helper/answers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const data = await readJson(res);
    if (!res.ok) { toast(data.error || "Not saved."); return; }
    helperReview = escDeep(data);
    helperDraft = null;
    toast("Answer saved. It works right away.");
    renderHelperAdminBody();
  });
}

const views = {
  account() { return accountView(); },
  "helper-admin"() {
    return `<h1>Helper answers</h1>
      <p class="sub">What customers asked the helper that it couldn't answer well, and the answers it uses. Write an answer once; every company gets it right away. Only accounts listed in HELPER_ADMIN_USER_IDS see this page.</p>
      <div id="helper-admin-body">Loading…</div>
      <div class="card"><h2>Companies</h2><p class="sub">Every company on this server, with its trial. Billing isn't live, so nothing is enforced.</p><div id="admin-companies">${adminCompaniesHtml()}</div></div>`;
  },
  inbox() {
    const items = (state.inbox || []).filter(i => i.status === "new").slice().reverse();
    const done = (state.inbox || []).filter(i => i.status !== "new").slice(-20).reverse();
    const withSuggestion = items.filter(i => i.suggested_job_id).length;
    const jobName = (id) => (state.jobs.find(j => j.id === id) || {}).name || "a job";
    return `
      <h1>Inbox</h1>
      <p class="sub">Bring in photos, PDFs, saved emails, text threads and notes. Billdable suggests the job from the job code, address, name or sender; you file it with one click. Nothing is filed without you.</p>
      <div class="dropzone" tabindex="0">
        <strong>Drop files anywhere on this page</strong>
        <span class="sub">Photos, PDFs, Word, Excel, CSV, saved emails (.eml), up to ${MAX_UPLOAD_MB} MB each. Or <button class="linkish" data-action="pick-files">choose files</button>, or press Ctrl+V / ⌘V to paste a screenshot. On a phone, choose files opens the camera too.</span>
      </div>
      <div class="card">
        <h2>Paste an email, a text thread, or notes</h2>
        <div class="form-inline">
          <select id="paste-source" aria-label="What are you pasting?">
            <option value="email">An email</option>
            <option value="sms">Text messages</option>
            <option value="notes">Notes: one daily record per date</option>
            <option value="paste">Something else</option>
          </select>
        </div>
        <textarea id="paste-text" rows="6" placeholder="Paste here. For notes, start each day with a date, for example:&#10;10/12 Framing done on Oak Street, 4 crew&#10;10/13 Inspector passed rough-in"></textarea>
        <div class="form-inline"><button class="btn" data-action="import-text">Add to Inbox</button>
        <span class="sub">Notes split at every line that starts with a date (10/12, Oct 12, 2026-10-12). Exported Apple Notes or a typed-up notebook work.</span></div>
      </div>
      <div class="card">
        <div class="top"><h2>To file (${items.length})</h2>${withSuggestion > 1 ? `<button class="btn small" data-action="inbox-file-all">File all ${withSuggestion} with a suggested job</button>` : ""}</div>
        ${items.length ? items.map(inboxItemCard).join("") : emptyState("Nothing waiting", "Drop files, paste an email or notes above, or set up email and text forwarding below.")}
      </div>
      ${done.length ? `<details class="card"><summary><strong>Recently filed or dismissed</strong></summary>
        ${done.map(i => `<div class="list-item">${i.status === "filed" ? `${i.auto_filed ? "Reply filed automatically" : "Filed"} to ${jobName(i.filed_job_id)} as ${RECORD_KINDS[i.filed_kind] || i.filed_kind}` : "Dismissed"}: ${i.subject || (i.text || "").slice(0, 60) || "item"} <button class="btn small secondary" data-action="inbox-undo" data-item="${i.id}">Undo</button></div>`).join("")}
      </details>` : ""}
      <details class="card">
        <summary><strong>Bring emails and texts in automatically</strong></summary>
        <h3>Email</h3>
        <p id="email-address" class="sub">Loading…</p>
        <p class="sub">Steps: in an email service that can post incoming mail to a web address (SendGrid Inbound Parse, Mailgun routes or Postmark inbound), set up an inbound address and point it at this private web address. Then forward or BCC job emails to that inbound address. Attachments come in as files.</p>
        <div class="copy-row"><code id="email-webhook">Loading…</code><button class="btn small secondary" data-action="copy" data-target="email-webhook">Copy</button><button class="btn small secondary" data-action="rotate-token" data-kind="email">Make a new address</button></div>
        <h3>Text messages</h3>
        <p id="sms-number" class="sub">Loading…</p>
        <p class="sub">Steps: in Twilio, buy a number and set its incoming-message webhook to this private web address. Set TWILIO_AUTH_TOKEN on the server so Billdable checks each message really came from Twilio. Photos sent by text show as links for now; they are not downloaded.</p>
        <div class="copy-row"><code id="sms-webhook">Loading…</code><button class="btn small secondary" data-action="copy" data-target="sms-webhook">Copy</button><button class="btn small secondary" data-action="rotate-token" data-kind="sms">Make a new address</button></div>
        <p class="sub">Anyone who knows these addresses can drop items in your Inbox. They are reviewed before filing. Make a new address if one leaks.</p>
      </details>
    `;
  },
  help() {
    const glossary = [
      ["Pay app (pay application)", "The bill for work done this period. It lists what was completed, at the contract rates, plus signed change orders, less retainage."],
      ["Pencil copy (pencil draft)", "A draft of the pay app sent to the owner or GC to agree quantities before the formal one. It avoids \"push it to next month\"."],
      ["Packet", "Everything the contract says must go with a pay app: lien waivers, delivery tickets, photos, signed change orders, schedule of values, insurance certificate."],
      ["Schedule of values", "The contract broken into line items with a value each. Each pay app bills a percentage of each line."],
      ["Change order (CO)", "Extra or changed work. Bill it only after the customer signs it."],
      ["Retainage", "A percentage (often 5 to 10) the payer holds back from every pay app until the job is closed out."],
      ["Punch list", "Small fixes found at final inspection: a dead outlet, a loose railing. Finishing it is what releases retainage."],
      ["Closeout packet", "The documents needed to finish the job and release retainage: punch-list signoff, final waivers, warranties."],
      ["Lien waiver", "A document giving up lien rights for money already paid. Conditional: only if the payment clears. Unconditional: payment received. Forms are set by state law in some states."],
      ["Pay-when-paid / pay-if-paid", "Contract terms that tie a sub's payment to the GC being paid. Pay-if-paid shifts the risk to the sub; some states limit it."],
      ["Payer playbook", "What each customer requires, who approves, how long they take, and why they reject pay apps."],
    ];
    return `
      <h1>Help</h1>
      <p class="sub">Hover any label with an (i) to see what it means. Tap it on a phone.</p>
      <div class="card">
        <h2>First job, start to finish</h2>
        <ol class="steps">
          <li>Jobs → New job: contract amount, payer, approver, AP email, retainage percent.</li>
          <li>Drop the contract, photos and tickets on the job record, or bring emails and texts in through the Inbox.</li>
          <li>Progress pay app: enter completed-to-date. Billdable drafts the pay app, withholds retainage and lists the packet.</li>
          <li>Optional: send a pencil copy so the payer agrees the numbers first.</li>
          <li>Attach records to each packet item. Approve and send unlocks when the packet is complete.</li>
          <li>Match the payment when it arrives, then send the waiver for that pay app.</li>
          <li>Mark done, finish the punch list (closeout packet), ask for retainage, record it when paid, close the job.</li>
        </ol>
      </div>
      <div class="card"><h2>Words you will see</h2>
        <dl class="glossary">${glossary.map(([t, d]) => `<dt>${t}</dt><dd>${d}</dd>`).join("")}</dl>
      </div>
      <p class="sub">Your account id: <code>${me ? me.id : ""}</code> (the Billdable team uses it to set up access).</p>
    `;
  },
  dashboard() {
    const t = state.totals;
    const jobs = filteredJobs();
    const isDemo = me && me.company_id === DEMO_COMPANY_ID;
    const open = state.jobs.filter(j => !j.closed);
    const actionJobs = open.filter(j => isBlocked(j) || (j.waiver_due && !j.waiver_sent) || j.invoices.some(i => ["draft", "awaiting_approval"].includes(i.status)));
    const doneMessages = (state.inbox || []).filter(i => i.status === "new" && i.says_done_job_id);
    const hero = t.unbilled_cents > 0 ? "unbilled" : t.outstanding_cents > 0 ? "outstanding" : "";
    const sortHead = (key, label) => `<th data-action="sort" data-sort="${key}" aria-sort="${dashSort === key ? (dashSortDir > 0 ? "ascending" : "descending") : "none"}">${label} ${dashSort === key ? (dashSortDir > 0 ? "↑" : "↓") : ""}</th>`;
    const stage = (key, label) => `<button class="stage ${dashFilter === key ? "active" : ""}" data-action="filter" data-filter="${key}" aria-pressed="${dashFilter === key}">${label}</button>`;
    return `
      <h1>Today</h1>
      ${trialBannerHtml()}
      ${onboardingHtml()}
      ${!state.jobs.length && !state.closed_jobs_total && (state.company.onboarding_dismissed || isDemo) ? emptyState("No jobs yet", "Create the first job. Its record holds notes and files until a pay app needs them.", `<button class="btn" data-action="onb-newjob">New job</button>`) : ""}
      ${isDemo && !helpDismissed && state.jobs.length ? `
        <div class="help-card">
          <button class="help-dismiss" data-action="dismiss-help" aria-label="Hide this tip">×</button>
          <strong>Try the demo:</strong> open a job, clear its blockers, Approve & send, match the payment, send the waiver, then close it for the record.
        </div>` : ""}
      ${state.jobs.length ? `
      <section class="card action-queue" aria-labelledby="needs-action">
        <h2 id="needs-action">Needs you${actionJobs.length + doneMessages.length ? ` <span class="count-badge">${actionJobs.length + doneMessages.length}</span>` : ""}</h2>
        ${doneMessages.map(i => `<div class="action-item"><div class="action-text"><span class="pill copper">Job's done?</span> <strong>${(state.jobs.find(j => j.id === i.says_done_job_id) || {}).name || ""}</strong><div class="next-action">${SOURCE_LABELS[i.source] || i.source}${i.from ? " from " + i.from : ""}: ${(i.text || i.subject || "").split("\n")[0].slice(0, 120)}</div></div><button class="btn small" data-action="inbox-mark-done" data-item="${i.id}">Mark done and draft</button></div>`).join("")}
        ${actionJobs.length || doneMessages.length ? actionJobs.map(j => `
          <div class="action-item">
            <div class="action-text">${statusPill(j)} <strong>${j.name}</strong><div class="next-action">${nextStep(j)}</div></div>
            <button class="btn small" data-action="open-job" data-id="${j.id}">Open</button>
          </div>`).join("") : `<p class="sub">Nothing is waiting on you. Jobs show up here when a pay app is ready to send, a packet is blocked, or a waiver is due.</p>`}
      </section>` : ""}
      <div class="metrics">
        <div class="metric ${hero === "unbilled" ? "highlight" : ""}"><div class="k">Unbilled completed</div><div class="v">${money(t.unbilled_cents)}</div></div>
        <div class="metric ${hero === "outstanding" ? "highlight" : ""}"><div class="k">Outstanding</div><div class="v">${money(t.outstanding_cents)}</div></div>
        <div class="metric"><div class="k">Retainage open</div><div class="v">${money(t.retainage_cents)}</div></div>
        <div class="metric"><div class="k">Billed this month</div><div class="v">${money(billedThisMonth())}</div></div>
      </div>
      <p class="sub">${open.length} active job${open.length === 1 ? "" : "s"}${t.blocked_jobs ? ` · ${t.blocked_jobs} blocked` : ""}</p>
      ${state.jobs.length ? `
      <div class="stages" role="group" aria-label="Show jobs">
        ${stage("all", "Open and recent")}${stage("draft", "Ready to send")}${stage("blocked", "Blocked")}${stage("sent", "Sent")}${stage("waiver", "Waiver due")}${stage("complete", "Completed this month")}
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
            ${sortHead("retainage", "Retainage")}
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
  jobs() {
    const open = state.jobs.filter(j => !j.closed);
    const closed = state.closed_jobs_total || 0;
    const err = (fid) => jobFormErrors[fid] ? `<span class="field-error" id="${fid}-err">${esc(jobFormErrors[fid])}</span>` : "";
    const inv = (fid) => jobFormErrors[fid] ? ` aria-invalid="true" aria-describedby="${fid}-err"` : "";
    return `
      <div class="top">
        <h1>Jobs</h1>
        <button class="btn" data-action="toggle-form" data-form="newjob">${formOpen.newjob ? "Cancel" : "New job"}</button>
      </div>
      ${formOpen.newjob ? `
        <div class="card">
          <h2>New job</h2>
          <p class="sub">Fields marked <span class="req">*</span> are required. The packet is made later, with the first pay app.</p>
          ${jobFormErrors.form ? `<div class="form-error" role="alert">${esc(jobFormErrors.form)}</div>` : ""}
          <div class="form-section">Job</div>
          <div class="form-grid">
            <div><label>Job name <span class="req">*</span></label><input id="nj-name" required autocomplete="off"${inv("nj-name")} />${err("nj-name")}</div>
            <div><label>Address <span class="req">*</span></label><input id="nj-address" required autocomplete="street-address"${inv("nj-address")} />${err("nj-address")}</div>
            <div><label>Contract number</label><input id="nj-contract" /></div>
            <div><label>Contract amount ($) <span class="req">*</span></label><input id="nj-amount" type="number" step="0.01" min="0.01" inputmode="decimal" required${inv("nj-amount")} />${err("nj-amount")}</div>
            <div><label>Retainage %</label><input id="nj-retainage" type="number" min="0" max="20" value="0" inputmode="numeric"${inv("nj-retainage")} />${err("nj-retainage")}</div>
          </div>
          <div class="form-section">Customer</div>
          <div class="form-grid">
            <div><label>Customer <span class="req">*</span></label><input id="nj-customer" required${inv("nj-customer")} />${err("nj-customer")}</div>
            <div><label>Role</label><select id="nj-role"><option>Owner</option><option>GC</option></select></div>
            <div><label>Approver <span class="req">*</span></label><input id="nj-approver" required${inv("nj-approver")} />${err("nj-approver")}</div>
            <div><label>AP email <span class="req">*</span></label><input id="nj-email" type="email" required autocomplete="off"${inv("nj-email")} />${err("nj-email")}</div>
            <div><label>Phone</label><input id="nj-phone" type="tel" /></div>
          </div>
          <div class="form-section">Job record</div>
          <div class="form-grid">
            <div class="span-2">
              <label>Notes for the job record</label>
              <textarea id="nj-packet" placeholder="Contract notes, what this customer usually wants, anything to keep with the job"></textarea>
            </div>
          </div>
          <button class="btn" data-action="create-job">Save job</button>
        </div>` : ""}
      ${open.length ? `<div class="table-wrap">
        <table>
          <tr><th>Job</th><th>Code</th><th>Customer</th><th>Status</th><th>Outstanding</th><th>Unbilled</th><th>Retainage</th><th>Next</th></tr>
          ${open.map(j => `
            <tr>
              <td><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a><div class="sub row-sub">${j.status_label}</div></td>
              <td>${j.code}</td>
              <td>${j.payer ? j.payer.name : "—"}</td>
              <td>${statusPill(j)}</td>
              <td class="num">${money(j.money.outstanding_cents)}</td>
              <td class="num">${money(j.money.unbilled_completed_cents)}</td>
              <td class="num">${money(j.money.retainage_open_cents)}</td>
              <td class="next-action">${nextStep(j)}</td>
            </tr>`).join("")}
        </table>
      </div>` : formOpen.newjob ? "" : emptyState(state.jobs.length || state.closed_jobs_total ? "No open jobs" : "No jobs yet", state.jobs.length || state.closed_jobs_total ? "Every job is closed. Closed jobs are under Job history." : "Create a job. Its record holds notes and files until a pay app needs them.", `<button class="btn" data-action="toggle-form" data-form="newjob">New job</button>`)}
      ${closed ? `<p class="sub">${closed} closed job${closed === 1 ? " is" : "s are"} under <button class="linkish" data-action="nav" data-view="history">Job history</button>.</p>` : ""}
    `;
  },
  job() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j && jobDetailLoading === currentJobId) return backButton("jobs", "Jobs") + `<p class="sub">Loading the job…</p>`;
    if (!j) return backButton("jobs", "Jobs") + emptyState("Job not found", "It may have been deleted, or the link belongs to another company.", `<button class="btn" data-action="nav" data-view="jobs">Go to Jobs</button>`);
    const m = j.money;
    const blocked = isBlocked(j);
    const openDraft = j.invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
    const openForPay = j.invoices.filter(i => ["sent", "approved", "partial"].includes(i.status));
    const closeout = j.closeout_packet || [];
    const closeoutMissing = closeout.filter(p => p.required && !p.present).length;
    const { primary, more } = jobActions(j);
    const hasPayApps = j.invoices.some(i => i.status !== "void");
    const err = (fid) => jobFormErrors[fid] ? `<span class="field-error" id="${fid}-err">${esc(jobFormErrors[fid])}</span>` : "";
    const inv = (fid) => jobFormErrors[fid] ? ` aria-invalid="true" aria-describedby="${fid}-err"` : "";
    return `
      ${backButton("jobs", "Jobs")}
      <div class="job-head">
        <div>
          <h1>${j.name}</h1>
          <p class="sub">${statusPill(j)} ${j.code} · ${j.address} · ${j.payer.name}${j.done_notes ? " · " + j.done_notes : ""}</p>
        </div>
        ${j.closed ? "" : `<button class="btn secondary small" data-action="toggle-form" data-form="editjob" aria-expanded="${!!formOpen.editjob}">${formOpen.editjob ? "Cancel" : "Edit job"}</button>`}
      </div>
      ${formOpen.editjob ? `
        <div class="card">
          <h2>Edit job</h2>
          <div class="form-grid">
            <div><label>Job name</label><input id="ej-name" value="${j.name}"${inv("ej-name")}>${err("ej-name")}</div>
            <div><label>Address</label><input id="ej-address" value="${j.address}"${inv("ej-address")}>${err("ej-address")}</div>
            <div><label>Contract number</label><input id="ej-contract" value="${j.contract_number || ""}"></div>
            ${hasPayApps || (j.sov_view && j.sov_view.present) ? "" : `<div><label>Contract amount ($)</label><input id="ej-amount" type="number" step="0.01" min="0.01" inputmode="decimal" value="${(j.contract_cents / 100).toFixed(2)}"${inv("ej-amount")}>${err("ej-amount")}</div>`}
            ${hasPayApps ? "" : `<div><label>Retainage %</label><input id="ej-retainage" type="number" min="0" max="20" inputmode="numeric" value="${j.retainage_pct || 0}"${inv("ej-retainage")}>${err("ej-retainage")}</div>`}
          </div>
          <p class="sub">${hasPayApps ? "The contract amount and retainage are fixed once a pay app exists; use a change order. " : ""}The job code ${j.code} stays the same, because emails and texts are filed by it. Payer contacts are in the <button class="linkish" data-action="nav" data-view="playbook">Payer playbook</button>.</p>
          <div class="form-inline">
            <button class="btn" data-action="save-job" data-id="${j.id}">Save changes</button>
            ${j.invoices.length || j.payments.length ? "" : `<button class="btn secondary danger-text" data-action="delete-job" data-id="${j.id}" data-name="${j.name}">Delete job</button>`}
          </div>
        </div>` : ""}
      <section class="next-card" aria-label="Next step">
        <div class="next-text"><span class="eyebrow">Next step</span><strong>${nextStep(j)}</strong>${primary && primary.why ? `<span class="sub">${primary.why}</span>` : ""}</div>
        ${primary ? `<button class="btn" ${primary.attrs}>${primary.label}</button>` : ""}
      </section>
      ${blocked ? `<div class="card blockers-card" role="note">
        <strong class="blocker">Blocking send:</strong>
        <ul class="blocker-list">${j.blockers.map(b => `<li>${b}</li>`).join("")}</ul>
        <details><summary>Payer sent a pay app back? Teach the playbook why.</summary>
          <div class="form-inline">
            <input id="learn-reason" placeholder="For example: needs a revised schedule of values" class="grow">
            <button class="btn small secondary" data-action="learn" data-id="${j.id}">Save to playbook</button>
          </div>
          <p class="sub">The reason is kept in this payer's playbook and shown before the next send.</p>
        </details>
      </div>` : ""}
      ${formOpen.progress && j.sov_view && j.sov_view.present ? sovProgressHtml(j) : ""}
      ${formOpen.progress && !(j.sov_view && j.sov_view.present) ? `<div class="card form-card"><h2>Progress pay app</h2><div class="form-inline"><input id="progress-amt" type="number" step="0.01" min="0.01" inputmode="decimal" placeholder="Completed to date $"><input id="progress-notes" placeholder="What was completed" class="grow"><button class="btn small" data-action="progress" data-id="${j.id}">Draft progress pay app</button></div></div>` : ""}
      ${formOpen.pencilchanges ? `<div class="card form-card"><h2>Payer wants changes</h2><div class="form-inline"><input id="pencil-note" placeholder="What to change: quantities, change orders, missing items" class="grow"><button class="btn small" data-action="pencil-changes" data-id="${j.id}">Save</button></div></div>` : ""}
      ${formOpen.done ? `<div class="card form-card"><h2>Mark done</h2><div class="form-inline"><input id="done-notes" placeholder="Completion notes" class="grow"><button class="btn small" data-action="done" data-id="${j.id}">Confirm done</button></div></div>` : ""}
      ${formOpen.match ? `<div class="card form-card"><h2>Match payment</h2>${openForPay.length ? `<div class="form-inline"><select id="match-inv" aria-label="Pay app">${openForPay.map(i => `<option value="${i.id}">${i.number} · ${money(i.amount_cents)} · ${INVOICE_WORDS[i.status] || i.status}</option>`).join("")}</select><input id="match-amt" type="number" step="0.01" min="0.01" inputmode="decimal" placeholder="Amount in dollars"><input id="match-ref" placeholder="Reference (check no., QBO id)"><button class="btn small" data-action="match" data-id="${j.id}">Match</button></div>` : `<p class="sub">No sent pay app is waiting for payment.</p>`}</div>` : ""}
      ${formOpen.retpay ? `<div class="card form-card"><h2>Record retainage paid</h2><div class="form-inline"><input id="retpay-amt" type="number" step="0.01" min="0.01" inputmode="decimal" placeholder="Retainage received $"><input id="retpay-ref" placeholder="Reference"><button class="btn small" data-action="retainage-paid" data-id="${j.id}">Record</button></div></div>` : ""}
      ${j.closed ? `<p class="sub">This job is closed. Its money record is read-only; you can still add notes to the job record.</p>` : more.length ? `
        <div class="more-actions" role="group" aria-label="More actions">
          ${more.map(a => `<button class="btn secondary small" ${a.attrs} title="${esc(a.why || "")}">${a.label}</button>`).join("")}
        </div>` : ""}
      ${!j.closed && j.work_status === "complete" && (j.close_blockers || []).length ? `<p class="sub">Close for record unlocks when: ${(j.close_blockers || []).join(" ")}</p>` : ""}
      <div class="detail-grid">
        <div class="card">
          <h2>Money file</h2>
          <div class="money-row"><span class="label">Contract</span><span>${money(m.contract_cents)}</span></div>
          <div class="money-row"><span class="label">Approved COs</span><span>${money(m.approved_co_cents)}</span></div>
          <div class="money-row"><span class="label">Pending COs</span><span>${money(m.pending_co_cents)}</span></div>
          <div class="money-row strong"><span class="label">Contract total</span><span>${money(m.contract_total_cents)}</span></div>
          <div class="money-row"><span class="label">Billed</span><span>${money(m.billed_cents)}</span></div>
          <div class="money-row"><span class="label">Collected</span><span>${money(m.collected_cents)}</span></div>
          <div class="money-row strong"><span class="label">Outstanding</span><span>${money(m.outstanding_cents)}</span></div>
          <div class="money-row"><span class="label">Unbilled completed</span><span>${money(m.unbilled_completed_cents)}</span></div>
          <div class="money-row"><span class="label">Retainage held</span><span>${money(m.retainage_held_cents)}${j.retainage_pct ? " · " + j.retainage_pct + "%" : ""}</span></div>
          <div class="money-row"><span class="label">Retainage released</span><span>${money(m.retainage_released_cents)}</span></div>
          <div class="money-row"><span class="label">Retainage open</span><span>${money(m.retainage_open_cents)}</span></div>
        </div>
        <div class="card">
          <h2>Pay apps</h2>
          ${j.invoices.length ? j.invoices.map(i => {
            const pkt = i.packet || j.packet || [];
            const missing = pkt.filter(p => p.required && !p.present).length;
            const pencil = i.pencil_status ? ` · pencil ${i.pencil_status === "sent" ? "with payer" : words(i.pencil_status)}${i.pencil_note ? ": " + i.pencil_note : ""}` : "";
            return `<div class="list-item row-link">
              <div><a href="${pathFor("payapp-detail", j.id, i.id)}" data-action="open-payapp" data-job="${j.id}" data-inv="${i.id}"><strong>${i.number}</strong></a> ${invoicePill(i)} <span class="num">${money(i.amount_cents)}</span>
              <div class="sub row-sub">${i.status === "void" ? "Voided" : missing ? `${missing} packet item${missing === 1 ? "" : "s"} missing` : "Packet complete"}${i.sent_at ? " · sent " + fmtDate(i.sent_at) : ""}${i.received_at ? " · received " + fmtDate(i.received_at) : ""}${i.approved_by ? " · approved by " + i.approved_by : ""}${pencil}</div></div>
            </div>`;
          }).join("") : `<p class="sub">No pay apps yet. ${j.work_status === "complete" ? "Draft one from the unbilled work." : "Record progress or mark the job done to draft one."}</p>`}
          ${closeout.length ? `<div class="list-item row-link"><div><a href="${pathFor("packet-detail", j.id, "closeout")}" data-action="open-packet" data-job="${j.id}" data-inv="closeout"><strong>Closeout packet</strong></a> <span class="sub">gates the retainage release · ${closeoutMissing ? closeoutMissing + " missing" : "complete"}</span></div></div>` : ""}
          <h2 class="h2-gap">Change orders</h2>
          ${j.change_orders.length ? j.change_orders.map(co => `
            <div class="list-item">CO ${co.number} · ${co.title} · ${money(co.amount_cents)} <span class="pill ${co.status === "approved" ? "green" : co.status === "withdrawn" ? "" : "amber"}">${words(co.status)}</span>${co.included_in_billing ? ` <span class="sub">on a pay app</span>` : ""}
              ${coSignControls(j, co, !co.included_in_billing && !openDraft ? `<button class="btn small secondary" data-action="include-co" data-id="${j.id}" data-co="${co.id}" title="Bill it on the next pay app; send stays blocked until it is signed">Put on next pay app</button>` : "")}
            </div>`).join("") : `<p class="sub">No change orders. Add one if the scope changed.</p>`}
        </div>
      </div>
      <div class="card">
        <h2>Job record</h2>
        ${j.closed ? "" : `<div class="dropzone small" data-job="${j.id}">
          <strong>${(j.records || []).length ? "Add photos, PDFs, saved emails or spreadsheets" : "Nothing in the record yet. Add the contract, photos and tickets"}</strong>
          <span class="sub">Drop files here, <button class="linkish" data-action="pick-files" data-job="${j.id}">choose files</button>, <button class="linkish" data-action="pick-files" data-job="${j.id}" data-capture="1">take a photo</button>, or paste a screenshot. Emails and texts come in through the <button class="linkish" data-action="nav" data-view="inbox">Inbox</button>.</span>
        </div>`}
        ${(j.records || []).slice().reverse().map(r => `<div class="list-item"><span class="pill">${RECORD_KINDS[r.kind] || r.kind}</span> ${r.label}${r.note ? ` <span class="sub">— ${r.note.length > 240 ? r.note.slice(0, 240) + "…" : r.note}</span>` : ""}${recordFiles(r) ? `<div class="sub">${recordFiles(r)}</div>` : ""}${r.source || r.added_at ? `<div class="sub">${r.source ? r.source + " · " : ""}${fmtDate(r.added_at)}</div>` : ""}</div>`).join("")}
        <details class="add-record"${(j.records || []).length ? "" : " open"}><summary>Add a note to the record</summary>
          <div class="form-inline">
            <input id="rec-label" placeholder="Label, e.g. Rough-in inspection passed" />
            <input id="rec-note" placeholder="Note" />
            <select id="rec-kind" aria-label="Kind">${Object.entries(RECORD_KINDS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
            <button class="btn small" data-action="add-record" data-id="${j.id}">Add to record</button>
          </div>
        </details>
      </div>
      ${sovCardHtml(j)}
    `;
  },
  results() {
    const f = (key, label) => `<button class="${metricsPeriod === key ? "btn" : "btn secondary"} small" data-action="metrics-period" data-period="${key}" aria-pressed="${metricsPeriod === key}">${label}</button>`;
    const head = `<h1>Results</h1>
      <p class="sub">What Billdable recorded for your company, counted from your own pay apps and payments. Nothing here is estimated.</p>
      <div class="filter-bar" role="group" aria-label="Period">${f("90", "Last 90 days")}${f("all", "All time")}
        <button class="btn small secondary" data-action="metrics-email">Email me last month's summary</button></div>`;
    if (!metricsData || metricsData.period !== metricsPeriod) return head + `<p class="sub">Loading…</p>`;
    const m = metricsData.data;
    const days = (v) => v === null || v === undefined ? "—" : `${v} day${v === 1 ? "" : "s"}`;
    return head + `
      <div class="metrics">
        <div class="metric"><div class="k">Work done to pay app sent</div><div class="v">${days(m.done_to_sent_median_days)}</div><div class="sub row-sub">median, ${m.done_to_sent_jobs} job${m.done_to_sent_jobs === 1 ? "" : "s"}</div></div>
        <div class="metric"><div class="k">Pay app sent to paid</div><div class="v">${days(m.sent_to_paid_median_days)}</div><div class="sub row-sub">median; average ${days(m.sent_to_paid_average_days)}; ${m.pay_apps_paid} paid</div></div>
        <div class="metric"><div class="k">Follow-ups per pay app</div><div class="v">${m.follow_ups_per_pay_app ?? "—"}</div><div class="sub row-sub">${m.follow_ups} follow-up${m.follow_ups === 1 ? "" : "s"}, ${m.pay_apps_sent} pay apps sent</div></div>
        <div class="metric"><div class="k">Sent back by the payer</div><div class="v">${m.sent_back}</div><div class="sub row-sub">rejections taught to the playbook and pencil changes</div></div>
      </div>
      <div class="metrics">
        <div class="metric"><div class="k">Billed</div><div class="v">${money(m.billed_cents)}</div><div class="sub row-sub">${m.pay_apps_sent} pay apps</div></div>
        <div class="metric"><div class="k">Paid</div><div class="v">${money(m.paid_cents)}</div><div class="sub row-sub">${m.pay_apps_paid} pay apps</div></div>
        <div class="metric"><div class="k">Change orders billed</div><div class="v">${money(m.change_orders_billed_cents)}</div><div class="sub row-sub">signed, all time</div></div>
        <div class="metric"><div class="k">Retainage collected</div><div class="v">${money(m.retainage_collected_cents)}</div><div class="sub row-sub">all time</div></div>
      </div>
      <div class="card"><h2>Done but not billed</h2>
        ${m.waiting_to_bill.length ? m.waiting_to_bill.map(w => `<div class="list-item row-link"><a href="${pathFor("job", w.job_id)}" data-action="open-job" data-id="${w.job_id}">${w.job}</a><span>${days(w.days)} since done</span></div>`).join("") : `<p class="sub">Every finished job has a pay app out.</p>`}
      </div>
      ${m.by_payer.length ? `<div class="table-wrap"><table>
        <tr><th>Payer</th><th>Average days from sent to paid</th><th>Pay apps paid</th></tr>
        ${m.by_payer.map(r => `<tr><td>${r.payer}</td><td class="num">${r.average_days}</td><td class="num">${r.paid}</td></tr>`).join("")}
      </table></div>` : ""}
    `;
  },
  daily() {
    const openJobs = state.jobs.filter(j => !j.closed);
    const records = state.daily_records || [];
    return `
      <h1>Daily record</h1>
      <p class="sub">What happened on site today, saved on the job record. Attach it to a pay app packet later.</p>
      <div class="card">
        <h2>New record</h2>
        ${openJobs.length ? `<div class="form-grid">
          <div><label>Job</label><select id="daily-job"><option value="">Pick a job…</option>${openJobs.map(j => `<option value="${j.id}">${j.name}</option>`).join("")}</select></div>
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
  packetDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return backButton("packets", "Packets") + emptyState("Packet not found", "The job may have been deleted.", `<button class="btn" data-action="nav" data-view="packets">Go to Packets</button>`);
    const isCloseout = currentDetailId === "closeout";
    const inv = isCloseout ? null : (j.invoices.find(i => i.id === currentDetailId) || [...j.invoices].reverse().find(i => i.packet) || j.invoices[j.invoices.length - 1]);
    const packet = isCloseout ? (j.closeout_packet || []) : ((inv && inv.packet) || j.packet || []);
    const jobRecords = j.records || [];
    if (!inv && !packet.length) return backButton("job", j.name, j.id) + emptyState("No packet yet", "Draft a pay app. The packet is created with it, from this payer's playbook. Until then, add notes and files to the job record.", `<button class="btn" data-action="open-job" data-id="${j.id}">Back to ${j.name}</button>`);
    const missing = packet.filter(p => p.required && !p.present).length;
    return `
      ${backButton("job", j.name, j.id)}
      <h1>${isCloseout ? "Closeout packet" : (inv ? inv.number + " packet" : "Packet")}</h1>
      <p class="sub">${j.name}${inv ? " · " + money(inv.amount_cents) + " · " + (INVOICE_WORDS[inv.status] || words(inv.status)) : ""}${isCloseout ? " · must be complete before asking for retainage" : ""} · ${missing ? `<span class="blocker">${missing} missing</span>` : "complete"}</p>
      <div class="card">
        <h2>Required items</h2>
        ${packet.length ? packet.map(p => `
          <div class="list-item packet-item ${p.present ? "present" : "absent"}">
            <div><span class="mark" aria-hidden="true">${p.present ? "✓" : "✗"}</span> <strong>${p.label}</strong> <span class="visually-hidden">${p.present ? "present" : "missing"}</span>
              ${!p.present ? `<span class="field-error">${p.blocker}</span>` : ""}
              ${p.present && p.present_reason ? `<div class="sub">Marked present: ${p.present_reason}</div>` : ""}
              ${(p.attachments || []).map(a => `<div class="sub">Attached: ${a.name}</div>`).join("")}
            </div>
            ${!p.present ? `<div class="form-inline">
              ${jobRecords.length ? `<select id="attach-rec-${p.id}" aria-label="Job record to attach">${jobRecords.map(r => `<option value="${r.id}">${r.label}</option>`).join("")}</select><button class="btn small" data-action="attach-record" data-id="${j.id}" data-item="${p.id}">Attach</button>` : `<span class="sub">Add the document to the <button class="linkish" data-action="open-job" data-id="${j.id}">job record</button> first.</span>`}
              <button class="btn small secondary" data-action="toggle-form" data-form="resolve-${p.id}" aria-expanded="${!!formOpen["resolve-" + p.id]}">Mark present…</button>
            </div>
            ${formOpen["resolve-" + p.id] ? `<div class="form-inline"><input id="resolve-reason-${p.id}" placeholder="Why it is present, e.g. emailed to the payer on 10/12" class="grow"><button class="btn small" data-action="resolve" data-id="${j.id}" data-item="${p.id}">Save reason</button></div>` : ""}` : ""}
          </div>`).join("") : `<p class="sub">This pay app has no packet items.</p>`}
      </div>
    `;
  },
  coDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return backButton("cos", "Change orders") + emptyState("Change order not found", "The job may have been deleted.", `<button class="btn" data-action="nav" data-view="cos">Go to Change orders</button>`);
    const co = j.change_orders.find(c => c.id === currentDetailId) || j.change_orders[j.change_orders.length - 1];
    if (!co) {
      return `
        ${backButton("cos", "Change orders")}
        <h1>New change order</h1>
        <div class="form-inline">
          <input id="co-title" placeholder="Title">
          <input id="co-amt" type="number" step="0.01" inputmode="decimal" placeholder="Amount in dollars">
          <input id="co-desc" placeholder="What changed (optional)" class="grow">
          <button class="btn" data-action="add-co" data-id="${j.id}">Create</button>
        </div>`;
    }
    return `
      ${backButton("cos", "Change orders")}
      <h1>CO ${co.number}: ${co.title}</h1>
      <p class="sub"><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a> · ${money(co.amount_cents)} · <span class="pill ${co.status === "approved" ? "green" : "amber"}">${words(co.status)}</span></p>
      <div class="card">
        <h2>Summary</h2>
        ${co.description ? `<p>${co.description}</p>` : ""}
        <div class="money-row"><span class="label">Waiting on</span><span>${co.waiting_on || (co.status === "pending" ? "Customer signature" : "Nothing")}</span></div>
        <div class="money-row"><span class="label">On a pay app</span><span>${co.included_in_billing ? "Yes" : "No"}</span></div>
        ${coSignature(co)}
        ${coSignControls(j, co)}
      </div>
      <div class="card">
        <h2>Signed copy</h2>
        ${(co.attachments || []).map(a => `<div class="list-item">${a.name}</div>`).join("") || `<p class="sub">Drop the signed change order on the <button class="linkish" data-action="open-job" data-id="${j.id}">job record</button> (or forward it to the Inbox), then attach it to the pay app's packet.</p>`}
      </div>
    `;
  },
  payappDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    const inv = j && (j.invoices.find(i => i.id === currentDetailId) || j.invoices[j.invoices.length - 1]);
    if (!j || !inv) return backButton("payapps", "Pay apps") + emptyState("Pay app not found", "The job may have been deleted.", `<button class="btn" data-action="nav" data-view="payapps">Go to Pay apps</button>`);
    const pays = j.payments.filter(p => p.invoice_id === inv.id);
    const pkt = inv.packet || j.packet || [];
    const missing = pkt.filter(p => p.required && !p.present).length;
    const draft = ["draft", "awaiting_approval"].includes(inv.status);
    const copies = (j.payer.copy && unesc(j.payer.copy) !== "None") ? j.payer.copy : "";
    const firstSend = draft && !j.payer.sent_before && !state.jobs.some(x => x.payer_id === j.payer_id && x.invoices.some(i => i.sent_at));
    return `
      ${backButton("job", j.name, j.id)}
      <h1>${inv.number}</h1>
      <p class="sub">${invoicePill(inv)} <a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a> · ${money(inv.amount_cents)}</p>
      <p><a class="btn secondary small" href="/api/jobs/${j.id}/payapp/${inv.id}.pdf" target="_blank" rel="noopener">Open the PDF</a> <span class="sub">The same PDF goes with the email when the pay app is sent.</span></p>
      ${draft ? `<div class="card send-check">
        <h2>Before you send</h2>
        <div class="money-row"><span class="label">To</span><span>${j.payer.approver ? j.payer.approver + " · " : ""}${j.payer.ap_email || '<span class="blocker">no AP email</span>'}</span></div>
        <div class="money-row"><span class="label">Copy</span><span>${copies || "Nobody"}</span></div>
        <div class="money-row"><span class="label">Amount</span><span>${money(inv.amount_cents)}</span></div>
        <div class="money-row"><span class="label">Attached</span><span>Pay app PDF${inv.sov_sheet && inv.sov_sheet.length ? " with continuation sheet" : ""}, packet checklist, ${pkt.filter(p => p.present).length} packet item${pkt.filter(p => p.present).length === 1 ? "" : "s"}</span></div>
        ${firstSend ? `<p class="notice">First pay app to ${j.payer.name}. Check the AP email above; a typo sends it to a stranger. Change it in the <button class="linkish" data-action="nav" data-view="playbook">Payer playbook</button>.</p>` : ""}
        ${missing ? `<p class="blocker">${missing} packet item${missing === 1 ? " is" : "s are"} missing, so sending is blocked.</p>` : ""}
        <button class="btn" data-action="send" data-id="${j.id}" ${missing || (j.blockers || []).length ? "disabled" : ""}>Approve & send to ${j.payer.ap_email || "the payer"}</button>
      </div>` : ""}
      <div class="card">
        <h2>Lines</h2>
        ${(inv.lines || []).map(l => `<div class="money-row"><span>${l.label}</span><span>${money(l.amount_cents)}</span></div>`).join("")}
      </div>
      ${continuationHtml(inv, j)}
      <div class="card">
        <h2>Packet</h2>
        ${pkt.length ? pkt.map(p => `<div class="list-item"><span aria-hidden="true">${p.present ? "✓" : "✗"}</span> ${p.label}<span class="visually-hidden">${p.present ? " present" : " missing"}</span></div>`).join("") : `<p class="sub">No packet on this pay app.</p>`}
        <button class="btn secondary small" data-action="open-packet" data-job="${j.id}" data-inv="${inv.id}">Open packet</button>
      </div>
      <div class="card">
        <h2>Payments matched</h2>
        ${pays.length ? pays.map(p => `<div class="list-item">${money(p.amount_cents)} · ${p.reference} · ${fmtDate(p.matched_at)}</div>`).join("") : `<p class="sub">None yet.</p>`}
      </div>
      <div class="card">
        <h2>Approval</h2>
        <div class="money-row"><span class="label">Approved by</span><span>${inv.approved_by || "—"}</span></div>
        <div class="money-row"><span class="label">Sent</span><span>${fmtDateTime(inv.sent_at)}</span></div>
        <div class="money-row"><span class="label">Received by the payer</span><span>${inv.received_at ? `${inv.received_by || "the payer"}, ${fmtDateTime(inv.received_at)}` : inv.sent_at ? "Not confirmed yet" : "—"}</span></div>
        ${inv.payer_token ? `<p class="sub">The payer's page for this pay app (it went in the email): <a href="/p/${me.company_id}.${inv.payer_token}" target="_blank" rel="noopener">open it</a>. They can see the pay app and its packet and mark it received.</p>` : ""}
      </div>
    `;
  },
  waiverDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return backButton("waivers", "Waivers") + emptyState("Waiver not found", "The job may have been deleted.", `<button class="btn" data-action="nav" data-view="waivers">Go to Waivers</button>`);
    const due = j.invoices.filter(i => i.waiver_due && !i.waiver_sent);
    const sent = j.invoices.filter(i => i.waiver_sent);
    return `
      ${backButton("waivers", "Waivers")}
      <h1>Waivers: ${j.name}</h1>
      <p class="sub">Billdable never writes a waiver: state law sets the forms. Drop your signed waiver on the job record; sending attaches the newest one.</p>
      <div class="card">
        <h2>Payer's requirement</h2>
        <p>${j.waiver_standard || j.payer.packet || "See the payer playbook."}</p>
      </div>
      <div class="card">
        <h2>By pay app</h2>
        ${due.concat(sent).length ? due.concat(sent).map(i => `<div class="list-item"><strong>${i.number}</strong> · ${money(i.amount_cents)} · ${i.waiver_sent ? `sent ${fmtDate(i.waiver_sent_at)}${i.waiver_kind ? " · " + waiverKindLabel(i.waiver_kind) : ""}` : `<span class="pill amber">Due</span>`}
          ${!i.waiver_sent ? waiverSendForm(j, i) : ""}</div>`).join("")
          : j.waiver_due && !j.waiver_sent ? waiverSendForm(j, null) : `<p class="sub">${j.waiver_sent ? "Sent " + fmtDate(j.waiver_sent_at) : "No waiver is due yet. One is due after each paid pay app."}</p>`}
      </div>
    `;
  },
  "packet-detail": function() { return views.packetDetail(); },
  "co-detail": function() { return views.coDetail(); },
  "payapp-detail": function() { return views.payappDetail(); },
  "waiver-detail": function() { return views.waiverDetail(); },
  history() {
    const page = listPage("history");
    const closed = page ? page.entries : [];
    const totalCollected = state.totals.closed_collected_cents || 0;
    return `
      <h1>Job history</h1>
      <p class="sub">Closed jobs, kept for the record.</p>
      <div class="summary-row">
        <div class="metric"><div class="k">Closed jobs</div><div class="v">${state.closed_jobs_total || 0}</div></div>
        <div class="metric"><div class="k">Collected on closed</div><div class="v">${money(totalCollected)}</div></div>
      </div>
      ${closed.length ? `<div class="table-wrap">
        <table>
          <tr><th>Job</th><th>Customer</th><th>Contract total</th><th>Collected</th><th>Closed</th><th>Notes</th></tr>
          ${closed.map(j => `
            <tr>
              <td><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a></td>
              <td>${j.payer.name}</td>
              <td class="num">${money(j.money.contract_total_cents)}</td>
              <td class="num">${money(j.money.collected_cents)}</td>
              <td>${fmtDate(j.closed_at || j.completed_at)}</td>
              <td>${j.done_notes || j.status_label}</td>
            </tr>`).join("")}
        </table>
      </div>${moreButton("history", page)}` : !page ? `<p class="sub">Loading…</p>` : emptyState("No closed jobs yet", "Finish the money path on a job and use Close for record.")}
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
      j.invoices.forEach(inv => {
        if (inv.sent_at) add(inv.sent_at, `${inv.number} sent`, "", j.id);
        if (inv.status === "sent" && inv.sent_at) {
          const due = toDate(inv.sent_at);
          due.setDate(due.getDate() + (j.payer.follow_up_days || 7));
          add(localDay(due), `${j.code} follow-up`, due < today ? "overdue" : "", j.id);
          const appr = toDate(inv.sent_at);
          appr.setDate(appr.getDate() + (j.payer.usual_approval_days || 14));
          add(localDay(appr), `${j.code} expected approval`, "", j.id);
        }
      });
      j.payments.forEach(p => add(p.matched_at, `${j.code} payment matched`, "paid", j.id));
      if (j.waiver_sent_at) add(j.waiver_sent_at, `${j.code} waiver sent`, "waiver", j.id);
      if (j.waiver_due && !j.waiver_sent) add(todayKey, `${j.code} waiver ready`, "waiver", j.id);
      if (j.closed_at) add(j.closed_at, `${j.code} closed`, "paid", j.id);
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
      ${thisWeek.length ? `<h2>This week</h2><div class="due-strip">${thisWeek.slice(0, 8).map(e => `<button class="due-item ${e.kind === "overdue" ? "overdue" : ""}" data-action="cal-day" data-day="${e.date}">${fmtDate(e.date).replace(/, \d{4}$/, "")} · ${e.label}</button>`).join("")}</div>` : ""}
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
               ${c.events.length ? `<span class="cal-dot" aria-hidden="true">${c.events.length}</span>` : ""}
             </button>`
        ).join("")}
      </div>
      <details class="card subscribe">
        <summary><strong>Put these dates on your phone (Google Calendar, Apple, Outlook)</strong></summary>
        <p class="sub">Copy this private address. In Google Calendar on a computer: Other calendars, +, From URL, paste, Add calendar. It then shows on your phone. Calendar apps refresh subscribed calendars on their own schedule, often every few hours, so new dates are not instant.</p>
        <div class="copy-row"><code id="cal-url">Loading…</code><button class="btn small secondary" data-action="copy" data-target="cal-url">Copy</button><button class="btn small secondary" data-action="rotate-token" data-kind="calendar">Make a new address</button></div>
        <p class="sub">Anyone with this address can see the dates and job names. Make a new address if it was shared by mistake.</p>
      </details>
    `;
  },
  payapps() {
    let apps = [];
    state.jobs.forEach(j => j.invoices.forEach(inv => apps.push({ ...inv, job: j.name, jobId: j.id, blockers: ["draft", "awaiting_approval"].includes(inv.status) ? j.blockers : [] })));
    if (payappFilter !== "all") apps = apps.filter(a => a.status === payappFilter || (payappFilter === "draft" && ["draft", "awaiting_approval"].includes(a.status)));
    const f = (key, label) => `<button class="${payappFilter === key ? "btn" : "btn secondary"} small" data-action="payapp-filter" data-filter="${key}" aria-pressed="${payappFilter === key}">${label}</button>`;
    return `
      <h1>Pay apps</h1>
      <div class="filter-bar" role="group" aria-label="Show">${f("all", "All")}${f("draft", "Ready to send")}${f("sent", "Sent")}${f("paid", "Paid")}</div>
      ${apps.length ? `<div class="table-wrap">
        <table>
          <tr><th>Number</th><th>Job</th><th>Amount</th><th>Status</th><th>Sent</th><th>Blockers</th></tr>
          ${apps.slice().reverse().map(a => `<tr>
            <td><a href="${pathFor("payapp-detail", a.jobId, a.id)}" data-action="open-payapp" data-job="${a.jobId}" data-inv="${a.id}">${a.number}</a></td>
            <td><a href="${pathFor("job", a.jobId)}" data-action="open-job" data-id="${a.jobId}">${a.job}</a></td>
            <td class="num">${money(a.amount_cents)}</td>
            <td>${invoicePill(a)}</td>
            <td>${a.sent_at ? fmtDate(a.sent_at) : "—"}</td>
            <td>${a.blockers.join("; ") || "—"}</td>
          </tr>`).join("")}
        </table>
      </div>` : emptyState(payappFilter === "all" ? "No pay apps yet" : "None in this view", payappFilter === "all" ? "Draft one from a job. The packet is created with the pay app." : "Pick another filter above.")}
    `;
  },
  packets() {
    const rows = [];
    state.jobs.forEach(j => j.invoices.forEach(inv => {
      const pkt = inv.packet || j.packet || [];
      if (!pkt.length && !inv.packet) return;
      rows.push({ j, inv, missing: pkt.filter(p => p.required && !p.present).length });
    }));
    return `
      <h1>Packets</h1>
      <p class="sub">The paperwork that goes with each pay app. Open one to attach what is missing.</p>
      ${rows.length ? `<div class="table-wrap">
        <table>
          <tr><th>Pay app</th><th>Job</th><th>Missing</th><th>Pay app status</th></tr>
          ${rows.slice().reverse().map(({ j, inv, missing }) => `<tr>
            <td><a href="${pathFor("packet-detail", j.id, inv.id)}" data-action="open-packet" data-job="${j.id}" data-inv="${inv.id}">${inv.number}</a></td>
            <td>${j.name}</td>
            <td>${missing ? `<span class="pill red">${missing} missing</span>` : `<span class="pill green">Complete</span>`}</td>
            <td>${invoicePill(inv)}</td>
          </tr>`).join("")}
        </table>
      </div>` : emptyState("No packets yet", "Draft a pay app. The packet is created with it and stays tied to that pay app.")}
    `;
  },
  cos() {
    const cos = [];
    state.jobs.forEach(j => j.change_orders.forEach(co => cos.push({ ...co, job: j.name, jobId: j.id })));
    const openJobs = state.jobs.filter(j => !j.closed);
    return `
      <h1>Change orders</h1>
      <p class="sub">Extra or changed work. Not billable until the customer signs.</p>
      ${openJobs.length ? `<div class="card">
        <h2>New change order</h2>
        <div class="form-grid">
          <div><label>Job</label><select id="new-co-job">${openJobs.map(j => `<option value="${j.id}" ${coPreselectJob === j.id ? "selected" : ""}>${j.name}</option>`).join("")}</select></div>
          <div><label>Title</label><input id="co-title" placeholder="Add two outlets in the kitchen"></div>
          <div><label>Amount ($)</label><input id="co-amt" type="number" step="0.01" min="0.01" inputmode="decimal"></div>
          <div><label>What changed (optional)</label><input id="co-desc"></div>
        </div>
        <button class="btn" data-action="add-co-from-list">Create change order</button>
      </div>` : ""}
      ${cos.length ? `<div class="table-wrap">
        <table>
          <tr><th>Change order</th><th>Job</th><th>Amount</th><th>Status</th><th>Waiting on</th><th>On a pay app</th></tr>
          ${cos.slice().reverse().map(c => `<tr>
            <td><a href="${pathFor("co-detail", c.jobId, c.id)}" data-action="open-co" data-job="${c.jobId}" data-co="${c.id}">CO ${c.number}: ${c.title}</a>${c.description ? `<div class="sub row-sub">${c.description.slice(0, 80)}</div>` : ""}</td>
            <td>${c.job}</td>
            <td class="num">${money(c.amount_cents)}</td>
            <td><span class="pill ${c.status === "approved" ? "green" : c.status === "withdrawn" ? "" : "amber"}">${words(c.status)}</span></td>
            <td>${c.waiting_on || (c.status === "pending" ? "Signature" : "—")}</td>
            <td>${c.included_in_billing ? "Yes" : "No"}</td>
          </tr>`).join("")}
        </table>
      </div>` : emptyState("No change orders", openJobs.length ? "Create one above when the scope changes." : "Create a job first.")}
    `;
  },
  collections() {
    const open = state.jobs.filter(j => j.money.outstanding_cents > 0);
    const today = new Date();
    return `
      <h1>Collections</h1>
      <p class="sub">What is unpaid, who to contact, and a one-click follow-up.</p>
      ${open.length ? `<div class="table-wrap">
        <table>
          <tr><th>Job</th><th>Unpaid</th><th>Days since sent</th><th>Contact</th><th>Follow up</th></tr>
          ${open.map(j => {
            const sent = j.invoices.find(i => ["sent", "partial"].includes(i.status) && i.sent_at);
            const days = sent ? Math.floor((today - toDate(sent.sent_at)) / 86400000) : "—";
            const unpaid = j.invoices.filter(i => ["sent", "partial", "approved"].includes(i.status)).map(i => i.number + " " + money(i.amount_cents)).join(", ") || money(j.money.outstanding_cents);
            return `<tr>
              <td><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a><div class="sub row-sub">${statusPill(j)}</div></td>
              <td>${unpaid}</td>
              <td class="num">${days}</td>
              <td>${j.payer.approver}<div class="sub row-sub">${j.payer.ap_email}${j.payer.phone ? "<br>" + j.payer.phone : ""}</div></td>
              <td class="actions-cell">
                <button class="btn small" data-action="follow-up" data-id="${j.id}">Send follow-up</button>
                <a class="btn small secondary" href="mailto:${j.payer.ap_email}">Email</a>
                ${j.payer.phone ? `<a class="btn small secondary" href="tel:${(j.payer.phone || "").replace(/[^0-9+]/g, "")}">Call</a>` : ""}
              </td>
            </tr>`;
          }).join("")}
        </table>
      </div>` : emptyState("Nothing to collect", "Unpaid pay apps show up here after one is sent.")}
    `;
  },
  payments() {
    const pays = [];
    state.jobs.forEach(j => j.payments.forEach(p => pays.push({ ...p, job: j.name, jobId: j.id })));
    const imp = paymentImport;
    const importCard = `<details class="card"${imp ? " open" : ""}><summary><strong>Import payments from QuickBooks (CSV)</strong></summary>
      <p class="sub">In QuickBooks, run a report of payments received (for example Transaction List by Date, filtered to payments) and export it to CSV. Paste it here. Billdable proposes a pay app for each payment; nothing is recorded until you tick and apply. A check number already in Billdable is skipped, so importing the same file twice is safe.</p>
      ${imp ? `${imp.warnings.length ? `<div class="sov-warn">${imp.warnings.map(w => `<div>${w}</div>`).join("")}</div>` : ""}
        <div class="table-wrap"><table>
          <tr><th>Record</th><th>Date</th><th>Customer</th><th>Number</th><th>Amount</th><th>Pay app</th><th>Why</th></tr>
          ${imp.rows.map((row, n) => `<tr><td>${row.status === "match" ? `<input type="checkbox" data-qbo-row="${n}" checked aria-label="Record ${row.ref || "this payment"}">` : ""}</td>
            <td>${row.date ? fmtDate(row.date) : "—"}</td><td>${row.customer}</td><td>${row.ref}</td><td class="num">${money(row.amount_cents)}</td>
            <td>${row.number ? `${row.number} · ${row.job}` : `<span class="pill">${words(row.status)}</span>`}</td><td class="sub">${row.reason}</td></tr>`).join("")}
        </table></div>
        <div class="form-inline"><button class="btn" data-action="qbo-apply">Record the ticked payments</button><button class="btn secondary" data-action="qbo-cancel">Cancel</button></div>`
      : `<textarea id="qbo-csv" rows="5" placeholder="Paste the CSV here" class="full-input"></textarea>
        <button class="btn small" data-action="qbo-read">Read payments</button>`}
    </details>`;
    const q = qboStatus;
    const owner = me && me.role === "owner";
    const qboCard = !q || q.loading ? "" : `<div class="card"><h2>QuickBooks connection</h2>${
      !q.configured ? `<p class="sub">Not set up on this server yet: it needs the keys of Billdable's Intuit app. Until then, use the CSV import below.</p>`
      : !q.connected ? (owner
        ? `<p class="sub">Read-only: Billdable reads payments and invoices from QuickBooks and never changes your books.</p><button class="btn" data-action="qbo-connect">Connect QuickBooks</button>`
        : `<p class="sub">An owner of this company can connect QuickBooks here.</p>`)
      : `<p class="sub"><span class="pill green">Connected</span> ${q.environment === "sandbox" ? "Sandbox company. " : ""}Since ${fmtDate(q.connected_at)}${q.connected_by ? ", by " + q.connected_by : ""}. Read-only.</p>
        <div class="form-inline"><button class="btn" data-action="qbo-sync">Read payments from QuickBooks</button><button class="btn secondary" data-action="qbo-reconcile">Check against QuickBooks</button>${owner ? `<button class="btn secondary" data-action="qbo-disconnect">Disconnect</button>` : ""}</div>
        ${qboReconcile ? (qboReconcile.rows.length
          ? `<h3>Where Billdable and QuickBooks differ</h3>${qboReconcile.rows.map(r => `<div class="list-item"><span class="pill">${RECON_WORDS[r.kind] || words(r.kind)}</span> <a href="${pathFor("job", r.job_id)}" data-action="open-job" data-id="${r.job_id}">${r.job}</a>: ${r.detail}</div>`).join("")}`
          : `<p class="sub">Billdable and QuickBooks agree on every sent pay app (${qboReconcile.invoices} QuickBooks invoices checked).</p>`) : ""}`}</div>`;
    return `
      <h1>Payments</h1>
      ${qboCard}
      ${importCard}
      ${pays.length ? `<div class="table-wrap">
        <table>
          <tr><th>Job</th><th>Pay app</th><th>Amount</th><th>Reference</th><th>Matched</th></tr>
          ${pays.slice().reverse().map(p => {
            const job = state.jobs.find(j => j.id === p.jobId);
            const inv = job && job.invoices.find(i => i.id === p.invoice_id);
            return `<tr>
              <td><a href="${pathFor("job", p.jobId)}" data-action="open-job" data-id="${p.jobId}">${p.job}</a></td>
              <td>${inv ? `<a href="${pathFor("payapp-detail", p.jobId, inv.id)}" data-action="open-payapp" data-job="${p.jobId}" data-inv="${inv.id}">${inv.number}</a>` : "—"}</td>
              <td class="num">${money(p.amount_cents)}</td>
              <td>${p.reference}</td>
              <td>${fmtDate(p.matched_at)}</td>
            </tr>`;
          }).join("")}
        </table>
      </div>` : emptyState("No payments matched", "Match a payment from a job once the payer has paid.")}
    `;
  },
  waivers() {
    const due = state.jobs.filter(j => j.waiver_due && !j.waiver_sent);
    const sent = state.jobs.filter(j => j.waiver_sent);
    const rows = due.concat(sent);
    return `
      <h1>Waivers</h1>
      <p class="sub">One lien waiver per paid pay app. Billdable never writes the waiver; it sends the signed one on the job record.</p>
      ${rows.length ? `<div class="table-wrap">
        <table>
          <tr><th>Job</th><th>Type</th><th>Amount</th><th>Status</th><th>Action</th></tr>
          ${rows.map(j => {
            const paid = j.invoices.find(i => i.waiver_due && !i.waiver_sent) || [...j.invoices].reverse().find(i => i.status === "paid");
            return `<tr>
              <td><a href="${pathFor("waiver-detail", j.id)}" data-action="open-waiver" data-id="${j.id}">${j.name}</a></td>
              <td>${paid && paid.waiver_kind ? waiverKindLabel(paid.waiver_kind) : j.waiver_kind || "Pick when sending"}</td>
              <td class="num">${paid ? money(paid.amount_cents) : "—"}</td>
              <td>${j.waiver_sent ? `<span class="pill green">Sent</span>` : `<span class="pill amber">Due</span>`}</td>
              <td>${!j.waiver_sent && j.waiver_due ? `<button class="btn small" data-action="open-waiver" data-id="${j.id}">Choose type and send</button>` : "—"}</td>
            </tr>`;
          }).join("")}
        </table>
      </div>` : emptyState("No waivers due", "A waiver becomes due after a payment is matched.")}
    `;
  },
  retainage() {
    const open = state.jobs.filter(j => j.money.retainage_open_cents > 0);
    return `
      <h1>Retainage</h1>
      <p class="sub">What is holding each release, and how to unlock it.</p>
      ${open.length ? `<div class="table-wrap">
        <table>
          <tr><th>Job</th><th>Open</th><th>Holding it up</th><th>Next</th><th>Contact</th></tr>
          ${open.map(j => {
            const closeoutItems = (j.closeout_packet && j.closeout_packet.length) ? j.closeout_packet : (j.packet || []);
            const missing = closeoutItems.filter(p => p.required && !p.present);
            return `<tr>
              <td><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a></td>
              <td class="num">${money(j.money.retainage_open_cents)}</td>
              <td>${missing.length ? missing.map(m => m.label).join(", ") : (j.work_status === "complete" ? "Closeout packet complete" : "Job still in progress")}</td>
              <td>${j.work_status !== "complete" ? "Mark the job done first" : missing.length ? `<button class="btn small" data-action="open-packet" data-job="${j.id}" ${j.closeout_packet && j.closeout_packet.length ? `data-inv="closeout"` : ""}>Open closeout packet</button>` : `<button class="btn small" data-action="retainage" data-id="${j.id}">Ask for retainage</button>`}</td>
              <td>${j.payer.approver}<div class="sub row-sub"><a href="mailto:${j.payer.ap_email}">${j.payer.ap_email}</a></div></td>
            </tr>`;
          }).join("")}
        </table>
      </div>` : emptyState("No retainage open", "Retainage shows here once a job holds it. Set a retainage percent when creating the job.")}
    `;
  },
  playbook() {
    const clean = (v) => (v && unesc(v) !== "None" ? v : "");
    return `
      <h1>Payer playbook</h1>
      <p class="sub">What each payer requires, who approves, how long they take, and why they reject. These values set follow-up timing and the calendar.</p>
      ${state.payers.length ? state.payers.map(p => `
        <details class="card playbook-card"${(p.packet_suggestions || []).some(x => x.status === "open") ? " open" : ""}>
          <summary><strong>${p.name}</strong> <span class="sub">${p.role} · ${p.approver || "no approver"} · ${p.ap_email || "no AP email"}</span>${(p.packet_suggestions || []).some(x => x.status === "open") ? ` <span class="pill copper">${p.packet_suggestions.filter(x => x.status === "open").length} suggested</span>` : ""}</summary>
          ${(p.packet_suggestions || []).filter(x => x.status === "open").map(x => `<div class="list-item suggestion">
            <div><strong>Add to the packet?</strong> “${x.text}” <span class="sub">from ${x.source}${x.job ? " on " + x.job : ""}, ${fmtDate(x.at)}</span></div>
            <div class="form-inline"><button class="btn small" data-action="sug-add" data-id="${p.id}" data-sug="${x.id}">Add to packet</button><button class="btn small secondary" data-action="sug-dismiss" data-id="${p.id}" data-sug="${x.id}">Dismiss</button></div>
          </div>`).join("")}
          <form data-id="${p.id}" class="form-grid">
            <div><label>Approver</label><input name="approver" value="${clean(p.approver)}"></div>
            <div><label>AP email</label><input name="ap_email" type="email" value="${clean(p.ap_email)}"></div>
            <div><label>Follow-up days</label><input name="follow_up_days" type="number" min="1" inputmode="numeric" value="${p.follow_up_days || 7}"></div>
            <div><label>Usual approval days</label><input name="usual_approval_days" type="number" min="1" inputmode="numeric" value="${p.usual_approval_days || 14}"></div>
            <div class="span-2"><label>Packet</label><textarea name="packet" rows="2">${clean(p.packet)}</textarea></div>
            <div class="span-2"><label>Rejects when</label><textarea name="rejects_when" rows="2">${clean(p.rejects_when)}</textarea></div>
            <div class="span-2"><label>Copy</label><input name="copy" value="${clean(p.copy)}" placeholder="other@payer.com, pm@payer.com"></div>
            <div class="span-2"><button type="button" class="btn small" data-action="save-playbook" data-id="${p.id}">Save playbook</button></div>
          </form>
        </details>`).join("") : emptyState("No payers yet", "A payer is created with the first job for that customer.")}
    `;
  },
  outbox() {
    const page = listPage("outbox", outboxJobFilter);
    const emails = page ? page.entries : [];
    return `
      <h1>Outbox</h1>
      <p class="sub">Every approved send and follow-up, with what happened to it.</p>
      <p class="sub" id="outbox-mode"></p>
      <div class="filter-bar">
        <label for="outbox-filter" class="inline-label">Job</label>
        <select id="outbox-filter" data-filter-for="outbox">
          <option value="all">All jobs</option>
          ${state.jobs.map(j => `<option value="${j.id}" ${outboxJobFilter === j.id ? "selected" : ""}>${j.name}</option>`).join("")}
        </select>
      </div>
      ${emails.length ? `<div class="table-wrap">
        <table>
          <tr><th>Subject</th><th>When</th><th>Kind</th><th>To</th><th>Status</th></tr>
          ${emails.map(e => `<tr><td><a href="${pathFor("job", e.job_id)}" data-action="open-job" data-id="${e.job_id}">${e.subject}</a><details class="email-details"><summary>Show message</summary><div class="sub email-body">${e.body || ""}</div></details>${e.payapp_invoice_id ? `<div class="sub">Pay app PDF attached</div>` : ""}${(e.attachments || []).length ? `<div class="sub">${e.attachments.length} attachment${e.attachments.length === 1 ? "" : "s"}${(e.not_attached || []).length ? "; not attached: " + e.not_attached.join("; ") : ""}</div>` : ""}</td><td>${fmtDateTime(e.at)}</td><td>${words(e.kind)}</td><td>${e.to}</td><td>${emailStatusHtml(e)}</td></tr>`).join("")}
        </table>
      </div>${moreButton("outbox", page)}` : !page ? `<p class="sub">Loading…</p>` : emptyState("Outbox is empty", "Approved sends and follow-ups are listed here.")}
    `;
  },
  audit() {
    const page = listPage("audit", auditJobFilter);
    const events = page ? page.entries : [];
    return `
      <h1>Audit</h1>
      <p class="sub">Every action: what was drafted, who approved, and when.${state.audit_chain ? ` ${state.audit_chain.ok ? "The chain of records checks out." : `<span class="blocker">The chain of records is broken at ${state.audit_chain.broken_at}.</span>`}` : ""}</p>
      <div class="filter-bar">
        <label for="audit-filter" class="inline-label">Job</label>
        <select id="audit-filter" data-filter-for="audit">
          <option value="all">All jobs</option>
          ${state.jobs.map(j => `<option value="${j.id}" ${auditJobFilter === j.id ? "selected" : ""}>${j.name}</option>`).join("")}
        </select>
      </div>
      ${events.length ? `<div class="table-wrap">
        <table>
          <tr><th>What happened</th><th>When</th><th>Who</th><th>Action</th></tr>
          ${events.map(e => `<tr><td>${e.detail}</td><td>${fmtDateTime(e.at)}</td><td>${e.actor}</td><td>${words(e.action)}</td></tr>`).join("")}
        </table>
      </div>${moreButton("audit", page)}` : !page ? `<p class="sub">Loading…</p>` : emptyState("No audit events", "Actions on jobs are recorded here.")}
    `;
  },
};

// ----- Helper chat -----
// Answers come from the prepared-answers database on the server, which reads
// this company's jobs for questions like "what should I do today". Action
// buttons call the same endpoints as the rest of the app, so every gate and
// confirmation still applies.
let helperAdmin = false;
const helperLog = [];   // {who: "you"|"helper", text, actions, suggestions, logId, rated}
let helperBusy = false;

function updateAiContext() {
  if (aiOpen) renderHelper();
}

function helperStarters() {
  const job = currentView === "job" && state && state.jobs.find(j => j.id === currentJobId);
  if (job) { const n = unesc(job.name); return [`What's blocking ${n}?`, `Draft a follow-up for ${n}`, `Status of ${n}`]; }
  if (currentView === "inbox") return ["How does it know which job?", "How do I import 30 days of notes?", "How do I forward emails to Billdable?"];
  if (currentView === "retainage") return ["How do I get retainage released?", "Which jobs are holding my retainage?"];
  return ["What should I do today?", "Who owes me money?", "How do I import emails and texts?"];
}

function renderHelper() {
  const box = document.getElementById("ai-replies");
  const chips = document.getElementById("ai-chips");
  if (!box || !chips) return;
  const greeting = `<div class="msg helper"><p>Hi${me && me.name ? " " + me.name.split(" ")[0] : ""}. Ask me how to do something in Billdable, or about your own jobs: what to do today, who owes you, what's blocking a job.</p></div>`;
  {
    box.innerHTML = greeting + helperLog.map((m, idx) => m.who === "you"
      ? `<div class="msg you">${esc(m.text)}</div>`
      : `<div class="msg helper">${helperFormat(m.text)}
          ${(m.actions || []).length ? `<div class="msg-actions">${m.actions.map((a, i) => `<button class="btn small ${a.type === "post" ? "" : "secondary"}" data-helper-msg="${idx}" data-helper-action="${i}">${esc(a.label)}</button>`).join("")}</div>` : ""}
          ${m.logId ? `<div class="msg-rate">${m.rated === "asking" ? `<form class="ai-why" data-helper-why="${idx}"><label for="why-${idx}">What were you looking for? (optional)</label><input id="why-${idx}" maxlength="300"><button class="btn small" type="submit">Send</button></form>`
            : m.rated ? (m.rated === "yes" ? "Thanks." : "Thanks, the team will improve this answer.") : `Helpful? <button class="linkish" data-helper-rate="${idx}" data-value="yes">Yes</button> <button class="linkish" data-helper-rate="${idx}" data-value="no">No</button>`}</div>` : ""}
        </div>`).join("") + (helperBusy ? `<div class="msg helper typing" aria-label="Looking that up">Looking that up…</div>` : "");
  }
  const last = [...helperLog].reverse().find(m => m.who === "helper");
  const suggestions = (last && last.suggestions && last.suggestions.length) ? last.suggestions : helperStarters();
  chips.innerHTML = suggestions.map(s => `<button class="chip" data-helper-ask="${esc(s)}">${esc(s)}</button>`).join("");
  box.scrollTop = box.scrollHeight;
}

async function helperAsk(q) {
  q = (q || "").trim();
  if (!q || helperBusy) return;
  helperLog.push({ who: "you", text: q });
  helperBusy = true;
  renderHelper();
  let reply;
  try {
    const res = await fetch("/api/helper/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q, view: currentView, job_id: currentView === "job" ? currentJobId : "" }),
    });
    reply = await readJson(res);
    if (res.status === 401) { helperBusy = false; me = null; state = null; renderLanding(); return; }
    if (!res.ok) reply = { text: reply.error || "Something went wrong. Try again." };
  } catch (_) {
    reply = { text: "I can't reach Billdable right now. Check your connection and try again." };
  }
  helperBusy = false;
  helperLog.push({ who: "helper", text: reply.text, actions: reply.actions || [], suggestions: reply.suggestions || [], logId: reply.log_id });
  if (helperLog.length > 60) helperLog.splice(0, helperLog.length - 60);
  renderHelper();
}

async function helperRunAction(a) {
  if (a.type === "nav") nav(a.view);
  else if (a.type === "job") nav("job", a.job_id);
  else if (a.type === "packet") nav("packet-detail", a.job_id, a.inv || null);
  else if (a.type === "ask") helperAsk(a.q);
  else if (a.type === "copy") {
    try { await navigator.clipboard.writeText(a.text); toast("Copied"); }
    catch (_) { toast("Copy blocked by the browser. Select the text and copy it."); }
  } else if (a.type === "post") {
    if (a.confirm && !(await askConfirm(unesc(a.confirm), "Go ahead"))) return;
    await post(a.path);
    helperLog.push({ who: "helper", text: "Done. The page is updated; the Outbox and Audit show what happened." });
    renderHelper();
  }
}

document.getElementById("ai-badge").addEventListener("click", () => {
  aiOpen = !aiOpen;
  document.getElementById("ai-panel").classList.toggle("open", aiOpen);
  document.getElementById("ai-badge").setAttribute("aria-expanded", aiOpen ? "true" : "false");
  if (aiOpen) { renderHelper(); document.getElementById("ai-input").focus(); }
});
document.getElementById("ai-close").addEventListener("click", () => {
  aiOpen = false;
  document.getElementById("ai-panel").classList.remove("open");
  document.getElementById("ai-badge").focus();
});
document.getElementById("ai-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = document.getElementById("ai-input");
  const q = input.value;
  input.value = "";
  helperAsk(q);
});
document.getElementById("ai-panel").addEventListener("click", async (e) => {
  const askBtn = e.target.closest("[data-helper-ask]");
  if (askBtn) { helperAsk(askBtn.textContent); return; }   // textContent is the decoded text
  const actBtn = e.target.closest("[data-helper-action]");
  if (actBtn) {
    const msg = helperLog[Number(actBtn.dataset.helperMsg)];
    const action = msg && msg.actions[Number(actBtn.dataset.helperAction)];
    if (action) await helperRunAction(action);
    return;
  }
  const rate = e.target.closest("[data-helper-rate]");
  if (rate) {
    const msg = helperLog[Number(rate.dataset.helperRate)];
    if (!msg || !msg.logId) return;
    if (rate.dataset.value === "no") {   // ask why, inline, before sending
      msg.rated = "asking";
      renderHelper();
      document.getElementById(`why-${rate.dataset.helperRate}`)?.focus();
      return;
    }
    msg.rated = "yes";
    renderHelper();
    sendHelperFeedback(msg, true, "");
  }
});
document.getElementById("ai-panel").addEventListener("submit", (e) => {
  const form = e.target.closest("[data-helper-why]");
  if (!form) return;
  e.preventDefault();
  const msg = helperLog[Number(form.dataset.helperWhy)];
  const note = form.querySelector("input").value || "";
  msg.rated = "no";
  renderHelper();
  sendHelperFeedback(msg, false, note);
});
async function sendHelperFeedback(msg, helpful, note) {
  try {
    await fetch("/api/helper/feedback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ log_id: msg.logId, helpful, note }) });
  } catch (_) { /* rating is best effort */ }
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && aiOpen && document.getElementById("ai-panel").contains(document.activeElement)) {
    aiOpen = false;
    document.getElementById("ai-panel").classList.remove("open");
    document.getElementById("ai-badge").focus();
  }
});

// Drop files anywhere, or paste a screenshot, to import. On a job page they
// go straight onto that job's record; elsewhere they land in the Inbox.
let dragDepth = 0;
function dropOverlay(on) {
  let el = document.getElementById("drop-overlay");
  if (!el && on) {
    el = document.createElement("div");
    el.id = "drop-overlay";
    el.className = "drop-overlay";
    document.body.appendChild(el);
  }
  if (!el) return;
  const job = currentView === "job" && state && state.jobs.find(j => j.id === currentJobId && !j.closed);
  el.textContent = job ? `Drop to add to ${job.name}` : "Drop to add to the Inbox";
  el.style.display = on ? "flex" : "none";
}
function importTargetJob() {
  const job = currentView === "job" && state && state.jobs.find(j => j.id === currentJobId && !j.closed);
  return job ? job.id : "";
}
document.addEventListener("dragenter", (e) => {
  if (!state || !e.dataTransfer || ![...e.dataTransfer.types].includes("Files")) return;
  dragDepth++;
  dropOverlay(true);
});
document.addEventListener("dragover", (e) => {
  if (!state || !e.dataTransfer || ![...e.dataTransfer.types].includes("Files")) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "copy";
});
document.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) dropOverlay(false);
});
document.addEventListener("drop", (e) => {
  dragDepth = 0;
  dropOverlay(false);
  if (!state || !e.dataTransfer) return;
  if (!e.dataTransfer.files.length) {
    if ([...e.dataTransfer.types].includes("Files")) { e.preventDefault(); toast("That item has no file to import. Save it as a file first, then drop the file."); }
    return;
  }
  e.preventDefault();
  const jobId = importTargetJob();
  uploadFiles(e.dataTransfer.files, jobId, !!jobId);
});
window.addEventListener("blur", () => { dragDepth = 0; dropOverlay(false); });
document.addEventListener("dragend", () => { dragDepth = 0; dropOverlay(false); });
document.addEventListener("paste", (e) => {
  if (!state || !e.clipboardData) return;
  const files = [...e.clipboardData.files];
  if (!files.length) return; // plain text pastes behave normally
  // Copying cells from Excel or Word puts both text and a picture on the clipboard.
  // In a text box, the text is what the person meant.
  const target = e.target;
  const editable = target && (target.tagName === "TEXTAREA" || target.tagName === "INPUT" || target.isContentEditable);
  if (editable && e.clipboardData.types.includes("text/plain")) return;
  e.preventDefault();
  const jobId = importTargetJob();
  uploadFiles(files, jobId, !!jobId);
});
document.addEventListener("scroll", hideHelpTip, true);
// One file picker for every screen ("choose files" on the Inbox or a job page).
document.getElementById("file-input").addEventListener("change", (e) => {
  const input = e.target;
  const jobId = input.dataset.job || "";
  const files = [...input.files];
  input.value = "";
  input.dataset.job = "";   // the next pick starts with no job unless its button sets one
  uploadFiles(files, jobId, !!jobId);
});

// A reset, verify or invite link opened in a tab that already has Billdable loaded only changes
// the part after "#", which doesn't reload the page; handle it here too.
window.addEventListener("hashchange", async () => {
  const t = readTokenFromUrl();
  if (!t) return;
  const message = await handleTokenLink(t);
  if (tokenScreen) return;
  await boot(false);
  if (message) toast(message);
});

// Back and Forward move between pages; the address is the source of truth.
window.addEventListener("popstate", () => {
  if (!state || tokenScreen) return;
  const r = routeFromPath(location.pathname);
  if (r) nav(r.view, r.jobId || null, r.detailId || null, { fromHistory: true });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && drawerOpen) { drawerOpen = false; render(); document.getElementById("menu-btn")?.focus(); }
});

// Installable on a phone (manifest + a service worker that caches nothing), so photos and
// PDFs can be shared straight to Billdable from the share menu.
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

window.nav = nav;
boot();
