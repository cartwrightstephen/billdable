// Automations: each person's reminders, by kind, in the app, by email and by text, off until they
// turn one on, and their notifications with the unread count the menu shows. The server works out
// what is due (reminders.py).
import { esc, fmtDate } from "../format.js";
import { readJson, postJson } from "../net.js";
import { formErrorLine, refuseForm, clearForm } from "../forms.js";
import { me, currentView, onSessionEnd } from "../state.js";
import { DEMO_COMPANY_ID, render, toast } from "../app-core.js";

const STALE_MS = 15 * 60 * 1000;
let data = null;      // GET /api/reminders, raw: esc() at the point it is drawn
let loadedAt = 0;
let asking = false;

onSessionEnd(() => { data = null; loadedAt = 0; });

export function reminderCount() { return data ? data.unread : 0; }

export async function loadReminders() {
  if (asking || !me || Date.now() - loadedAt < STALE_MS) return;
  asking = true;
  const asked = me;
  loadedAt = Date.now();
  try {
    const res = await fetch("/api/reminders");
    const reply = res.ok ? await readJson(res) : null;
    if (reply && me === asked) {
      // Only when it shows: a redraw on another page for nothing new is wasted.
      const changed = (data ? data.unread : 0) !== reply.unread;
      data = reply;
      if (changed || currentView === "automations") render();
    }
  } catch (_) { /* the page keeps its loading line; the banner covers outages */ }
  finally { asking = false; }
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

function notificationsHtml(d) {
  if (!Object.values(d.prefs).some(c => c.includes("app")) && !d.notifications.length) return "";
  const row = (n) => `<div class="money-row${n.read || n.done ? " sub" : ""}" data-note="${esc(n.id)}"><span>${n.read || n.done ? "" : `<span class="pill copper">New</span> `}${n.done ? `<span class="pill green">Done</span> ` : ""}<strong>${esc(n.job)}</strong>: ${esc(n.text)} <span class="sub">${esc(fmtDate(n.at))}</span></span>
      <span class="row-actions"><a href="${esc(n.path)}">Open</a>${n.read || n.done ? "" : ` <button class="linkish" data-action="reminder-read" data-note="${esc(n.id)}">Mark read</button>`}</span></div>`;
  return `<div class="card" id="reminder-items"><div class="top"><h2>Notifications${d.unread ? ` <span class="count-badge">${d.unread}</span>` : ""}</h2>${d.unread ? `<button class="btn small secondary" data-action="reminder-read-all">Mark all read</button>` : ""}</div>
    ${d.notifications.length ? d.notifications.map(row).join("") : `<p class="sub">Nothing right now.</p>`}
  </div>`;
}

function channelBoxes(kind, d) {
  // A redraw while the page is open keeps what was ticked and not yet saved.
  const ticked = (c) => {
    const box = globalThis.document?.querySelector(`input[data-reminder="${kind.key}"][value="${c}"]`);
    return box ? box.checked : d.prefs[kind.key].includes(c);
  };
  return `<fieldset class="reminder-kind" id="reminder-${esc(kind.key)}" data-field="${esc(kind.key)}"><legend><strong>${esc(kind.label)}</strong></legend>
    <p class="sub">${esc(kind.help)}</p>
    ${d.channels.map(([c, label]) => `<label class="terms-check"><input type="checkbox" data-reminder="${esc(kind.key)}" value="${esc(c)}"${ticked(c) ? " checked" : ""}> ${esc(label)}</label>`).join("")}
    <span class="field-error" id="reminder-${esc(kind.key)}-err"></span></fieldset>`;
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  automations() {
    if (!data) return `<h1>Automations</h1><div class="card"><p class="sub">Loading…</p></div>`;
    const demo = me && me.company_id === DEMO_COMPANY_ID;
    return `<h1>Automations</h1>
      ${notificationsHtml(data)}
      <div class="card" data-form-key="reminders">${formErrorLine("reminders")}
        <h2>Reminders</h2>
        <p class="sub">Only for you and your team, never your clients. Each reminder comes round every ${esc(String(data.repeat_days))} days until it is dealt with, and email and text go out at most once a day.</p>
        ${data.kinds.map(k => channelBoxes(k, data)).join("")}
        ${data.can_text ? "" : `<p class="sub">To get texts, add your mobile number and agree to texts on <a href="/settings/account" data-action="nav" data-view="account">My account</a>.</p>`}
        ${notes(data)}${lastLine(data.last)}
        ${demo ? `<p class="sub">The demo account can't turn reminders on.</p>` : `<div class="form-inline"><button class="btn" data-action="save-reminders">Save</button></div>`}
      </div>`;
  },
};

async function markRead(id) {
  const r = await postJson("/api/reminders/read", id ? { id } : {});
  if (r.ok) { data = r.data; render(); } else toast(r.data.error || "Not saved.");
}

export const actions = {
  "save-reminders": async () => {
    const prefs = Object.fromEntries(data.kinds.map(k => [k.key, [...document.querySelectorAll(`input[data-reminder="${k.key}"]:checked`)].map(b => b.value)]));
    const r = await postJson("/api/reminders/prefs", { prefs });
    if (!r.ok) { refuseForm("reminders", r.data.fields || { _form: r.data.error || "Not saved." }); return; }
    data = r.data;
    clearForm("reminders");
    toast("Saved");
    render();
  },
  "reminder-read": (el) => markRead(el.dataset.note),
  "reminder-read-all": () => markRead(""),
};
