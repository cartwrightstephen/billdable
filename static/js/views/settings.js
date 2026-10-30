// Settings: Company, Team, My account, Billing, Activity log and Helper answers (the Duracash
// team's page). The Client playbook is in clients.js.
import { esc, unesc, escDeep, fmtDate, fmtDateTime, words } from "../format.js";
import { readJson, setNetBanner, postJson } from "../net.js";
import { moreButton, askConfirm } from "../dom.js";
import { field, formErrorLine, refuseForm, formValues, clearForm } from "../forms.js";
import { US_STATES, checkFields } from "../validate.js";
import { actionWords } from "../terms.js";
import { inviteLocked, inviteLockHtml } from "../setup.js";
import { state, me, currentView, formOpen, setMe, clearSession, onSessionEnd, setTeamData, teamData, mailReady, billingInfo, codesByEmail, twoStepPrompt, setTwoStepPrompt } from "../state.js";
import { DEMO_COMPANY_ID, post, render, toast, patch, moreDetails, emptyState, listPage, renderLanding, backButton, nav, can } from "../app-core.js";
import { THEMES, ACCENTS, applyAppearance, normalizeAppearance, storedAppearance } from "../theme.js";
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
  if (currentView === "team" && !(patch("#team-card", teamCardHtml()) && patch("#profiles-card", profilesCardHtml()) && patch("#admin-card", adminCardHtml()))) render();
}

// The Team page's data from a reply: people, open invites, profiles and the permission list.
function teamOnly(d) {
  return { users: d.users || [], invites: d.invites || [], profiles: d.profiles || [], permissions: d.permissions || [] };
}

async function teamPost(path, body, okMessage) {
  const r = await postJson(path, body);
  if (!r.ok) { toast(r.data.error || "That didn't work."); return null; }
  if (r.data.users) setTeamData(escDeep(teamOnly(r.data)));
  if (okMessage) toast(okMessage);
  render();
  return r.data;
}

// The team list fills in when /api/team answers, in place: the rest of the page (a company
// address being typed, R2-03's step 0) is not redrawn.
// Each person has a permission profile (S6); those who manage the team change it, invite with
// one, and add profiles of their own. The table of profiles answers what each may do.
let profileDraft = null;   // {id, name, perms} while a profile is being added or changed
// Only an owner gives the Owner profile or changes an owner's (the server refuses the rest).
const ownerMe = () => me && (me.profile_id || me.role) === "owner";
function profileOptions(selected) {
  return ((teamData && teamData.profiles) || []).filter(p => ownerMe() || p.id !== "owner").map(p => `<option value="${p.id}"${p.id === selected ? " selected" : ""}>${p.name}</option>`).join("");
}

function teamCardHtml() {
  const manage = can("team");
  const isDemo = me.company_id === DEMO_COMPANY_ID;
  const users = teamData ? teamData.users : [];
  const invites = teamData ? teamData.invites : [];
  // An unconfirmed owner can't invite on a server that sends mail (the server refuses): say so
  // before the tap, with the confirmation link to send again (Oct 23 note 2).
  const locked = inviteLocked(me, mailReady, DEMO_COMPANY_ID);
  const edit = manage && !isDemo;
  const mayChange = (u) => edit && u.id !== me.id && (ownerMe() || u.profile_id !== "owner");
  return `
      <h2>Team</h2>
      <div class="table-wrap"><table>
        <tr><th>Name</th><th>Email</th><th>Profile</th><th>Joined</th><th></th></tr>
        ${users.map(u => `<tr><td>${u.name}${u.id === me.id ? " (you)" : ""}${u.company_admin ? ` <span class="pill copper">Company Admin</span>` : ""}</td><td>${u.email}</td>
          <td>${mayChange(u) ? `<span class="form-inline"><select id="prof-${u.id}" aria-label="Profile for ${u.name}">${profileOptions(u.profile_id)}</select>
            <button class="btn small secondary" data-action="team-profile" data-user="${u.id}">Change</button></span>` : u.profile_name || words(u.role)}</td>
          <td>${fmtDate(u.created_at)}</td>
          <td>${mayChange(u) && !u.company_admin ? `<button class="btn small secondary" data-action="team-remove" data-user="${u.id}" data-name="${u.name}">Remove</button>` : ""}</td></tr>`).join("")}
      </table></div>
      ${edit ? `
        <h3>Invite someone</h3>
        <p class="sub">They get an email with a link to join. Pick the profile that says what they can do; you can change it later.</p>
        ${locked ? inviteLockHtml() : ""}
        <div data-form-key="invite">${formErrorLine("invite")}<div class="form-row">
          ${field({ form: "invite", name: "email", id: "invite-email", label: "Their email", type: "email", autocomplete: "off", wide: true, rule: "email" })}
          ${field({ form: "invite", name: "profile_id", id: "invite-role", label: "Profile", value: "member", options: ((teamData && teamData.profiles) || [{ id: "member", name: "Member" }]).filter(p => ownerMe() || p.id !== "owner").map(p => [unesc(p.id), unesc(p.name)]) })}
          <button class="btn small" data-action="team-invite"${locked ? " disabled" : ""}>Invite</button>
        </div></div>
        ${lastInvite ? `<div class="invite-result">${lastInvite.emailed ? `Invite emailed to ${esc(lastInvite.email)}.` : `Not emailed (${esc(lastInvite.email_status)}). Send them this link yourself; it works once, for 7 days:`}
          <div class="copy-row"><code id="invite-link">${esc(lastInvite.link)}</code><button class="btn small secondary" data-action="copy" data-target="invite-link">Copy</button></div></div>` : ""}
        ${invites.length ? `<h3>Open invites</h3>${invites.map(i => `<div class="list-item">${i.email} · ${i.profile_name || i.role} · invited by ${i.invited_by} · expires ${fmtDate(i.expires_at)}
          <button class="btn small secondary" data-action="team-revoke" data-invite="${i.id}">Revoke</button></div>`).join("")}` : ""}` : ""}`;
}

// What each profile may do, one column each (S6). Owner holds everything and is locked; Member
// and custom profiles can be changed by those who manage the team.
function profilesCardHtml() {
  if (!teamData || !teamData.profiles) return "";
  const edit = can("team") && me.company_id !== DEMO_COMPANY_ID;
  const list = teamData.profiles;
  const perms = teamData.permissions || [];
  const admin = (teamData.users || []).find(u => u.company_admin);
  return `<h2>Permission profiles</h2>
    <p class="sub">What each profile can do. Everyone on the team can draft pay apps, add records and paperwork, and follow up. The Owner profile can do everything; only the Company Admin${admin ? ` (${admin.name})` : ""} can delete the company.</p>
    <div class="table-wrap"><table class="profiles-table">
      <tr><th>Permission</th>${list.map(p => `<th>${p.name}</th>`).join("")}</tr>
      ${perms.map(k => `<tr><td>${k.words}</td>${list.map(p => `<td class="center">${p.perms.includes(unesc(k.key)) ? `<span class="pill green" aria-label="Yes">Yes</span>` : `<span class="sub" aria-label="No">No</span>`}</td>`).join("")}</tr>`).join("")}
      ${edit ? `<tr><td></td>${list.map(p => `<td class="center">${p.locked ? `<span class="sub">Built in</span>` : `<button class="btn small secondary" data-action="profile-edit" data-profile="${p.id}">Edit</button>${p.built_in ? "" : ` <button class="btn small secondary" data-action="profile-delete" data-profile="${p.id}" data-name="${p.name}">Delete</button>`}`}</td>`).join("")}</tr>` : ""}
    </table></div>
    ${edit && !profileDraft ? `<button class="btn small" data-action="profile-new">New profile</button>` : ""}
    ${edit && profileDraft ? profileFormHtml(perms) : ""}`;
}

function profileFormHtml(perms) {
  const d = profileDraft;
  const member = d.id === "member";
  return `<div class="card profile-form" data-form-key="profile">${formErrorLine("profile")}
    <h3>${d.id ? `Edit ${esc(d.name)}` : "New profile"}</h3>
    ${member ? `<p class="sub">Member is built in: its name stays, its permissions can change.</p>` : `<div class="form-grid">${field({ form: "profile", name: "name", id: "profile-name", label: "Name", value: d.name || "", placeholder: "Office manager", required: true })}</div>`}
    <fieldset class="perm-list"><legend>It can</legend>
      ${perms.map(k => `<label class="terms-check"><input type="checkbox" data-perm="${k.key}"${(d.perms || []).includes(unesc(k.key)) ? " checked" : ""}> ${k.words}</label>`).join("")}
    </fieldset>
    <div class="form-inline"><button class="btn small" data-action="profile-save">Save profile</button><button class="btn small secondary" data-action="profile-cancel">Cancel</button></div>
  </div>`;
}

// The Company Admin (S8): one owner, the only person who can delete the company. They can hand
// the role to another owner.
function adminCardHtml() {
  if (!teamData || me.company_id === DEMO_COMPANY_ID) return "";
  const admin = (teamData.users || []).find(u => u.company_admin);
  const owners = (teamData.users || []).filter(u => u.profile_id === "owner" && !u.company_admin);
  const mine = admin && admin.id === me.id;
  return `<h2>Company Admin</h2>
    <p class="sub">${admin ? `${admin.name}${mine ? " (you)" : ""} is the Company Admin` : "No Company Admin yet"}: an owner who can also delete the company. ${mine ? "You can hand the role to another owner." : ""}</p>
    ${mine ? owners.length ? `<div class="form-inline">
        <label for="admin-to" class="inline-label">Hand it to</label>
        <select id="admin-to">${owners.map(u => `<option value="${u.id}">${u.name}</option>`).join("")}</select>
        <input id="admin-password" type="password" placeholder="Your password" autocomplete="current-password" aria-label="Your password">
        <button class="btn small secondary" data-action="admin-hand-over">Hand over</button></div>`
      : `<p class="sub">To hand it on, give someone the Owner profile first.</p>` : ""}`;
}

// The company's on/off settings (R4-09, R4-10), owners only. Ticked boxes are saved with the rest
// of the page by its one Save (S1); until then a tick is kept here, through redraws.
let switchDraft = {};
function companySwitchesHtml(owner) {
  const co = state.company || {};
  const on = (key, saved) => (key in switchDraft ? switchDraft[key] : saved);
  const box = (key, checked, label, help) => `<label class="terms-check switch-row"><input type="checkbox" id="sw-${key}" data-company-switch="${key}"${checked ? " checked" : ""}${owner ? "" : " disabled"}> <span><strong>${label}</strong><br><span class="sub">${help}</span></span></label>`;
  return `<div class="card" id="switches-card">
      <h2>Sending and email</h2>
      ${box("daily_email", on("daily_email", co.daily_email !== false), "Daily email of what's due",
        "Each morning owners get one email listing what's due today, with a link to each. Nothing is sent on a day with nothing due.")}
      ${owner ? `<p class="sub">Who can send pay apps, waivers and retainage requests is set by each person's profile, on <button class="linkish" data-action="nav" data-view="team">Team</button>.</p>` : `<p class="sub">Your profile doesn't include changing these.</p>`}
    </div>`;
}

// A switch ticked (app.js's change listener): kept until Save.
export function companySwitch(box) {
  switchDraft[box.dataset.companySwitch] = !!box.checked;
}

// The company form's boxes and the G1 rule each is checked by (as domain.COMPANY_RULES).
const COMPANY_RULES = { name: "name", phone: "phone", license: "licence", street: "street", city: "city", state: "state", zip: "zip" };
const ADDRESS_KEYS = ["street", "city", "state", "zip"];

// What Save sends for the company page, and the boxes to mark first: a box is checked only when
// it changed (a value saved before the rules stays until edited), and once any address box is
// filled, all four are needed.
export function companyChanges(values, company, switches) {
  const changed = Object.fromEntries(Object.entries(values).filter(([k, v]) => v !== String(unesc(company[k] ?? ""))));
  const errors = checkFields(changed, COMPANY_RULES);
  if (!values.name) errors.name = "Enter the company name.";
  if (ADDRESS_KEYS.some(k => values[k])) Object.assign(errors, checkFields(values, Object.fromEntries(ADDRESS_KEYS.map(k => [k, COMPANY_RULES[k]])), ADDRESS_KEYS.filter(k => !values[k])));
  return { body: { ...values, ...switches }, errors };
}

// Settings, one page each (R6-03): Company, Team, My account and Billing (members see it read-only, R6-10).
// Company is one form with one Save at the bottom (S1): its details, the address in four boxes
// (S3) and the switches. Your data sits folded at the end.
function companyView() {
  const owner = can("company");
  const data = can("data");
  const isDemo = me.company_id === DEMO_COMPANY_ID;
  // What was typed and not yet saved stays through a redraw (QuickBooks status arriving): the
  // boxes are a form (data-field), so watchForms keeps it.
  const co = state.company;
  const saved = (name) => unesc(co[name] || "");
  const lock = owner ? "" : "disabled";
  const box = (name, id, label, rule, extra = {}) => field({ form: "company", name, id, label, rule, value: saved(name), attrs: lock, ...extra });
  return `
    <h1>Company</h1>
    <div data-form-key="company">${formErrorLine("company")}
    <div class="card" id="company-card">
      <h2>Company details</h2>
      ${owner ? "" : `<p class="sub">Your profile doesn't include changing these.</p>`}
      <div class="form-grid">
        ${box("name", "co-name", "Company name", "name", { required: true, autocomplete: "organization" })}
        ${box("phone", "co-phone", "Phone", "phone", { type: "tel", inputmode: "tel", autocomplete: "tel", placeholder: "(404) 555-0123" })}
        ${box("street", "co-address", "Street address", "street", { wide: true, autocomplete: "address-line1" })}
        ${box("city", "co-city", "City", "city", { autocomplete: "address-level2" })}
        ${box("state", "co-state", "State", "state", { options: [["", "Pick a state"], ...US_STATES.map(c => [c, c])] })}
        ${box("zip", "co-zip", "ZIP code", "zip", { inputmode: "numeric", autocomplete: "postal-code", placeholder: "30303" })}
        ${box("license", "co-license", "Contractor licence number", "licence")}
        ${box("legal_name", "co-legal-name", "Legal name (on contracts and lien waivers)", undefined, { wide: true, placeholder: "Same as the company name" })}
      </div>
    </div>
    ${isDemo ? "" : companySwitchesHtml(owner)}
    </div>
    ${qboCardHtml(false)}
    <details class="card danger-zone" data-more="danger"${formOpen.reset || moreDetails.danger ? " open" : ""}>
      <summary><strong>Your data</strong></summary>
      <h2>Your data</h2>
      ${data ? `<p class="sub">Download everything in your company as one file on <a href="/data/export" data-action="nav" data-view="export">Export</a>, or start over.</p>
      <div class="form-inline">
        <button class="btn small secondary" id="reset" data-action="reset-data" aria-expanded="${!!formOpen.reset}">${isDemo ? "Reset the demo" : "Reset data"}</button>
      </div>` : `<p class="sub">Exports are on <a href="/data/export" data-action="nav" data-view="export">Export</a>; someone whose profile includes exporting data can download everything in the company as one file there.</p>`}
      ${formOpen.reset && !isDemo ? `<div class="form-inline">
        <label for="reset-confirm" class="inline-label">Reset deletes every job, client and record; a snapshot is kept on the server. Type the company name and your password to confirm:</label>
        <input id="reset-confirm" placeholder="${state.company.name}" class="grow">
        <input id="reset-password" type="password" placeholder="Your password" autocomplete="current-password" aria-label="Your password">
        <button class="btn small danger" data-action="reset-confirm">Reset everything</button></div>` : ""}
      ${me.company_admin && !isDemo ? `<p class="sub">As the Company Admin you can also <button class="linkish" data-action="nav" data-view="delete-company">delete the company</button>.</p>` : ""}
    </details>
    ${owner ? `<div class="save-bar"><button class="btn" data-action="save-company">Save</button></div>` : ""}
  `;
}

function teamView() {
  if (!teamData) loadTeam();
  return `
    <h1>Team</h1>
    <div class="card" id="team-card">${teamCardHtml()}</div>
    <div class="card" id="profiles-card">${profilesCardHtml()}</div>
    <div class="card" id="admin-card">${adminCardHtml()}</div>
  `;
}

// Delete the company (S8): its own page, for the Company Admin only (the server checks too).
function deleteCompanyView() {
  const back = backButton("company", "Company");
  if (me.company_id === DEMO_COMPANY_ID) return `${back}<h1>Delete the company</h1>${emptyState("The demo company can't be deleted")}`;
  if (!me.company_admin) return `${back}<h1>Delete the company</h1>${emptyState("Only the Company Admin can delete the company", "The Company Admin is shown on Settings, Team.")}`;
  return `${back}
    <h1>Delete the company</h1>
    <div class="card danger-zone">
      <p>This deletes every job, file, client and record, and every user on this team. Export your data first if you want a copy. Server backups that already hold it expire on their own (the newest 14 are kept by default).</p>
      <div class="form-grid">
        <div class="field"><label for="del-password">Your password</label><input id="del-password" type="password" autocomplete="current-password"></div>
        <div class="field"><label for="del-confirm">Type the company name: ${state.company.name}</label><input id="del-confirm" autocomplete="off"></div>
      </div>
      <button class="btn danger" data-action="delete-account">Delete everything</button>
    </div>`;
}

function myAccountView() {
  const isDemo = me.company_id === DEMO_COMPANY_ID;
  const verified = me.email_verified
    ? `<span class="pill green">confirmed</span>`
    : `<span class="pill amber">not confirmed</span> ${mailReady ? `<button class="btn small secondary" data-action="resend-verify">Send the link again</button>` : `<span class="sub">Confirming needs email set up on the server.</span>`}`;
  return `
    <h1>My account</h1>
    ${twoStepPrompt && !isDemo ? twoStepCardHtml(true) : ""}
    <div class="card" data-form-key="account">${formErrorLine("account")}
      <h2>You</h2>
      ${isDemo ? `<div class="money-row"><span class="label">Name</span><span>${me.name}</span></div>` : `<div class="form-grid">${field({ form: "account", name: "name", id: "my-name", label: "Name", value: unesc(me.name), autocomplete: "name", rule: "name", required: true })}</div>`}
      <div class="money-row"><span class="label">Email</span><span>${me.email} ${isDemo ? "" : verified}</span></div>
      <div class="money-row"><span class="label">Profile</span><span>${me.profile_name || words(me.role)}${me.company_admin ? ` <span class="pill copper">Company Admin</span>` : ""}</span></div>
      ${isDemo ? "" : `<div class="money-row"><span class="label">Password</span><span><button class="btn small secondary" data-action="nav" data-view="password">Change password</button></span></div>`}
    </div>
    ${appearanceCardHtml()}
    ${isDemo || twoStepPrompt ? "" : twoStepCardHtml()}
    ${me.onboarding_dismissed && !isDemo ? `<p><button class="btn small secondary" data-action="onb-show">Show the setup checklist</button></p>` : ""}
    <p class="sub">Your account id: <code>${me.id}</code></p>
    <div class="save-bar"><button class="btn" data-action="save-account">Save</button></div>
  `;
}

// Appearance (G9): the theme and accent, kept on this person's account, saved with the page.
function savedAppearance() {
  if (me.company_id === DEMO_COMPANY_ID) return storedAppearance();
  return normalizeAppearance(me.appearance ? { theme: unesc(me.appearance.theme), accent: unesc(me.appearance.accent) } : null);
}

function appearanceCardHtml() {
  const a = savedAppearance();
  return `<div class="card" id="appearance" data-form-key="appearance">${formErrorLine("appearance")}
      <h2>Appearance</h2>
      <div class="form-grid">
        ${field({ form: "appearance", name: "theme", id: "app-theme", label: "Theme", value: a.theme, options: THEMES })}
        ${field({ form: "appearance", name: "accent", id: "app-accent", label: "Accent colour", value: a.accent, options: ACCENTS })}
      </div>
      <p class="sub">Only for you, on every device you sign in on. Pay app PDFs and your clients' pages stay as they are.</p>
    </div>`;
}

// Two-step sign-in (S7) is on for everyone but the demo. With mail on the server, a sign-in
// from a new device asks for a code emailed to the person, and the authenticator app is the
// stronger choice. Without mail, the app is the second step: right after signing in, this card
// comes first (prompt), until it is set up or put off with Not now. It can't be turned off.
function twoStepCardHtml(prompt = false) {
  const setup = twoStepSetup ? `<ol class="steps">
          <li>In an authenticator app (Google Authenticator, 1Password, Authy, Microsoft Authenticator), add an account and scan this code. ${/Mobi|Android|iPhone/.test(navigator.userAgent) ? `On this phone, <a href="${esc(unesc(twoStepSetup.uri))}">open it in the app</a> instead.` : ""}
            ${twoStepSetup.qr ? `<img class="qr-code" id="2fa-qr" src="${twoStepSetup.qr}" alt="QR code of the two-step key" width="180" height="180">` : ""}</li>
          <li>Can't scan it? Choose to enter a key and type this one: <code id="2fa-key">${twoStepSetup.secret.match(/.{1,4}/g).join(" ")}</code> <button class="btn small secondary" data-action="copy" data-target="2fa-key">Copy</button></li>
          <li>Type the 6-digit code the app shows: <span class="form-inline"><input id="2fa-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="123456"><button class="btn small" data-action="2fa-enable">Turn on</button></span></li>
        </ol>` : "";
  let body;
  if (recoveryCodes) {
    body = `<div class="invite-result" role="status"><strong>Two-step sign-in uses your authenticator app now.</strong> Save these recovery codes somewhere safe, such as your password manager. Each one signs you in once if you lose your phone. They are not shown again.
        <div class="copy-row"><code id="recovery-codes">${recoveryCodes.join("  ")}</code><button class="btn small secondary" data-action="copy" data-target="recovery-codes">Copy</button><button class="btn small" data-action="2fa-done">I saved them</button></div></div>`;
  } else if (me.two_step) {
    body = `<p class="sub"><span class="pill green">On</span> Authenticator app. Signing in asks for a code from the app.</p>
      ${codesByEmail ? `<details><summary>Use email codes instead</summary><div class="form-inline">
          <input id="2fa-off-pw" type="password" placeholder="Your password" autocomplete="current-password" aria-label="Your password">
          <input id="2fa-off-code" placeholder="Code from the app" autocomplete="one-time-code" inputmode="numeric" aria-label="Code from the app">
          <button class="btn small secondary" data-action="2fa-disable">Use email codes</button></div></details>` : ""}`;
  } else if (codesByEmail) {
    body = `<p class="sub"><span class="pill green">On</span> Email codes. Signing in on a new device asks for a 6-digit code we email to ${me.email}; that device is then remembered for 30 days.</p>
      ${setup || `<button class="btn small secondary" data-action="2fa-begin">Use an authenticator app instead</button>`}`;
  } else {
    body = `<p class="sub"><span class="pill amber">Not set up</span> Add a code from your phone to every sign-in, so a stolen password isn't enough.</p>
      ${setup || `<button class="btn small" data-action="2fa-begin">Set up two-step sign-in</button>`}`;
  }
  return `<div class="card${prompt ? " two-step-prompt" : ""}" id="two-step">
      <h2>${prompt ? "One more step: two-step sign-in" : "Two-step sign-in"}</h2>
      ${body}
      ${prompt && !recoveryCodes ? `<div class="form-inline"><button class="btn small secondary" data-action="2fa-later">Not now</button></div>` : ""}
    </div>`;
}

// Change password (S2), its own page under My account. On a server that sends mail, a 6-digit
// code emailed to the person is needed too; without mail, the current password is enough.
let codeSentTo = "";   // the address the last code went to, while this page is open
function passwordView() {
  if (me.company_id === DEMO_COMPANY_ID) return `${backButton("account", "My account")}<h1>Change password</h1>${emptyState("The demo account's password can't be changed")}`;
  return `${backButton("account", "My account")}
    <h1>Change password</h1>
    <div class="card" data-form-key="password">${formErrorLine("password")}
      ${codesByEmail ? `<div class="form-inline">
        <button class="btn small${codeSentTo ? " secondary" : ""}" data-action="pw-code">${codeSentTo ? "Send a new code" : "Email me a code"}</button>
        ${codeSentTo ? `<span class="sub" role="status">Code sent to ${codeSentTo}. It works for 15 minutes.</span>` : `<span class="sub">To ${me.email}</span>`}
      </div>` : ""}
      <div class="form-grid">
        ${codesByEmail ? field({ form: "password", name: "code", id: "pw-code", label: "Code from the email", autocomplete: "one-time-code", inputmode: "numeric", placeholder: "123456" }) : ""}
        ${field({ form: "password", name: "current", id: "pw-current", label: "Current password", type: "password", autocomplete: "current-password" })}
        ${field({ form: "password", name: "new", id: "pw-new", label: "New password (10+ characters)", type: "password", autocomplete: "new-password" })}
      </div>
      <button class="btn" data-action="change-password">Change password</button>
    </div>`;
}

function billingView() {
  const owner = can("billing");
  const isDemo = me.company_id === DEMO_COMPANY_ID;
  // A member sees where the trial and the subscription stand; an owner changes them (R6-10).
  if (!owner) return `<h1>Billing</h1>
    ${trialBannerHtml()}
    ${!isDemo && billingInfo && billingInfo.configured ? `<p class="sub billing-status">Subscription: ${billingInfo.status ? (BILLING_WORDS[billingInfo.status] || words(billingInfo.status)) : "none yet"}.</p>` : ""}
    ${billingInfo && billingInfo.warning ? `<p class="blocker">${billingInfo.warning}</p>` : ""}
    <p class="sub">Someone whose profile includes billing manages the subscription.</p>`;
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
  twoStepSetup = null; recoveryCodes = null; lastInvite = null; adminCompanies = null; switchDraft = {}; codeSentTo = ""; profileDraft = null;
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
  password() { return passwordView(); },
  "delete-company"() { return deleteCompanyView(); },
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
  "pw-code": async () => {
    const r = await postJson("/api/auth/password/code", {});
    if (!r.ok) { refuseForm("password", { _form: r.data.error || "No code was sent." }); return; }
    codeSentTo = me.email;
    render();
    document.getElementById("pw-code")?.focus();
  },
  "change-password": async () => {
    const current = document.getElementById("pw-current").value, next = document.getElementById("pw-new").value;
    const code = codesByEmail ? (document.getElementById("pw-code")?.value || "").trim() : "";
    const errors = {};
    if (codesByEmail && !/^\d{6}$/.test(code.replace(/\s+/g, ""))) errors.code = "Enter the 6-digit code from the email.";
    if (!current) errors.current = "Enter your current password.";
    if (next.length < 10) errors.new = "Use at least 10 characters for the new password.";
    if (refuseForm("password", errors)) return;
    const r = await postJson("/api/auth/password", { current, new: next, ...(codesByEmail ? { code } : {}) });
    if (!r.ok) { refuseForm("password", r.data.fields || { _form: r.data.error || "That didn't work." }); return; }
    clearForm("password");
    codeSentTo = "";
    nav("account");
    toast(r.data.message);
  },
  "team-invite": async () => {
    const v = formValues("invite");
    if (refuseForm("invite", /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v.email) ? {} : { email: "Enter their email, like pat@yourcompany.com." })) return;
    const r = await postJson("/api/team/invite", { email: v.email, profile_id: v.profile_id || "member" });
    if (!r.ok) { refuseForm("invite", r.data.fields || { _form: r.data.error || "That didn't work." }); return; }
    const data = r.data;
    if (data.users) setTeamData(escDeep(teamOnly(data)));
    clearForm("invite");
    lastInvite = { link: data.invite_link || (location.origin + data.invite_path), emailed: data.emailed, email_status: data.email_status, email: v.email };
    render();
  },
  "team-profile": async (el) => {
    const pick = document.getElementById(`prof-${el.dataset.user}`);
    if (pick) await teamPost(`/api/team/${el.dataset.user}/role`, { profile_id: pick.value }, "Profile changed.");
  },
  "profile-new": () => { profileDraft = { id: null, name: "", perms: [] }; clearForm("profile"); render(); document.getElementById("profile-name")?.focus(); },
  "profile-edit": (el) => {
    const p = (teamData.profiles || []).find(x => x.id === el.dataset.profile);
    if (!p) return;
    profileDraft = { id: unesc(p.id), name: unesc(p.name), perms: p.perms.map(unesc) };
    clearForm("profile"); render();
  },
  "profile-cancel": () => { profileDraft = null; clearForm("profile"); render(); },
  "profile-save": async () => {
    const perms = [...document.querySelectorAll("[data-perm]")].filter(b => b.checked).map(b => b.dataset.perm);
    const name = profileDraft.id === "member" ? "" : (formValues("profile").name || "");
    if (profileDraft.id !== "member" && refuseForm("profile", name.trim() ? {} : { name: "Name the profile, like Office manager." })) return;
    const r = await postJson(profileDraft.id ? `/api/team/profiles/${encodeURIComponent(profileDraft.id)}` : "/api/team/profiles", { name, perms });
    if (!r.ok) { refuseForm("profile", r.data.fields || { _form: r.data.error || "Not saved." }); return; }
    setTeamData(escDeep(teamOnly(r.data)));
    profileDraft = null; clearForm("profile");
    toast("Profile saved.");
    render();
  },
  "profile-delete": async (el) => {
    if (!(await askConfirm(`Delete the ${unesc(el.dataset.name)} profile?`, "Delete", true))) return;
    await teamPost(`/api/team/profiles/${encodeURIComponent(el.dataset.profile)}/delete`, {}, "Profile deleted.");
  },
  "admin-hand-over": async () => {
    const to = document.getElementById("admin-to")?.value || "";
    const name = document.querySelector(`#admin-to option[value="${to}"]`)?.textContent || "them";
    if (!(await askConfirm(`Hand the Company Admin role to ${name}? Only they will be able to delete the company.`, "Hand over", true))) return;
    const r = await teamPost("/api/team/admin", { user_id: to, password: document.getElementById("admin-password")?.value || "" }, "The Company Admin role was handed over.");
    if (r) setMe({ ...me, company_admin: false });
  },
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
  "2fa-done": () => {
    recoveryCodes = null;
    if (twoStepPrompt) { setTwoStepPrompt(false); me.two_step_setup_due = false; nav("dashboard"); return; }
    render();
  },
  "2fa-later": () => { setTwoStepPrompt(false); twoStepSetup = null; nav("dashboard"); },
  "2fa-disable": async () => {
    const r = await postJson("/api/auth/2fa/disable", { password: document.getElementById("2fa-off-pw")?.value || "", code: document.getElementById("2fa-off-code")?.value || "" });
    toast(r.ok ? r.data.message : (r.data.error || "That didn't work."));
    if (r.ok) { me.two_step = false; render(); }
  },
  // The Company page's one Save (S1): the details, the address and the switches together.
  "save-company": async () => {
    const { body, errors } = companyChanges(formValues("company"), state.company, switchDraft);
    if (refuseForm("company", errors)) return;
    const r = await post("/api/company", body, { formKey: "company" });
    // Saved: the boxes show what the server kept (a phone as (404) 555-0123), not the typed copy.
    if (r && r.ok) { switchDraft = {}; clearForm("company"); render(); }
  },
  // My account's one Save (S1): the name, when it changed, and the appearance (G9).
  "save-account": async () => {
    const v = formValues("account");
    // The sample company has no name box: only its appearance is saved, in this browser.
    if ("name" in v && refuseForm("account", checkFields(v, { name: "name" }, ["name"]))) return;
    if ("name" in v && v.name !== unesc(me.name)) {
      const r = await postJson("/api/auth/name", { name: v.name });
      if (!r.ok) { refuseForm("account", r.data.fields || { _form: r.data.error || "Not saved." }); return; }
      setMe(escDeep(r.data.user));
    }
    const look = formValues("appearance");
    const was = savedAppearance();
    const wanted = { theme: look.theme || was.theme, accent: look.accent || was.accent };
    if (me.company_id === DEMO_COMPANY_ID) applyAppearance(wanted);   // this browser only
    else if (wanted.theme !== was.theme || wanted.accent !== was.accent) {
      const r = await postJson("/api/me/appearance", wanted);
      if (!r.ok) { refuseForm("appearance", r.data.fields || { _form: r.data.error || "Not saved." }); return; }
      setMe(escDeep(r.data.user));
      applyAppearance(r.data.user.appearance);
    }
    clearForm("account"); clearForm("appearance");
    toast("Saved");
    render();
  },
  "billing-checkout": () => openBilling("/api/billing/checkout"),
  "billing-portal": () => openBilling("/api/billing/portal"),
};
