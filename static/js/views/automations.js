// Notifications (round 1, item 19: once the Automations page): each person's reminders, by kind,
// in the app, by email and by text, chosen on My account and saved with that page's Save. Someone
// new starts with each kind in the app (auth.NEW_USER_REMINDERS). The ones that fire in the app
// are rows on the Dashboard's To Do (reminderItems). The server works out what is due (reminders.py).
import { esc } from "../format.js";
import { routeFromPath } from "../routes.js";
import { readJson, postJson } from "../net.js";
import { formErrorLine, refuseForm, clearForm } from "../forms.js";
import { me, currentView, onSessionEnd } from "../state.js";
import { DEMO_COMPANY_ID, render, toast, nav, finishHtml } from "../app-core.js";

const STALE_MS = 15 * 60 * 1000;
let data = null;      // GET /api/reminders, raw: esc() at the point it is drawn
let loadedAt = 0;
let asking = false;

onSessionEnd(() => { data = null; loadedAt = 0; });

// force: ask again now, whatever the last answer's age.
export async function loadReminders(force = false) {
  if (asking || !me || (!force && Date.now() - loadedAt < STALE_MS)) return;
  asking = true;
  const asked = me;
  loadedAt = Date.now();
  try {
    const res = await fetch("/api/reminders");
    const reply = res.ok ? await readJson(res) : null;
    if (reply && me === asked) {
      data = reply;
      // Only where it shows: a redraw on another page for nothing new is wasted. On My account
      // only the Notifications card is redrawn: a redraw of the whole page would replace a box
      // the person is in the middle of (the Theme list they have open, say) and drop the pick.
      const card = currentView === "account" && globalThis.document && document.querySelector("#notifications");
      if (card) {
        card.outerHTML = notificationsCardHtml();
        const drawn = document.querySelector("#notifications");
        if (drawn) finishHtml(drawn);
      } else if (currentView === "dashboard" || currentView === "account") render();
    }
  } catch (_) { /* the card keeps its loading line; the banner covers outages */ }
  finally { asking = false; }
}

// The To Do rows for the reminders fired in the app, unread and not yet dealt with. A dates or
// held-up pay app reminder about a job that already has its own To Do row would say it twice, so
// it is left off; the client and change order ones always show.
export function reminderItems(rows = []) {
  if (!data) return [];
  const rowJobs = new Set(rows.map(r => r.job_id));
  return data.notifications
    .filter(n => !n.read && !n.done)
    .filter(n => !(rowJobs.has(n.job_id) && (n.kind === "dates" || String(n.key).startsWith("missing:blocked:"))))
    .map((n, i) => ({ source: "reminder", key: esc(n.id), title: esc(n.text), text: esc(n.text), path: esc(n.path), job_id: esc(n.job_id || ""), job: esc(n.job),
      ball: "us", assignee_id: me ? me.id : "", due: "", rank: 6, order: 2000 + i }));
}

function lastLine(last) {
  if (!last || !last.results || !Object.keys(last.results).length) return "";
  const words = Object.entries(last.results).map(([how, result]) => `${how === "text" ? "Text" : "Email"}: ${result}`).join(". ");
  return `<p class="sub" id="reminder-last">Last sent ${esc(last.day)}. ${esc(words)}.</p>`;
}

function notes(d) {
  const out = [];
  if (!d.email_ready) out.push("Email isn't set up on this server yet, so email reminders wait.");
  if (!d.texts_ready) out.push("Texting isn't set up on this server yet, so text reminders wait.");
  if (d.texts_stopped) out.push("You replied STOP, so texts are off.");
  return out.map(n => `<p class="sub">${esc(n)}</p>`).join("");
}

function channelBoxes(kind, d) {
  // A redraw while the page is open keeps what was ticked and not yet saved.
  const ticked = (c) => {
    const box = globalThis.document?.querySelector(`input[data-reminder="${kind.key}"][value="${c}"]`);
    return box ? box.checked : (d.prefs[kind.key] || []).includes(c);
  };
  return `<fieldset class="reminder-kind" id="reminder-${esc(kind.key)}" data-field="${esc(kind.key)}"><legend><strong>${esc(kind.label)}</strong></legend>
    <p class="sub">${esc(kind.help)}</p>
    ${d.channels.map(([c, label]) => `<label class="terms-check"><input type="checkbox" data-reminder="${esc(kind.key)}" value="${esc(c)}"${ticked(c) ? " checked" : ""}> ${esc(label)}</label>`).join("")}
    <span class="field-error" id="reminder-${esc(kind.key)}-err"></span></fieldset>`;
}

// My account's Notifications card.
export function notificationsCardHtml() {
  if (!data) return `<div class="card" id="notifications"><h2>Notifications</h2><p class="sub">Loading…</p></div>`;
  const demo = me && me.company_id === DEMO_COMPANY_ID;
  return `<div class="card" id="notifications" data-form-key="reminders">${formErrorLine("reminders")}
      <h2>Notifications</h2>
      <p class="sub">For you and your team, never your clients. In the app they are rows on the Dashboard's To do. Each comes round every ${esc(String(data.repeat_days))} days until it is dealt with; email and text go out at most once a day.</p>
      ${data.kinds.map(k => channelBoxes(k, data)).join("")}
      ${data.can_text ? "" : `<p class="sub">To get texts, add your mobile number above and agree to texts.</p>`}
      ${notes(data)}${lastLine(data.last)}
      ${demo ? `<p class="sub">The demo account can't turn reminders on.</p>` : ""}
    </div>`;
}

// Saves the card's choices when they changed (from My account's Save). False when refused: the
// reasons are under the kinds.
export async function saveReminderPrefs() {
  if (!data || (me && me.company_id === DEMO_COMPANY_ID)) return true;
  const prefs = Object.fromEntries(data.kinds.map(k => [k.key, [...document.querySelectorAll(`input[data-reminder="${k.key}"]:checked`)].map(b => b.value)]));
  const same = data.kinds.every(k => (prefs[k.key] || []).join() === (data.prefs[k.key] || []).join());
  if (same) return true;
  const r = await postJson("/api/reminders/prefs", { prefs });
  if (!r.ok) { refuseForm("reminders", r.data.fields || { _form: r.data.error || "Not saved." }); return false; }
  data = r.data;
  clearForm("reminders");
  return true;
}

async function markRead(id) {
  const r = await postJson("/api/reminders/read", id ? { id } : {});
  if (r.ok) { data = r.data; render(); } else toast(r.data.error || "Not saved.");
}

// No page of its own any more: /automations opens My account at Notifications (routes.js).
export const views = {};

export const actions = {
  "reminder-read": (el) => markRead(el.dataset.note),
  // Opening a reminder reads it, then goes where it points.
  "reminder-open": async (el) => {
    const to = routeFromPath(el.dataset.path || "") || { view: "dashboard" };
    await markRead(el.dataset.note);
    nav(to.view, to.jobId || null, to.detailId || null);
  },
};
