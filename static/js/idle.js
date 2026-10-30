// Idle sign-out (G2). The server ends a session after DURACASH_IDLE_MINUTES without use (60 by
// default); the page counts the same time from the last click or key, warns two minutes before
// with a Stay signed in button, and then signs out with the reason. While someone works without
// saving (typing a long note), a light ping at most once a minute keeps the server's count level
// with the page's.

const WARN_MS = 2 * 60 * 1000;
const PING_MS = 60 * 1000;
const CHECK_MS = 5 * 1000;

// The stage at `now`: "active", "warn" (the warning shows) or "out". The warning comes two
// minutes before the end, or halfway through a shorter time.
export function idleStage(now, lastActive, minutes) {
  const total = minutes * 60 * 1000;
  const lead = Math.min(WARN_MS, total / 2);
  const idle = now - lastActive;
  return idle >= total ? "out" : idle >= total - lead ? "warn" : "active";
}

// What the warning and the sign-in page say.
export function idleWords(minutes) {
  return `Signed out after ${minutes === 60 ? "an hour" : `${minutes} minute${minutes === 1 ? "" : "s"}`} without use.`;
}

let minutes = 0;          // 0: not watching (signed out)
let lastActive = 0;
let lastPing = 0;
let timer = null;
let signOut = () => {};   // set by views/auth.js: sign out and show the reason
let warnEl = null;

export function setIdleSignOut(fn) { signOut = fn; }

function ping() {
  lastPing = Date.now();
  fetch("/api/auth/ping", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => {});
}

function activity() {
  if (!minutes) return;
  if (idleStage(Date.now(), lastActive, minutes) === "out") { check(); return; }
  lastActive = Date.now();
  hideWarning();
  if (lastActive - lastPing >= PING_MS) ping();
}

function showWarning() {
  if (warnEl || typeof document.createElement !== "function" || !document.body) return;
  warnEl = document.createElement("div");
  warnEl.className = "idle-warning";
  warnEl.id = "idle-warning";
  warnEl.setAttribute("role", "alertdialog");
  warnEl.setAttribute("aria-live", "assertive");
  const text = document.createElement("p");
  text.textContent = "Signing you out in 2 minutes.";
  const stay = document.createElement("button");
  stay.type = "button";
  stay.className = "btn";
  stay.id = "idle-stay";
  stay.textContent = "Stay signed in";
  stay.addEventListener("click", () => { lastActive = Date.now(); ping(); hideWarning(); });
  warnEl.append(text, stay);
  document.body.appendChild(warnEl);
  stay.focus?.();
}

function hideWarning() {
  if (!warnEl) return;
  warnEl.remove?.();
  warnEl = null;
}

function check() {
  if (!minutes) return;
  const stage = idleStage(Date.now(), lastActive, minutes);
  if (stage === "out") {
    const words = idleWords(minutes);
    stopIdleWatch();
    signOut(words);
  } else if (stage === "warn") showWarning();
  else hideWarning();
}

let listening = false;
const EVENTS = ["pointerdown", "keydown", "wheel", "touchstart"];

// Start (or restart) counting, from now, for a session that ends after `mins` minutes idle.
export function startIdleWatch(mins) {
  const m = Number(mins);
  if (!(m > 0)) return;
  minutes = m;
  lastActive = lastPing = Date.now();
  if (!listening && typeof document.addEventListener === "function") {
    listening = true;
    EVENTS.forEach(e => document.addEventListener(e, activity, { capture: true, passive: true }));
    // Back to a tab left open: the time away counts; a hidden tab's timers may have slept.
    document.addEventListener("visibilitychange", check);
  }
  clearInterval(timer);
  timer = setInterval(check, CHECK_MS);
  if (timer && typeof timer.unref === "function") timer.unref();   // under node (tests) it doesn't hold the process
}

export function stopIdleWatch() {
  minutes = 0;
  clearInterval(timer);
  timer = null;
  hideWarning();
}

export function idleWatching() { return minutes > 0; }

// For the tests: run the check now, as the timer would.
export { check as idleCheck };
