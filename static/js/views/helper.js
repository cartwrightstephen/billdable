// Ask Duracash: the helper chat panel beside every page.
import { esc, unesc, helperFormat } from "../format.js";
import { routeFromPath } from "../routes.js";
import { readJson } from "../net.js";
import { formDraft } from "../forms.js";
import { state, me, currentView, currentJobId, clearSession, onSessionEnd } from "../state.js";
import { render, renderLanding, nav, toast } from "../app-core.js";
import { openSupportingDocs } from "./paperwork.js";
import { startPayApp } from "./payapps.js";

export function setAiVisible(on) {
  const badge = document.getElementById("ai-badge");
  const panel = document.getElementById("ai-panel");
  if (badge) badge.style.display = on ? "flex" : "none";
  if (panel && !on) panel.classList.remove("open");
}

// ----- Helper chat -----
// Answers come from the prepared-answers database on the server, which reads
// this company's jobs for questions like "what should I do today". Its buttons
// only open a page or a form (Stephen, 10 Nov, decision 12): the helper never
// changes anything itself; the person saves the form, through the page's own
// gates. A reply with `go` opens its first button straight away ("take me to
// Unpaid").
export const helperLog = [];   // {who: "you"|"helper", text, actions, suggestions, logId, rated}
let helperBusy = false;
let aiOpen = false;

// Signed out: the panel is closed and emptied.
export function forgetHelper() {
  helperLog.length = 0;   // never show one person's questions to the next
  document.getElementById("ai-replies").innerHTML = "";
  document.getElementById("ai-chips").innerHTML = "";
  aiOpen = false;
  document.getElementById("ai-panel").classList.remove("open");
}
onSessionEnd(forgetHelper);

export function updateAiContext() {
  if (aiOpen) renderHelper();
}

function helperStarters() {
  const job = currentView === "job" && state && state.jobs.find(j => j.id === currentJobId);
  if (job) { const n = unesc(job.name); return [`What's blocking ${n}?`, `Draft a follow-up for ${n}`, `Status of ${n}`]; }
  if (currentView === "inbox") return ["How does it know which job?", "How do I import 30 days of notes?", "How do I forward emails to Duracash?"];
  if (currentView === "retainage") return ["How do I get retainage released?", "Which jobs are holding my retainage?"];
  return ["What should I do today?", "What's due this week?", "Who owes me money?"];
}

function renderHelper() {
  const box = document.getElementById("ai-replies");
  const chips = document.getElementById("ai-chips");
  if (!box || !chips) return;
  const greeting = `<div class="msg helper"><p>Hi${me && me.name ? " " + me.name.split(" ")[0] : ""}. Ask me about your jobs, or tell me where to go: “take me to Unpaid”, “new change order”.</p></div>`;
  {
    box.innerHTML = greeting + helperLog.map((m, idx) => m.who === "you"
      ? `<div class="msg you">${esc(m.text)}</div>`
      : `<div class="msg helper">${helperFormat(m.text)}
          ${(m.actions || []).length ? `<div class="msg-actions">${m.actions.map((a, i) => `<button class="btn small ${a.type === "form" ? "" : "secondary"}" data-helper-msg="${idx}" data-helper-action="${i}">${esc(a.label)}</button>`).join("")}</div>` : ""}
          ${m.logId ? `<div class="msg-rate">${m.rated === "asking" ? `<form class="ai-why" data-helper-why="${idx}"><label for="why-${idx}">What were you looking for? (optional)</label><input id="why-${idx}" maxlength="300"><button class="btn small" type="submit">Send</button></form>`
            : m.rated ? (m.rated === "yes" ? "Thanks." : "Thanks, the team will improve this answer.") : `Helpful? <button class="linkish" data-helper-rate="${idx}" data-value="yes">Yes</button> <button class="linkish" data-helper-rate="${idx}" data-value="no">No</button>`}</div>` : ""}
        </div>`).join("") + (helperBusy ? `<div class="msg helper typing" aria-label="Looking that up">Looking that up…</div>` : "");
  }
  const last = [...helperLog].reverse().find(m => m.who === "helper");
  const suggestions = (last && last.suggestions && last.suggestions.length) ? last.suggestions : helperStarters();
  chips.innerHTML = suggestions.map(s => `<button class="chip" data-helper-ask="${esc(s)}">${esc(s)}</button>`).join("");
  box.scrollTop = box.scrollHeight;
}

// jobId: a "Which job?" choice asks about that job by its id (the page's own job otherwise).
export function helperAskBody(q, jobId = "") {
  return { q, view: currentView, job_id: jobId || (currentView === "job" ? currentJobId : "") };
}

async function helperAsk(q, jobId = "", label = "") {
  q = (q || "").trim();
  if (!q || helperBusy) return;
  helperLog.push({ who: "you", text: jobId && label ? `${q} (${label})` : q });
  helperBusy = true;
  renderHelper();
  let reply;
  try {
    const res = await fetch("/api/helper/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(helperAskBody(q, jobId)),
    });
    reply = await readJson(res);
    if (res.status === 401) { helperBusy = false; clearSession(); renderLanding(); return; }
    if (!res.ok) reply = { text: reply.error || "Something went wrong. Try again." };
  } catch (_) {
    reply = { text: "I can't reach Duracash right now. Check your connection and try again." };
  }
  helperBusy = false;
  const actions = (reply.actions || []).filter(a => a && OPENS.has(a.type));
  helperLog.push({ who: "helper", text: reply.text, actions, suggestions: reply.suggestions || [], logId: reply.log_id });
  if (helperLog.length > 60) helperLog.splice(0, helperLog.length - 60);
  renderHelper();
  const go = Number.isInteger(reply.go) ? actions[reply.go] : null;
  if (go) await helperRunAction(go);
}

// The only buttons the helper runs: each opens a page or a form, none changes data (decision 12,
// mirrored by helper_intents.SAFE_ACTIONS on the server). Anything else is dropped.
export const OPENS = new Set(["nav", "job", "client", "form", "ask", "copy", "packet", "path"]);

// The page modules' actions, loaded on first use: index.js reaches this module through other views,
// so a static import would be a cycle.
const pageActions = async () => (await import("./index.js")).actions;

// A form the helper starts, opened the way its own page opens it, with what is obvious filled in.
// Nothing is saved: the person saves it, through the form's own checks.
export async function openHelperForm(a) {
  const el = { dataset: {} };
  const actions = await pageActions();
  if (a.form === "newjob") {
    actions["onb-newjob"](el);
    if (a.client || a.name) {
      const d = (formDraft.newjob ||= {});
      if (a.client) { d.client_mode = "existing"; d.customer = String(a.client); }
      if (a.name) d.name = String(a.name);
      render();
    }
    return;
  }
  if (!a.job_id) return;
  if (a.form === "newco") { nav("job", a.job_id); actions["new-co"](el); }
  else if (a.form === "match") actions["record-payment"](el, a.job_id);
  else if (a.form === "daily") actions["daily-for-job"](el, a.job_id);
  else if (a.form === "progress") startPayApp(a.job_id, "progress");
}

export async function helperRunAction(a) {
  if (!a || !OPENS.has(a.type)) return;
  if (a.type === "nav") nav(a.view);
  else if (a.type === "job") nav("job", a.job_id);
  else if (a.type === "client") (await pageActions())["open-client-card"]({ dataset: {} }, a.payer_id);
  else if (a.type === "form") await openHelperForm(a);
  else if (a.type === "packet") openSupportingDocs(a.job_id, a.inv || null);
  else if (a.type === "path") { const r = routeFromPath(a.path); if (r) nav(r.view, r.jobId || null, r.detailId || null); }
  else if (a.type === "ask") helperAsk(a.q, a.job_id || "", a.label || "");
  else if (a.type === "copy") {
    try { await navigator.clipboard.writeText(a.text); toast("Copied"); }
    catch (_) { toast("Copy blocked by the browser. Select the text and copy it."); }
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
