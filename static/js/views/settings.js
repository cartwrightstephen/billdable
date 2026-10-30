// Settings: Company, Team, My account, Billing, Activity log and Helper answers (the Duracash
// team's page). The Client playbook is in clients.js.
import { esc, unesc, escDeep, fmtDate, fmtDateTime, words } from "../format.js";
import { readJson, setNetBanner, postJson } from "../net.js";
import { moreButton, askConfirm } from "../dom.js";
import { field, formErrorLine, refuseForm, formValues, clearForm, draftValue } from "../forms.js";
import { actionWords } from "../terms.js";
import { inviteLocked, inviteLockHtml } from "../setup.js";
import { state, me, currentView, formOpen, setMe, clearSession, onSessionEnd, setTeamData, teamData, mailReady, billingInfo } from "../state.js";
import { DEMO_COMPANY_ID, post, render, toast, patch, moreDetails, emptyState, listPage, renderLanding } from "../app-core.js";
import { qboCardHtml } from "./payments.js";
import { forgetImportSettings } from "./inbox.js";

let twoStepSetup = null;  // {secret, uri} while setting up two-step sign-in
let recoveryCodes = null; // shown once, right after two-step sign-in is turned on
const ordinal = (n) => n + ([11, 12, 13].includes(n % 100) ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th"));
const BILLING_WORDS = { trialing: "Trial", active: "Active", past_due: "Card declined", unpaid: "Unpaid", canceled: "Cancelled", incomplete: "Incomplete", incomplete_expired: "Expired", paused: "Paused" };
let auditJobFilter = "all";
export function setAuditJobFilter(value) { auditJobFilter = value; }

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

// Stripe's checkout or billing portal opens on Stripe's own page.
async function openBilling(path) {
  const r = await postJson(path, {});
  if (!r.ok) { toast(r.data.error || "Couldn't open billing."); return; }
  if (typeof r.data.url === "string" && r.data.url.startsWith("https://")) location.assign(r.data.url);
}
let lastInvite = null;    // { link, emailed, email_status } after inviting

async function loadTeam() {
  try {
    const res = await fetch("/api/team");
    if (!res.ok) return;
    setTeamData(escDeep(await readJson(res)));
  } catch (_) { setNetBanner(true); return; }
  if (currentView === "team" && !patch("#team-card", teamCardHtml())) render();
}

async function teamPost(path, body, okMessage) {
  const r = await postJson(path, body);
  if (!r.ok) { toast(r.data.error || "That didn't work."); return null; }
  if (r.data.users) setTeamData(escDeep({ users: r.data.users, invites: r.data.invites || [] }));
  if (okMessage) toast(okMessage);
  render();
  return r.data;
}

// The team list fills in when /api/team answers, in place: the rest of the page (a company
// address being typed, R2-03's step 0) is not redrawn.
function teamCardHtml() {
  const owner = me.role === "owner";
  const isDemo = me.company_id === DEMO_COMPANY_ID;
  const users = teamData ? teamData.users : [];
  const invites = teamData ? teamData.invites : [];
  // An unconfirmed owner can't invite on a server that sends mail (the server refuses): say so
  // before the tap, with the confirmation link to send again (Oct 23 note 2).
  const locked = inviteLocked(me, mailReady, DEMO_COMPANY_ID);
  return `
      <h2>Team</h2>
      <div class="table-wrap"><table>
        <tr><th>Name</th><th>Email</th><th>Role</th><th>Joined</th><th></th></tr>
        ${users.map(u => `<tr><td>${u.name}${u.id === me.id ? " (you)" : ""}</td><td>${u.email}</td><td>${words(u.role)}</td><td>${fmtDate(u.created_at)}</td>
          <td>${owner && !isDemo && u.id !== me.id ? `
            <button class="btn small secondary" data-action="team-role" data-user="${u.id}" data-role="${u.role === "owner" ? "member" : "owner"}">Make ${u.role === "owner" ? "member" : "owner"}</button>
            <button class="btn small secondary" data-action="team-remove" data-user="${u.id}" data-name="${u.name}">Remove</button>` : ""}</td></tr>`).join("")}
      </table></div>
      ${owner && !isDemo ? `
        <h3>Invite someone</h3>
        ${locked ? inviteLockHtml() : ""}
        <div data-form-key="invite">${formErrorLine("invite")}<div class="form-row">
          ${field({ form: "invite", name: "email", id: "invite-email", label: "Their email", type: "email", autocomplete: "off", wide: true })}
          ${field({ form: "invite", name: "role", id: "invite-role", label: "They join as", options: [["member", "Member"], ["owner", "Owner"]] })}
          <button class="btn small" data-action="team-invite"${locked ? " disabled" : ""}>Invite</button>
        </div></div>
        ${lastInvite ? `<div class="invite-result">${lastInvite.emailed ? `Invite emailed to ${esc(lastInvite.email)}.` : `Not emailed (${esc(lastInvite.email_status)}). Send them this link yourself; it works once, for 7 days:`}
          <div class="copy-row"><code id="invite-link">${esc(lastInvite.link)}</code><button class="btn small secondary" data-action="copy" data-target="invite-link">Copy</button></div></div>` : ""}
        ${invites.length ? `<h3>Open invites</h3>${invites.map(i => `<div class="list-item">${i.email} · ${i.role} · invited by ${i.invited_by} · expires ${fmtDate(i.expires_at)}
          <button class="btn small secondary" data-action="team-revoke" data-invite="${i.id}">Revoke</button></div>`).join("")}` : ""}` : ""}`;
}

// The company's on/off settings (R4-09, R4-10). Saved as soon as a box is ticked; owners only.
function companySwitchesHtml(owner) {
  const co = state.company || {};
  const box = (key, on, label, help) => `<label class="terms-check switch-row"><input type="checkbox" data-company-switch="${key}"${on ? " checked" : ""}${owner ? "" : " disabled"}> <span><strong>${label}</strong><br><span class="sub">${help}</span></span></label>`;
  return `<div class="card" id="switches-card">
      <h2>Sending and email</h2>
      ${box("members_can_send", !!co.members_can_send, "Members can send pay apps, lien waivers and retainage requests",
        "Off: you approve every send. Members draft and get the paperwork ready; an owner sends, deletes drafts, pulls back pay apps and finishes jobs.")}
      ${box("daily_email", co.daily_email !== false, "Daily email of what's due",
        "Each morning owners get one email listing what's due today, with a link to each. Nothing is sent on a day with nothing due.")}
      ${owner ? "" : `<p class="sub">Owners can change these.</p>`}
    </div>`;
}

export async function companySwitch(box) {
  await post("/api/company", { [box.dataset.companySwitch]: box.checked }, { anchor: box });
}

// Settings, one page each (R6-03): Company, Team, My account and Billing (members see it read-only, R6-10).
// Your data and Delete the company sit folded at the end of Company.
function companyView() {
  const owner = me.role === "owner";
  const isDemo = me.company_id === DEMO_COMPANY_ID;
  // What was typed and not yet saved stays through a redraw (a switch saved, QuickBooks status
  // arriving): the boxes are a form (data-field), so watchForms keeps it.
  const co = state.company;
  const typed = (name) => esc(draftValue("company", name, unesc(co[name] || "")));
  return `
    <h1>Company</h1>
    <div class="card" id="company-card" data-form-key="company">
      <h2>Company details</h2>
      ${owner ? "" : `<p class="sub">Owners can change these.</p>`}
      <div class="form-grid">
        <div><label for="co-name">Company name</label><input id="co-name" data-field="name" value="${typed("name")}"${owner ? "" : " disabled"}></div>
        <div><label for="co-phone">Phone</label><input id="co-phone" data-field="phone" value="${typed("phone")}" inputmode="tel" autocomplete="tel"${owner ? "" : " disabled"}></div>
        <div class="span-2"><label for="co-address">Address</label><input id="co-address" data-field="address" value="${typed("address")}" placeholder="Street, city, state ZIP" autocomplete="street-address"${owner ? "" : " disabled"}></div>
        <div><label for="co-license">Contractor licence number</label><input id="co-license" data-field="license" value="${typed("license")}"${owner ? "" : " disabled"}></div>
      </div>
      ${owner ? `<button class="btn small" data-action="save-company">Save company details</button>` : ""}
    </div>
    ${isDemo ? "" : companySwitchesHtml(owner)}
    ${qboCardHtml(false)}
    <details class="card danger-zone" data-more="danger"${formOpen.reset || moreDetails.danger ? " open" : ""}>
      <summary><strong>${owner ? "Your data, reset and delete" : "Your data"}</strong></summary>
      <h2>Your data</h2>
      ${owner ? `<p class="sub">Download everything in your company as one file on <a href="/data/export" data-action="nav" data-view="export">Export</a>, or start over.</p>
      <div class="form-inline">
        <button class="btn small secondary" id="reset" data-action="reset-data" aria-expanded="${!!formOpen.reset}">${isDemo ? "Reset the demo" : "Reset data"}</button>
      </div>` : `<p class="sub">Exports are on <a href="/data/export" data-action="nav" data-view="export">Export</a>; an owner can download everything in the company as one file there.</p>`}
      ${formOpen.reset && !isDemo ? `<div class="form-inline">
        <label for="reset-confirm" class="inline-label">Reset deletes every job, client and record; a snapshot is kept on the server. Type the company name and your password to confirm:</label>
        <input id="reset-confirm" placeholder="${state.company.name}" class="grow">
        <input id="reset-password" type="password" placeholder="Your password" autocomplete="current-password" aria-label="Your password">
        <button class="btn small danger" data-action="reset-confirm">Reset everything</button></div>` : ""}
      ${owner && !isDemo ? `
      <h2>Delete the company</h2>
      <p class="sub">Deletes every job, file, client and record, and every user on this team. Export your data first if you want a copy. Server backups that already hold it expire on their own (the newest 14 are kept by default).</p>
      <div class="form-inline">
        <input id="del-password" type="password" placeholder="Your password" autocomplete="current-password">
        <input id="del-confirm" placeholder="Type the company name: ${state.company.name}" class="grow">
        <button class="btn small danger" data-action="delete-account">Delete everything</button>
      </div>` : ""}
    </details>
  `;
}

function teamView() {
  if (!teamData) loadTeam();
  return `
    <h1>Team</h1>
    <div class="card" id="team-card">${teamCardHtml()}</div>
  `;
}

function myAccountView() {
  const isDemo = me.company_id === DEMO_COMPANY_ID;
  const verified = me.email_verified
    ? `<span class="pill green">confirmed</span>`
    : `<span class="pill amber">not confirmed</span> ${mailReady ? `<button class="btn small secondary" data-action="resend-verify">Send the link again</button>` : `<span class="sub">Confirming needs email set up on the server.</span>`}`;
  return `
    <h1>My account</h1>
    <div class="card">
      <h2>You</h2>
      ${isDemo ? `<div class="money-row"><span class="label">Name</span><span>${me.name}</span></div>` : `<div class="form-inline"><label for="my-name" class="inline-label">Name</label><input id="my-name" value="${me.name}" autocomplete="name" class="grow"><button class="btn small secondary" data-action="save-my-name">Save name</button></div>`}
      <div class="money-row"><span class="label">Email</span><span>${me.email} ${isDemo ? "" : verified}</span></div>
      <div class="money-row"><span class="label">Role</span><span>${words(me.role)}</span></div>
      ${isDemo ? "" : `<div data-form-key="password">${formErrorLine("password")}<div class="form-row">
        ${field({ form: "password", name: "current", id: "pw-current", label: "Current password", type: "password", autocomplete: "current-password" })}
        ${field({ form: "password", name: "new", id: "pw-new", label: "New password (10+ characters)", type: "password", autocomplete: "new-password" })}
        <button class="btn small" data-action="change-password">Change password</button>
      </div></div>`}
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
          <li>In an authenticator app (Google Authenticator, 1Password, Authy, Microsoft Authenticator), add an account and scan this code. ${/Mobi|Android|iPhone/.test(navigator.userAgent) ? `On this phone, <a href="${esc(unesc(twoStepSetup.uri))}">open it in the app</a> instead.` : ""}
            ${twoStepSetup.qr ? `<img class="qr-code" id="2fa-qr" src="${twoStepSetup.qr}" alt="QR code of the two-step key" width="180" height="180">` : ""}</li>
          <li>Can't scan it? Choose to enter a key and type this one: <code id="2fa-key">${twoStepSetup.secret.match(/.{1,4}/g).join(" ")}</code> <button class="btn small secondary" data-action="copy" data-target="2fa-key">Copy</button></li>
          <li>Type the 6-digit code the app shows: <span class="form-inline"><input id="2fa-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="123456"><button class="btn small" data-action="2fa-enable">Turn on</button></span></li>
        </ol>`
      : `<p class="sub"><span class="pill">Off</span> Add a code from your phone to every sign-in, so a stolen password isn't enough. Recommended for owners.</p>
        <button class="btn small" data-action="2fa-begin">Set up two-step sign-in</button>`}
    </div>`}
    ${me.onboarding_dismissed && !isDemo ? `<p><button class="btn small secondary" data-action="onb-show">Show the setup checklist</button></p>` : ""}
    <p class="sub">Your account id: <code>${me.id}</code></p>
  `;
}

function billingView() {
  const owner = me.role === "owner";
  const isDemo = me.company_id === DEMO_COMPANY_ID;
  // A member sees where the trial and the subscription stand; an owner changes them (R6-10).
  if (!owner) return `<h1>Billing</h1>
    ${trialBannerHtml()}
    ${!isDemo && billingInfo && billingInfo.configured ? `<p class="sub billing-status">Subscription: ${billingInfo.status ? (BILLING_WORDS[billingInfo.status] || words(billingInfo.status)) : "none yet"}.</p>` : ""}
    ${billingInfo && billingInfo.warning ? `<p class="blocker">${billingInfo.warning}</p>` : ""}
    <p class="sub">An owner of this company manages the subscription.</p>`;
  return `
    <h1>Billing</h1>
    ${trialBannerHtml()}
    ${!isDemo && owner && billingInfo && billingInfo.configured ? `<div class="card" id="billing">
      <h2>Billing</h2>
      ${billingInfo.status ? `<p class="sub"><span class="pill ${["active", "trialing"].includes(billingInfo.status) ? "green" : "red"}">${BILLING_WORDS[billingInfo.status] || words(billingInfo.status)}</span>${billingInfo.period_end ? ` Next renewal ${fmtDate(new Date(billingInfo.period_end * 1000).toISOString())}.` : ""}</p>`
        : `<p class="sub">No subscription yet. The free trial still applies: the first charge comes when it ends.</p>`}
      ${billingInfo.warning ? `<p class="blocker">${billingInfo.warning}</p>` : ""}
      <div class="form-inline">${billingInfo.has_customer && billingInfo.status && !["canceled", "incomplete_expired"].includes(billingInfo.status)
        ? `<button class="btn" data-action="billing-portal">Manage billing</button>` : `<button class="btn" data-action="billing-checkout">Subscribe</button>`}</div>
      ${billingInfo.partner ? `<p class="sub"><span class="pill copper">Design partner</span> The design-partner price applies.</p>` : ""}
      <p class="sub">Charged on the ${ordinal(Number(billingInfo.billing_day) || 5)} of each month, after your own pay apps are out. Payment by card through Stripe. Duracash never stores the card. Nothing is ever deleted for an unpaid bill; at worst the account becomes read-only.</p>
    </div>` : ""}
  `;
}

export function trialBannerHtml() {
  const t = state.trial;
  if (!t || !t.tracked) return "";
  const day = (iso) => new Date(unesc(iso)).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  const live = !!(billingInfo && billingInfo.configured);
  if (t.state === "active") {
    return `<div class="trial-banner">Free trial: nothing to pay until the first pay app you send through Duracash is paid, or until ${day(t.ends_by)}${t.extended ? " (extended because a pay app is still unpaid)" : ""}.</div>`;
  }
  return `<div class="trial-banner">Your free trial ended on ${day(t.ended_at)}${t.reason === "paid" ? `, when ${t.first_paid} on ${t.first_paid_job} was paid` : ""}.${live ? (billingInfo.status === "active" || billingInfo.status === "trialing" ? " Your subscription is on." : me && me.role === "owner" ? " Subscribe in Settings, Billing." : " An owner can subscribe in Settings, Billing.") : ""}</div>`;
}

// Signed out: what one person had open in Settings isn't kept for the next.
export function forgetSettings() {
  twoStepSetup = null; recoveryCodes = null; lastInvite = null; adminCompanies = null;
  helperReview = null; helperDraft = null; auditJobFilter = "all";
}
onSessionEnd(forgetSettings);

let adminCompanies = null;
export async function loadAdminCompanies() {
  try {
    const res = await fetch("/api/admin/companies");
    if (!res.ok) return;
    adminCompanies = escDeep((await readJson(res)).companies);
  } catch (_) { return; }
  patch("#admin-companies", adminCompaniesHtml());
}

function adminCompaniesHtml() {
  if (!adminCompanies) return "Loading…";
  const trialText = (t) => !t.tracked ? "not tracked (signed up before trials)" : t.state === "active"
    ? `active until ${fmtDate(t.ends_by)}${t.extended ? " (extended)" : ""}` : `ended ${fmtDate(t.ended_at)}${t.reason === "paid" ? ` (${t.first_paid} paid)` : " (time)"}`;
  return `<div class="table-wrap"><table>
    <tr><th>Company</th><th>Id</th><th>Signed up</th><th>Users</th><th>Jobs</th><th>Pay apps sent</th><th>Trial</th><th>Billing</th><th>Days to first paid</th></tr>
    ${adminCompanies.filter(c => !c.demo).map(c => `<tr><td>${c.name}</td><td><code>${c.company_id || ""}</code></td><td>${fmtDate(c.signed_up)}</td><td>${c.users}</td><td>${c.jobs}</td><td>${c.pay_apps_sent}</td><td>${trialText(c.trial)}</td><td>${c.billing ? (BILLING_WORDS[c.billing] || words(c.billing)) : "not set up"}</td><td class="num">${c.activation_days ?? "—"}</td></tr>`).join("")}
  </table></div>`;
}

let helperReview = null;
let helperDraft = null;   // the answer being written or edited
const HELPER_VIEWS = ["dashboard", "results", "calendar", "jobs", "daily", "history", "playbook", "inbox", "outbox", "import", "export", "payapps", "packets", "cos", "collections", "payments", "waivers", "retainage", "automations", "account", "audit"];

export async function loadHelperReview() {
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

// This module's pages, by the name their address opens (routes.js).
export const views = {
  company() { return companyView(); },
  team() { return teamView(); },
  account() { return myAccountView(); },
  billing() { return billingView(); },
  "helper-admin"() {
    return `<h1>Helper answers</h1>
      <p class="sub">What users asked the helper that it couldn't answer well, and the answers it uses. Write an answer once; every company gets it right away. Only accounts listed in HELPER_ADMIN_USER_IDS see this page.</p>
      <div id="helper-admin-body">Loading…</div>
      <div class="card"><h2>Companies</h2><p class="sub">Every company on this server, with its trial and, once Stripe is set up, its subscription. Nothing is enforced unless BILLING_ENFORCE=1.</p><div id="admin-companies">${adminCompaniesHtml()}</div></div>`;
  },
  audit() {
    const page = listPage("audit", auditJobFilter);
    const events = page ? page.entries : [];
    return `
      <h1>Activity log</h1>
      ${state.audit_chain && !state.audit_chain.ok ? `<p class="blocker">The chain of records is broken at ${state.audit_chain.broken_at}.</p>` : ""}
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
          ${events.map(e => `<tr><td>${e.detail}</td><td>${fmtDateTime(e.at)}</td><td>${e.actor}</td><td>${actionWords(e.action)}</td></tr>`).join("")}
        </table>
      </div>${moreButton("audit", page)}` : !page ? `<p class="sub">Loading…</p>` : emptyState("Nothing logged yet")}
    `;
  },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in app.js.
export const actions = {
  "reset-data": () => resetData(),
  "change-password": async () => {
    const current = document.getElementById("pw-current").value, next = document.getElementById("pw-new").value;
    const errors = {};
    if (!current) errors.current = "Enter your current password.";
    if (next.length < 10) errors.new = "Use at least 10 characters for the new password.";
    if (refuseForm("password", errors)) return;
    const r = await postJson("/api/auth/password", { current, new: next });
    if (!r.ok) { refuseForm("password", r.data.fields || { _form: r.data.error || "That didn't work." }); return; }
    clearForm("password");
    toast(r.data.message);
    render();
  },
  "team-invite": async () => {
    const v = formValues("invite");
    if (refuseForm("invite", /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v.email) ? {} : { email: "Enter their email, like pat@yourcompany.com." })) return;
    const r = await postJson("/api/team/invite", { email: v.email, role: v.role || "member" });
    if (!r.ok) { refuseForm("invite", r.data.fields || { _form: r.data.error || "That didn't work." }); return; }
    const data = r.data;
    if (data.users) setTeamData(escDeep({ users: data.users, invites: data.invites || [] }));
    clearForm("invite");
    lastInvite = { link: data.invite_link || (location.origin + data.invite_path), emailed: data.emailed, email_status: data.email_status, email: v.email };
    render();
  },
  "team-role": async (el) => { await teamPost(`/api/team/${el.dataset.user}/role`, { role: el.dataset.role }, "Role changed."); },
  "team-remove": async (el) => {
    if (!(await askConfirm(`Remove ${unesc(el.dataset.name)} from the team? They are signed out right away.`, "Remove", true))) return;
    // The server made new inbound email, text and calendar addresses: the Inbox and Calendar show those.
    if (await teamPost(`/api/team/${el.dataset.user}/remove`, {}, "Removed and signed out. The inbound email, text and calendar addresses changed too: update any forwarding rule or calendar subscription.")) forgetImportSettings();
  },
  "team-revoke": async (el) => { await teamPost(`/api/team/invites/${el.dataset.invite}/revoke`, {}, "Invite revoked."); },
  "delete-account": async () => {
    if (!(await askConfirm("Delete the company, every job and file, and every user? This can't be undone here.", "Delete everything", true))) return;
    const r = await postJson("/api/account/delete", { password: document.getElementById("del-password").value, confirm: document.getElementById("del-confirm").value });
    if (!r.ok) { toast(r.data.error || "Nothing was deleted."); return; }
    clearSession(); setTeamData(null); lastInvite = null;
    renderLanding();
    toast(r.data.message);
  },
  "reset-confirm": async () => {
    const r = await post("/api/reset", { confirm: document.getElementById("reset-confirm")?.value || "", password: document.getElementById("reset-password")?.value || "" });
    if (r && r.ok) { formOpen.reset = false; render(); }
  },
  "2fa-begin": async () => {
    const r = await postJson("/api/auth/2fa/begin", {});
    if (!r.ok) { toast(r.data.error || "That didn't work."); return; }
    twoStepSetup = escDeep(r.data); render(); document.getElementById("2fa-code")?.focus();
  },
  "2fa-enable": async () => {
    const r = await postJson("/api/auth/2fa/enable", { code: document.getElementById("2fa-code")?.value || "" });
    if (!r.ok) { toast(r.data.error || "That didn't work."); return; }
    twoStepSetup = null; recoveryCodes = escDeep(r.data.recovery_codes); me.two_step = true; render();
  },
  "2fa-done": () => { recoveryCodes = null; render(); },
  "2fa-disable": async () => {
    const r = await postJson("/api/auth/2fa/disable", { password: document.getElementById("2fa-off-pw")?.value || "", code: document.getElementById("2fa-off-code")?.value || "" });
    toast(r.ok ? r.data.message : (r.data.error || "That didn't work."));
    if (r.ok) { me.two_step = false; render(); }
  },
  "save-company": async () => {
    const v = (k) => document.getElementById(k)?.value || "";
    const r = await post("/api/company", { name: v("co-name"), address: v("co-address"), phone: v("co-phone"), license: v("co-license") });
    // Saved: the boxes show what the server kept (spaces tidied), not the typed copy.
    if (r && r.ok) { clearForm("company"); render(); }
  },
  "save-my-name": async () => {
    const r = await postJson("/api/auth/name", { name: document.getElementById("my-name")?.value || "" });
    if (!r.ok) { toast(r.data.error || "Not saved."); return; }
    setMe(escDeep(r.data.user)); toast("Saved"); render();
  },
  "billing-checkout": () => openBilling("/api/billing/checkout"),
  "billing-portal": () => openBilling("/api/billing/portal"),
};
