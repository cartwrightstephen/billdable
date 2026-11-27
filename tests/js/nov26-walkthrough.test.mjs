// The Nov 26 walkthrough's interface items: the (i) explanations instead of meta text, everyone's
// notifications on the Team page, the starting templates for a new profile, the % inside its box,
// and the job page's Money card without a fold on a wider screen.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installFakeDom } from "./fakedom.mjs";

const dom = installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const settings = await import("../../static/js/views/settings.js");
const reg = await import("../../static/js/views/index.js");
const { titleWithInfo } = await import("../../static/js/dom.js");
const { field } = await import("../../static/js/forms.js");
const css = readFileSync(new URL("../../static/styles.css", import.meta.url), "utf8");

const R = { kinds: [{ key: "dates", label: "Important dates" }, { key: "missing", label: "Missing information" }],
  channels: [["app", "In the app"], ["email", "Email"], ["text", "Text"]] };
const owner = { id: "u1", name: "Pat", company_id: "co-p", role: "owner", profile_id: "owner", perms: ["team", "company"] };
const team = (extra = {}) => ({
  users: [
    { id: "u1", name: "Pat", email: "pat@x.example", profile_id: "owner", profile_name: "Owner", created_at: "2026-11-01",
      notifications: { prefs: { dates: ["app"], missing: ["app"] }, set_by: "", changed: [], texts_on: false } },
    { id: "u2", name: "Sam", email: "sam@x.example", profile_id: "member", profile_name: "Member", created_at: "2026-11-02",
      notifications: { prefs: { dates: ["app"], missing: ["app", "email"] }, set_by: "Pat", changed: [{ kind: "dates", channel: "email", on: false, words: "Email off for Important dates" }], texts_on: false } },
    { id: "u3", name: "Lee", email: "lee@x.example", profile_id: "member", profile_name: "Member", created_at: "2026-11-03",
      notifications: { prefs: { dates: ["app", "text"], missing: ["app"] }, set_by: "Pat", changed: [], texts_on: false } },
  ],
  invites: [], profiles: [{ id: "owner", name: "Owner", locked: true, perms: [] }, { id: "member", name: "Member", built_in: true, perms: [] }],
  permissions: [{ key: "send_payapps", words: "Send pay apps" }, { key: "manage_jobs", words: "Change a job's contract" }],
  templates: [{ id: "super", name: "Superintendent", profile_name: "Superintendent", about: "On site.", perms: ["manage_jobs"] },
    { id: "field", name: "Field", profile_name: "Field", about: "Records only.", perms: [] }],
  reminders: R, ...extra });

test("a section's explanation sits behind a small (i) beside its title", () => {
  const html = titleWithInfo("Notifications", "About notifications", "<p>For you and your team.</p>");
  assert.match(html, /<div class="title-info"><h2>Notifications<\/h2><details class="info-pop"><summary aria-label="About notifications"/);
  assert.match(html, /<div class="info-pop-body" role="note"><p>For you and your team.<\/p><\/div>/);
  assert.match(titleWithInfo("Invite someone", "About invites", "", { level: 3 }), /<h3>Invite someone<\/h3>/);
});

test("no settings page carries the prompt's meta text in its body", () => {
  const src = readFileSync(new URL("../../static/js/views/automations.js", import.meta.url), "utf8");
  // Only inside the (i): the line that drew it on the card itself is gone.
  assert.doesNotMatch(src, /<p class="sub">For you and your team/);
  assert.match(src, /titleWithInfo\("Notifications", "About notifications"/);
  const s = readFileSync(new URL("../../static/js/views/settings.js", import.meta.url), "utf8");
  assert.doesNotMatch(s, /<p class="sub">They get an email with a link to join/);
  assert.doesNotMatch(s, /<p class="sub">Who can send pay apps/);
  assert.doesNotMatch(s, /<p class="sub">Charged on the/);
});

test("the Team page shows each person's notifications, who set them and what they changed", () => {
  const html = settings.notificationSummaryHtml(team().users[1].notifications, R);
  assert.match(html, /<div>In the app, Email<\/div>/);
  assert.match(html, /<span class="pill amber">Changed by them<\/span> Email off for Important dates/);
  assert.match(settings.notificationSummaryHtml(team().users[2].notifications, R), /In the app, Text <span class="sub">\(texts wait until they agree on My account\)<\/span><\/div><div class="sub">As Pat set them<\/div>/);
  assert.match(settings.notificationSummaryHtml(team().users[0].notifications, R), /Their own choice/);
});

test("an owner sets one person's notifications, or everyone's, from the Team page", async () => {
  page.setMe(owner);
  core.setState({ company: { name: "Pine" }, jobs: [], payers: [] });
  page.setTeamData(team());
  page.setLocation("team");
  let html = reg.views.team();
  assert.match(html, /<th>Notifications<\/th>/);
  assert.match(html, /data-action="team-notes-edit" data-user="u2" aria-label="Set notifications for Sam"/);
  assert.doesNotMatch(html, /data-user="u1" aria-label="Set notifications/, "your own are on My account");
  assert.match(html, /data-action="team-notes-edit" data-user="\*">Set notifications for everyone/);
  reg.actions["team-notes-edit"]({ dataset: { user: "u2" } });
  html = reg.views.team();
  assert.match(html, /<h3>Notifications for Sam<\/h3>/);
  assert.match(html, /data-team-reminder="missing" value="email" checked/);
  assert.match(html, /Texts wait until Sam adds a mobile number and agrees to texts on My account\./);
  const sent = [];
  dom.answers = [(p, opts) => (p === "/api/team/reminders" ? (sent.push(JSON.parse(opts.body)), { status: 200, body: team() }) : undefined)];
  dom.all['input[data-team-reminder="dates"]:checked'] = [{ value: "app" }, { value: "email" }];
  dom.all['input[data-team-reminder="missing"]:checked'] = [{ value: "app" }];
  await reg.actions["team-notes-save"]();
  assert.deepEqual(sent, [{ user_ids: ["u2"], prefs: { dates: ["app", "email"], missing: ["app"] } }]);
  assert.doesNotMatch(reg.views.team(), /Notifications for Sam/, "closed once saved");
  reg.actions["team-notes-edit"]({ dataset: { user: "*" } });
  assert.match(reg.views.team(), /<h3>Notifications for everyone you can change<\/h3>/);
  reg.actions["team-notes-cancel"]();
  dom.answers = [];
  for (const k of Object.keys(dom.all)) delete dom.all[k];
});

test("a new profile can start from a template, then every box stays editable", () => {
  page.setMe(owner);
  page.setTeamData(team());
  reg.actions["profile-new"]();
  let html = reg.views.team();
  assert.match(html, /Start from/);
  assert.match(html, /data-action="profile-template" data-template="super" aria-pressed="false">Superintendent<\/button>/);
  reg.actions["profile-template"]({ dataset: { template: "super" } });
  html = reg.views.team();
  assert.match(html, /data-template="super" aria-pressed="true"/);
  assert.match(html, /On site\. Change any box before you save\./);
  assert.match(html, /id="profile-name"[^>]*value="Superintendent"/);
  assert.match(html, /data-perm="manage_jobs" checked/);
  assert.doesNotMatch(html, /data-perm="send_payapps" checked/);
  reg.actions["profile-cancel"]();
});

test("the % sits inside its box, and the main buttons are 44 px tall at every width", () => {
  const html = field({ form: "f", name: "retainage_pct", id: "r", label: "Retainage", suffix: "%" });
  assert.match(html, /<span class="with-suffix"><input [^>]*><span class="suffix" aria-hidden="true">%<\/span><\/span>/);
  assert.match(css, /\.with-suffix \.suffix \{ position: absolute; right: 10px;/);
  assert.match(css, /\.btn:not\(\.small\) \{ min-height: 44px; \}/);
  assert.match(css, /@media \(pointer: coarse\), \(max-width: 1024px\) \{\n  \.btn, \.btn\.small,/);
  assert.match(css, /\.detail-grid \{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; \}/);
});
