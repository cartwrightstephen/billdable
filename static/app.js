// Duracash's browser app, loaded as an ES module (no bundler). It starts the page (boot), draws
// the shell (top bar, menu, tabs) and dispatches to the page's view, and routes every click on a
// [data-action] to ACTIONS. The views and their actions live in static/js/views/, one module per
// screen, merged in static/js/views/index.js; what they share is in static/js/app-core.js and
// static/js/state.js.
import { esc, unesc, escDeep, companyLine } from "./js/format.js";
import { waiverHint } from "./js/jobs.js";
import { DETAIL_PARENT, routeFromPath } from "./js/routes.js";
import { readJson, setNetBanner, readTokenFromUrl } from "./js/net.js";
import { formDraft, watchForms } from "./js/forms.js";
import { pickPayApp } from "./js/payimport.js";
import { termText } from "./js/terms.js";
import { state, me, currentView, currentJobId, setMe, clearSession, setAccount, helperAdmin, setPending, drawerOpen, cameraAsk, setCameraAsk, setDrawerOpen } from "./js/state.js";
import { nav, DEMO_COMPANY_ID, applyLocation, load, toast, hideHelpTip, jobDetails, missingJobs, jobDetailLoading, jobDetailFailed, loadJobDetail, emptyState, finishHtml, listPages, loadListPage, toggleForm, runAction, moreDetails, setPage } from "./js/app-core.js";
import { views, actions as pageActions } from "./js/views/index.js";
import { metricsData, metricsPeriod, loadMetrics, setCalJobFilter } from "./js/views/today.js";
import { syncNewJobCustomer, prefillApprover, prefillRetainage, hideSameCustomer } from "./js/views/jobs.js";
import { updateProgressPreview, prefillPaymentAmount } from "./js/views/job.js";
import { sovDraft, updateSovTotals } from "./js/views/job-sov.js";
import { qboStatus, loadQboStatus, paymentImport } from "./js/views/payments.js";
import { setOutboxJobFilter } from "./js/views/communications.js";
import { newInboxCount, loadImportSettings, guessPasteSource, rememberUploadJob } from "./js/views/inbox.js";
import { loadHelperReview, loadAdminCompanies, companySwitch, setAuditJobFilter } from "./js/views/settings.js";
import { handleTokenLink, tokenScreen, renderLanding, rememberSignIn, renderOutage, setDataNotice, setAuthMode, checkDemo, verifyBannerHtml, heldShareHtml } from "./js/views/auth.js";
import { setAiVisible, updateAiContext } from "./js/views/helper.js";

// ---------- navigation: seven destinations with tabs; every page has its own address ----------
// On a phone the menu shows five; the two marked more go under More (styles.css).
const SECTIONS = [
  { key: "today", label: "Dashboard", views: [["dashboard", "Overview"], ["results", "Results"], ["calendar", "Calendar"]] },
  { key: "jobs", label: "Jobs", views: [["jobs", "Open jobs"], ["daily", "Daily record"], ["history", "Job history"], ["playbook", "Client playbook"]] },
  { key: "communications", label: "Communications", views: [["inbox", "Inbox"], ["outbox", "Outbox"]] },
  { key: "data", label: "Import/Export", more: true, views: [["import", "Import"], ["export", "Export"]] },
  { key: "money", label: "Money", views: [["payapps", "Pay apps"], ["cos", "Change orders"], ["collections", "Unpaid"], ["payments", "Payments"], ["waivers", "Closeout"]] },
  { key: "automations", label: "Automations", more: true, views: [["automations", "Automations"]] },
  { key: "settings", label: "Settings", views: [["company", "Company"], ["team", "Team"], ["account", "My account"], ["billing", "Billing"], ["audit", "Activity log"], ["helper-admin", "Helper answers"]] },
];

// One menu button, with its count: new Inbox items on Communications, emails that didn't go out on Dashboard.
function sectionButton(s, section, counts) {
  const on = s.key === section.key;
  const badge = (n, label) => n ? ` <span class="count" aria-label="${label}">${n}</span>` : "";
  return `<button class="${on ? "active" : ""}${s.more ? " nav-more-item" : ""}" data-action="section" data-section="${s.key}" ${on ? 'aria-current="page"' : ""}>${s.label}${s.key === "communications" ? badge(counts.inbox, `${counts.inbox} new`) : ""}${s.key === "today" ? badge(counts.failed, `${counts.failed} email${counts.failed === 1 ? "" : "s"} didn't go out`) : ""}</button>`;
}

function sectionOf(view) {
  const v = DETAIL_PARENT[view] || view;
  return SECTIONS.find(s => s.views.some(([k]) => k === v)) || SECTIONS[0];
}
function tabLabel(view) {
  for (const s of SECTIONS) for (const [k, label] of s.views) if (k === view) return termText(label);
  return "";
}
function pageTitle() {
  if (!state) return "Duracash";
  const j = currentJobId && state.jobs.find(x => x.id === currentJobId);
  const name = currentView === "job" && j ? unesc(j.name) : DETAIL_PARENT[currentView] && j ? `${tabLabel(DETAIL_PARENT[currentView])}: ${unesc(j.name)}` : (tabLabel(currentView) || sectionOf(currentView).label);
  return `${name} · Duracash`;
}

async function boot(checkLink = true) {
  listenForActions();
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
    const user = escDeep(data.user);
    // Someone else is signed in now (a link opened in this tab): nothing of the last one stays.
    if (me && user && (me.id !== user.id || me.company_id !== user.company_id)) clearSession();
    setMe(user);
    setAccount({
      helperAdmin: data.helper_admin === true,
      mailReady: data.mail_ready === true,
      readOnly: typeof data.read_only === "string" ? esc(data.read_only) : "",
      billingInfo: escDeep(data.billing || null),
      heldShare: Number(data.held_share) || 0,
    });
    // A visit to the sample company doesn't count as having an account on this device.
    if (data.user && data.user.company_id !== DEMO_COMPANY_ID) rememberSignIn();
    applyLocation();
    await load();
  } else if (res.status === 503) {
    // The company's data is missing on the server (R6-06): say so; never show sign-in as if signed out.
    renderOutage((await readJson(res)).error || "Your company's data isn't available right now.");
    return;
  } else {
    setDataNotice((await readJson(res)).data_notice === true);
    if (location.pathname !== "/" && routeFromPath(location.pathname)) { setPending(location.pathname, location.search); setAuthMode("login"); }
    renderLanding();
    checkDemo();
  }
  setAiVisible(!!me);
  if (linkMessage) toast(linkMessage);
}

// The shell (top bar, menu, tabs) as last drawn. render() redraws only #view while the shell
// would come out the same, so a save or a filter doesn't rebuild the menu or move the focus.
let shellHtml = "";

function render() {
  if (!state) return;
  hideHelpTip();
  const cj = currentJobId && state.jobs.find(j => j.id === currentJobId);
  if (currentJobId && (!cj || cj.summary) && !jobDetails[currentJobId] && !missingJobs.has(currentJobId) && jobDetailFailed !== currentJobId && jobDetailLoading !== currentJobId) loadJobDetail(currentJobId);
  const app = document.getElementById("app");
  const company = state.company;
  const section = sectionOf(currentView);
  const tabView = DETAIL_PARENT[currentView] || currentView;
  const inboxCount = newInboxCount();
  const failedEmails = state.pending_email_failures || 0;
  // Helper answers is for the Duracash team. Billing shows everyone the trial and the
  // subscription; only an owner changes it (R6-10).
  const tabs = section.views.filter(([v]) => v !== "helper-admin" || helperAdmin);
  const shell = `
    <a class="skip-link" href="#main">Skip to content</a>
    <div class="shell ${drawerOpen ? "drawer-open" : ""}">
      <header class="topbar">
        <button class="menu-btn" id="menu-btn" data-action="menu" aria-label="Menu" aria-controls="rail" aria-expanded="${drawerOpen}">☰</button>
        <div class="brand">Duracash</div>
        <span class="topbar-section">${section.label}</span>
      </header>
      <aside class="rail" id="rail">
        <div class="brand">Duracash</div>
        <div class="company"><strong>${company.name}</strong>${companyLine(company) ? `<br>${companyLine(company)}` : ""}</div>
        <nav class="nav" aria-label="Main">
          ${SECTIONS.map(s => sectionButton(s, section, { inbox: inboxCount, failed: failedEmails })).join("")}
          <details class="nav-more"${section.more ? " open" : ""}><summary>More</summary>
            ${SECTIONS.filter(s => s.more).map(s => sectionButton(s, section, {})).join("")}
          </details>
        </nav>
        <div class="user-meta">
          ${me ? me.name : ""}<br>${me ? me.email : ""}
          <button class="reset-btn" id="logout" data-action="logout">Log out</button>
        </div>
      </aside>
      <div class="scrim" id="scrim" data-action="scrim" aria-hidden="true"></div>
      <main class="main" id="main" tabindex="-1">
        ${tabs.length > 1 ? `<nav class="tabs" aria-label="${section.label}">${tabs.map(([v, label]) => `<button class="tab ${v === tabView ? "active" : ""}" data-action="tab" data-tab="${v}" ${v === tabView ? 'aria-current="page"' : ""}>${termText(label)}</button>`).join("")}</nav>` : ""}
        <div id="view"></div>
      </main>
    </div>
  `;
  // A redraw while someone is in a box (a background load finishing, say) puts them back in it,
  // with the cursor where it was, instead of dropping the focus on the page.
  const was = document.activeElement;
  const keep = was && was.id && app.contains(was) ? { id: was.id, start: was.selectionStart, end: was.selectionEnd } : null;
  const whole = shell !== shellHtml || !document.getElementById("view");
  if (whole) {
    app.innerHTML = shell;
    shellHtml = shell;
    document.querySelector(".tabs")?.addEventListener("scroll", fadeTabs, { passive: true });
  }
  const view = document.getElementById("view");
  view.innerHTML = verifyBannerHtml() + heldShareHtml() + (views[currentView] ? views[currentView]() : emptyState("Page not found", "There is nothing at this address.", `<button class="btn" data-action="nav" data-view="dashboard">Go to Dashboard</button>`));
  if (keep && !document.activeElement?.id) {
    const again = document.getElementById(keep.id);
    if (again && again !== document.activeElement) {
      again.focus({ preventScroll: true });
      try { if (typeof keep.start === "number") again.setSelectionRange(keep.start, keep.end); } catch (_) { /* not a text box */ }
    }
  }
  document.title = pageTitle();
  fadeTabs();
  finishHtml(whole ? app : view);
  if (cameraAsk && currentView === "inbox") {
    setCameraAsk(false);
    document.querySelector('.dropzone [data-capture="1"]')?.focus();
  }
  if (["inbox", "calendar", "outbox"].includes(currentView)) loadImportSettings();
  if (currentView === "results" && (!metricsData || metricsData.period !== metricsPeriod)) loadMetrics();
  if (["payments", "company"].includes(currentView) && !qboStatus) loadQboStatus();
  if (sovDraft) updateSovTotals();
  if (currentView === "helper-admin") { loadHelperReview(); loadAdminCompanies(); }
  updateAiContext();
}

// The tab row scrolls sideways on a phone: it fades at the right edge until its last tab is in
// view, so more tabs are seen to be there (R6-03).
function fadeTabs() {
  const t = document.querySelector(".tabs");
  if (t) t.classList.toggle("more-right", t.scrollWidth - t.scrollLeft - t.clientWidth > 2);
}
window.addEventListener("resize", fadeTabs);

// What each data-action does: ACTIONS[name](el, el.dataset.id, event). One click listener on
// #app (listenForActions, set once in boot) finds the nearest [data-action] and runs its entry,
// so a redraw never wires buttons again. The shell's own are here; each page module in js/views/
// brings its own entries.
const DEV = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
const ACTIONS = {
  // the shell: menu, sections, tabs
  "section": (el) => nav(SECTIONS.find(s => s.key === el.dataset.section).views[0][0]),
  "tab": (el) => nav(el.dataset.tab),
  "menu": () => { setDrawerOpen(!drawerOpen); render(); if (drawerOpen) document.querySelector(".nav button")?.focus(); },
  "scrim": () => { setDrawerOpen(false); render(); },
  "nav": (el) => { nav(el.dataset.view, el.dataset.id || null, el.dataset.detail || null); },
  "list-more": (el) => { const k = el.dataset.kind, pg = listPages[k]; if (pg && !pg.loading) loadListPage(k, pg.job, pg.entries.length, 50); },
  "copy": async (el) => {
    const text = document.getElementById(el.dataset.target)?.textContent || "";
    try { await navigator.clipboard.writeText(text); toast("Copied"); }
    catch (_) { toast("Copy blocked by the browser. Select the text and copy it."); }
  },
  "toggle-form": (el) => toggleForm(el.dataset.form),
  // each page's own, from its module in js/views/ (merged in views/index.js)
  ...pageActions,
};

let listening = false;

function listenForActions() {
  if (listening) return;
  listening = true;
  const app = document.getElementById("app");
  app.addEventListener("click", (e) => {
    const el = e.target.closest("[data-action]");
    if (!el || !app.contains(el)) return;
    // Ctrl-, Cmd- or Shift-click on a link opens its page in a new tab, as links do.
    if (el.tagName === "A" && (e.metaKey || e.ctrlKey || e.shiftKey)) return;
    e.preventDefault();
    const run = ACTIONS[el.dataset.action];
    if (!run) { if (DEV) console.warn(`No action named "${el.dataset.action}".`); return; }
    runAction(run, el, e);
  });
  app.addEventListener("input", (e) => {
    if (e.target.closest && e.target.closest('[data-form-key="progress"]')) updateProgressPreview();
    if (e.target.id === "nj-customer") syncNewJobCustomer();
    if (e.target.id === "ap-name") hideSameCustomer("addpayer");
    if (e.target.id === "paste-text") guessPasteSource();
    if (e.target.id === "nj-retainage") (formDraft.newjob ||= {}).retainage_touched = true;
  });
  app.addEventListener("focusout", (e) => { if (e.target.id === "nj-customer") prefillApprover(); });
  app.addEventListener("change", (e) => {
    if (e.target.id === "upload-job") rememberUploadJob(e.target.value);
    if (e.target.id === "paste-source") { e.target.dataset.touched = "1"; const hint = document.getElementById("paste-hint"); if (hint) hint.textContent = ""; }
    if (e.target.id === "nj-role" || e.target.id === "nj-customer") prefillRetainage();
    if (e.target.id === "match-inv") prefillPaymentAmount(e.target.value);
    // The line under the waiver type follows the type picked (R6-10).
    if (e.target.id && e.target.id.startsWith("waiver-kind-")) {
      const hint = app.querySelector(`[data-waiver-hint="${e.target.id.slice("waiver-kind-".length)}"]`);
      if (hint) hint.textContent = waiverHint(e.target.value);
    }
    if (e.target.dataset && e.target.dataset.companySwitch) companySwitch(e.target);
    // The Payments import: a pay app picked for a row ticks it; a tick is remembered across redraws (R5-03).
    if (e.target.dataset && e.target.dataset.qboPick !== undefined && paymentImport) {
      const n = +e.target.dataset.qboPick;
      paymentImport.rows[n] = pickPayApp(paymentImport.rows[n], e.target.value);
      render();
    }
    if (e.target.dataset && e.target.dataset.qboRow !== undefined && paymentImport && paymentImport.rows[+e.target.dataset.qboRow]) {
      paymentImport.rows[+e.target.dataset.qboRow].ticked = e.target.checked;
    }
  });
  // A details' toggle doesn't bubble; catch it on the way down.
  app.addEventListener("toggle", (e) => { if (e.target.dataset && e.target.dataset.more) moreDetails[e.target.dataset.more] = e.target.open; }, true);
  // Filter dropdowns listen for "change" only. A click handler that re-renders
  // destroys the open dropdown, which is why mouse selection used to fail.
  app.addEventListener("change", (e) => {
    const s = e.target.closest("select[data-filter-for]");
    if (!s) return;
    if (s.dataset.filterFor === "outbox") setOutboxJobFilter(s.value);
    else if (s.dataset.filterFor === "audit") setAuditJobFilter(s.value);
    else if (s.dataset.filterFor === "calendar") setCalJobFilter(s.value);
    render();
  });
}
document.addEventListener("scroll", hideHelpTip, true);

// A reset, verify or invite link opened in a tab that already has Duracash loaded only changes
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
  if (e.key === "Escape" && drawerOpen) { setDrawerOpen(false); render(); document.getElementById("menu-btn")?.focus(); }
});

// Installable on a phone (manifest + a service worker that caches nothing), so photos and
// PDFs can be shared straight to Duracash from the share menu.
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

// The views reach render, the landing page and boot through app-core.js.
setPage({ render, renderLanding, boot });
window.nav = nav;
watchForms(document);
boot();
