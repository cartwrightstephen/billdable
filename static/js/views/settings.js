// Settings: Company, Team, My account, Billing, Activity log and Helper answers (the Duracash
// team's page). The Client playbook is in clients.js.
import { esc, unesc, escDeep, fmtDate, fmtDateTime, words } from "../format.js";
import { readJson, setNetBanner, postJson } from "../net.js";
import { moreButton, askConfirm, titleWithInfo } from "../dom.js";
import { field, formErrorLine, refuseForm, formValues, clearForm, formDraft } from "../forms.js";
import { US_STATES, checkFields } from "../validate.js";
import { actionWords } from "../terms.js";
import { jobFilterOptions, jobFilterShown } from "../jobs.js";
import { inviteLocked, inviteLockHtml } from "../setup.js";
import { state, me, currentView, formOpen, setMe, clearSession, onSessionEnd, setTeamData, teamData, mailReady, billingInfo, codesByEmail, twoStepPrompt, setTwoStepPrompt, helperAdmin } from "../state.js";
import { DEMO_COMPANY_ID, post, render, toast, patch, moreDetails, emptyState, listPage, renderLanding, backButton, nav, can } from "../app-core.js";
import { THEMES, ACCENTS, applyAppearance, normalizeAppearance, storedAppearance } from "../theme.js";
import { qboCardHtml } from "./payments.js";
import { docusignCardHtml } from "./docusign.js";
import { approverBoxes, approverIds, forgetApprovers, approverNames } from "../approvers.js";
import { forgetImportSettings } from "./inbox.js";
import { notificationsCardHtml, saveReminderPrefs } from "./automations.js";

let twoStepSetup = null;  // {secret, uri} while setting up two-step sign-in
let recoveryCodes = null; // shown once, right after two-step sign-in is turned on
const ordinal = (n) => n + ([11, 12, 13].includes(n % 100) ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th"));
const BILLING_WORDS = { trialing: "Trial", active: "Active", past_due: "Card declined", unpaid: "Unpaid", canceled: "Cancelled", incomplete: "Incomplete", incomplete_expired: "Expired", paused: "Paused" };
let auditJobFilter = "all";
let auditWho = "";   // "people": without the app's own entries (round 1, item 14)
export function setAuditJobFilter(value) { auditJobFilter = value; }
// The log's query: the job picked and whose entries (app-core's LIST_QUERY lists).
function auditQuery() {
  return [auditJobFilter !== "all" ? "job=" + encodeURIComponent(unesc(auditJobFilter)) : "", auditWho ? "who=" + auditWho : ""].filter(Boolean).join("&");
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
  return { users: d.users || [], invites: d.invites || [], profiles: d.profiles || [], permissions: d.permissions || [],
    templates: d.templates || [], reminders: d.reminders || null };
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
  // Notifications (Nov 26 walkthrough): those who manage the team see and set everyone's.
  const notes = !!(teamData && teamData.reminders) && users.some(u => u.notifications);
  const others = users.filter(mayChange);
  return `
      ${titleWithInfo("Team", "About the team", `<p>Each person's profile says what they can do. Those who manage the team change profiles, invite people and set anyone's notifications.</p>
        ${notes ? `<p>When you set someone's notifications and they change them later on My account, their change applies, and this list shows what they changed.</p>` : ""}`)}
      <div class="table-wrap"><table class="team-table">
        <tr><th>Name</th><th>Email</th><th>Profile</th>${notes ? "<th>Notifications</th>" : ""}<th>Joined</th><th></th></tr>
        ${users.map(u => `<tr><td>${u.name}${u.id === me.id ? " (you)" : ""}${u.company_admin ? ` <span class="pill copper">Company Admin</span>` : ""}</td><td>${u.email}</td>
          <td>${mayChange(u) ? `<span class="form-inline"><select id="prof-${u.id}" aria-label="Profile for ${u.name}">${profileOptions(u.profile_id)}</select>
            <button class="btn small secondary" data-action="team-profile" data-user="${u.id}">Change</button></span>` : u.profile_name || words(u.role)}</td>
          ${notes ? `<td class="team-notes">${notificationSummaryHtml(u.notifications, teamData.reminders, u.id === me.id)}${mayChange(u) && edit && !isDemo ? `<div><button class="btn small secondary" data-action="team-notes-edit" data-user="${u.id}" aria-label="Set notifications for ${u.name}">Set</button></div>` : ""}</td>` : ""}
          <td>${fmtDate(u.created_at)}</td>
          <td>${mayChange(u) && !u.company_admin ? `<button class="btn small secondary" data-action="team-remove" data-user="${u.id}" data-name="${u.name}">Remove</button>` : ""}</td></tr>`).join("")}
      </table></div>
      ${notes && edit && others.length > 1 && !notesDraft ? `<p><button class="btn small secondary" data-action="team-notes-edit" data-user="*">Set notifications for everyone</button></p>` : ""}
      ${notes && edit ? teamNotesEditorHtml(users) : ""}
      ${edit ? `
        ${titleWithInfo("Invite someone", "About invites", "<p>They get an email with a link to join. Pick the profile that says what they can do; you can change it later.</p>", { level: 3 })}
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

// One person's notifications on the Team page (Nov 26 walkthrough): how they reach them, then who
// set them, or what the person changed since. n: the server's reminders.team_view, escaped; r: the
// kinds and channels. Exported for the tests.
export function notificationSummaryHtml(n, r, self = false) {
  if (!n || !r) return "";
  const label = Object.fromEntries(r.channels.map(([k, v]) => [k, v]));
  const used = r.channels.map(([k]) => k).filter(k => r.kinds.some(kind => (n.prefs[kind.key] || []).includes(k)));
  const how = used.length ? used.map(k => label[k]).join(", ") : "None";
  const textWaits = used.includes("text") && !n.texts_on ? ` <span class="sub">(texts wait until they agree on My account)</span>` : "";
  const status = n.changed && n.changed.length
    ? `<div class="sub notes-changed"><span class="pill amber">Changed by them</span> ${n.changed.map(c => c.words).join("; ")}</div>`
    : self ? `<div class="sub">Yours, on <button class="linkish" data-action="nav" data-view="account">My account</button></div>`
    : n.set_by ? `<div class="sub">As ${n.set_by} set them</div>` : `<div class="sub">Their own choice</div>`;
  return `<div>${how}${textWaits}</div>${status}`;
}

// The notifications being set from the Team page: {ids, who, prefs} while open.
let notesDraft = null;
function teamNotesEditorHtml(users) {
  const r = teamData && teamData.reminders;
  if (!notesDraft || !r) return "";
  const targets = users.filter(u => notesDraft.ids.includes(unesc(u.id)));
  const noTexts = targets.filter(u => u.notifications && !u.notifications.texts_on).map(u => u.name);
  return `<div class="card team-notes-form" id="team-notes-form" data-form-key="teamnotes">${formErrorLine("teamnotes")}
    <h3>Notifications for ${notesDraft.who}</h3>
    ${r.kinds.map(k => `<fieldset class="reminder-kind"><legend><strong>${k.label}</strong></legend>
      ${r.channels.map(([c, words]) => `<label class="terms-check"><input type="checkbox" data-team-reminder="${k.key}" value="${c}"${(notesDraft.prefs[unesc(k.key)] || []).includes(unesc(c)) ? " checked" : ""}> ${words}</label>`).join("")}
    </fieldset>`).join("")}
    ${noTexts.length ? `<p class="sub">Texts wait until ${noTexts.join(", ")} ${noTexts.length === 1 ? "adds a mobile number and agrees" : "add a mobile number and agree"} to texts on My account.</p>` : ""}
    <div class="form-inline"><button class="btn" data-action="team-notes-save">Save notifications</button><button class="btn secondary" data-action="team-notes-cancel">Cancel</button></div>
  </div>`;
}

// What each profile may do, one column each (S6). Owner holds everything and is locked; Member
// and custom profiles can be added, changed and deleted by owners only (A4; the server refuses
// anyone else, so nobody who manages the team can give their own profile more).
function profilesCardHtml() {
  if (!teamData || !teamData.profiles) return "";
  const edit = ownerMe() && me.company_id !== DEMO_COMPANY_ID;
  const list = teamData.profiles;
  const perms = teamData.permissions || [];
  return `<h2>Permission profiles</h2>
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
  // Starting templates (Nov 26 walkthrough): Owner, Accountant or office, Project manager,
  // Superintendent, Field. Picking one fills the boxes; everything stays editable.
  const templates = (teamData && teamData.templates) || [];
  const picked = templates.find(t => unesc(t.id) === d.template);
  return `<div class="card profile-form" data-form-key="profile">${formErrorLine("profile")}
    <h3>${d.id ? `Edit ${esc(d.name)}` : "New profile"}</h3>
    ${!d.id && templates.length ? `<div class="profile-templates"><span class="field-label" id="profile-template-label">Start from</span>
      <div class="seg" role="group" aria-labelledby="profile-template-label">${templates.map(t => `<button type="button" class="btn small${d.template === unesc(t.id) ? "" : " secondary"}" data-action="profile-template" data-template="${t.id}" aria-pressed="${d.template === unesc(t.id)}">${t.name}</button>`).join("")}</div>
      ${picked ? `<p class="sub">${picked.about} Change any box before you save.</p>` : ""}</div>` : ""}
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
  return `${titleWithInfo("Company Admin", "About the Company Admin", "<p>The Company Admin is an owner who can also delete the company. They can hand the role to another owner.</p>")}
    <p class="sub">${admin ? `${admin.name}${mine ? " (you)" : ""} is the Company Admin.` : "No Company Admin yet."}</p>
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
      ${owner ? titleWithInfo("Sending and email", "About sending", `<p>Who can send pay apps, waivers and retainage requests is set by each person's profile, on <button class="linkish" data-action="nav" data-view="team">Team</button>.</p>`) : "<h2>Sending and email</h2>"}
      ${box("daily_email", on("daily_email", co.daily_email !== false), "Daily email of what's due",
        "Each morning owners get one email listing what's due today, with a link to each. Nothing is sent on a day with nothing due.")}
      ${owner ? "" : `<p class="sub">Your profile doesn't include changing these.</p>`}
    </div>`;
}

// Who approves pay apps before they go (Stephen, 10 Nov, decision 8). A client can have its own
// list on its Client playbook card.
function approversCardHtml(owner) {
  const saved = (state.company || {}).payapp_approvers || [];
  return `<div class="card" id="approvers-card" data-form-key="approvers">
      <h2>Pay app approvers</h2>${formErrorLine("approvers")}
      ${approverBoxes("company", saved, render, !owner)}
      ${saved.length ? "" : `<p class="sub approvers-default">Nobody ticked, so the owners approve: ${approverNames(state.team_owners, "every owner")}.</p>`}
      ${owner ? `<button class="btn small" data-action="approvers-save">Save approvers</button>` : ""}
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
// File storage (H5): how much of the company's room uploaded files take, on Settings, Company.
let storageInfo = null;   // {used_bytes, cap_bytes, ...} once /api/storage answers
let storageAsked = 0;     // when it was last asked for: asked again a minute later, after uploads
export function sizeWords(n) {
  const gb = n / (1024 ** 3);
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.max(1, Math.round(n / (1024 ** 2)))} MB`;
}
export function storageHtml(info) {
  if (!info) return `<p class="sub" id="storage-use">Files: checking how much room they take…</p>`;
  const used = Number(info.used_bytes) || 0, cap = Number(info.cap_bytes) || 1;
  const share = Math.min(100, Math.round((used / cap) * 100));
  const full = used >= cap * 0.9;
  return `<p class="sub" id="storage-use">Files: ${sizeWords(used)} of ${sizeWords(cap)} used (${share}%).
      <progress class="storage-meter" max="100" value="${share}" aria-label="File storage used">${share}%</progress>
      ${full ? `<span class="pill amber">Nearly full</span> Delete files you no longer need, or contact Duracash support for more room.` : ""}
      Each file can be up to ${Number(info.max_file_mb) || 15} MB.</p>`;
}
async function loadStorage() {
  storageAsked = Date.now();
  try {
    const res = await fetch("/api/storage");
    if (!res.ok) return;
    storageInfo = await readJson(res);
  } catch (_) { return; }
  patch("#storage-use-box", storageHtml(storageInfo));
}

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
  if (Date.now() - storageAsked > 60_000) loadStorage();
  return `
    <h1>Company</h1>
    <div data-form-key="company">${formErrorLine("company")}
    <div class="card" id="company-card">
      <h2>Company details</h2>
      ${owner ? "" : `<p class="sub">Your profile doesn't include changing these.</p>`}
      <div class="form-grid">
        ${box("name", "co-name", "Company name", "name", { required: true, autocomplete: "organization" })}
        ${box("phone", "co-phone", "Phone", "phone", { type: "tel", inputmode: "tel", autocomplete: "tel" })}
        ${box("street", "co-address", "Street address", "street", { wide: true, autocomplete: "address-line1" })}
        ${box("city", "co-city", "City", "city", { autocomplete: "address-level2" })}
        ${box("state", "co-state", "State", "state", { options: [["", "Pick a state"], ...US_STATES.map(c => [c, c])] })}
        ${box("zip", "co-zip", "ZIP code", "zip", { inputmode: "numeric", autocomplete: "postal-code" })}
        ${box("license", "co-license", "Contractor licence number", "licence")}
        ${box("legal_name", "co-legal-name", "Legal name (on contracts and lien waivers)", undefined, { wide: true, placeholder: "Same as the company name" })}
      </div>
    </div>
    ${isDemo ? "" : companySwitchesHtml(owner)}
    </div>
    ${approversCardHtml(owner)}
    ${qboCardHtml(false)}
    ${docusignCardHtml()}
    <details class="card danger-zone" data-more="danger"${formOpen.reset || moreDetails.danger ? " open" : ""}>
      <summary><strong>Your data</strong></summary>
      <h2>Your data</h2>
      <div id="storage-use-box">${storageHtml(storageInfo)}</div>
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

// Where you're signed in (L2): each live session, and Sign out everywhere else.
let sessionList = null;   // [{id, current, browser, created_at, last_seen_at}] once /api/auth/sessions answers
let sessionsAsked = 0;
export function setSessionList(rows) { sessionList = rows; }
export function sessionsCardHtml(rows = sessionList, demo = false) {
  const others = (rows || []).filter(r => !r.current);
  const line = (r) => `<div class="money-row"><span class="label">${r.browser}${r.current ? ` <span class="pill green">This browser</span>` : ""}</span>
      <span>${r.last_seen_at ? `last used ${fmtDateTime(r.last_seen_at)}` : ""}${r.created_at ? `, signed in ${fmtDateTime(r.created_at)}` : ""}
      ${r.current || demo ? "" : ` <button class="btn small secondary" data-action="session-end" data-id="${r.id}">Sign out</button>`}</span></div>`;
  return `<div class="card" id="sessions-card">
      <h2>Where you're signed in</h2>
      ${rows ? rows.map(line).join("") : `<p class="sub">Loading…</p>`}
      ${demo ? `<p class="sub">The sample company is shared, so other visitors aren't listed.</p>`
        : others.length ? `<div class="form-inline"><button class="btn small secondary" data-action="sessions-end-others">Sign out everywhere else</button></div>`
        : rows ? `<p class="sub">Signed in on this browser only.</p>` : ""}
    </div>`;
}
async function loadSessions() {
  sessionsAsked = Date.now();
  try {
    const res = await fetch("/api/auth/sessions");
    if (!res.ok) return;
    sessionList = escDeep((await readJson(res)).sessions || []);
  } catch (_) { return; }
  patch("#sessions-box", sessionsCardHtml(sessionList, me && me.company_id === DEMO_COMPANY_ID));
}
async function endSessions(id) {
  const r = await postJson("/api/auth/sessions/end", id ? { id } : {});
  if (!r.ok) { toast(r.data.error || "That didn't work."); return; }
  sessionList = escDeep(r.data.sessions || []);
  toast(r.data.ended === 1 ? "Signed out of 1 other session." : `Signed out of ${r.data.ended} other sessions.`);
  patch("#sessions-box", sessionsCardHtml(sessionList, me && me.company_id === DEMO_COMPANY_ID));
}

function myAccountView() {
  const isDemo = me.company_id === DEMO_COMPANY_ID;
  if (Date.now() - sessionsAsked > 30_000) loadSessions();
  const verified = me.email_verified
    ? `<span class="pill green">confirmed</span>`
    : `<span class="pill amber">not confirmed</span> ${mailReady ? `<button class="btn small secondary" data-action="resend-verify">Send the link again</button>` : `<span class="sub">Confirming needs email set up on the server.</span>`}`;
  return `
    <h1>My account</h1>
    ${twoStepPrompt && !isDemo ? twoStepCardHtml(true) : ""}
    <div class="card" data-form-key="account">${formErrorLine("account")}
      <h2>You</h2>
      ${isDemo ? `<div class="money-row"><span class="label">Name</span><span>${me.name}</span></div>` : `<div class="form-grid">${field({ form: "account", name: "name", id: "my-name", label: "Name", value: unesc(me.name), autocomplete: "name", rule: "name", required: true })}</div>`}
      <div class="money-row"><span class="label">Email</span><span class="long-word">${me.email} ${isDemo ? "" : verified}</span></div>
      ${isDemo ? "" : textsHtml()}
      <div class="money-row"><span class="label">Profile</span><span>${me.profile_name || words(me.role)}${me.company_admin ? ` <span class="pill copper">Company Admin</span>` : ""}</span></div>
      ${isDemo ? "" : `<div class="money-row"><span class="label">Password</span><span><button class="btn small secondary" data-action="nav" data-view="password">Change password</button></span></div>`}
    </div>
    ${notificationsCardHtml()}
    ${appearanceCardHtml()}
    ${isDemo || twoStepPrompt ? "" : twoStepCardHtml()}
    <div id="sessions-box">${sessionsCardHtml(sessionList, isDemo)}</div>
    ${me.onboarding_dismissed && !isDemo ? `<p><button class="btn small secondary" data-action="onb-show">Show the setup checklist</button></p>` : ""}
    <p class="sub">Your account id: <code>${me.id}</code></p>
    <div class="save-bar"><button class="btn" data-action="save-account">Save</button></div>
  `;
}

// The mobile number and the agreement to texts (Decision 7). Texts are this person's own reminders.
let smsCodeTo = "";   // the number a code was just texted to, until it is confirmed
const shownPhone = (e164) => { const m = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(unesc(e164 || "")); return m ? `(${m[1]}) ${m[2]}-${m[3]}` : ""; };
function textsHtml() {
  // A redraw while the box is open (a background load) keeps what was ticked.
  const box = globalThis.document?.getElementById("my-sms-opt-in");
  const ticked = box ? box.checked : me.sms_opt_in;
  return `<div class="form-grid">${field({ form: "account", name: "phone", id: "my-phone", label: "Mobile phone", value: shownPhone(smsCodeTo || me.phone), autocomplete: "tel", inputmode: "tel", rule: "phone" })}</div>
      <label class="terms-check"><input type="checkbox" id="my-sms-opt-in"${ticked ? " checked" : ""}> Text me Duracash work reminders at this number: important dates and missing information, up to one message a day per company. Message and data rates may apply. Reply STOP to stop, HELP for help. Agreeing is not a condition of using Duracash.</label>
      ${smsCodeTo ? `<div class="invite-result" role="status" data-form-key="smscode"><strong>Enter the code we texted to ${esc(shownPhone(smsCodeTo))}.</strong> Texts turn on once it is confirmed.
        <div class="form-grid">${field({ form: "smscode", name: "sms_code", id: "my-sms-code", label: "Code from the text", autocomplete: "one-time-code", inputmode: "numeric" })}</div>
        ${formErrorLine("smscode")}
        <div class="form-inline"><button class="btn small" data-action="sms-confirm">Confirm number</button><button class="btn small secondary" data-action="sms-resend">Send a new code</button></div></div>` : ""}
      ${me.sms_stopped ? `<p class="sub">You replied STOP, so texts are off. To turn them back on, text START to ${me.texts_number ? esc(shownPhone(me.texts_number)) : "the Duracash number"} first, then tick the box and save.</p>` : ""}
      ${me.sms_needs_confirming && !smsCodeTo ? `<p class="sub">Confirm your number to keep getting texts: tick the box and save, then enter the code we text you.</p>` : ""}`;
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
          <li>Type the 6-digit code the app shows: <span class="form-inline"><input id="2fa-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6"><button class="btn small" data-action="2fa-enable">Turn on</button></span></li>
        </ol>` : "";
  // Starting the set-up asks for the password again (M2): an open session alone can't add a phone.
  const begin = (cls, words) => `<div class="form-inline"><input id="2fa-begin-pw" type="password" placeholder="Your password" autocomplete="current-password" aria-label="Your password">
        <button class="btn small${cls}" data-action="2fa-begin">${words}</button></div>`;
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
    body = `<p class="sub"><span class="pill green">On</span> Email codes. Signing in on a new device asks for a 6-digit code we email to <span class="long-word">${me.email}</span>; that device is then remembered for 30 days.</p>
      ${setup || begin(" secondary", "Use an authenticator app instead")}`;
  } else {
    body = `<p class="sub"><span class="pill amber">Not set up</span> Add a code from your phone to every sign-in, so a stolen password isn't enough.</p>
      ${setup || begin("", "Set up two-step sign-in")}`;
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
        ${codeSentTo ? `<span class="sub long-word" role="status">Code sent to ${codeSentTo}. It works for 15 minutes.</span>` : `<span class="sub long-word">To ${me.email}</span>`}
      </div>` : ""}
      <div class="form-grid">
        ${codesByEmail ? field({ form: "password", name: "code", id: "pw-code", label: "Code from the email", autocomplete: "one-time-code", inputmode: "numeric" }) : ""}
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
      ${titleWithInfo("Billing", "About billing", `<p>Charged on the ${ordinal(Number(billingInfo.billing_day) || 5)} of each month, after your own pay apps are out. Payment by card through Stripe. Duracash never stores the card.</p><p>Nothing is ever deleted for an unpaid bill; at worst the account becomes read-only.</p>`)}
      ${billingInfo.status ? `<p class="sub"><span class="pill ${["active", "trialing"].includes(billingInfo.status) ? "green" : "red"}">${BILLING_WORDS[billingInfo.status] || words(billingInfo.status)}</span>${billingInfo.period_end ? ` Next renewal ${fmtDate(new Date(billingInfo.period_end * 1000).toISOString())}.` : ""}</p>`
        : `<p class="sub">No subscription yet. The free trial still applies: the first charge comes when it ends.</p>`}
      ${billingInfo.warning ? `<p class="blocker">${billingInfo.warning}</p>` : ""}
      <div class="form-inline">${billingInfo.has_customer && billingInfo.status && !["canceled", "incomplete_expired"].includes(billingInfo.status)
        ? `<button class="btn" data-action="billing-portal">Manage billing</button>` : `<button class="btn" data-action="billing-checkout">Subscribe</button>`}</div>
      ${billingInfo.partner ? `<p class="sub"><span class="pill copper">Design partner</span> The design-partner price applies.</p>` : ""}
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
  return `<div class="trial-banner">Your free trial ended on ${day(t.ended_at)}${t.reason === "paid" ? `, when ${t.first_paid} on ${t.first_paid_job} was paid` : ""}.${live ? (billingInfo.status === "active" || billingInfo.status === "trialing" ? " Your subscription is on." : can("billing") ? " Subscribe in Settings, Billing." : " An owner can subscribe in Settings, Billing.") : ""}</div>`;
}

// Signed out: what one person had open in Settings isn't kept for the next.
export function forgetSettings() {
  twoStepSetup = null; recoveryCodes = null; lastInvite = null; adminCompanies = null; switchDraft = {}; codeSentTo = ""; profileDraft = null; notesDraft = null;
  helperReview = null; helperDraft = null; auditJobFilter = "all"; auditWho = "";
  storageInfo = null; storageAsked = 0; sessionList = null; sessionsAsked = 0;
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
const HELPER_VIEWS = ["dashboard", "calendar", "jobs", "daily", "history", "playbook", "inbox", "outbox", "import", "export", "payapps", "packets", "cos", "collections", "waivers", "retainage", "financials", "account", "audit"];

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
    // The Duracash team's page (HELPER_ADMIN_USER_IDS): anyone else who types its address sees no
    // such page (D6). The server refuses its data to them too (is_helper_admin, server.py).
    if (!helperAdmin) return emptyState("Page not found", "There is nothing at this address.", `<button class="btn" data-action="nav" data-view="dashboard">Go to Dashboard</button>`);
    return `<h1>Helper answers</h1>
      <p class="sub">What users asked the helper that it couldn't answer well, and the answers it uses. Write an answer once; every company gets it right away. Only accounts listed in HELPER_ADMIN_USER_IDS see this page.</p>
      <div id="helper-admin-body">Loading…</div>
      <div class="card"><h2>Companies</h2><p class="sub">Every company on this server, with its trial and, once Stripe is set up, its subscription. Nothing is enforced unless BILLING_ENFORCE=1.</p><div id="admin-companies">${adminCompaniesHtml()}</div></div>`;
  },
  audit() {
    // The company's whole log takes "Export and reset data" (L1); each job's history is on its page.
    if (!can("data")) return `<h1>Activity log</h1><p class="sub">The company's activity log needs the permission "Export and reset data". Each job's history is on its own page.</p>`;
    const page = listPage("audit", auditQuery());
    const events = page ? page.entries : [];
    return `
      <h1>Activity log</h1>
      ${state.audit_chain && !state.audit_chain.ok ? `<p class="blocker">The chain of records is broken at ${state.audit_chain.broken_at}.</p>` : ""}
      ${jobFilterShown(state.jobs) ? `<div class="filter-bar">
        <label for="audit-filter" class="inline-label">Job</label>
        <select id="audit-filter" data-filter-for="audit">
          <option value="all">All jobs</option>
          ${jobFilterOptions(state.jobs, auditJobFilter)}
        </select>
      </div>` : ""}
      <div class="filter-bar" role="group" aria-label="Whose entries">
        <button class="${auditWho ? "btn secondary" : "btn"} small" data-action="audit-who" data-who="" aria-pressed="${!auditWho}">Everything</button>
        <button class="${auditWho ? "btn" : "btn secondary"} small" data-action="audit-who" data-who="people" aria-pressed="${!!auditWho}">Without the app's own entries</button>
      </div>
      ${events.length ? `<div class="table-wrap">
        <table>
          <tr><th>What happened</th><th>When</th><th>Who</th><th>Action</th></tr>
          ${events.map(e => `<tr><td>${e.detail}</td><td>${fmtDateTime(e.at)}</td><td>${e.actor}</td><td>${actionWords(e.action)}</td></tr>`).join("")}
        </table>
      </div>${moreButton("audit", page)}` : !page ? `<p class="sub">Loading…</p>` : emptyState("No activity yet")}
    `;
  },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in app.js.
export const actions = {
  "audit-who": (el) => { auditWho = el.dataset.who === "people" ? "people" : ""; render(); },
  "reset-data": () => resetData(),
  "sessions-end-others": () => endSessions(""),
  "session-end": (el) => endSessions(unesc(el.dataset.id || "")),
  "sms-resend": async () => {
    const r = await postJson("/api/auth/sms", { phone: smsCodeTo, sms_opt_in: true });
    if (!r.ok) { refuseForm("smscode", r.data.fields || { _form: r.data.error || "Not sent." }); return; }
    setMe(escDeep(r.data.user));
    if (r.data.code_sent) smsCodeTo = r.data.sent_to;
    clearForm("smscode");
    toast("A new code is on its way.");
    render();
  },
  "sms-confirm": async () => {
    const r = await postJson("/api/auth/sms/confirm", { code: formValues("smscode").sms_code || "" });
    if (!r.ok) { refuseForm("smscode", r.data.fields || { _form: r.data.error || "Not confirmed." }); return; }
    setMe(escDeep(r.data.user));
    smsCodeTo = "";
    clearForm("smscode"); clearForm("account");
    toast("Number confirmed. Texts are on.");
    render();
  },
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
  "profile-new": () => { profileDraft = { id: null, name: "", perms: [], template: "" }; clearForm("profile"); render(); document.getElementById("profile-name")?.focus(); },
  // A starting template fills the boxes and, unless a name of their own was typed, the name.
  "profile-template": (el) => {
    const t = ((teamData && teamData.templates) || []).find(x => unesc(x.id) === unesc(el.dataset.template || ""));
    if (!t || !profileDraft) return;
    const typed = (formValues("profile").name || "").trim();
    const names = teamData.templates.map(x => unesc(x.profile_name));
    if (!typed || names.includes(typed)) (formDraft.profile ||= {}).name = unesc(t.profile_name);
    profileDraft = { ...profileDraft, template: unesc(t.id), perms: t.perms.map(unesc) };
    render();
  },
  // Notifications set from the Team page (Nov 26 walkthrough): one person, or everyone you can change.
  "team-notes-edit": (el) => {
    const users = (teamData && teamData.users) || [];
    const mine = (u) => u.id !== me.id && (ownerMe() || u.profile_id !== "owner");
    if (el.dataset.user === "*") {
      const kinds = ((teamData.reminders || {}).kinds || []).map(k => unesc(k.key));
      notesDraft = { ids: users.filter(mine).map(u => unesc(u.id)), who: "everyone you can change", prefs: Object.fromEntries(kinds.map(k => [k, ["app"]])) };
    } else {
      const u = users.find(x => x.id === el.dataset.user);
      if (!u || !u.notifications) return;
      notesDraft = { ids: [unesc(u.id)], who: u.name, prefs: Object.fromEntries(Object.entries(u.notifications.prefs).map(([k, v]) => [unesc(k), v.map(unesc)])) };
    }
    clearForm("teamnotes");
    render();
    document.getElementById("team-notes-form")?.scrollIntoView?.({ block: "nearest" });
  },
  "team-notes-cancel": () => { notesDraft = null; clearForm("teamnotes"); render(); },
  "team-notes-save": async () => {
    if (!notesDraft) return;
    const kinds = ((teamData.reminders || {}).kinds || []).map(k => unesc(k.key));
    const prefs = Object.fromEntries(kinds.map(k => [k, [...document.querySelectorAll(`input[data-team-reminder="${k}"]:checked`)].map(b => b.value)]));
    const r = await postJson("/api/team/reminders", { user_ids: notesDraft.ids, prefs });
    if (!r.ok) { refuseForm("teamnotes", { _form: r.data.error || "Not saved." }); return; }
    setTeamData(escDeep(teamOnly(r.data)));
    notesDraft = null; clearForm("teamnotes");
    toast("Notifications saved. If they change them later, theirs apply and this list shows it.");
    render();
  },
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
    const r = await postJson("/api/auth/2fa/begin", { password: document.getElementById("2fa-begin-pw")?.value || "" });
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
  "approvers-save": async () => {
    const r = await post("/api/approvers", { user_ids: approverIds("company", (state.company || {}).payapp_approvers || []) }, { formKey: "approvers" });
    if (r && r.ok) { forgetApprovers("company"); toast("Approvers saved."); }
  },
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
    const optIn = document.getElementById("my-sms-opt-in");
    // A Save while a code is waiting for this same number doesn't ask for another.
    const waiting = optIn && optIn.checked && smsCodeTo && (v.phone || "") === shownPhone(smsCodeTo);
    if (optIn && !waiting && (("phone" in v && v.phone !== shownPhone(me.phone)) || optIn.checked !== !!me.sms_opt_in)) {
      const r = await postJson("/api/auth/sms", { phone: v.phone || "", sms_opt_in: optIn.checked });
      if (!r.ok) { refuseForm("account", r.data.fields || { _form: r.data.error || "Not saved." }); return; }
      setMe(escDeep(r.data.user));
      smsCodeTo = r.data.code_sent ? r.data.sent_to : "";
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
    if (!(await saveReminderPrefs())) return;
    clearForm("account"); clearForm("appearance");
    toast(smsCodeTo ? "Saved. Enter the code we texted you to turn texts on." : "Saved");
    render();
    if (smsCodeTo) document.getElementById("my-sms-code")?.focus();
  },
  "billing-checkout": () => openBilling("/api/billing/checkout"),
  "billing-portal": () => openBilling("/api/billing/portal"),
};
