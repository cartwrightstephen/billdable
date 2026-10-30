// Signing in and out: the landing page with sign-in and sign-up, the sample company, reset,
// verify and invite links, and the banners about the account above every page.
import { esc, escDeep } from "../format.js";
import { readJson, setNetBanner, postJson } from "../net.js";
import { field, formErrorLine, refuseForm, showFieldErrors, formValues, formErrors, clearForm } from "../forms.js";
import { me, setMe, clearSession, pendingPath, setCheckEmail, setAccount, mailReady, readOnly, billingInfo, heldShare, setDrawerOpen, checkEmail, signedOutNote, setSignedOutNote, setPending, setTwoStepPrompt } from "../state.js";
import { setIdleSignOut } from "../idle.js";
import { render, load, post, applyLocation, DEMO_COMPANY_ID, boot, toast, nav } from "../app-core.js";
import { setAiVisible, helperLog } from "./helper.js";

// Someone who has signed in on this device before lands on Sign in, not on Create account (R2-02).
const SIGNED_IN_KEY = "duracash-signed-in";
function signedInBefore() {
  try { return localStorage.getItem(SIGNED_IN_KEY) === "1"; } catch (_) { return false; }
}
export function rememberSignIn() {
  try { localStorage.setItem(SIGNED_IN_KEY, "1"); } catch (_) { /* private window: start on Create account */ }
}
let authMode = signedInBefore() ? "login" : "signup";
export function setAuthMode(mode) { authMode = mode; }
let demoAvailable = false;  // /api/auth/demo-available: the landing offers the sample company
let dataNotice = false;   // server says data may be erased (no persistent disk confirmed yet)
export function setDataNotice(on) { dataNotice = on; }
let authError = "";      // the two-step code screen's error; the sign-in form uses formErrors.auth
let loginCode = null;     // {email, password} while the sign-in asks for a two-step code
let loginNote = "";       // why the code is asked for, when it isn't the usual sign-in (a password reset)

export function renderOutage(message) {
  setAiVisible(false);
  document.getElementById("app").innerHTML = `<div class="landing"><header class="land-bar"><div class="brand">Duracash</div></header>
    <section class="land-hero"><div class="auth-card land-form"><h2>Not available right now</h2>
      <p class="sub" role="alert">${esc(message)}</p>
      <button class="btn" id="outage-retry">Try again</button></div></section></div>`;
  document.getElementById("outage-retry").addEventListener("click", () => location.reload());
}

export async function checkDemo() {
  try {
    const res = await fetch("/api/auth/demo-available");
    const on = res.ok && (await readJson(res)).available === true;
    if (on !== demoAvailable) { demoAvailable = on; if (!me && document.querySelector(".landing")) renderLanding(); }
  } catch (_) { /* offline: the landing works without it */ }
}

export function renderLanding() {
  document.title = "Duracash";
  setAiVisible(false);
  const app = document.getElementById("app");
  const signup = authMode === "signup";
  app.innerHTML = `
    <div class="landing">
      ${dataNotice ? `<p class="notice land-notice" role="status">Demo: data may be erased. Don't enter real job or client data yet.</p>` : ""}
      <header class="land-bar">
        <div class="brand">Duracash</div>
        <div class="land-bar-actions">
          <button class="btn secondary small" id="bar-signin">Sign in</button>
          <button class="btn small" id="bar-start">Create account</button>
        </div>
      </header>
      <section class="land-hero">
        <div class="land-copy">
          <p class="eyebrow">For small GCs and remodelers</p>
          <h1>You finish the work.<br>Duracash gets it billed and collected.</h1>
          <p class="lede">Works beside QuickBooks. Catches the missing paperwork, the unsigned change order, and the money held back that nobody chased. You approve every send.</p>
          <div class="land-points">
            <div><strong>Same-week invoice</strong><span>From “job’s done” to a draft you can send.</span></div>
            <div><strong>Paperwork check</strong><span>Nothing leaves with a missing waiver or photo.</span></div>
            <div><strong>Money</strong><span>Contract, change orders, billed, received, held back — one place.</span></div>
          </div>
          ${demoAvailable ? `<div class="land-demo">
            <button class="btn secondary" id="land-demo">Look around a sample company</button>
            <span class="sub">No account needed. Jobs, pay apps and payments are already in.</span>
          </div>` : ""}
        </div>
        <div class="auth-card land-form" id="signup">
          <h2>${signup ? "Create your company" : "Sign in"}</h2>
          <p class="sub">${signup ? "Free until your first pay app sent through Duracash is paid, or 90 days. Passwords need 10+ characters." : pendingPath ? "Sign in to open that page." : "Back to your jobs, paperwork and clients."}</p>
          ${!signup && signedOutNote ? `<p class="notice signed-out-note" role="status">${esc(signedOutNote)}</p>` : ""}
          ${!signup && loginCode ? `<form id="auth-form">
            <label for="login-code">${loginCode.method === "email" ? "Code from the email" : "Code from your authenticator app"}</label>
            <input id="login-code" name="code" required autocomplete="one-time-code" inputmode="numeric" maxlength="12" placeholder="123456" />
            ${loginNote ? `<p class="sub" role="status">${esc(loginNote)}</p>` : ""}
            ${loginCode.method === "email" ? `<label class="terms-check"><input type="checkbox" id="login-remember" name="remember" checked> Don't ask again on this device for 30 days</label>
            <p class="sub">No email? Check the spam folder, or go back and sign in again for a new code.</p>`
            : `<p class="sub">Lost your phone? Type one of your recovery codes instead.</p>`}
            <button class="btn" type="submit">Sign in</button>
            ${authError ? `<div class="auth-error" role="alert">${esc(authError)}</div>` : ""}
            <div class="auth-toggle"><button type="button" id="code-back">Back</button></div>
          </form>` : `<form id="auth-form" data-form-key="auth" novalidate>
            ${signup ? field({ form: "auth", name: "name", id: "auth-name", label: "Your name", autocomplete: "name" }) : ""}
            ${signup ? field({ form: "auth", name: "company_name", id: "auth-company", label: "Company name", autocomplete: "organization", placeholder: "Company name (you can add it later)" }) : ""}
            ${field({ form: "auth", name: "email", id: "auth-email", label: "Email", type: "email", autocomplete: "username" })}
            ${field({ form: "auth", name: "password", id: "auth-password", label: "Password", type: "password", autocomplete: signup ? "new-password" : "current-password" })}
            ${signup ? `<label class="terms-check"><input type="checkbox" id="auth-terms" name="accept_terms" data-field="accept_terms" aria-describedby="auth-terms-err"> I agree to the <a href="/terms" target="_blank" rel="noopener">Terms of Service</a> and the <a href="/privacy" target="_blank" rel="noopener">Privacy Policy</a>.</label>
            <span class="field-error" id="auth-terms-err"></span>` : ""}
            <button class="btn" type="submit">${signup ? "Create account" : "Sign in"}</button>
            ${formErrorLine("auth")}
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
          <div class="card"><span class="step">1</span><h3>Mark the job done</h3><p>Notes and photos land on the job record. Money already knows the contract.</p></div>
          <div class="card"><span class="step">2</span><h3>Add the paperwork</h3><p>Duracash names what is missing — a waiver, a signed change order, photos — before anything is sent.</p></div>
          <div class="card"><span class="step">3</span><h3>Approve and collect</h3><p>You approve the send. Payments are recorded. Money held back and closeout stay on the calendar.</p></div>
        </div>
      </section>
      <section class="land-price">
        <div class="card">
          <p class="eyebrow">Pricing</p>
          <h2>$299<span>/mo</span></h2>
          <p class="sub">Free until the first pay app you send through Duracash is paid. Then $299/mo. No percent of collections.</p>
          <button class="btn" id="price-start">Create your company</button>
        </div>
      </section>
      <footer class="land-foot">Reads payments from QuickBooks (connect it, or import a CSV). Never changes your books.
        <div class="land-legal"><a href="/terms">Terms of Service</a> · <a href="/privacy">Privacy Policy</a></div></footer>
    </div>
  `;
  document.getElementById("auth-form").addEventListener("submit", onAuthSubmit);
  document.getElementById("code-back")?.addEventListener("click", () => { loginCode = null; loginNote = ""; authError = ""; renderLanding(); });
  document.getElementById("login-code")?.focus();
  document.getElementById("auth-switch").addEventListener("click", () => {
    authMode = signup ? "login" : "signup";
    authError = "";
    delete formErrors.auth;
    renderLanding();
  });
  const toSignup = () => { authMode = "signup"; authError = ""; delete formErrors.auth; renderLanding(); };
  const toSignin = () => { authMode = "login"; authError = ""; delete formErrors.auth; renderLanding(); };
  // On a phone the form sits above the pitch; the bar's buttons bring it into view.
  const showCard = () => document.getElementById("signup")?.scrollIntoView({ block: "start", behavior: "smooth" });
  document.getElementById("bar-start").addEventListener("click", () => { toSignup(); showCard(); });
  document.getElementById("price-start").addEventListener("click", () => { toSignup(); showCard(); });
  document.getElementById("bar-signin").addEventListener("click", () => { toSignin(); showCard(); });
  // The password rule is checked when the box is left, in place: no redraw, nothing lost.
  document.getElementById("auth-password")?.addEventListener("blur", (e) => {
    if (!signup || !e.target.value || e.target.value.length >= 10) return;
    showFieldErrors("auth", { ...(formErrors.auth || {}), password: "Use at least 10 characters for the password." });
  });
  document.getElementById("auth-forgot")?.addEventListener("click", () => renderForgot());
  document.getElementById("land-demo")?.addEventListener("click", lookAround);
}

async function onAuthSubmit(e) {
  e.preventDefault();
  const form = e.target;
  loginNote = "";
  const v = loginCode && authMode === "login" ? {} : formValues("auth");
  const body = loginCode && authMode === "login"
    ? { email: loginCode.email, password: loginCode.password, code: form.code.value, ...(loginCode.method === "email" ? { remember: !!form.remember?.checked } : {}) }
    : { email: v.email, password: form.password.value };
  if (authMode === "signup") {
    // Checked here first, in place: nothing typed is lost.
    const errors = {};
    if (!v.name) errors.name = "Enter your name.";
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v.email)) errors.email = "Enter your email, like you@yourcompany.com.";
    if (form.password.value.length < 10) errors.password = "Use at least 10 characters for the password.";
    if (!form.accept_terms.checked) errors.accept_terms = "To create an account, accept the Terms of Service and the Privacy Policy.";
    if (refuseForm("auth", errors)) return;
    body.name = v.name;
    body.company_name = v.company_name;
    body.accept_terms = true;
  } else if (!loginCode && (!v.email || !body.password)) {
    refuseForm("auth", { ...(v.email ? {} : { email: "Enter your email." }), ...(body.password ? {} : { password: "Enter your password." }) });
    return;
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
      // Two-step sign-in (S7): a code from the authenticator app, or one emailed to this
      // address from a browser that hasn't signed in with a code in the last 30 days.
      authError = loginCode ? (data.error || "That code didn't work.") : "";
      if (!loginCode && data.method === "email") loginNote = data.error || "";
      loginCode = { email: body.email, password: body.password, method: data.method === "email" ? "email" : "app" };
      renderLanding();
    } else if (loginCode) {
      authError = data.error || "Could not sign in.";
      renderLanding();
    } else if (data.exists && authMode === "signup") {
      // That email has an account already: offer to sign in with it, keeping the email typed.
      refuseForm("auth", { email: data.error });
      const err = document.getElementById("auth-email-err");
      if (err && !document.getElementById("signin-instead")) {
        err.insertAdjacentHTML("afterend", `<button type="button" class="linkish" id="signin-instead">Sign in instead</button>`);
        document.getElementById("signin-instead").addEventListener("click", () => { authMode = "login"; delete formErrors.auth; renderLanding(); document.getElementById("auth-password")?.focus(); });
      }
    } else {
      refuseForm("auth", data.fields || { _form: data.error || "Could not sign in." });
    }
    return;
  }
  rememberSignIn();
  // After sign-up (R1-07): say where the confirmation link went, or why none did.
  setCheckEmail(authMode === "signup" && data.user
    ? { email: esc(String(data.user.email || "")), verification: esc(String(data.verification || "")) } : null);
  await startSession(data.user);
}

// Look around before signing up (R2-06): the sample company, signed in without a password.
// It doesn't count as having an account on this device.
async function lookAround() {
  const r = await postJson("/api/auth/demo", {});
  if (!r.ok) { demoAvailable = false; renderLanding(); return; }
  setCheckEmail(null);
  await startSession(r.data.user);
}

async function startSession(user) {
  clearSession();   // nothing kept from whoever was signed in before in this tab
  setSignedOutNote("");
  clearForm("auth");
  loginCode = null;
  setMe(escDeep(user));
  authError = "";
  helperLog.length = 0;
  try {
    const who = await fetch("/api/auth/me");
    if (who.ok) {
      const info = await readJson(who);
      setAccount({
        helperAdmin: info.helper_admin === true,
        mailReady: info.mail_ready === true,
        readOnly: typeof info.read_only === "string" ? esc(info.read_only) : "",
        billingInfo: escDeep(info.billing || null),
        heldShare: Number(info.held_share) || 0,
        idleMinutes: Number(info.idle_minutes) || 60,
        codesByEmail: info.codes_by_email === true,
      });
    }
  } catch (_) { setAccount({ helperAdmin: false }); }
  applyLocation();
  await load();
  setAiVisible(true);
  // Two-step sign-in (S7): on a server that can't email codes, the authenticator app is the
  // second step, and setting it up is the first thing asked after signing in.
  if (me && me.two_step_setup_due) { setTwoStepPrompt(true); nav("account"); }
}

// note: why, when the page signed out by itself (G2's idle sign-out); shown on Sign in.
async function logout(note = "") {
  const wasDemo = me && me.company_id === DEMO_COMPANY_ID;
  try { await fetch("/api/auth/logout", { method: "POST" }); } catch (_) { /* offline: the session ends on the server by itself */ }
  setSignedOutNote(note);
  clearSession();   // and with it Settings, the helper and every page's kept data (onSessionEnd)
  loginCode = null; setCheckEmail(null);
  setAccount({ helperAdmin: false });
  // They have an account on this device now; leaving the sample company goes back to where they were.
  authMode = wasDemo && !signedInBefore() && !note ? "signup" : "login";
  setAiVisible(false);
  setDrawerOpen(false);
  // Signed out for being idle: signing in again opens the page that was open.
  if (note && location.pathname !== "/") setPending(location.pathname, location.search);
  history.replaceState(null, "", "/");
  renderLanding();
}

setIdleSignOut((note) => logout(note));

// ---------- accounts: reset, verify and invite links ----------
// Links in emails carry a one-time token after "#", which browsers never send to a server.
export let tokenScreen = null;   // { kind: "reset" | "invite", token, info }

export async function handleTokenLink(t) {
  if (t.kind === "verify") {
    const r = await postJson("/api/auth/verify", { token: t.token });
    // Sending unlocks at once in this tab too (boot reads /api/auth/me again as well).
    if (r.ok && me) setMe({ ...me, email_verified: true });
    return r.ok ? "Your email is confirmed. You can send pay apps now." : (r.data.error || "That link didn't work.");
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
      <p class="sub">${s.info.invited_by ? s.info.invited_by + " invited you" : "You were invited"} as ${s.info.profile_name || s.info.role}. Your sign-in email is <strong>${s.info.email}</strong>.</p>
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
  app.innerHTML = `<div class="landing"><header class="land-bar"><div class="brand">Duracash</div></header>
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
    if (r.data.needs_code) {
      // Two-step accounts: the link changed the password, and the code still signs in (R6-05).
      authMode = "login"; authError = "";
      loginCode = { email: r.data.email, password: f.password.value };
      loginNote = r.data.message || "Password changed. Sign in with your new password and your authenticator code.";
      renderLanding();
      return;
    }
    await boot(false);
    if (me && me.two_step_setup_due) { setTwoStepPrompt(true); nav("account"); }
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

// Shown after sign-up until the email is confirmed or the panel is put away. When no link went
// out, the server's reason is shown instead (email not set up on this server, too many links).
function checkEmailHtml() {
  const sent = checkEmail.verification === "sent";
  return `<div class="card check-email" role="status">
    <h2>${sent ? "Check your email" : "Your email isn't confirmed yet"}</h2>
    <p>${sent ? `We sent a confirmation link to ${checkEmail.email}.` : `No confirmation link went to ${checkEmail.email}: ${checkEmail.verification || "it could not be sent"}.`} You can set up your first job meanwhile.</p>
    <div class="form-inline">${mailReady ? `<button class="btn small secondary" data-action="resend-verify">Send the link again</button>` : ""}
      <button class="btn small secondary" data-action="dismiss-check-email">Got it</button></div>
  </div>`;
}

// Shared from the phone before signing in (R6-10): held for half an hour, added here with one tap.
export function heldShareHtml() {
  if (!heldShare || readOnly) return "";
  return `<div class="card held-share" role="status">
    <p>You shared ${heldShare === 1 ? "something" : `${heldShare} things`} to Duracash before signing in.</p>
    <div class="form-inline"><button class="btn small" data-action="claim-share">Finish adding ${heldShare} shared item${heldShare === 1 ? "" : "s"}</button>
      <button class="btn small secondary" data-action="discard-share">Discard</button></div>
  </div>`;
}

export function verifyBannerHtml() {
  if (readOnly) return `<div class="verify-banner read-only-banner" role="status">${readOnly}</div>`;
  if (billingInfo && billingInfo.warning) return `<div class="verify-banner" role="status">${billingInfo.warning}${me && me.role === "owner" ? ` <button class="linkish" data-action="nav" data-view="billing">Billing</button>` : ""}</div>`;
  if (checkEmail && me && !me.email_verified) return checkEmailHtml();
  if (!mailReady || !me || me.email_verified || me.company_id === DEMO_COMPANY_ID) return "";
  return `<div class="verify-banner">Confirm your email (${me.email}) so Duracash can send pay apps for you; replies come back to it.
    <button class="linkish" data-action="resend-verify">Send the link again</button></div>`;
}

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in app.js.
export const actions = {
  "logout": () => logout(),
  "dismiss-check-email": () => { setCheckEmail(null); render(); },
  "claim-share": async () => {
    const r = await post("/api/share/claim", {}, { quiet: true });
    setAccount({ heldShare: 0 });
    if (r && r.ok) { toast("Added to the Inbox."); nav("inbox"); }
    else render();
  },
  "discard-share": async () => {
    await postJson("/api/share/claim", { discard: true });
    setAccount({ heldShare: 0 });
    render();
  },
  "resend-verify": async () => {
    const r = await postJson("/api/auth/resend-verify", {});
    toast(r.ok ? r.data.message : (r.data.error || "That didn't work."));
  },
};
