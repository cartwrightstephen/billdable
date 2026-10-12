let state = null;
let me = null;
let authMode = "signup";
let authError = "";
let jobFormError = "";
let currentView = "dashboard";
let currentJobId = null;
let currentDetailId = null; // for packet, co, payapp, waiver detail
let aiOpen = false;
let calYear = new Date().getFullYear();
let calMonth = new Date().getMonth();
let calSelectedDay = null;
let helpDismissed = localStorage.getItem("billdable-help-dismissed") === "1";
let dashFilter = "all";
let dashSort = "name";
let dashSortDir = 1;
let payappFilter = "all";
let outboxJobFilter = "all";
let auditJobFilter = "all";
let coPreselectJob = null;
let formOpen = {};
let toastTimer = null;
const DEMO_COMPANY_ID = "co-hartline";
let calJobFilter = "all";
let importSettings = null;
const RECORD_KINDS = {
  note: "Note", daily: "Daily record", photo: "Photo", contract: "Contract", change_order: "Change order",
  waiver: "Lien waiver", pay_app: "Pay app / invoice backup", ticket: "Delivery or time ticket", other: "Other",
};
const SOURCE_LABELS = { upload: "Upload", paste: "Pasted", email: "Email", sms: "Text message", notes: "Notes" };

// Plain-language help for every field, column, tile and section. The (i) icon
// next to a label is hidden until the area is hovered or focused; hovering,
// focusing or tapping the icon shows the text. Keys are the visible label,
// lower case, without arrows or asterisks.
const FIELD_HELP = {
  // money file and tiles
  "contract": "The original contract amount, before any change orders.",
  "approved cos": "Change orders the customer has signed. They add to the contract total.",
  "pending cos": "Change orders not signed yet. They are not billable until signed.",
  "contract total": "Contract plus signed change orders: the most you can bill on this job.",
  "billed": "Pay apps sent to the payer, after retainage is withheld.",
  "collected": "Payments matched to this job's pay apps.",
  "outstanding": "Billed but not yet paid.",
  "unbilled completed": "Work you finished that is not on any pay app yet. This is money you can bill now.",
  "unbilled": "Work you finished that is not on any pay app yet.",
  "retainage held": "The percentage the payer keeps back from each pay app until closeout.",
  "retainage released": "Retainage the payer has paid you.",
  "retainage open": "Retainage still held. It is released after the punch list and closeout packet are done.",
  "retainage": "Retainage still held on this job.",
  "billed this month": "Total of pay apps sent this calendar month.",
  "blocked jobs": "Open jobs with a missing packet item or unsigned change order that stops a pay app from going out.",
  "active jobs": "Jobs not yet closed for the record.",
  // tables
  "job": "The job. Click it to open the money file, packet and actions.",
  "customer": "Who pays this job: the owner, homeowner or general contractor.",
  "status": "What is holding the job up right now, in words. Never just \"pending\".",
  "next": "The next thing to do to get paid on this job.",
  "approved cos ": "Signed change orders.",
  "code": "Short job code. Put it in an email subject or text and the Inbox files it to this job.",
  "number": "Pay app number: PA, the job code, then the sequence.",
  "amount": "Amount of this pay app after retainage, or of this payment.",
  "blockers": "What must be fixed before this pay app can be sent.",
  "missing": "Required packet items that are not in yet.",
  "pay app": "The pay application this packet belongs to.",
  "co": "Change order number on this job.",
  "summary": "What the change order covers.",
  "status / waiting on": "Signed or not, and who it is waiting on.",
  "in billing": "Whether this unsigned change order is on the next pay app. Send stays blocked until it is signed.",
  "unpaid": "Pay apps sent and not fully paid.",
  "days": "Days since the pay app was sent.",
  "contact": "Who approves payment, and their AP email and phone.",
  "follow up": "Send a follow-up once the payer's follow-up window has passed.",
  "reference": "Check number or QuickBooks payment id. Each can be used once per job.",
  "matched": "Date the payment was matched.",
  "kind": "What type of document or message this is.",
  "open": "Retainage still held on this job.",
  "holding it up": "Closeout items that must be done before you can ask for retainage.",
  "unlock": "What to do next to release the retainage.",
  "contact / follow up": "Who to ask for the retainage, and buttons to ask.",
  "closed": "Date the job was closed for the record.",
  "notes": "Completion notes, or the current status.",
  "when": "When it happened.",
  "to": "Who it was addressed to. Email delivery is not built yet; nothing has left Billdable.",
  "subject": "Subject line of the logged message.",
  "actor": "The signed-in person who took the action.",
  "action": "What was done.",
  "detail": "What changed, in words.",
  // new job form
  "job name": "What you call the job, for example \"Oak Street kitchen\".",
  "address": "The job site address. The Inbox uses it to match emails and texts to this job.",
  "contract number": "The number on the contract, if it has one.",
  "contract amount ($)": "The signed contract amount in dollars, before change orders.",
  "retainage %": "Percent the payer holds back from each pay app until closeout. Commonly 5 to 10 percent; check your contract.",
  "role": "Owner: you bill the property owner or homeowner. GC: you are a sub billing a general contractor.",
  "approver": "The person who approves your pay app.",
  "ap email": "Where pay apps and follow-ups go: the payer's accounts payable address.",
  "phone": "Payer phone for follow-up calls.",
  "notes for the job record": "Anything to keep with the job: what the customer wants in a packet, contract notes.",
  // playbook
  "follow-up days": "Days to wait after sending before a follow-up is allowed. Set it to the payer's normal payment window.",
  "usual approval days": "How long this payer usually takes to approve. Drives the expected-approval date on the calendar.",
  "packet": "What this payer requires with every pay app, separated by commas. New pay apps list these items.",
  "rejects when": "Why this payer has sent pay apps back before. Grows as you teach it.",
  "copy": "Who to copy on pay apps for this payer.",
};
const NAV_HELP = {
  inbox: "Bring in photos, PDFs, emails, texts and notes, then file them to a job in one click.",
  dashboard: "What needs attention and the money across open jobs.",
  daily: "A dated note of what happened on site today, saved on the job record.",
  jobs: "Every job, with its money file, packet and actions.",
  calendar: "Follow-ups, expected approvals, payments and closeouts by date. Can be added to Google Calendar.",
  payapps: "Pay applications: the bill for work done this period, with the paperwork the payer requires.",
  packets: "The paperwork that must go with each pay app: waivers, tickets, photos, signed change orders.",
  cos: "Change orders: extra or changed work. Not billable until the customer signs.",
  collections: "What is unpaid and who to contact.",
  payments: "Money received and matched to a pay app.",
  waivers: "Lien waivers: you give up lien rights for amounts you have been paid. One per paid pay app.",
  retainage: "Money the payer holds back until closeout, and what is holding it up.",
  history: "Jobs closed for the record.",
  playbook: "What each payer requires, who approves, how long they take, and why they reject.",
  outbox: "Every message approved to send. Email delivery is not built yet.",
  audit: "Every action, who took it and when.",
  help: "What the terms mean and how to start.",
  "helper-admin": "Questions the helper couldn't answer, and the answers it uses. Team only.",
};

function helpFor(text) {
  const key = (text || "").replace(/[↑↓*]/g, "").replace(/\s+/g, " ").trim().toLowerCase();
  return FIELD_HELP[key];
}

let helpTip = null;
function showHelpTip(icon) {
  if (!helpTip) {
    helpTip = document.createElement("div");
    helpTip.id = "help-tip";
    helpTip.className = "help-tip";
    helpTip.setAttribute("role", "tooltip");
    document.body.appendChild(helpTip);
  }
  helpTip.textContent = icon.dataset.help;
  helpTip.style.display = "block";
  const r = icon.getBoundingClientRect();
  const w = helpTip.offsetWidth, h = helpTip.offsetHeight;
  let left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), window.innerWidth - w - 8);
  let top = r.bottom + 8;
  if (top + h > window.innerHeight - 8) top = r.top - h - 8;
  helpTip.style.left = left + "px";
  helpTip.style.top = Math.max(8, top) + "px";
  icon.setAttribute("aria-describedby", "help-tip");
}
function hideHelpTip() { if (helpTip) helpTip.style.display = "none"; }

function addInfoIcon(labelEl, text, hostEl) {
  if (!text || labelEl.querySelector(".info")) return;
  const icon = document.createElement("span");
  icon.className = "info";
  icon.textContent = "i";
  icon.tabIndex = 0;
  icon.setAttribute("role", "button");
  icon.setAttribute("aria-label", "What is this?");
  icon.dataset.help = text;
  icon.addEventListener("mouseenter", () => showHelpTip(icon));
  icon.addEventListener("mouseleave", hideHelpTip);
  icon.addEventListener("focus", () => showHelpTip(icon));
  icon.addEventListener("blur", hideHelpTip);
  icon.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); showHelpTip(icon); });
  icon.addEventListener("keydown", (e) => { if (e.key === "Escape") { hideHelpTip(); icon.blur(); } });
  labelEl.appendChild(icon);
  (hostEl || labelEl).classList.add("has-info");
}

function decorateHelp(root) {
  root.querySelectorAll(".money-row .label, .metric .k, th, label").forEach(el => {
    if (el.closest(".auth-card")) return;
    const host = el.closest(".money-row, .metric, th, .form-grid > div") || el;
    addInfoIcon(el, helpFor(el.textContent), host);
  });
  root.querySelectorAll(".nav button[data-v]").forEach(b => addInfoIcon(b, NAV_HELP[b.dataset.v], b));
}

// Every string that comes from the server is HTML-escaped once, when it
// arrives. Templates below interpolate into innerHTML, so this is what keeps
// a job named <img onerror=...> from running as code. Never interpolate raw
// user input; wrap it in esc().
function esc(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function escDeep(value) {
  if (typeof value === "string") return esc(value);
  if (Array.isArray(value)) return value.map(escDeep);
  if (value && typeof value === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = escDeep(v);
    return out;
  }
  return value;
}

function setNetBanner(on) {
  let el = document.getElementById("net-banner");
  if (!el && on) {
    el = document.createElement("div");
    el.id = "net-banner";
    el.className = "net-banner";
    el.textContent = "Can't reach Billdable. Your last change may not be saved. Check your connection and try again.";
    document.body.prepend(el);
  }
  if (el) el.style.display = on ? "block" : "none";
}

async function readJson(res) {
  try { return await res.json(); } catch (_) { return {}; }
}

const money = (cents) => {
  if (cents == null) return "$0.00";
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  return `${sign}$${(abs / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

function toast(msg) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.display = "block";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.style.display = "none"; }, 2200);
}

async function load() {
  let res;
  try {
    res = await fetch("/api/state");
  } catch (_) {
    setNetBanner(true);
    return;
  }
  if (res.status === 401) {
    me = null;
    state = null;
    renderLanding();
    return;
  }
  if (!res.ok) {
    setNetBanner(true);
    return;
  }
  setNetBanner(false);
  state = escDeep(await readJson(res));
  render();
}

async function boot() {
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
    me = escDeep(data.user);
    helperAdmin = data.helper_admin === true;
    await load();
  } else {
    renderLanding();
  }
  setAiVisible(!!me);
}

function setAiVisible(on) {
  const badge = document.getElementById("ai-badge");
  const panel = document.getElementById("ai-panel");
  if (badge) badge.style.display = on ? "flex" : "none";
  if (panel && !on) panel.classList.remove("open");
}

function renderLanding() {
  setAiVisible(false);
  const app = document.getElementById("app");
  const signup = authMode === "signup";
  app.innerHTML = `
    <div class="landing">
      <header class="land-bar">
        <div class="brand">Billdable</div>
        <div class="land-bar-actions">
          <button class="btn secondary small" id="bar-signin">Sign in</button>
          <button class="btn small" id="bar-start">Create account</button>
        </div>
      </header>
      <section class="land-hero">
        <div class="land-copy">
          <p class="eyebrow">For small GCs and remodelers</p>
          <h1>You finish the work.<br>Billdable gets it billed and collected.</h1>
          <p class="lede">Works beside QuickBooks. Catches the missing packet, the unsigned change order, and the retainage nobody chased. You approve every send.</p>
          <div class="land-points">
            <div><strong>Same-week invoice</strong><span>From “job’s done” to a draft you can send.</span></div>
            <div><strong>Packet gate</strong><span>Nothing leaves with a missing waiver or photo.</span></div>
            <div><strong>Money file</strong><span>Contract, COs, billed, collected, retainage — one place.</span></div>
          </div>
        </div>
        <div class="auth-card land-form" id="signup">
          <h2>${signup ? "Create your company" : "Sign in"}</h2>
          <p class="sub">${signup ? "Your jobs stay in your company space. Passwords need at least 10 characters." : "Back to your jobs, packets, and playbook."}</p>
          <form id="auth-form">
            ${signup ? `<label>Your name</label><input name="name" required autocomplete="name" />` : ""}
            ${signup ? `<label>Company name</label><input name="company_name" required />` : ""}
            <label>Email</label>
            <input name="email" type="email" required autocomplete="username" />
            <label>Password</label>
            <input name="password" type="password" required autocomplete="${signup ? "new-password" : "current-password"}" />
            <button class="btn" type="submit">${signup ? "Create account" : "Sign in"}</button>
            ${authError ? `<div class="auth-error">${esc(authError)}</div>` : ""}
          </form>
          <div class="auth-toggle">
            ${signup ? "Already have an account?" : "New company?"}
            <button type="button" id="auth-switch">${signup ? "Sign in" : "Create one"}</button>
          </div>
        </div>
      </section>
      <section class="land-how">
        <h2>How it works</h2>
        <div class="land-steps">
          <div class="card"><span class="step">1</span><h3>Mark the job done</h3><p>Notes and evidence land in the binder. The money file already knows the contract.</p></div>
          <div class="card"><span class="step">2</span><h3>Clear the packet</h3><p>Billdable names the blocker — missing waiver, unsigned CO, thin photos — before anything is sent.</p></div>
          <div class="card"><span class="step">3</span><h3>Approve and collect</h3><p>You approve the send. Payments match. Retainage and closeout stay on the calendar.</p></div>
        </div>
      </section>
      <section class="land-price">
        <div class="card">
          <p class="eyebrow">Planned pricing</p>
          <h2>$299<span>/mo</span></h2>
          <p class="sub">Planned price for companies between $750k and $2.5M. No percent of collections. Billing is not live yet.</p>
          <button class="btn" id="price-start">Create your company</button>
        </div>
      </section>
      <footer class="land-foot">Billdable works beside QuickBooks; a direct QuickBooks connection is planned, not built. It does not replace your books or your field app.</footer>
    </div>
  `;
  document.getElementById("auth-form").addEventListener("submit", onAuthSubmit);
  document.getElementById("auth-switch").addEventListener("click", () => {
    authMode = signup ? "login" : "signup";
    authError = "";
    renderLanding();
  });
  const toSignup = () => { authMode = "signup"; authError = ""; renderLanding(); };
  const toSignin = () => { authMode = "login"; authError = ""; renderLanding(); };
  document.getElementById("bar-start").addEventListener("click", toSignup);
  document.getElementById("price-start").addEventListener("click", toSignup);
  document.getElementById("bar-signin").addEventListener("click", toSignin);
}

async function onAuthSubmit(e) {
  e.preventDefault();
  const form = e.target;
  if (authMode === "signup" && form.password.value.length < 10) {
    authError = "Password must be at least 10 characters.";
    renderLanding();
    return;
  }
  const body = {
    email: form.email.value,
    password: form.password.value,
  };
  if (authMode === "signup") {
    body.name = form.name.value;
    body.company_name = form.company_name.value;
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
    authError = data.error || "Could not sign in.";
    renderLanding();
    return;
  }
  me = escDeep(data.user);
  authError = "";
  helperLog.length = 0;
  try {
    const who = await fetch("/api/auth/me");
    if (who.ok) helperAdmin = (await readJson(who)).helper_admin === true;
  } catch (_) { helperAdmin = false; }
  await load();
  setAiVisible(true);
}

async function logout() {
  await fetch("/api/auth/logout", { method: "POST" });
  me = null;
  state = null;
  helperAdmin = false;
  helperLog.length = 0;   // never show one person's questions to the next
  document.getElementById("ai-replies").innerHTML = "";
  document.getElementById("ai-chips").innerHTML = "";
  aiOpen = false;
  document.getElementById("ai-panel").classList.remove("open");
  authMode = "signup";
  setAiVisible(false);
  renderLanding();
}

async function post(path, body = {}) {
  let res;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (_) {
    setNetBanner(true);
    toast("Not saved: can't reach Billdable.");
    return;
  }
  setNetBanner(false);
  const data = await readJson(res);
  if (res.status === 401) {
    me = null;
    state = null;
    renderLanding();
    return;
  }
  if (res.status === 409) {
    const lines = data.blockers || [data.error];
    toast((data.error && lines[0] !== data.error ? data.error + " " : "Blocked: ") + lines.join("; "));
  } else if (!res.ok) {
    toast(data.error || "Request failed");
  } else {
    toast("Saved");
  }
  await load();
}

function nav(view, jobId = null, detailId = null) {
  currentView = view;
  currentJobId = jobId;
  currentDetailId = detailId;
  formOpen = {};
  render();
}

function toggleForm(key) {
  formOpen[key] = !formOpen[key];
  if (key === "newjob") jobFormError = "";
  render();
}

function backButton(view, label) {
  return `<div class="top"><button class="back" onclick="nav('${view}')">← ${label}</button></div>`;
}

function emptyState(title, body, action = "") {
  return `<div class="empty-state"><strong>${title}</strong><p>${body}</p>${action}</div>`;
}

function nextStep(j) {
  if (j.closed) return "Closed for the record.";
  if (j.work_status !== "complete") return "Mark the job done to start the money path.";
  if (j.blockers && j.blockers.length) return "Clear blockers: " + j.blockers[0];
  const draft = j.invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
  if (draft) return "Pay app is ready — review and Approve & send.";
  if (j.invoices.some(i => i.status === "sent")) return "Waiting on payer. Follow up or match a payment.";
  if (j.waiver_due && !j.waiver_sent) return "Payment received — approve and send the waiver.";
  if (j.money.retainage_open_cents > 0) return "Chase retainage once the closeout packet is complete.";
  if (j.money.outstanding_cents === 0) return "Ready to close for the record.";
  return "Draft a pay app for unbilled completed work.";
}

function render() {
  if (!state) return;
  hideHelpTip();
  const app = document.getElementById("app");
  const company = state.company;
  const active = (v) => (currentView === v ? "active" : "");
  app.innerHTML = `
    <div class="shell">
      <aside class="rail">
        <div class="brand">Billdable</div>
        <div class="company">${company.name}<br>${company.crew ? company.crew + " people · " : ""}${company.books || "QuickBooks"}</div>
        <nav class="nav">
          <div class="nav-section">Operations</div>
          <button class="${active("inbox")}" data-v="inbox">Inbox${newInboxCount() ? ` <span class="count">${newInboxCount()}</span>` : ""}</button>
          <button class="${active("dashboard")}" data-v="dashboard">Dashboard</button>
          <button class="${active("daily")}" data-v="daily">Daily Record</button>
          <button class="${active("jobs")}" data-v="jobs">Jobs</button>
          <button class="${active("calendar")}" data-v="calendar">Calendar</button>
          <div class="nav-section">Money</div>
          <button class="${active("payapps")}" data-v="payapps">Pay Apps</button>
          <button class="${active("packets")}" data-v="packets">Packets</button>
          <button class="${active("cos")}" data-v="cos">Change Orders</button>
          <button class="${active("collections")}" data-v="collections">Collections</button>
          <button class="${active("payments")}" data-v="payments">Payments</button>
          <button class="${active("waivers")}" data-v="waivers">Waivers</button>
          <button class="${active("retainage")}" data-v="retainage">Retainage</button>
          <div class="nav-section">Records</div>
          <button class="${active("history")}" data-v="history">Job history</button>
          <button class="${active("playbook")}" data-v="playbook">Payer Playbook</button>
          <button class="${active("outbox")}" data-v="outbox">Outbox</button>
          <button class="${active("audit")}" data-v="audit">Audit</button>
          <button class="${active("help")}" data-v="help">Help</button>
          ${helperAdmin ? `<button class="${active("helper-admin")}" data-v="helper-admin">Helper answers</button>` : ""}
        </nav>
        <a class="reset-btn" href="/api/export" download>Export data</a>
        <button class="reset-btn" id="reset">Reset data</button>
        <div class="user-meta">
          ${me ? me.name : ""}<br>${me ? me.email : ""}
          <button class="reset-btn" id="logout">Log out</button>
        </div>
      </aside>
      <main class="main" id="main"></main>
    </div>
  `;
  document.querySelectorAll(".nav button").forEach((b) =>
    b.addEventListener("click", () => nav(b.dataset.v))
  );
  document.getElementById("reset").addEventListener("click", resetData);
  document.getElementById("logout").addEventListener("click", logout);
  document.getElementById("main").innerHTML = views[currentView] ? views[currentView]() : "<p>Unknown view</p>";
  bindActions();
  decorateHelp(document.getElementById("app"));
  if (["inbox", "calendar"].includes(currentView)) loadImportSettings();
  if (currentView === "helper-admin") loadHelperReview();
  updateAiContext();
}

async function resetData() {
  if (me && me.company_id === DEMO_COMPANY_ID) {
    if (!confirm("Reset the demo account to its starting data?")) return;
    await post("/api/reset");
    return;
  }
  const typed = prompt(
    "Reset deletes every job, payer and record in your company. A snapshot is kept on the server.\n\n" +
    "Type your company name exactly as it appears in the left rail to confirm:"
  );
  if (typed === null) return;
  await post("/api/reset", { confirm: typed });
}

function bindActions() {
  document.querySelectorAll("[data-action]").forEach((el) => {
    el.addEventListener("click", async (e) => {
      e.preventDefault();
      const a = el.dataset.action;
      const id = el.dataset.id;
      if (a === "draft") await post(`/api/jobs/${id}/draft`);
      else if (a === "send") await post(`/api/jobs/${id}/send`);
      else if (a === "progress") {
        const dollars = parseFloat(document.querySelector("#progress-amt")?.value || "0");
        const notes = document.querySelector("#progress-notes")?.value || "";
        if (!(dollars > 0)) { toast("Enter completed-to-date in dollars."); return; }
        formOpen.progress = false;
        await post(`/api/jobs/${id}/progress`, { completed_cents: Math.round(dollars * 100), notes });
      }
      else if (a === "done") {
        const notes = document.querySelector("#done-notes")?.value || "";
        await post(`/api/jobs/${id}/done`, { notes });
        formOpen.done = false;
      }
      else if (a === "resolve") {
        const reason = prompt("Why is this item present? For example: \"Emailed to the payer on 10/12\". Or cancel and attach a job record instead.");
        if (reason === null) return;
        if (!reason.trim()) { toast("Give a reason, or attach a job record instead."); return; }
        await post(`/api/jobs/${id}/resolve/${el.dataset.item}`, { reason });
      }
      else if (a === "waiver") await post(`/api/jobs/${id}/waiver`, el.dataset.inv ? { invoice_id: el.dataset.inv } : {});
      else if (a === "retainage") await post(`/api/jobs/${id}/retainage`);
      else if (a === "retainage-paid") {
        const dollars = parseFloat(document.querySelector("#retpay-amt")?.value || "0");
        const reference = document.querySelector("#retpay-ref")?.value || "";
        const cents = Math.round(dollars * 100);
        if (!(cents > 0)) { toast("Enter the retainage amount received."); return; }
        formOpen.retpay = false;
        await post(`/api/jobs/${id}/retainage-paid`, { amount_cents: cents, reference });
      }
      else if (a === "include-co") await post(`/api/jobs/${id}/include-co/${el.dataset.co}`);
      else if (a === "pencil") await post(`/api/jobs/${id}/pencil`);
      else if (a === "pencil-agreed") await post(`/api/jobs/${id}/pencil-response`, { agreed: true });
      else if (a === "pencil-changes") {
        const note = prompt("What does the payer want changed? (quantities, change orders, missing items)");
        if (note === null) return;
        await post(`/api/jobs/${id}/pencil-response`, { agreed: false, note });
      }
      else if (a === "pick-files") {
        const input = document.getElementById("file-input");
        if (input) { input.dataset.job = el.dataset.job || ""; input.click(); }
      }
      else if (a === "import-text") {
        const text = document.querySelector("#paste-text")?.value || "";
        const source = document.querySelector("#paste-source")?.value || "paste";
        const jobId = el.dataset.job || "";
        if (!text.trim()) { toast("Paste some text first."); return; }
        const ok = await postImport("/api/import/text" + (jobId ? "?file=1" : ""), { text, source, job_id: jobId });
        if (ok) { const box = document.querySelector("#paste-text"); if (box) box.value = ""; }
      }
      else if (a === "inbox-file") {
        const itemId = el.dataset.item;
        const jobId = document.querySelector(`#inb-job-${itemId}`)?.value || "";
        const kind = document.querySelector(`#inb-kind-${itemId}`)?.value || "";
        if (!jobId) { toast("Pick a job for this item."); return; }
        await post(`/api/inbox/${itemId}/file`, { job_id: jobId, kind });
      }
      else if (a === "inbox-file-all") {
        const items = (state.inbox || []).filter(i => i.status === "new" && i.suggested_job_id);
        for (const i of items) {
          const jobId = document.querySelector(`#inb-job-${i.id}`)?.value || i.suggested_job_id;
          const kind = document.querySelector(`#inb-kind-${i.id}`)?.value || i.kind;
          await post(`/api/inbox/${i.id}/file`, { job_id: jobId, kind });
        }
      }
      else if (a === "inbox-dismiss") await post(`/api/inbox/${el.dataset.item}/dismiss`);
      else if (a === "inbox-undo") await post(`/api/inbox/${el.dataset.item}/undo`);
      else if (a === "copy") {
        const text = document.getElementById(el.dataset.target)?.textContent || "";
        try { await navigator.clipboard.writeText(text); toast("Copied"); }
        catch (_) { toast("Copy blocked by the browser. Select the text and copy it."); }
      }
      else if (a === "rotate-token") {
        if (!confirm("Make a new address? The old one stops working right away.")) return;
        await fetch("/api/import/rotate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: el.dataset.kind }) });
        importSettings = null;
        render();
      }
      else if (a === "withdraw-co") {
        if (!confirm("Withdraw this change order? Use this when the customer will not sign it.")) return;
        await post(`/api/jobs/${id}/withdraw-co/${el.dataset.co}`);
      }
      else if (a === "void-draft") {
        if (!confirm("Void the open draft pay app? Its work goes back to unbilled.")) return;
        await post(`/api/jobs/${id}/void-draft`);
      }
      else if (a === "follow-up") await post(`/api/jobs/${id}/follow-up`);
      else if (a === "match") {
        const dollars = parseFloat(document.querySelector("#match-amt")?.value || "0");
        const ref = document.querySelector("#match-ref")?.value || "";
        const invoiceId = document.querySelector("#match-inv")?.value || "";
        const cents = Math.round(dollars * 100);
        if (!(cents > 0)) { toast("Enter the payment amount in dollars."); return; }
        await post(`/api/jobs/${id}/match-payment`, { amount_cents: cents, reference: ref, invoice_id: invoiceId });
      }
      else if (a === "add-co") {
        const title = (document.querySelector("#co-title")?.value || "").trim();
        const dollars = parseFloat(document.querySelector("#co-amt")?.value || "0");
        const cents = Math.round(dollars * 100);
        if (!title) { toast("Give the change order a title."); return; }
        if (!(cents > 0)) { toast("Enter the change order amount."); return; }
        await post(`/api/jobs/${id}/add-co`, { title, amount_cents: cents });
        nav("co-detail", id);
      }
      else if (a === "goto-co") { coPreselectJob = id; nav("cos"); }
      else if (a === "add-co-from-list") {
        const jobId = document.querySelector("#new-co-job")?.value;
        const title = (document.querySelector("#co-title")?.value || "").trim();
        const dollars = parseFloat(document.querySelector("#co-amt")?.value || "0");
        const cents = Math.round(dollars * 100);
        if (!jobId) { toast("Pick a job."); return; }
        if (!title) { toast("Give the change order a title."); return; }
        if (!(cents > 0)) { toast("Enter the change order amount."); return; }
        {
          await post(`/api/jobs/${jobId}/add-co`, { title, amount_cents: cents });
          nav("co-detail", jobId);
        }
      }
      else if (a === "goto-packet") nav("packet-detail", id);
      else if (a === "open-packet") nav("packet-detail", el.dataset.job, el.dataset.inv);
      else if (a === "open-co") nav("co-detail", el.dataset.job, el.dataset.co);
      else if (a === "open-payapp") nav("payapp-detail", el.dataset.job, el.dataset.inv);
      else if (a === "open-waiver") nav("waiver-detail", id);
      else if (a === "daily-save") {
        const notes = document.querySelector("#daily-notes")?.value || "";
        const job = document.querySelector("#daily-job")?.value;
        if (job && notes) await post(`/api/jobs/${job}/daily-record`, { notes });
      }
      else if (a === "attach") {
        const name = document.querySelector("#attach-name")?.value || "attachment.pdf";
        await post(`/api/jobs/${id}/attach/${el.dataset.item}`, { name });
      }
      else if (a === "import-qbo") await post(`/api/jobs/${id}/import-qbo`);
      else if (a === "close") await post(`/api/jobs/${id}/close`);
      else if (a === "cal-prev") { calMonth--; if (calMonth < 0) { calMonth = 11; calYear--; } calSelectedDay = null; render(); }
      else if (a === "cal-next") { calMonth++; if (calMonth > 11) { calMonth = 0; calYear++; } calSelectedDay = null; render(); }
      else if (a === "cal-today") { const n = new Date(); calYear = n.getFullYear(); calMonth = n.getMonth(); calSelectedDay = null; render(); }
      else if (a === "cal-day") { calSelectedDay = el.dataset.day; render(); }
      else if (a === "approve-co") await post(`/api/jobs/${id}/approve-co/${el.dataset.co}`);
      else if (a === "add-evidence") {
        const label = document.querySelector("#ev-label")?.value;
        if (label) await post(`/api/jobs/${id}/add-evidence`, { label, kind: "note" });
      }
      else if (a === "learn") {
        const reason = document.querySelector("#learn-reason")?.value;
        if (reason) await post(`/api/jobs/${id}/learn-rejection`, { reason });
      }
      else if (a === "add-record") {
        const label = document.querySelector("#rec-label")?.value.trim();
        const note = document.querySelector("#rec-note")?.value.trim() || "";
        const kind = document.querySelector("#rec-kind")?.value || "note";
        if (!label) { toast("A record needs a label."); return; }
        await post(`/api/jobs/${id}/add-record`, { label, note, kind });
      }
      else if (a === "attach-record") {
        const recordId = document.querySelector(`#attach-rec-${el.dataset.item}`)?.value;
        if (!recordId) { toast("Pick a job record to attach."); return; }
        await post(`/api/jobs/${id}/attach-record/${el.dataset.item}`, { record_id: recordId });
      }
      else if (a === "open-job") nav("job", id);
      else if (a === "create-job") {
        const name = document.querySelector("#nj-name")?.value.trim() || "";
        const address = document.querySelector("#nj-address")?.value.trim() || "";
        const customer = document.querySelector("#nj-customer")?.value.trim() || "";
        const approver = document.querySelector("#nj-approver")?.value.trim() || "";
        const apEmail = document.querySelector("#nj-email")?.value.trim() || "";
        const phone = document.querySelector("#nj-phone")?.value.trim() || "";
        const role = document.querySelector("#nj-role")?.value || "Owner";
        const contractNumber = document.querySelector("#nj-contract")?.value.trim() || "";
        const packetNotes = document.querySelector("#nj-packet")?.value.trim() || "";
        const dollars = parseFloat(document.querySelector("#nj-amount")?.value || "0");
        const retainage = parseInt(document.querySelector("#nj-retainage")?.value || "0", 10);
        const errors = [];
        if (!name) errors.push("Job name is required.");
        if (!address) errors.push("Address is required.");
        if (!customer) errors.push("Customer is required.");
        if (!approver) errors.push("Approver is required.");
        if (!apEmail || !apEmail.includes("@")) errors.push("A real AP email is required.");
        if (!(dollars > 0)) errors.push("Contract amount must be greater than zero.");
        if (retainage < 0 || retainage > 20) errors.push("Retainage must be between 0 and 20.");
        if (errors.length) {
          jobFormError = errors.join(" ");
          render();
          return;
        }
        jobFormError = "";
        let res;
        try {
          res = await fetch("/api/jobs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name, address, customer, approver,
            ap_email: apEmail, phone, role,
            contract_number: contractNumber,
            packet_notes: packetNotes,
            contract_cents: Math.round(dollars * 100),
            retainage_pct: retainage,
          }),
          });
        } catch (_) {
          setNetBanner(true);
          toast("Not saved: can't reach Billdable.");
          return;
        }
        const data = await readJson(res);
        if (!res.ok) {
          jobFormError = (data.blockers || [data.error || "Could not create the job."]).join(" ");
          render();
          return;
        }
        formOpen.newjob = false;
        state = escDeep(data);
        toast("Job saved");
        render();
      }
      else if (a === "toggle-form") toggleForm(el.dataset.form);
      else if (a === "dismiss-help") { helpDismissed = true; localStorage.setItem("billdable-help-dismissed", "1"); render(); }
      else if (a === "filter") { dashFilter = el.dataset.filter; render(); }
      else if (a === "sort") {
        if (dashSort === el.dataset.sort) dashSortDir *= -1;
        else { dashSort = el.dataset.sort; dashSortDir = 1; }
        render();
      }
      else if (a === "payapp-filter") { payappFilter = el.dataset.filter; render(); }
      else if (a === "save-playbook") {
        const form = el.closest("form");
        const updates = {};
        new FormData(form).forEach((v, k) => { if (v !== "") updates[k] = isNaN(v) ? v : Number(v); });
        await post(`/api/payers/${id}/update`, updates);
      }
    });
  });
  // Filter dropdowns listen for "change" only. A click handler that re-renders
  // destroys the open dropdown, which is why mouse selection used to fail.
  document.querySelectorAll("select[data-filter-for]").forEach(s => {
    s.addEventListener("change", () => {
      if (s.dataset.filterFor === "outbox") outboxJobFilter = s.value;
      else if (s.dataset.filterFor === "audit") auditJobFilter = s.value;
      else if (s.dataset.filterFor === "calendar") calJobFilter = s.value;
      render();
    });
  });
}

function newInboxCount() {
  return (state && state.inbox ? state.inbox.filter(i => i.status === "new").length : 0);
}

const MAX_UPLOAD_MB = 15;

async function uploadFiles(fileList, jobId = "", fileNow = false) {
  const files = [...(fileList || [])];
  if (!files.length) return;
  let ok = 0;
  const failed = [];
  for (const f of files) {
    if (f.size > MAX_UPLOAD_MB * 1024 * 1024) { failed.push(`${f.name}: larger than ${MAX_UPLOAD_MB} MB`); continue; }
    const name = f.name && f.name !== "image.png" ? f.name : `Screenshot ${new Date().toISOString().slice(0, 16).replace("T", " ").replace(":", ".")}.png`;
    const query = jobId ? `?job=${encodeURIComponent(jobId)}${fileNow ? "&file=1" : ""}` : "";
    try {
      const res = await fetch(`/api/import/file${query}`, {
        method: "POST",
        headers: { "Content-Type": f.type || "application/octet-stream", "X-Filename": encodeURIComponent(name) },
        body: f,
      });
      const data = await readJson(res);
      if (res.status === 401) { me = null; state = null; renderLanding(); return; }
      if (res.ok) { ok++; state = escDeep(data); setNetBanner(false); }
      else failed.push(data.error || `${name}: upload failed`);
    } catch (_) {
      setNetBanner(true);
      failed.push(`${name}: can't reach Billdable`);
    }
  }
  const where = fileNow ? "added to the job record" : "in the Inbox";
  toast([ok ? `${ok} file${ok === 1 ? "" : "s"} ${where}.` : "", failed.join("; ")].filter(Boolean).join(" "));
  render();
}

async function postImport(path, body) {
  let res;
  try {
    res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch (_) { setNetBanner(true); toast("Not saved: can't reach Billdable."); return false; }
  const data = await readJson(res);
  if (!res.ok) { toast(data.error || "Import failed."); return false; }
  state = escDeep(data);
  toast(body.job_id ? "Added to the job record." : "Added to the Inbox.");
  render();
  return true;
}

async function loadImportSettings() {
  if (importSettings) { fillImportSettings(); return; }
  try {
    const res = await fetch("/api/import/settings");
    if (res.ok) { importSettings = escDeep(await readJson(res)); fillImportSettings(); }
  } catch (_) { /* the cards keep their "loading" text; the banner covers outages */ }
}

function fillImportSettings() {
  const s = importSettings;
  if (!s) return;
  const set = (id, value) => { const el = document.getElementById(id); if (el) el.innerHTML = value; };
  set("email-webhook", s.email_webhook);
  set("sms-webhook", s.sms_webhook);
  set("cal-url", s.calendar_url);
  set("email-address", s.email_address ? `Forward or BCC job email to <strong>${s.email_address}</strong>.` : "No forwarding address is set up yet (an email provider has to be connected first; see steps).");
  set("sms-number", s.sms_number ? `Text photos and notes to <strong>${s.sms_number}</strong>.` : "No texting number is set up yet (a Twilio number has to be connected first; see steps).");
}

function recordFiles(r) {
  return (r.file_ids || []).map(fid => {
    const f = (state.files || []).find(x => x.id === fid);
    return f ? `<a href="/api/files/${f.id}" target="_blank" rel="noopener">${f.name}</a>` : "";
  }).filter(Boolean).join(" · ");
}

function inboxItemCard(i) {
  const openJobs = state.jobs.filter(j => !j.closed);
  const files = (i.file_ids || []).map(fid => (state.files || []).find(x => x.id === fid)).filter(Boolean);
  const title = i.subject || (files[0] && files[0].name) || (i.text || "").split("\n")[0].slice(0, 80) || "Untitled";
  const preview = (i.text || "").slice(0, 600);
  const kinds = Object.entries(RECORD_KINDS).map(([k, v]) => `<option value="${k}" ${i.kind === k ? "selected" : ""}>${v}</option>`).join("");
  const jobs = `<option value="">Pick a job…</option>` + openJobs.map(j => `<option value="${j.id}" ${i.suggested_job_id === j.id ? "selected" : ""}>${j.name} (${j.code})</option>`).join("");
  return `<div class="inbox-item">
    <div class="inbox-head"><span class="pill">${SOURCE_LABELS[i.source] || i.source}</span> <strong>${title}</strong>
      <span class="sub">${i.from ? "from " + i.from + " · " : ""}${(i.sent_at || i.received_at || "").slice(0, 10)}${i.note_date ? " · dated " + i.note_date : ""}</span></div>
    ${preview ? `<details><summary>${preview.slice(0, 140)}${preview.length > 140 ? "…" : ""}</summary><pre class="inbox-text">${preview}</pre></details>` : ""}
    ${files.length ? `<div class="sub">Files: ${files.map(f => `<a href="/api/files/${f.id}" target="_blank" rel="noopener">${f.name}</a>`).join(" · ")}</div>` : ""}
    ${(i.amounts || []).length || (i.dates || []).length ? `<div class="sub">Found: ${(i.amounts || []).map(a => money(a)).join(", ")}${(i.amounts || []).length && (i.dates || []).length ? " · " : ""}${(i.dates || []).join(", ")}</div>` : ""}
    ${(i.skipped_attachments || []).length ? `<div class="field-error">${i.skipped_attachments.join(" ")}</div>` : ""}
    <div class="sub">${i.suggested_job_id ? "Suggested because it " + i.match_reason : i.match_reason}</div>
    <div class="form-inline">
      <select id="inb-job-${i.id}" aria-label="Job">${jobs}</select>
      <select id="inb-kind-${i.id}" aria-label="Kind">${kinds}</select>
      <button class="btn small" data-action="inbox-file" data-item="${i.id}">File to job record</button>
      <button class="btn small secondary" data-action="inbox-dismiss" data-item="${i.id}">Dismiss</button>
    </div>
  </div>`;
}

function filteredJobs() {
  const now = new Date();
  const thisMonth = (d) => d && new Date(d).getMonth() === now.getMonth() && new Date(d).getFullYear() === now.getFullYear();
  let jobs = [...state.jobs];
  if (dashFilter === "all") {
    // all open + completed this month
    jobs = jobs.filter(j => !j.closed || thisMonth(j.completed_at) || thisMonth(j.closed_at));
  } else if (dashFilter === "complete") {
    jobs = jobs.filter(j => j.work_status === "complete" && thisMonth(j.completed_at));
  } else if (dashFilter === "sent") {
    jobs = jobs.filter(j => j.invoices.some(i => i.status === "sent"));
  } else if (dashFilter === "blocked") jobs = jobs.filter(j => j.blockers && j.blockers.length && !j.closed);
  else if (dashFilter === "draft") jobs = jobs.filter(j => j.invoices.some(i => ["draft", "awaiting_approval"].includes(i.status)));
  else if (dashFilter === "waiver") jobs = jobs.filter(j => j.waiver_due && !j.waiver_sent);
  const key = dashSort;
  jobs.sort((a, b) => {
    let va, vb;
    if (key === "name") { va = a.name; vb = b.name; }
    else if (key === "outstanding") { va = a.money.outstanding_cents; vb = b.money.outstanding_cents; }
    else if (key === "unbilled") { va = a.money.unbilled_completed_cents; vb = b.money.unbilled_completed_cents; }
    else if (key === "contract") { va = a.money.contract_total_cents; vb = b.money.contract_total_cents; }
    else if (key === "billed") { va = a.money.billed_cents; vb = b.money.billed_cents; }
    else if (key === "collected") { va = a.money.collected_cents; vb = b.money.collected_cents; }
    else if (key === "retainage") { va = a.money.retainage_open_cents; vb = b.money.retainage_open_cents; }
    else { va = a.name; vb = b.name; }
    if (va < vb) return -1 * dashSortDir;
    if (va > vb) return 1 * dashSortDir;
    return 0;
  });
  return jobs;
}

function billedThisMonth() {
  const now = new Date();
  let total = 0;
  state.jobs.forEach(j => j.invoices.forEach(inv => {
    if (inv.sent_at && new Date(inv.sent_at).getMonth() === now.getMonth() && new Date(inv.sent_at).getFullYear() === now.getFullYear()) {
      total += inv.amount_cents;
    }
  }));
  return total;
}

let helperReview = null;
let helperDraft = null;   // the answer being written or edited
const HELPER_VIEWS = ["inbox", "dashboard", "daily", "jobs", "calendar", "payapps", "packets", "cos", "collections", "payments", "waivers", "retainage", "history", "playbook", "outbox", "audit", "help"];

async function loadHelperReview() {
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
      <div class="span-2"><label for="ha-questions">Example questions, one per line</label><textarea id="ha-questions" rows="4">${(d.questions || []).join("\n")}</textarea></div>
      <div class="span-2"><label for="ha-answer">Answer</label><textarea id="ha-answer" rows="6" placeholder="Plain words. Start a line with - for a bullet.">${d.answer || ""}</textarea></div>
      <div><label for="ha-category">Category</label><input id="ha-category" value="${d.category || "General"}"></div>
      <div><label for="ha-keywords">Keywords, comma separated</label><input id="ha-keywords" value="${(d.keywords || []).join(", ")}"></div>
      <div><label for="ha-handler">Add live data from the company's jobs</label><select id="ha-handler"><option value="">None</option>${handlers.map(h => `<option ${d.handler === h ? "selected" : ""}>${h}</option>`).join("")}</select></div>
      <div><label for="ha-nav">Button to open a page</label><select id="ha-nav"><option value="">None</option>${HELPER_VIEWS.map(v => `<option value="${v}" ${navAction && navAction.view === v ? "selected" : ""}>${v}</option>`).join("")}</select></div>
      <div class="span-2"><label for="ha-follow">Suggested next questions, one per line</label><textarea id="ha-follow" rows="2">${(d.follow_ups || []).join("\n")}</textarea></div>
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
  const raw = (s) => { const t = document.createElement("textarea"); t.innerHTML = s; return t.value; };
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

const views = {
  "helper-admin"() {
    return `<h1>Helper answers</h1>
      <p class="sub">What customers asked the helper that it couldn't answer well, and the answers it uses. Write an answer once; every company gets it right away. Only people listed in HELPER_ADMIN_EMAILS see this page.</p>
      <div id="helper-admin-body">Loading…</div>`;
  },
  inbox() {
    const items = (state.inbox || []).filter(i => i.status === "new").slice().reverse();
    const done = (state.inbox || []).filter(i => i.status !== "new").slice(-20).reverse();
    const withSuggestion = items.filter(i => i.suggested_job_id).length;
    const jobName = (id) => (state.jobs.find(j => j.id === id) || {}).name || "a job";
    return `
      <h1>Inbox</h1>
      <p class="sub">Bring in photos, PDFs, saved emails, text threads and notes. Billdable suggests the job from the job code, address, name or sender; you file it with one click. Nothing is filed without you.</p>
      <div class="dropzone" tabindex="0">
        <strong>Drop files anywhere on this page</strong>
        <span class="sub">Photos, PDFs, Word, Excel, CSV, saved emails (.eml), up to ${MAX_UPLOAD_MB} MB each. Or <button class="linkish" data-action="pick-files">choose files</button>, or press Ctrl+V / ⌘V to paste a screenshot. On a phone, choose files opens the camera too.</span>
      </div>
      <div class="card">
        <h2>Paste an email, a text thread, or notes</h2>
        <div class="form-inline">
          <select id="paste-source" aria-label="What are you pasting?">
            <option value="email">An email (with or without the From/Subject lines)</option>
            <option value="sms">Text messages</option>
            <option value="notes">Notes: one daily record per date</option>
            <option value="paste">Something else</option>
          </select>
        </div>
        <textarea id="paste-text" rows="6" placeholder="Paste here. For notes, start each day with a date, for example:&#10;10/12 Framing done on Oak Street, 4 crew&#10;10/13 Inspector passed rough-in"></textarea>
        <div class="form-inline"><button class="btn" data-action="import-text">Add to Inbox</button>
        <span class="sub">Notes split at every line that starts with a date (10/12, Oct 12, 2026-10-12). Exported Apple Notes or a typed-up notebook work.</span></div>
      </div>
      <div class="card">
        <div class="top"><h2>To file (${items.length})</h2>${withSuggestion > 1 ? `<button class="btn small" data-action="inbox-file-all">File all ${withSuggestion} with a suggested job</button>` : ""}</div>
        ${items.length ? items.map(inboxItemCard).join("") : emptyState("Nothing waiting", "Drop files, paste an email or notes above, or set up email and text forwarding below.")}
      </div>
      ${done.length ? `<details class="card"><summary><strong>Recently filed or dismissed</strong></summary>
        ${done.map(i => `<div class="list-item">${i.status === "filed" ? `Filed to ${jobName(i.filed_job_id)} as ${RECORD_KINDS[i.filed_kind] || i.filed_kind}` : "Dismissed"}: ${i.subject || (i.text || "").slice(0, 60) || "item"} <button class="btn small secondary" data-action="inbox-undo" data-item="${i.id}">Undo</button></div>`).join("")}
      </details>` : ""}
      <details class="card">
        <summary><strong>Bring emails and texts in automatically</strong></summary>
        <h3>Email</h3>
        <p id="email-address" class="sub">Loading…</p>
        <p class="sub">Steps: in an email service that can post incoming mail to a web address (SendGrid Inbound Parse, Mailgun routes or Postmark inbound), set up an inbound address and point it at this private web address. Then forward or BCC job emails to that inbound address. Attachments come in as files.</p>
        <div class="copy-row"><code id="email-webhook">Loading…</code><button class="btn small secondary" data-action="copy" data-target="email-webhook">Copy</button><button class="btn small secondary" data-action="rotate-token" data-kind="email">Make a new address</button></div>
        <h3>Text messages</h3>
        <p id="sms-number" class="sub">Loading…</p>
        <p class="sub">Steps: in Twilio, buy a number and set its incoming-message webhook to this private web address. Set TWILIO_AUTH_TOKEN on the server so Billdable checks each message really came from Twilio. Photos sent by text show as links for now; they are not downloaded.</p>
        <div class="copy-row"><code id="sms-webhook">Loading…</code><button class="btn small secondary" data-action="copy" data-target="sms-webhook">Copy</button><button class="btn small secondary" data-action="rotate-token" data-kind="sms">Make a new address</button></div>
        <p class="sub">Anyone who knows these addresses can drop items in your Inbox. They are reviewed before filing. Make a new address if one leaks.</p>
      </details>
    `;
  },
  help() {
    const glossary = [
      ["Pay app (pay application)", "The bill for work done this period. It lists what was completed, at the contract rates, plus signed change orders, less retainage."],
      ["Pencil copy (pencil draft)", "A draft of the pay app sent to the owner or GC to agree quantities before the formal one. It avoids \"push it to next month\"."],
      ["Packet", "Everything the contract says must go with a pay app: lien waivers, delivery tickets, photos, signed change orders, schedule of values, insurance certificate."],
      ["Schedule of values", "The contract broken into line items with a value each. Each pay app bills a percentage of each line."],
      ["Change order (CO)", "Extra or changed work. Bill it only after the customer signs it."],
      ["Retainage", "A percentage (often 5 to 10) the payer holds back from every pay app until the job is closed out."],
      ["Punch list", "Small fixes found at final inspection: a dead outlet, a loose railing. Finishing it is what releases retainage."],
      ["Closeout packet", "The documents needed to finish the job and release retainage: punch-list signoff, final waivers, warranties."],
      ["Lien waiver", "A document giving up lien rights for money already paid. Conditional: only if the payment clears. Unconditional: payment received. Forms are set by state law in some states."],
      ["Pay-when-paid / pay-if-paid", "Contract terms that tie a sub's payment to the GC being paid. Pay-if-paid shifts the risk to the sub; some states limit it."],
      ["Payer playbook", "What each customer requires, who approves, how long they take, and why they reject pay apps."],
    ];
    return `
      <h1>Help</h1>
      <p class="sub">Hover any label with an (i) to see what it means. Tap it on a phone.</p>
      <div class="card">
        <h2>First job, start to finish</h2>
        <ol class="steps">
          <li>Jobs → New job: contract amount, payer, approver, AP email, retainage percent.</li>
          <li>Drop the contract, photos and tickets on the job record, or bring emails and texts in through the Inbox.</li>
          <li>Progress pay app: enter completed-to-date. Billdable drafts the pay app, withholds retainage and lists the packet.</li>
          <li>Optional: send a pencil copy so the payer agrees the numbers first.</li>
          <li>Attach records to each packet item. Approve and send unlocks when the packet is complete.</li>
          <li>Match the payment when it arrives, then send the waiver for that pay app.</li>
          <li>Mark done, finish the punch list (closeout packet), ask for retainage, record it when paid, close the job.</li>
        </ol>
      </div>
      <div class="card"><h2>Words you will see</h2>
        <dl class="glossary">${glossary.map(([t, d]) => `<dt>${t}</dt><dd>${d}</dd>`).join("")}</dl>
      </div>
    `;
  },
  dashboard() {
    const t = state.totals;
    const jobs = filteredJobs();
    const actionJobs = state.jobs.filter(j => (j.blockers && j.blockers.length) || (j.waiver_due && !j.waiver_sent) || j.invoices.some(i => i.status === "awaiting_approval"));
    return `
      <h1>Dashboard</h1>
      <p class="sub">What needs attention and the money across open jobs.</p>
      ${state.jobs.length === 0 ? emptyState("No jobs yet", "Create the first job. Its record holds notes and files until a pay app needs them.") : ""}
      ${!helpDismissed && state.jobs.length ? `
        <div class="help-card">
          <button class="help-dismiss" data-action="dismiss-help" title="Dismiss">×</button>
          <strong>How to simulate the product</strong><br>
          Open a job → clear blockers → Approve & send → match payment → send waiver → Close for record.
          Calendar and Outbox update from the same actions.
        </div>` : ""}
      <div class="metrics">
        <div class="metric highlight"><div class="k">Billed this month</div><div class="v">${money(billedThisMonth())}</div></div>
        <div class="metric"><div class="k">Unbilled completed</div><div class="v">${money(t.unbilled_cents)}</div></div>
        <div class="metric"><div class="k">Outstanding</div><div class="v">${money(t.outstanding_cents)}</div></div>
        <div class="metric"><div class="k">Retainage open</div><div class="v">${money(t.retainage_cents)}</div></div>
      </div>
      <div class="metrics">
        <div class="metric"><div class="k">Blocked jobs</div><div class="v">${t.blocked_jobs}</div></div>
        <div class="metric"><div class="k">Active jobs</div><div class="v">${state.jobs.filter(j => !j.closed).length}</div></div>
      </div>
      ${actionJobs.length ? `
        <div class="card action-queue">
          <h2>Needs action</h2>
          ${actionJobs.map(j => `
            <div class="action-item">
              <div><strong>${j.name}</strong> · <span class="next-action">${nextStep(j)}</span></div>
              <button class="btn small" data-action="open-job" data-id="${j.id}">Open</button>
            </div>`).join("")}
        </div>` : ""}
      <div class="stages">
        <div class="stage clickable ${dashFilter === "all" ? "active" : ""}" data-action="filter" data-filter="all">All (incl. completed this month)</div>
        <div class="stage clickable ${dashFilter === "complete" ? "active" : ""}" data-action="filter" data-filter="complete">Completed this month</div>
        <div class="stage clickable ${dashFilter === "draft" ? "active" : ""}" data-action="filter" data-filter="draft">Draft open</div>
        <div class="stage clickable ${dashFilter === "sent" ? "active" : ""}" data-action="filter" data-filter="sent">Pay App Sent</div>
        <div class="stage clickable ${dashFilter === "waiver" ? "active" : ""}" data-action="filter" data-filter="waiver">Waiver due</div>
        <div class="stage clickable ${dashFilter === "blocked" ? "active" : ""}" data-action="filter" data-filter="blocked">Blocked</div>
      </div>
      <div class="table-wrap">
        <table class="sortable">
          <tr>
            <th data-action="sort" data-sort="name">Job ${dashSort === "name" ? (dashSortDir > 0 ? "↑" : "↓") : ""}</th>
            <th>Customer</th>
            <th data-action="sort" data-sort="contract">Contract total ${dashSort === "contract" ? (dashSortDir > 0 ? "↑" : "↓") : ""}</th>
            <th>Approved COs</th>
            <th data-action="sort" data-sort="billed">Billed ${dashSort === "billed" ? (dashSortDir > 0 ? "↑" : "↓") : ""}</th>
            <th data-action="sort" data-sort="collected">Collected ${dashSort === "collected" ? (dashSortDir > 0 ? "↑" : "↓") : ""}</th>
            <th data-action="sort" data-sort="outstanding">Outstanding ${dashSort === "outstanding" ? (dashSortDir > 0 ? "↑" : "↓") : ""}</th>
            <th data-action="sort" data-sort="unbilled">Unbilled ${dashSort === "unbilled" ? (dashSortDir > 0 ? "↑" : "↓") : ""}</th>
            <th data-action="sort" data-sort="retainage">Retainage ${dashSort === "retainage" ? (dashSortDir > 0 ? "↑" : "↓") : ""}</th>
            <th>Status</th>
            <th>Next</th>
          </tr>
          ${jobs.map(j => {
            const m = j.money;
            return `<tr>
              <td><a href="#" data-action="open-job" data-id="${j.id}">${j.name}</a></td>
              <td>${j.payer.name}</td>
              <td>${money(m.contract_total_cents)}</td>
              <td>${money(m.approved_co_cents)}</td>
              <td>${money(m.billed_cents)}</td>
              <td>${money(m.collected_cents)}</td>
              <td>${money(m.outstanding_cents)}</td>
              <td>${money(m.unbilled_completed_cents)}</td>
              <td>${money(m.retainage_open_cents)}</td>
              <td><span class="pill ${j.blockers.length ? "red" : (j.closed ? "green" : "amber")}">${j.status_label}</span></td>
              <td class="next-action">${nextStep(j)}</td>
            </tr>`;
          }).join("")}
        </table>
      </div>
    `;
  },
  jobs() {
    return `
      <div class="top">
        <h1>Jobs</h1>
        <button class="btn" data-action="toggle-form" data-form="newjob">${formOpen.newjob ? "Cancel" : "New job"}</button>
      </div>
      <p class="sub">Click a job for its money file, packet, and actions.</p>
      ${formOpen.newjob ? `
        <div class="card">
          <h2>New job</h2>
          <p class="sub">Required fields are marked. Saving creates the job record. A packet is created later, with a pay app.</p>
          ${jobFormError ? `<div class="form-error">${esc(jobFormError)}</div>` : ""}
          <div class="form-section">Job</div>
          <div class="form-grid">
            <div><label>Job name <span class="req">*</span></label><input id="nj-name" required /></div>
            <div><label>Address <span class="req">*</span></label><input id="nj-address" required /></div>
            <div><label>Contract number</label><input id="nj-contract" /></div>
            <div><label>Contract amount ($) <span class="req">*</span></label><input id="nj-amount" type="number" step="0.01" min="0.01" required /></div>
            <div><label>Retainage %</label><input id="nj-retainage" type="number" min="0" max="20" value="0" /></div>
          </div>
          <div class="form-section">Customer</div>
          <div class="form-grid">
            <div><label>Customer <span class="req">*</span></label><input id="nj-customer" required /></div>
            <div><label>Role</label><select id="nj-role"><option>Owner</option><option>GC</option></select></div>
            <div><label>Approver <span class="req">*</span></label><input id="nj-approver" required /></div>
            <div><label>AP email <span class="req">*</span></label><input id="nj-email" type="email" required /></div>
            <div><label>Phone</label><input id="nj-phone" /></div>
          </div>
          <div class="form-section">Job record</div>
          <div class="form-grid">
            <div class="span-2">
              <label>Notes for the job record</label>
              <textarea id="nj-packet" placeholder="Contract notes, what this customer usually wants, anything to keep with the job"></textarea>
              <p class="sub">Stored on the job. Attach these to a pay app packet when you draft one.</p>
            </div>
          </div>
          <button class="btn" data-action="create-job">Save job</button>
        </div>` : ""}
      ${state.jobs.length ? `<div class="table-wrap">
        <table>
          <tr><th>Code</th><th>Job</th><th>Customer</th><th>Status</th><th>Outstanding</th><th>Unbilled</th><th>Retainage</th><th>Next</th></tr>
          ${state.jobs.map(j => `
            <tr>
              <td>${j.code}</td>
              <td><a href="#" data-action="open-job" data-id="${j.id}">${j.name}</a></td>
              <td>${j.payer ? j.payer.name : "—"}</td>
              <td>${j.status_label}</td>
              <td>${money(j.money.outstanding_cents)}</td>
              <td>${money(j.money.unbilled_completed_cents)}</td>
              <td>${money(j.money.retainage_open_cents)}</td>
              <td class="next-action">${nextStep(j)}</td>
            </tr>`).join("")}
        </table>
      </div>` : emptyState("No jobs yet", "Create a job. Its record holds notes and files until a pay app needs them.", `<button class="btn" data-action="toggle-form" data-form="newjob">New job</button>`)}
    `;
  },
  job() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return "<p>Job not found</p>";
    const m = j.money;
    const blocked = j.blockers && j.blockers.length;
    const canClose = !j.closed && (j.close_blockers || []).length === 0;
    const hasDraft = j.invoices.some(i => ["draft", "awaiting_approval"].includes(i.status));
    const openDraft = j.invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
    const pencilOut = openDraft && openDraft.pencil_status === "sent";
    const openForPay = j.invoices.filter(i => ["sent", "approved", "partial"].includes(i.status));
    const isDemo = me && me.company_id === DEMO_COMPANY_ID;
    const closeout = j.closeout_packet || [];
    const closeoutMissing = closeout.filter(p => p.required && !p.present).length;
    return `
      ${backButton("jobs", "Jobs")}
      <h1>${j.name}</h1>
      <p class="sub">${j.address} · ${j.payer.name} · ${j.work_status}${j.done_notes ? " · " + j.done_notes : ""}</p>
      <div class="next-step"><strong>Next:</strong> ${nextStep(j)}</div>
      ${blocked ? `<div class="card" style="border-color:var(--red);background:#fff5f0">
        <strong class="blocker">Blocking send:</strong> ${j.blockers.join(" · ")}
        <div class="form-inline">
          <input id="learn-reason" placeholder="Teach playbook this rejection reason" style="flex:1">
          <button class="btn small secondary" data-action="learn" data-id="${j.id}">Teach playbook</button>
        </div>
      </div>` : ""}
      <div class="detail-grid">
        <div class="card">
          <h2>Money file</h2>
          <div class="money-row"><span class="label">Contract</span><span>${money(m.contract_cents)}</span></div>
          <div class="money-row"><span class="label">Approved COs</span><span>${money(m.approved_co_cents)}</span></div>
          <div class="money-row"><span class="label">Pending COs</span><span>${money(m.pending_co_cents)}</span></div>
          <div class="money-row"><span class="label">Contract total</span><span>${money(m.contract_total_cents)}</span></div>
          <div class="money-row"><span class="label">Billed</span><span>${money(m.billed_cents)}</span></div>
          <div class="money-row"><span class="label">Collected</span><span>${money(m.collected_cents)}</span></div>
          <div class="money-row"><span class="label">Outstanding</span><span>${money(m.outstanding_cents)}</span></div>
          <div class="money-row"><span class="label">Unbilled completed</span><span>${money(m.unbilled_completed_cents)}</span></div>
          <div class="money-row"><span class="label">Retainage held</span><span>${money(m.retainage_held_cents)}${j.retainage_pct ? " · " + j.retainage_pct + "%" : ""}</span></div>
          <div class="money-row"><span class="label">Retainage released</span><span>${money(m.retainage_released_cents)}</span></div>
          <div class="money-row"><span class="label">Retainage open</span><span>${money(m.retainage_open_cents)}</span></div>
        </div>
        <div class="card">
          <h2>Actions</h2>
          ${j.closed ? `<p class="sub">This job is closed. Its money record is read-only; you can still add notes to the job record.</p>` : `
          <div class="actions-vert">
            ${j.work_status !== "complete" ? `<div class="action-row"><button class="btn" data-action="toggle-form" data-form="progress">Progress pay app</button><div><div class="blurb">Bill completed-to-date without marking the job done.</div></div></div>` : ""}
            ${j.work_status !== "complete" ? `<div class="action-row"><button class="btn secondary" data-action="toggle-form" data-form="done">Mark done</button><div><div class="blurb">Record completion and draft the final pay app.</div></div></div>` : ""}
            <div class="action-row"><button class="btn secondary" data-action="draft" data-id="${j.id}">Draft pay app</button><div class="blurb">Create a same-week pay app from unbilled completed work.</div></div>
            <div class="action-row"><button class="btn" data-action="send" data-id="${j.id}" ${blocked || !hasDraft ? "disabled" : ""}>Approve & send</button><div class="blurb">Human gate. Sends only when the packet is clean.</div></div>
            ${hasDraft && !pencilOut ? `<div class="action-row"><button class="btn secondary" data-action="pencil" data-id="${j.id}">Send pencil copy</button><div class="blurb">Send the draft to the payer to agree quantities and change orders before the formal pay app.</div></div>` : ""}
            ${pencilOut ? `<div class="action-row"><button class="btn secondary" data-action="pencil-agreed" data-id="${j.id}">Payer agreed to pencil</button><button class="btn secondary" data-action="pencil-changes" data-id="${j.id}">Payer wants changes</button></div>` : ""}
            ${hasDraft ? `<div class="action-row"><button class="btn secondary" data-action="void-draft" data-id="${j.id}">Void draft</button><div class="blurb">Throw away the open draft. Its work goes back to unbilled.</div></div>` : ""}
            <div class="action-row"><button class="btn secondary" data-action="follow-up" data-id="${j.id}">Follow up</button><div class="blurb">Log a follow-up on a sent pay app using the playbook cadence.</div></div>
            <div class="action-row"><button class="btn secondary" data-action="toggle-form" data-form="match">Match payment</button><div class="blurb">Record cash received (dollars). Opens the waiver gate when paid in full.</div></div>
            ${isDemo ? `<div class="action-row"><button class="btn secondary" data-action="import-qbo" data-id="${j.id}">Import QBO sample</button><div class="blurb">Demo only: a stand-in for a QuickBooks payment read.</div></div>` : ""}
            ${j.waiver_due && !j.waiver_sent ? `<div class="action-row"><button class="btn" data-action="waiver" data-id="${j.id}">Send waiver</button><div class="blurb">Human gate. One waiver per paid pay app. The waiver document comes from you.</div></div>` : ""}
            ${m.retainage_open_cents > 0 ? `<div class="action-row"><button class="btn secondary" data-action="retainage" data-id="${j.id}">Chase retainage</button><div class="blurb">Request release once the closeout packet is complete.</div></div>` : ""}
            ${m.retainage_open_cents > 0 ? `<div class="action-row"><button class="btn secondary" data-action="toggle-form" data-form="retpay">Record retainage paid</button><div class="blurb">Record retainage the payer released.</div></div>` : ""}
            <div class="action-row"><button class="btn secondary" data-action="goto-co" data-id="${j.id}">Add change order</button><div class="blurb">Opens Change Orders to create a new CO for this job.</div></div>
            <div class="action-row"><button class="btn secondary" data-action="goto-packet" data-id="${j.id}">Add evidence to packet</button><div class="blurb">Opens this job’s packet to attach daily records and files.</div></div>
            ${canClose ? `<div class="action-row"><button class="btn" data-action="close" data-id="${j.id}">Close for record</button><div class="blurb">Move a fully settled job into Job history.</div></div>` : ""}
          </div>`}
          ${blocked ? `<div class="gate-reason">Approve & send is disabled until blockers are cleared.</div>` : ""}
          ${formOpen.progress ? `<div class="form-inline"><input id="progress-amt" type="number" step="0.01" min="0.01" placeholder="Completed to date $"><input id="progress-notes" placeholder="What was completed" style="flex:1"><button class="btn small" data-action="progress" data-id="${j.id}">Draft progress pay app</button></div>` : ""}
          ${formOpen.done ? `<div class="form-inline"><input id="done-notes" placeholder="Completion notes" style="flex:1"><button class="btn small" data-action="done" data-id="${j.id}">Confirm done</button></div>` : ""}
          ${formOpen.match ? (openForPay.length ? `<div class="form-inline"><select id="match-inv">${openForPay.map(i => `<option value="${i.id}">${i.number} · ${money(i.amount_cents)} · ${i.status}</option>`).join("")}</select><input id="match-amt" type="number" step="0.01" min="0.01" placeholder="Amount in dollars"><input id="match-ref" placeholder="Reference (check no., QBO id)"><button class="btn small" data-action="match" data-id="${j.id}">Match</button></div>` : `<p class="sub">No sent pay app is waiting for payment.</p>`) : ""}
          ${formOpen.retpay ? `<div class="form-inline"><input id="retpay-amt" type="number" step="0.01" min="0.01" placeholder="Retainage received $"><input id="retpay-ref" placeholder="Reference"><button class="btn small" data-action="retainage-paid" data-id="${j.id}">Record</button></div>` : ""}
          ${!j.closed && !canClose && j.work_status === "complete" ? `<p class="sub">Close for record is available when: ${(j.close_blockers || []).join(" ")}</p>` : ""}
          <p class="sub">Send, waiver, retainage, and close require explicit approval.</p>
        </div>
      </div>
      <div class="card">
        <h2>Job record</h2>
        <p class="sub">Anything associated with this job. Attach a record to a pay app packet when you draft one.</p>
        ${j.closed ? "" : `<div class="dropzone small" data-job="${j.id}">
          <strong>Drop photos, PDFs, saved emails or spreadsheets here</strong> to add them to this job.
          <span class="sub">Or <button class="linkish" data-action="pick-files" data-job="${j.id}">choose files</button>, or paste a screenshot anywhere on this page. Emails and texts: use the <button class="linkish" data-v-link="inbox" onclick="nav('inbox')">Inbox</button>.</span>
        </div>`}
        ${(j.records || []).length ? (j.records || []).slice().reverse().map(r => `<div class="list-item"><span class="pill">${RECORD_KINDS[r.kind] || r.kind}</span> ${r.label}${r.note ? ` <span class="sub">— ${r.note.length > 240 ? r.note.slice(0, 240) + "…" : r.note}</span>` : ""}${recordFiles(r) ? `<div class="sub">${recordFiles(r)}</div>` : ""}${r.source ? `<div class="sub">${r.source} · ${(r.added_at || "").slice(0, 10)}</div>` : ""}</div>`).join("") : emptyState("Nothing in the record yet", "Drop files above, add a note below, or bring in emails and texts from the Inbox.")}
        <div class="form-inline">
          <input id="rec-label" placeholder="Label" />
          <input id="rec-note" placeholder="Note" />
          <select id="rec-kind">${Object.entries(RECORD_KINDS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
          <button class="btn small" data-action="add-record" data-id="${j.id}">Add to record</button>
        </div>
      </div>
      <div class="card">
        <h2>Pay apps and packets</h2>
        ${j.invoices.length ? j.invoices.map(inv => {
          const pkt = inv.packet || j.packet || [];
          const missing = pkt.filter(p => p.required && !p.present).length;
          const pencil = inv.pencil_status ? ` · pencil ${inv.pencil_status === "sent" ? "with payer" : inv.pencil_status}${inv.pencil_note ? ": " + inv.pencil_note : ""}` : "";
          return `<div class="list-item">${inv.number} · ${money(inv.amount_cents)} · ${inv.status}${pencil} · ${missing ? missing + " packet items missing" : "packet complete"} <button class="btn small secondary" data-action="open-payapp" data-job="${j.id}" data-inv="${inv.id}">Open</button></div>`;
        }).join("") : emptyState("No pay apps yet", "Draft one after the job is marked done. The packet is created with it.")}
        ${closeout.length ? `<div class="list-item">Closeout packet (gates the retainage release) · ${closeoutMissing ? closeoutMissing + " items missing" : "complete"} <button class="btn small secondary" data-action="open-packet" data-job="${j.id}" data-inv="closeout">Open</button></div>` : ""}
      </div>
      <div class="card">
        <h2>Change orders</h2>
        ${j.change_orders.length ? j.change_orders.map(co => `
          <div class="list-item">CO ${co.number} — ${co.title} · ${money(co.amount_cents)} · <span class="pill ${co.status === "approved" ? "green" : "amber"}">${co.status}</span>${co.included_in_billing ? " · in billing" : ""}
            ${co.status === "pending" && !j.closed ? `<button class="btn small" data-action="approve-co" data-id="${j.id}" data-co="${co.id}">Approve</button>` : ""}
            ${co.status === "pending" && !j.closed ? `<button class="btn small secondary" data-action="withdraw-co" data-id="${j.id}" data-co="${co.id}">Withdraw</button>` : ""}
            ${co.status === "pending" && !co.included_in_billing && !j.closed && !hasDraft ? `<button class="btn small secondary" data-action="include-co" data-id="${j.id}" data-co="${co.id}" title="Bill it on the next pay app; send stays blocked until it is signed">Put on next pay app</button>` : ""}
          </div>`).join("") : emptyState("No change orders", "Add one if the scope changed.")}
      </div>
      <div class="card">
        <h2>Invoices / pay apps</h2>
        ${j.invoices.length ? j.invoices.map(inv => `
          <div class="list-item">${inv.number} · ${money(inv.amount_cents)} · <span class="pill ${inv.status === "paid" ? "green" : inv.status === "sent" ? "amber" : ""}">${inv.status}</span>${inv.approved_by ? " · approved by " + inv.approved_by : ""}</div>`).join("") : emptyState("No pay apps yet", "Draft one after the job is marked done.")}
      </div>
    `;
  },
  daily() {
    const openJobs = state.jobs.filter(j => !j.closed);
    const records = state.daily_records || [];
    return `
      <h1>Daily Record</h1>
      <p class="sub">Saved on the job record. Attach it to a pay app packet when you draft one.</p>
      <div class="card">
        <h2>New record</h2>
        <div class="form-inline">
          <select id="daily-job">
            <option value="">Select job…</option>
            ${openJobs.map(j => `<option value="${j.id}">${j.name}</option>`).join("")}
          </select>
          <input id="daily-notes" placeholder="What happened today" style="flex:1">
          <button class="btn" data-action="daily-save">Save to job record</button>
        </div>
      </div>
      <div class="card">
        <h2>Recent records</h2>
        ${records.length ? records.slice().reverse().map(r => {
          const j = state.jobs.find(x => x.id === r.job_id);
          return `<div class="list-item">${r.at.slice(0,10)} · ${j ? j.name : r.job_id} · ${r.notes} ${r.for_history ? "· history" : ""}</div>`;
        }).join("") : emptyState("No daily records", "Save one above. It lands in the job record, not a packet.")}
      </div>
    `;
  },
  packetDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return "<p>Select a pay app from Packets.</p>";
    const isCloseout = currentDetailId === "closeout";
    const inv = isCloseout ? null : (j.invoices.find(i => i.id === currentDetailId) || [...j.invoices].reverse().find(i => i.packet) || j.invoices[j.invoices.length - 1]);
    const packet = isCloseout ? (j.closeout_packet || []) : ((inv && inv.packet) || j.packet || []);
    const jobRecords = j.records || [];
    if (!inv && !packet.length) return backButton("job", "Back") + emptyState("No packet yet", "Draft a pay app. The packet is created with it, from this payer's playbook. Until then, add notes and files to the job record.", `<button class="btn" data-action="open-job" data-id="${j.id}">Back to ${j.name}</button>`);
    return `
      ${backButton("packets", "Packets")}
      <h1>${isCloseout ? j.name + " closeout" : (inv ? inv.number : j.name)} packet</h1>
      <p class="sub">${j.name}${inv ? " · " + money(inv.amount_cents) + " · " + inv.status + " · tied to this pay app" : ""}${isCloseout ? " · must be complete before asking for retainage" : ""}</p>
      <div class="card">
        <h2>Required items</h2>
        ${packet.length ? packet.map(p => `
          <div class="list-item">
            ${p.present ? "✓" : "✗"} ${p.label}
            ${!p.present ? `<span class="field-error">${p.blocker}</span>` : ""}
            ${!p.present ? `<button class="btn small pop" data-action="resolve" data-id="${j.id}" data-item="${p.id}">Mark present…</button>` : ""}
            ${p.present && p.present_reason ? `<div class="sub">Marked present: ${p.present_reason}</div>` : ""}
            ${(p.attachments || []).map(a => `<div class="sub">Attached: ${a.name}</div>`).join("")}
            ${jobRecords.length ? `<div class="form-inline"><select id="attach-rec-${p.id}">${jobRecords.map(r => `<option value="${r.id}">${r.label}</option>`).join("")}</select><button class="btn small" data-action="attach-record" data-id="${j.id}" data-item="${p.id}">Attach from job record</button></div>` : ""}
          </div>`).join("") : emptyState("No packet items", "This pay app has no packet.")}
      </div>
      <div class="card">
        <h2>Job record</h2>
        ${jobRecords.length ? jobRecords.map(r => `<div class="list-item">${r.kind}: ${r.label}</div>`).join("") : emptyState("Nothing in the job record", "Add notes or files on the job, then attach them here.")}
      </div>
    `;
  },
  coDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return "<p>Job not found</p>";
    const co = j.change_orders.find(c => c.id === currentDetailId) || j.change_orders[j.change_orders.length - 1];
    if (!co) {
      return `
        <div class="top"><button class="back" onclick="nav('cos')">← Change Orders</button></div>
        <h1>New change order</h1>
        <div class="form-inline">
          <input id="co-title" placeholder="Title">
          <input id="co-amt" type="number" step="0.01" placeholder="Amount in dollars">
          <button class="btn" data-action="add-co" data-id="${j.id}">Create</button>
        </div>`;
    }
    return `
      <div class="top"><button class="back" onclick="nav('cos')">← Change Orders</button></div>
      <h1>CO ${co.number} — ${co.title}</h1>
      <p class="sub">${j.name} · ${money(co.amount_cents)}</p>
      <div class="card">
        <h2>Summary</h2>
        <p>${co.description || "No description yet."}</p>
        <p><strong>Status:</strong> ${co.status} · Waiting on: ${co.waiting_on || (co.status === "pending" ? "Signature" : "Nothing")}</p>
        <p><strong>In current billing:</strong> ${co.included_in_billing ? "Yes" : "No"}</p>
      </div>
      <div class="card">
        <h2>Attachments</h2>
        ${(co.attachments || []).map(a => `<div class="list-item">${a.name}</div>`).join("") || "<p>None</p>"}
        <p class="sub">File uploads are not built yet. Keep the signed change order in the job record for now.</p>
      </div>
      <div class="card">
        <h2>Packet</h2>
        <button class="btn secondary" data-action="open-packet" data-job="${j.id}">Open pay app packet</button>
        <p class="sub">Copy supporting items from the packet into this change order as needed.</p>
      </div>
    `;
  },
  payappDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return "<p>Not found</p>";
    const inv = j.invoices.find(i => i.id === currentDetailId) || j.invoices[j.invoices.length - 1];
    if (!inv) return "<p>No pay app</p>";
    const pays = j.payments.filter(p => p.invoice_id === inv.id);
    return `
      <div class="top"><button class="back" onclick="nav('payapps')">← Pay Apps</button></div>
      <h1>${inv.number}</h1>
      <p class="sub">${j.name} · ${money(inv.amount_cents)} · ${inv.status}</p>
      <div class="card">
        <h2>Lines</h2>
        ${(inv.lines || []).map(l => `<div class="money-row"><span>${l.label}</span><span>${money(l.amount_cents)}</span></div>`).join("")}
      </div>
      <div class="card">
        <h2>Packet</h2>
        <p class="sub">This packet belongs to ${inv.number}.</p>
        ${(inv.packet || j.packet || []).map(p => `<div class="list-item">${p.present ? "✓" : "✗"} ${p.label}</div>`).join("") || "<p>No packet on this pay app.</p>"}
        <button class="btn secondary small" data-action="open-packet" data-job="${j.id}" data-inv="${inv.id}">Open packet</button>
      </div>
      <div class="card">
        <h2>Payments matched</h2>
        ${pays.length ? pays.map(p => `<div class="list-item">${money(p.amount_cents)} · ${p.reference} · ${p.matched_at.slice(0,10)}</div>`).join("") : "<p>None yet</p>"}
      </div>
      <div class="card">
        <h2>Approval</h2>
        <p>Approved by: ${inv.approved_by || "—"} · Sent: ${(inv.sent_at || "—").slice(0,16)}</p>
      </div>
    `;
  },
  waiverDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return "<p>Not found</p>";
    return `
      <div class="top"><button class="back" onclick="nav('waivers')">← Waivers</button></div>
      <h1>Waiver — ${j.name}</h1>
      <div class="card">
        <h2>Kind</h2>
        <p>${j.waiver_kind || "Conditional waiver on progress payment"}</p>
      </div>
      <div class="card">
        <h2>Standard / contract requirement</h2>
        <p>${j.waiver_standard || (j.payer.packet || "Per payer playbook packet requirements")}</p>
      </div>
      <div class="card">
        <h2>Status</h2>
        <p>${j.waiver_sent ? "Sent " + (j.waiver_sent_at || "").slice(0,10) : "Due — not yet sent"}</p>
        ${!j.waiver_sent && j.waiver_due ? `<button class="btn" data-action="waiver" data-id="${j.id}">Approve & send</button>` : ""}
      </div>
    `;
  },
  "packet-detail": function() { return views.packetDetail(); },
  "co-detail": function() { return views.coDetail(); },
  "payapp-detail": function() { return views.payappDetail(); },
  "waiver-detail": function() { return views.waiverDetail(); },
  history() {
    const closed = state.jobs.filter(j => j.closed);
    const totalCollected = closed.reduce((s, j) => s + j.money.collected_cents, 0);
    return `
      <h1>Job history</h1>
      <p class="sub">Closed-out jobs for the contractor’s permanent records.</p>
      <div class="summary-row">
        <div class="metric"><div class="k">Closed jobs</div><div class="v">${closed.length}</div></div>
        <div class="metric"><div class="k">Collected on closed</div><div class="v">${money(totalCollected)}</div></div>
      </div>
      <div class="table-wrap">
        <table>
          <tr><th>Job</th><th>Customer</th><th>Contract total</th><th>Collected</th><th>Closed</th><th>Notes</th></tr>
          ${closed.map(j => `
            <tr>
              <td><a href="#" data-action="open-job" data-id="${j.id}">${j.name}</a></td>
              <td>${j.payer.name}</td>
              <td>${money(j.money.contract_total_cents)}</td>
              <td>${money(j.money.collected_cents)}</td>
              <td>${j.closed_at ? j.closed_at.slice(0,10) : (j.completed_at || "").slice(0,10) || "—"}</td>
              <td>${j.done_notes || j.status_label}</td>
            </tr>`).join("") || "<tr><td colspan=6>No closed jobs yet. Finish the money path and use Close for record.</td></tr>"}
        </table>
      </div>
    `;
  },
  calendar() {
    const first = new Date(calYear, calMonth, 1);
    const startDow = first.getDay();
    const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
    const eventsByDay = {};
    const add = (dateStr, label, kind = "", jobId = "") => {
      if (!dateStr || dateStr.length < 10) return;
      const d = dateStr.slice(0, 10);
      if (!eventsByDay[d]) eventsByDay[d] = [];
      eventsByDay[d].push({ label, kind, jobId });
    };
    const today = new Date();
    state.jobs.filter(j => calJobFilter === "all" || j.id === calJobFilter).forEach(j => {
      if (j.completed_at) add(j.completed_at, `${j.code} work complete`, "paid", j.id);
      j.invoices.forEach(inv => {
        if (inv.sent_at) add(inv.sent_at, `${inv.number} sent`, "", j.id);
        if (inv.status === "sent" && inv.sent_at) {
          const due = new Date(inv.sent_at);
          due.setDate(due.getDate() + (j.payer.follow_up_days || 7));
          const overdue = due < today;
          add(due.toISOString().slice(0, 10), `${j.code} follow-up`, overdue ? "overdue" : "", j.id);
          const appr = new Date(inv.sent_at);
          appr.setDate(appr.getDate() + (j.payer.usual_approval_days || 14));
          add(appr.toISOString().slice(0, 10), `${j.code} expected approval`, "", j.id);
        }
      });
      j.payments.forEach(p => add(p.matched_at, `${j.code} payment matched`, "paid", j.id));
      if (j.waiver_sent_at) add(j.waiver_sent_at, `${j.code} waiver sent`, "waiver", j.id);
      if (j.waiver_due && !j.waiver_sent) add(today.toISOString().slice(0, 10), `${j.code} waiver ready`, "waiver", j.id);
      if (j.closed_at) add(j.closed_at, `${j.code} closed`, "paid", j.id);
    });
    const cells = [];
    for (let i = 0; i < startDow; i++) cells.push({ other: true });
    for (let d = 1; d <= daysInMonth; d++) {
      const ds = `${calYear}-${String(calMonth + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
      const isToday = ds === today.toISOString().slice(0, 10);
      cells.push({ day: d, ds, events: eventsByDay[ds] || [], today: isToday });
    }
    while (cells.length % 7) cells.push({ other: true });
    const monthName = first.toLocaleString("default", { month: "long" });
    const thisWeek = Object.entries(eventsByDay).filter(([d]) => {
      const dt = new Date(d);
      const diff = (dt - today) / 86400000;
      return diff >= -1 && diff <= 7;
    }).flatMap(([d, evs]) => evs.map(e => ({ ...e, date: d })));
    const selected = calSelectedDay && eventsByDay[calSelectedDay] ? eventsByDay[calSelectedDay] : [];
    return `
      <h1>Calendar</h1>
      <p class="sub">Deadlines, follow-ups, expected approvals, payments, and contractor action dates.</p>
      <div class="filter-bar">
        <select id="cal-filter" data-filter-for="calendar" aria-label="Job">
          <option value="all">All jobs</option>
          ${state.jobs.map(j => `<option value="${j.id}" ${calJobFilter === j.id ? "selected" : ""}>${j.name}</option>`).join("")}
        </select>
      </div>
      <details class="card subscribe">
        <summary><strong>Put these dates on your phone (Google Calendar, Apple, Outlook)</strong></summary>
        <p class="sub">Copy this private address. In Google Calendar on a computer: Other calendars, +, From URL, paste, Add calendar. It then shows on your phone. Calendar apps refresh subscribed calendars on their own schedule, often every few hours, so new dates are not instant.</p>
        <div class="copy-row"><code id="cal-url">Loading…</code><button class="btn small secondary" data-action="copy" data-target="cal-url">Copy</button><button class="btn small secondary" data-action="rotate-token" data-kind="calendar">Make a new address</button></div>
        <p class="sub">Anyone with this address can see the dates and job names. Make a new address if it was shared by mistake.</p>
      </details>
      <div class="legend">
        <span><i style="background:#e5f2ea"></i> Complete / paid</span>
        <span><i style="background:#f8efd8"></i> Waiver</span>
        <span><i style="background:#f8e8e4"></i> Overdue</span>
        <span><i style="background:var(--copper-soft)"></i> Other</span>
      </div>
      ${thisWeek.length ? `<div class="due-strip">${thisWeek.slice(0, 6).map(e => `<div class="due-item ${e.kind === "overdue" ? "overdue" : ""}">${e.date.slice(5)} · ${e.label}</div>`).join("")}</div>` : ""}
      <div class="cal-header">
        <div>
          <button class="btn secondary small" data-action="cal-prev">←</button>
          <strong style="margin:0 12px">${monthName} ${calYear}</strong>
          <button class="btn secondary small" data-action="cal-next">→</button>
        </div>
        <button class="btn secondary small" data-action="cal-today">Today</button>
      </div>
      <div class="cal-grid">
        ${["Sun","Mon","Tue","Wed","Thu","Fri","Sat"].map(d => `<div class="cal-dow">${d}</div>`).join("")}
        ${cells.map(c => c.other
          ? `<div class="cal-day other"></div>`
          : `<div class="cal-day ${c.today ? "today" : ""}" data-action="cal-day" data-day="${c.ds}" style="cursor:pointer">
               <div class="num">${c.day}</div>
               ${(() => {
                 const evs = c.events || [];
                 if (evs.length <= 3) return evs.map(e => `<div class="cal-event ${e.kind}">${e.label}</div>`).join("");
                 const cats = {};
                 evs.forEach(e => {
                   const k = e.kind || "other";
                   cats[k] = (cats[k] || 0) + 1;
                 });
                 const labels = { paid: "Complete / paid", waiver: "Waivers", overdue: "Overdue", "": "Other", other: "Other" };
                 return Object.entries(cats).map(([k, n]) => `<div class="cal-event ${k}">${n} ${labels[k] || k}</div>`).join("");
               })()}
             </div>`
        ).join("")}
      </div>
      ${calSelectedDay ? `
        <div class="card day-detail">
          <h2>${calSelectedDay}</h2>
          ${selected.length ? selected.map(e => `<div class="list-item">${e.label} ${e.jobId ? `<button class="btn small secondary" data-action="open-job" data-id="${e.jobId}">Open job</button>` : ""}</div>`).join("") : "<p>No events this day.</p>"}
        </div>` : ""}
    `;
  },
  payapps() {
    let apps = [];
    state.jobs.forEach(j => j.invoices.forEach(inv => apps.push({ ...inv, job: j.name, jobId: j.id, blockers: j.blockers })));
    if (payappFilter !== "all") apps = apps.filter(a => a.status === payappFilter || (payappFilter === "draft" && ["draft", "awaiting_approval"].includes(a.status)));
    return `
      <h1>Pay Apps</h1>
      <div class="filter-bar">
        <button class="${payappFilter === "all" ? "btn" : "btn secondary"} small" data-action="payapp-filter" data-filter="all">All</button>
        <button class="${payappFilter === "draft" ? "btn" : "btn secondary"} small" data-action="payapp-filter" data-filter="draft">Draft</button>
        <button class="${payappFilter === "sent" ? "btn" : "btn secondary"} small" data-action="payapp-filter" data-filter="sent">Sent</button>
        <button class="${payappFilter === "paid" ? "btn" : "btn secondary"} small" data-action="payapp-filter" data-filter="paid">Paid</button>
      </div>
      <div class="table-wrap">
        <table>
          <tr><th>Number</th><th>Job</th><th>Amount</th><th>Status</th><th>Blockers</th></tr>
          ${apps.map(a => `<tr>
            <td><a href="#" data-action="open-payapp" data-job="${a.jobId}" data-inv="${a.id}">${a.number}</a></td>
            <td><a href="#" data-action="open-job" data-id="${a.jobId}">${a.job}</a></td>
            <td>${money(a.amount_cents)}</td>
            <td><span class="pill ${a.status === "paid" ? "green" : a.status === "sent" ? "amber" : ""}">${a.status}</span></td>
            <td>${a.blockers.join("; ") || "—"}</td>
          </tr>`).join("")}
        </table>
      </div>
      ${apps.length ? "" : emptyState("No pay apps", "Draft one from a job. The packet is created with the pay app.")}
    `;
  },
  packets() {
    return `
      <h1>Packets</h1>
      <p class="sub">A packet belongs to a pay app. Click a row to open that pay app’s packet.</p>
      <div class="table-wrap">
        <table>
          <tr><th>Pay app</th><th>Job</th><th>Missing</th><th>Status</th></tr>
          ${(() => {
            const rows = [];
            state.jobs.forEach(j => j.invoices.forEach(inv => {
              const pkt = inv.packet || j.packet || [];
              if (!pkt.length && !inv.packet) return;
              const missing = pkt.filter(p => p.required && !p.present).length;
              rows.push(`<tr>
                <td><a href="#" data-action="open-packet" data-job="${j.id}" data-inv="${inv.id}">${inv.number}</a></td>
                <td>${j.name}</td>
                <td>${missing ? `<span class="pill red">${missing}</span>` : `<span class="pill green">complete</span>`}</td>
                <td>${inv.status}</td>
              </tr>`);
            }));
            return rows.join("");
          })()}
        </table>
      </div>
      ${state.jobs.some(j => j.invoices.some(inv => inv.packet || (j.packet || []).length)) ? "" : emptyState("No packets yet", "Draft a pay app. The packet is created with it and stays tied to that pay app.")}
    `;
  },
  cos() {
    const cos = [];
    state.jobs.forEach(j => j.change_orders.forEach(co => cos.push({ ...co, job: j.name, jobId: j.id })));
    return `
      <h1>Change Orders</h1>
      <p class="sub">Create and track change orders. Click a row for the full record.</p>
      <div class="card">
        <h2>New change order</h2>
        <div class="form-inline">
          <select id="new-co-job">
            ${state.jobs.filter(j => !j.closed).map(j => `<option value="${j.id}" ${coPreselectJob === j.id ? "selected" : ""}>${j.name}</option>`).join("")}
          </select>
          <input id="co-title" placeholder="Title">
          <input id="co-amt" type="number" step="0.01" placeholder="Amount in dollars">
          <button class="btn" data-action="add-co-from-list">Create</button>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <tr><th>Job</th><th>CO</th><th>Summary</th><th>Amount</th><th>Status / waiting on</th><th>In billing</th></tr>
          ${cos.map(c => `<tr>
            <td><a href="#" data-action="open-co" data-job="${c.jobId}" data-co="${c.id}">${c.job}</a></td>
            <td>${c.number}</td>
            <td>${c.title}${c.description ? " — " + c.description.slice(0, 40) : ""}</td>
            <td>${money(c.amount_cents)}</td>
            <td><span class="pill ${c.status === "approved" ? "green" : "amber"}">${c.status}</span> · ${c.waiting_on || (c.status === "pending" ? "Signature" : "—")}</td>
            <td>${c.included_in_billing ? "Yes" : "No"}</td>
          </tr>`).join("")}
        </table>
      </div>
      ${cos.length ? "" : emptyState("No change orders", "Add one once a job exists. Unsigned COs block send if they are in the billing amount.")}
    `;
  },
  collections() {
    const open = state.jobs.filter(j => j.money.outstanding_cents > 0);
    const today = new Date();
    return `
      <h1>Collections</h1>
      <p class="sub">What is unpaid, who to contact, and a one-click follow-up.</p>
      <div class="table-wrap">
        <table>
          <tr><th>Job</th><th>Unpaid</th><th>Status</th><th>Days</th><th>Contact</th><th>Follow up</th></tr>
          ${open.map(j => {
            const sent = j.invoices.find(i => ["sent", "partial"].includes(i.status) && i.sent_at);
            const days = sent ? Math.floor((today - new Date(sent.sent_at)) / 86400000) : "—";
            const unpaid = j.invoices.filter(i => ["sent", "partial", "approved"].includes(i.status)).map(i => i.number + " " + money(i.amount_cents)).join(", ") || money(j.money.outstanding_cents);
            return `<tr>
              <td><a href="#" data-action="open-job" data-id="${j.id}">${j.name}</a></td>
              <td>${unpaid}</td>
              <td>${j.status_label}</td>
              <td>${days}</td>
              <td>${j.payer.approver}<br><span class="sub">${j.payer.ap_email}<br>${j.payer.phone || ""}</span></td>
              <td>
                <button class="btn small" data-action="follow-up" data-id="${j.id}">Auto follow-up</button>
                <a class="btn small secondary" href="mailto:${j.payer.ap_email}">Email</a>
                <a class="btn small secondary" href="tel:${(j.payer.phone || "").replace(/[^0-9+]/g, "")}">Call</a>
              </td>
            </tr>`;
          }).join("")}
        </table>
      </div>
      ${open.length ? "" : emptyState("Nothing to collect", "Unpaid invoices show up here after a pay app is sent.")}
    `;
  },
  payments() {
    const pays = [];
    state.jobs.forEach(j => j.payments.forEach(p => pays.push({ ...p, job: j.name, jobId: j.id })));
    return `
      <h1>Payments</h1>
      <div class="table-wrap">
        <table>
          <tr><th>Job</th><th>Pay app</th><th>Amount</th><th>Reference</th><th>Matched</th></tr>
          ${pays.map(p => {
            const job = state.jobs.find(j => j.id === p.jobId);
            const inv = job && job.invoices.find(i => i.id === p.invoice_id);
            return `<tr>
              <td><a href="#" data-action="open-job" data-id="${p.jobId}">${p.job}</a></td>
              <td>${inv ? `<a href="#" data-action="open-payapp" data-job="${p.jobId}" data-inv="${inv.id}">${inv.number}</a>` : "—"}</td>
              <td>${money(p.amount_cents)}</td>
              <td>${p.reference}</td>
              <td>${(p.matched_at || "").slice(0,10)}</td>
            </tr>`;
          }).join("")}
        </table>
      </div>
      ${pays.length ? "" : emptyState("No payments matched", "Match a payment from a job once the payer has paid.")}
    `;
  },
  waivers() {
    const due = state.jobs.filter(j => j.waiver_due && !j.waiver_sent);
    const sent = state.jobs.filter(j => j.waiver_sent);
    return `
      <h1>Waivers</h1>
      <p class="sub">Click a job for the waiver kind and the contract/standard it follows.</p>
      <div class="table-wrap">
        <table>
          <tr><th>Job</th><th>Kind</th><th>Amount</th><th>Status</th><th>Action</th></tr>
          ${due.concat(sent).map(j => {
            const paid = j.invoices.find(i => i.waiver_due && !i.waiver_sent) || [...j.invoices].reverse().find(i => i.status === "paid");
            return `<tr>
              <td><a href="#" data-action="open-waiver" data-id="${j.id}">${j.name}</a></td>
              <td>${j.waiver_kind || "Conditional on progress"}</td>
              <td>${paid ? money(paid.amount_cents) : "—"}</td>
              <td>${j.waiver_sent ? "Sent" : "Due"}</td>
              <td>${!j.waiver_sent && j.waiver_due ? `<button class="btn small" data-action="waiver" data-id="${j.id}">Approve & send</button>` : "—"}</td>
            </tr>`;
          }).join("") || ""}
        </table>
      </div>
      ${due.concat(sent).length ? "" : emptyState("No waivers due", "A waiver becomes due after a payment is matched.")}
    `;
  },
  retainage() {
    const open = state.jobs.filter(j => j.money.retainage_open_cents > 0);
    return `
      <h1>Retainage</h1>
      <p class="sub">What is holding release, how to unlock it, and the same follow-up tools as Collections.</p>
      <div class="table-wrap">
        <table>
          <tr><th>Job</th><th>Open</th><th>Holding it up</th><th>Unlock</th><th>Contact / follow up</th></tr>
          ${open.map(j => {
            const closeoutItems = (j.closeout_packet && j.closeout_packet.length) ? j.closeout_packet : (j.packet || []);
            const missing = closeoutItems.filter(p => p.required && !p.present);
            return `<tr>
              <td><a href="#" data-action="open-job" data-id="${j.id}">${j.name}</a></td>
              <td>${money(j.money.retainage_open_cents)}</td>
              <td>${missing.length ? missing.map(m => m.label).join(", ") : (j.work_status === "complete" ? "Closeout packet complete" : "Job still in progress")}</td>
              <td>${j.work_status !== "complete" ? "Released at closeout: mark the job done first" : missing.length ? `<button class="btn small" data-action="open-packet" data-job="${j.id}" ${j.closeout_packet && j.closeout_packet.length ? `data-inv="closeout"` : ""}>Open packet</button>` : `<button class="btn small" data-action="retainage" data-id="${j.id}">Chase</button>`}</td>
              <td>
                ${j.payer.approver}<br>
                <button class="btn small" data-action="retainage" data-id="${j.id}">Auto follow-up</button>
                <a class="btn small secondary" href="mailto:${j.payer.ap_email}">Email</a>
                <a class="btn small secondary" href="tel:${(j.payer.phone || "").replace(/[^0-9+]/g, "")}">Call</a>
              </td>
            </tr>`;
          }).join("") || ""}
        </table>
      </div>
      ${open.length ? "" : emptyState("No retainage open", "Retainage shows here once a job holds it. Set a retainage percent when creating the job.")}
    `;
  },
  playbook() {
    return `
      <h1>Payer Playbook</h1>
      <p class="sub">These values drive follow-up cadence and expected-approval dates on the calendar. Edits persist. Rejected packets can teach the rejects-when field.</p>
      ${state.payers.length ? state.payers.map(p => `
        <div class="card">
          <h2>${p.name} <span class="sub">${p.role}</span></h2>
          <form data-id="${p.id}">
            <div class="money-row"><span class="label">Approver</span><input name="approver" value="${p.approver || ""}" style="width:60%"></div>
            <div class="money-row"><span class="label">AP email</span><input name="ap_email" value="${p.ap_email || ""}" style="width:60%"></div>
            <div class="money-row"><span class="label">Follow-up days</span><input name="follow_up_days" type="number" value="${p.follow_up_days || 7}" style="width:80px"></div>
            <div class="money-row"><span class="label">Usual approval days</span><input name="usual_approval_days" type="number" value="${p.usual_approval_days || 14}" style="width:80px"></div>
            <div class="money-row"><span class="label">Packet</span><textarea name="packet" style="width:100%;height:48px">${p.packet || ""}</textarea></div>
            <div class="money-row"><span class="label">Rejects when</span><textarea name="rejects_when" style="width:100%;height:48px">${p.rejects_when || ""}</textarea></div>
            <div class="money-row"><span class="label">Copy</span><input name="copy" value="${p.copy || ""}" style="width:60%"></div>
            <button type="button" class="btn small" data-action="save-playbook" data-id="${p.id}">Save playbook</button>
          </form>
        </div>`).join("") : emptyState("No payers yet", "A payer is created with the first job for that customer.")}
    `;
  },
  outbox() {
    let emails = [...(state.email_log || [])].reverse();
    if (outboxJobFilter !== "all") emails = emails.filter(e => e.job_id === outboxJobFilter);
    return `
      <h1>Outbox</h1>
      <p class="sub">Every approved send and follow-up is logged here. Email delivery is not built yet: nothing here has left Billdable.</p>
      <div class="filter-bar">
        <select id="outbox-filter" data-filter-for="outbox">
          <option value="all">All jobs</option>
          ${state.jobs.map(j => `<option value="${j.id}" ${outboxJobFilter === j.id ? "selected" : ""}>${j.name}</option>`).join("")}
        </select>
      </div>
      <div class="table-wrap">
        <table>
          <tr><th>When</th><th>Kind</th><th>To</th><th>Subject</th><th>Status</th></tr>
          ${emails.map(e => `<tr class="clickable" data-action="open-job" data-id="${e.job_id}" title="Open the job"><td>${e.at.slice(0,16)}</td><td>${e.kind.replace("_", " ")}</td><td>${e.to}</td><td><a href="#">${e.subject}</a><div class="sub">${e.body || ""}</div></td><td>${e.status}</td></tr>`).join("")}
        </table>
      </div>
      ${emails.length ? "" : emptyState("Outbox is empty", "Approved sends are logged here. Nothing has been sent yet.")}
    `;
  },
  audit() {
    let events = [...(state.audit || [])].sort((a, b) => (b.at > a.at ? 1 : -1));
    if (auditJobFilter !== "all") events = events.filter(e => e.job_id === auditJobFilter);
    return `
      <h1>Audit</h1>
      <p class="sub">Every action: what was seen, what was drafted, who approved.</p>
      <div class="filter-bar">
        <select id="audit-filter" data-filter-for="audit">
          <option value="all">All jobs</option>
          ${state.jobs.map(j => `<option value="${j.id}" ${auditJobFilter === j.id ? "selected" : ""}>${j.name}</option>`).join("")}
        </select>
      </div>
      <div class="table-wrap">
        <table>
          <tr><th>When</th><th>Actor</th><th>Action</th><th>Detail</th></tr>
          ${events.map(e => `<tr><td>${e.at}</td><td>${e.actor}</td><td>${e.action}</td><td>${e.detail}</td></tr>`).join("")}
        </table>
      </div>
      ${events.length ? "" : emptyState("No audit events", "Actions on jobs will be recorded here.")}
    `;
  },
};

// ----- Helper chat -----
// Answers come from the prepared-answers database on the server, which reads
// this company's jobs for questions like "what should I do today". Action
// buttons call the same endpoints as the rest of the app, so every gate and
// confirmation still applies.
let helperAdmin = false;
const helperLog = [];   // {who: "you"|"helper", text, actions, suggestions, logId, rated}
let helperBusy = false;

function updateAiContext() {
  if (aiOpen) renderHelper();
}

function helperStarters() {
  const job = currentView === "job" && state && state.jobs.find(j => j.id === currentJobId);
  if (job) return [`What's blocking ${job.name}?`, `Draft a follow-up for ${job.name}`, `Status of ${job.name}`];
  if (currentView === "inbox") return ["How does it know which job?", "How do I import 30 days of notes?", "How do I forward emails to Billdable?"];
  if (currentView === "retainage") return ["How do I get retainage released?", "Which jobs are holding my retainage?"];
  return ["What should I do today?", "Who owes me money?", "How do I import emails and texts?"];
}

function helperFormat(text) {
  // text is escaped first; then "- " lines become a list, blank lines paragraphs.
  const lines = esc(text || "").split("\n");
  let html = "", inList = false;
  for (const line of lines) {
    if (line.startsWith("- ")) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${line.slice(2)}</li>`;
    } else {
      if (inList) { html += "</ul>"; inList = false; }
      html += line.trim() ? `<p>${line}</p>` : "";
    }
  }
  return html + (inList ? "</ul>" : "");
}

function renderHelper() {
  const box = document.getElementById("ai-replies");
  const chips = document.getElementById("ai-chips");
  if (!box || !chips) return;
  const greeting = `<div class="msg helper"><p>Hi${me && me.name ? " " + me.name.split(" ")[0] : ""}. Ask me how to do something in Billdable, or about your own jobs: what to do today, who owes you, what's blocking a job.</p></div>`;
  {
    box.innerHTML = greeting + helperLog.map((m, idx) => m.who === "you"
      ? `<div class="msg you">${esc(m.text)}</div>`
      : `<div class="msg helper">${helperFormat(m.text)}
          ${(m.actions || []).length ? `<div class="msg-actions">${m.actions.map((a, i) => `<button class="btn small ${a.type === "post" ? "" : "secondary"}" data-helper-msg="${idx}" data-helper-action="${i}">${esc(a.label)}</button>`).join("")}</div>` : ""}
          ${m.logId ? `<div class="msg-rate">${m.rated ? (m.rated === "yes" ? "Thanks." : "Thanks, the team will improve this answer.") : `Helpful? <button class="linkish" data-helper-rate="${idx}" data-value="yes">Yes</button> <button class="linkish" data-helper-rate="${idx}" data-value="no">No</button>`}</div>` : ""}
        </div>`).join("") + (helperBusy ? `<div class="msg helper typing" aria-label="Looking that up">Looking that up…</div>` : "");
  }
  const last = [...helperLog].reverse().find(m => m.who === "helper");
  const suggestions = (last && last.suggestions && last.suggestions.length) ? last.suggestions : helperStarters();
  chips.innerHTML = suggestions.map(s => `<button class="chip" data-helper-ask="${esc(s)}">${esc(s)}</button>`).join("");
  box.scrollTop = box.scrollHeight;
}

async function helperAsk(q) {
  q = (q || "").trim();
  if (!q || helperBusy) return;
  helperLog.push({ who: "you", text: q });
  helperBusy = true;
  renderHelper();
  let reply;
  try {
    const res = await fetch("/api/helper/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q, view: currentView, job_id: currentView === "job" ? currentJobId : "" }),
    });
    reply = await readJson(res);
    if (res.status === 401) { helperBusy = false; me = null; state = null; renderLanding(); return; }
    if (!res.ok) reply = { text: reply.error || "Something went wrong. Try again." };
  } catch (_) {
    reply = { text: "I can't reach Billdable right now. Check your connection and try again." };
  }
  helperBusy = false;
  helperLog.push({ who: "helper", text: reply.text, actions: reply.actions || [], suggestions: reply.suggestions || [], logId: reply.log_id });
  if (helperLog.length > 60) helperLog.splice(0, helperLog.length - 60);
  renderHelper();
}

async function helperRunAction(a) {
  if (a.type === "nav") nav(a.view);
  else if (a.type === "job") nav("job", a.job_id);
  else if (a.type === "packet") nav("packet-detail", a.job_id, a.inv || null);
  else if (a.type === "ask") helperAsk(a.q);
  else if (a.type === "copy") {
    try { await navigator.clipboard.writeText(a.text); toast("Copied"); }
    catch (_) { toast("Copy blocked by the browser. Select the text and copy it."); }
  } else if (a.type === "post") {
    if (a.confirm && !confirm(a.confirm)) return;
    await post(a.path);
    helperLog.push({ who: "helper", text: "Done. The page is updated; the Outbox and Audit show what happened." });
    renderHelper();
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
  if (askBtn) { helperAsk(askBtn.textContent); return; }
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
    const helpful = rate.dataset.value === "yes";
    let note = "";
    if (!helpful) note = prompt("What were you looking for? (optional)") || "";
    msg.rated = helpful ? "yes" : "no";
    renderHelper();
    try {
      await fetch("/api/helper/feedback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ log_id: msg.logId, helpful, note }) });
    } catch (_) { /* rating is best effort */ }
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && aiOpen && document.getElementById("ai-panel").contains(document.activeElement)) {
    aiOpen = false;
    document.getElementById("ai-panel").classList.remove("open");
    document.getElementById("ai-badge").focus();
  }
});

// Drop files anywhere, or paste a screenshot, to import. On a job page they
// go straight onto that job's record; elsewhere they land in the Inbox.
let dragDepth = 0;
function dropOverlay(on) {
  let el = document.getElementById("drop-overlay");
  if (!el && on) {
    el = document.createElement("div");
    el.id = "drop-overlay";
    el.className = "drop-overlay";
    document.body.appendChild(el);
  }
  if (!el) return;
  const job = currentView === "job" && state && state.jobs.find(j => j.id === currentJobId && !j.closed);
  el.textContent = job ? `Drop to add to ${job.name}` : "Drop to add to the Inbox";
  el.style.display = on ? "flex" : "none";
}
function importTargetJob() {
  const job = currentView === "job" && state && state.jobs.find(j => j.id === currentJobId && !j.closed);
  return job ? job.id : "";
}
document.addEventListener("dragenter", (e) => {
  if (!state || !e.dataTransfer || ![...e.dataTransfer.types].includes("Files")) return;
  dragDepth++;
  dropOverlay(true);
});
document.addEventListener("dragover", (e) => {
  if (!state || !e.dataTransfer || ![...e.dataTransfer.types].includes("Files")) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "copy";
});
document.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) dropOverlay(false);
});
document.addEventListener("drop", (e) => {
  if (!state || !e.dataTransfer || !e.dataTransfer.files.length) return;
  e.preventDefault();
  dragDepth = 0;
  dropOverlay(false);
  const jobId = importTargetJob();
  uploadFiles(e.dataTransfer.files, jobId, !!jobId);
});
document.addEventListener("paste", (e) => {
  if (!state || !e.clipboardData) return;
  const files = [...e.clipboardData.files];
  if (!files.length) return; // plain text pastes behave normally
  e.preventDefault();
  const jobId = importTargetJob();
  uploadFiles(files, jobId, !!jobId);
});
document.addEventListener("scroll", hideHelpTip, true);
// One file picker for every screen ("choose files" on the Inbox or a job page).
document.getElementById("file-input").addEventListener("change", (e) => {
  const input = e.target;
  const jobId = input.dataset.job || "";
  const files = [...input.files];
  input.value = "";
  input.dataset.job = "";   // the next pick starts with no job unless its button sets one
  uploadFiles(files, jobId, !!jobId);
});

window.nav = nav;
boot();
