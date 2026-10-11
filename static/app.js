let state = null;
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
let activityJobFilter = "all";
let formOpen = {};
let toastTimer = null;

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
  const res = await fetch("/api/state");
  state = await res.json();
  render();
}

async function post(path, body = {}) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (res.status === 409) {
    toast("Blocked: " + (data.blockers || [data.error]).join("; "));
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
  render();
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
  const app = document.getElementById("app");
  const company = state.company;
  const active = (v) => (currentView === v ? "active" : "");
  app.innerHTML = `
    <div class="shell">
      <aside class="rail">
        <div class="brand">Billdable</div>
        <div class="company">${company.name}<br>${company.crew} people · ${company.books}</div>
        <nav class="nav">
          <div class="nav-section">Operations</div>
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
        </nav>
        <button class="reset-btn" id="reset">Reset sample data</button>
      </aside>
      <main class="main" id="main"></main>
    </div>
  `;
  document.querySelectorAll(".nav button").forEach((b) =>
    b.addEventListener("click", () => nav(b.dataset.v))
  );
  document.getElementById("reset").addEventListener("click", () => post("/api/reset"));
  document.getElementById("main").innerHTML = views[currentView] ? views[currentView]() : "<p>Unknown view</p>";
  bindActions();
  updateAiContext();
}

function bindActions() {
  document.querySelectorAll("[data-action]").forEach((el) => {
    el.addEventListener("click", async (e) => {
      e.preventDefault();
      const a = el.dataset.action;
      const id = el.dataset.id;
      if (a === "draft") await post(`/api/jobs/${id}/draft`);
      else if (a === "send") await post(`/api/jobs/${id}/send`, { actor: "owner" });
      else if (a === "done") {
        const notes = document.querySelector("#done-notes")?.value || "";
        await post(`/api/jobs/${id}/done`, { actor: "owner", notes });
        formOpen.done = false;
      }
      else if (a === "resolve") await post(`/api/jobs/${id}/resolve/${el.dataset.item}`);
      else if (a === "waiver") await post(`/api/jobs/${id}/waiver`, { actor: "owner" });
      else if (a === "retainage") await post(`/api/jobs/${id}/retainage`);
      else if (a === "follow-up") await post(`/api/jobs/${id}/follow-up`);
      else if (a === "match") {
        const dollars = parseFloat(document.querySelector("#match-amt")?.value || "0");
        const ref = document.querySelector("#match-ref")?.value || "QBO-manual";
        const cents = Math.round(dollars * 100);
        if (cents > 0) await post(`/api/jobs/${id}/match-payment`, { amount_cents: cents, reference: ref });
      }
      else if (a === "add-co") {
        const title = document.querySelector("#co-title")?.value || "New change order";
        const dollars = parseFloat(document.querySelector("#co-amt")?.value || "0");
        const cents = Math.round(dollars * 100);
        if (cents > 0) {
          await post(`/api/jobs/${id}/add-co`, { title, amount_cents: cents });
          nav("co-detail", id);
        }
      }
      else if (a === "goto-co") nav("cos");
      else if (a === "add-co-from-list") {
        const jobId = document.querySelector("#new-co-job")?.value;
        const title = document.querySelector("#co-title")?.value || "New change order";
        const dollars = parseFloat(document.querySelector("#co-amt")?.value || "0");
        const cents = Math.round(dollars * 100);
        if (jobId && cents > 0) {
          await post(`/api/jobs/${jobId}/add-co`, { title, amount_cents: cents });
          nav("co-detail", jobId);
        }
      }
      else if (a === "goto-packet") nav("packet-detail", id);
      else if (a === "open-packet") nav("packet-detail", el.dataset.job);
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
      else if (a === "close") await post(`/api/jobs/${id}/close`, { actor: "owner" });
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
      else if (a === "open-job") nav("job", id);
      else if (a === "toggle-form") toggleForm(el.dataset.form);
      else if (a === "dismiss-help") { helpDismissed = true; localStorage.setItem("billdable-help-dismissed", "1"); render(); }
      else if (a === "filter") { dashFilter = el.dataset.filter; render(); }
      else if (a === "sort") {
        if (dashSort === el.dataset.sort) dashSortDir *= -1;
        else { dashSort = el.dataset.sort; dashSortDir = 1; }
        render();
      }
      else if (a === "payapp-filter") { payappFilter = el.dataset.filter; render(); }
      else if (a === "activity-filter") { activityJobFilter = el.dataset.value || document.querySelector("#activity-filter")?.value || "all"; render(); }
      else if (a === "save-playbook") {
        const form = el.closest("form");
        const updates = {};
        new FormData(form).forEach((v, k) => { if (v !== "") updates[k] = isNaN(v) ? v : Number(v); });
        await post(`/api/payers/${id}/update`, updates);
      }
    });
  });
  document.querySelectorAll("select[data-action='activity-filter']").forEach(s => {
    s.addEventListener("change", () => { activityJobFilter = s.value; render(); });
  });
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
  } else if (dashFilter === "blocked") jobs = jobs.filter(j => j.blockers && j.blockers.length);
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

function chasedDown() {
  // revenue that had a follow-up or was previously blocked and is now billed/collected
  let total = 0;
  state.jobs.forEach(j => {
    if ((j.follow_ups || 0) > 0) total += j.money.collected_cents;
    else if (j.money.billed_cents > 0 && j.blockers.length === 0) total += Math.min(j.money.billed_cents, j.money.collected_cents || j.money.billed_cents * 0.3);
  });
  return total;
}

function paidOnTimeDelta() {
  // average days faster than playbook usual_approval_days
  let deltas = [];
  state.jobs.forEach(j => {
    const usual = j.payer.usual_approval_days || 14;
    j.invoices.forEach(inv => {
      if (inv.sent_at && inv.status === "paid") {
        const pay = j.payments.find(p => p.invoice_id === inv.id);
        if (pay) {
          const days = (new Date(pay.matched_at) - new Date(inv.sent_at)) / 86400000;
          deltas.push(usual - days);
        }
      }
    });
  });
  if (!deltas.length) return 4; // demo default
  return Math.round(deltas.reduce((a, b) => a + b, 0) / deltas.length);
}

const views = {
  dashboard() {
    const t = state.totals;
    const jobs = filteredJobs();
    const actionJobs = state.jobs.filter(j => (j.blockers && j.blockers.length) || (j.waiver_due && !j.waiver_sent) || j.invoices.some(i => i.status === "awaiting_approval"));
    return `
      <h1>Dashboard</h1>
      <p class="sub">What needs attention and the money across open jobs.</p>
      ${!helpDismissed ? `
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
        <div class="metric highlight"><div class="k">Billdable revenue chased down</div><div class="v">${money(chasedDown())}</div></div>
        <div class="metric highlight"><div class="k">Billdable revenue paid on time</div><div class="v">${paidOnTimeDelta()} days faster</div></div>
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
      <h1>Jobs</h1>
      <p class="sub">Click a job for its money file, packet, and actions.</p>
      <div class="table-wrap">
        <table>
          <tr><th>Code</th><th>Job</th><th>Customer</th><th>Status</th><th>Outstanding</th><th>Unbilled</th><th>Retainage</th><th>Next</th></tr>
          ${state.jobs.map(j => `
            <tr>
              <td>${j.code}</td>
              <td><a href="#" data-action="open-job" data-id="${j.id}">${j.name}</a></td>
              <td>${j.payer.name}</td>
              <td>${j.status_label}</td>
              <td>${money(j.money.outstanding_cents)}</td>
              <td>${money(j.money.unbilled_completed_cents)}</td>
              <td>${money(j.money.retainage_open_cents)}</td>
              <td class="next-action">${nextStep(j)}</td>
            </tr>`).join("")}
        </table>
      </div>
    `;
  },
  job() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return "<p>Job not found</p>";
    const m = j.money;
    const blocked = j.blockers && j.blockers.length;
    const canClose = !j.closed && m.outstanding_cents === 0 && m.retainage_open_cents === 0 && (!j.waiver_due || j.waiver_sent);
    const hasDraft = j.invoices.some(i => ["draft", "awaiting_approval"].includes(i.status));
    return `
      <div class="top"><button class="back" onclick="nav('jobs')">← Jobs</button></div>
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
          <div class="money-row"><span class="label">Retainage open</span><span>${money(m.retainage_open_cents)}</span></div>
        </div>
        <div class="card">
          <h2>Actions</h2>
          <div class="actions-vert">
            ${j.work_status !== "complete" ? `<div class="action-row"><button class="btn" data-action="toggle-form" data-form="done">Mark done</button><div><div class="blurb">Record completion notes and start the money path.</div></div></div>` : ""}
            <div class="action-row"><button class="btn secondary" data-action="draft" data-id="${j.id}">Draft pay app</button><div class="blurb">Create a same-week pay app from unbilled completed work.</div></div>
            <div class="action-row"><button class="btn" data-action="send" data-id="${j.id}" ${blocked || !hasDraft ? "disabled" : ""}>Approve & send</button><div class="blurb">Human gate. Sends only when the packet is clean.</div></div>
            <div class="action-row"><button class="btn secondary" data-action="follow-up" data-id="${j.id}">Follow up</button><div class="blurb">Log a follow-up on a sent pay app using the playbook cadence.</div></div>
            <div class="action-row"><button class="btn secondary" data-action="toggle-form" data-form="match">Match payment</button><div class="blurb">Record cash received (dollars). Opens the waiver gate when paid in full.</div></div>
            <div class="action-row"><button class="btn secondary" data-action="import-qbo" data-id="${j.id}">Import QBO sample</button><div class="blurb">Local stand-in for a QuickBooks payment read.</div></div>
            ${j.waiver_due && !j.waiver_sent ? `<div class="action-row"><button class="btn" data-action="waiver" data-id="${j.id}">Send waiver</button><div class="blurb">Human gate. Sends the conditional waiver after payment.</div></div>` : ""}
            ${m.retainage_open_cents > 0 ? `<div class="action-row"><button class="btn secondary" data-action="retainage" data-id="${j.id}">Chase retainage</button><div class="blurb">Request release once the closeout packet is complete.</div></div>` : ""}
            <div class="action-row"><button class="btn secondary" data-action="goto-co" data-id="${j.id}">Add change order</button><div class="blurb">Opens Change Orders to create a new CO for this job.</div></div>
            <div class="action-row"><button class="btn secondary" data-action="goto-packet" data-id="${j.id}">Add evidence to packet</button><div class="blurb">Opens this job’s packet to attach daily records and files.</div></div>
            ${canClose ? `<div class="action-row"><button class="btn" data-action="close" data-id="${j.id}">Close for record</button><div class="blurb">Move a fully settled job into Job history.</div></div>` : ""}
          </div>
          ${blocked ? `<div class="gate-reason">Approve & send is disabled until blockers are cleared.</div>` : ""}
          ${formOpen.done ? `<div class="form-inline"><input id="done-notes" placeholder="Completion notes" style="flex:1"><button class="btn small" data-action="done" data-id="${j.id}">Confirm done</button></div>` : ""}
          ${formOpen.match ? `<div class="form-inline"><input id="match-amt" type="number" step="0.01" placeholder="Amount in dollars"><input id="match-ref" placeholder="Reference" value="QBO-manual"><button class="btn small" data-action="match" data-id="${j.id}">Match</button></div>` : ""}
          <p class="sub">Send, waiver, retainage, and close require explicit approval.</p>
        </div>
      </div>
      <div class="card">
        <h2>Evidence binder</h2>
        ${(j.evidence || []).length ? (j.evidence || []).map(e => `<div class="list-item">${e.kind}: ${e.label} <span class="sub">${(e.added_at || "").slice(0,10)}</span></div>`).join("") : "<p>None yet.</p>"}
      </div>
      <div class="card">
        <h2>Packet</h2>
        ${j.packet.map(p => `
          <div class="list-item">
            ${p.present ? "✓" : "✗"} ${p.label}
            ${!p.present ? `<button class="btn small pop" data-action="resolve" data-id="${j.id}" data-item="${p.id}">Mark present</button>` : ""}
            ${!p.present ? `<div class="blocker">${p.blocker}</div>` : ""}
          </div>`).join("")}
      </div>
      <div class="card">
        <h2>Change orders</h2>
        ${j.change_orders.length ? j.change_orders.map(co => `
          <div class="list-item">CO ${co.number} — ${co.title} · ${money(co.amount_cents)} · <span class="pill ${co.status === "approved" ? "green" : "amber"}">${co.status}</span>${co.included_in_billing ? " · in billing" : ""}
            ${co.status === "pending" ? `<button class="btn small" data-action="approve-co" data-id="${j.id}" data-co="${co.id}">Approve</button>` : ""}
          </div>`).join("") : "<p>None</p>"}
      </div>
      <div class="card">
        <h2>Invoices / pay apps</h2>
        ${j.invoices.map(inv => `
          <div class="list-item">${inv.number} · ${money(inv.amount_cents)} · <span class="pill ${inv.status === "paid" ? "green" : inv.status === "sent" ? "amber" : ""}">${inv.status}</span>${inv.approved_by ? " · approved by " + inv.approved_by : ""}</div>`).join("")}
      </div>
    `;
  },
  daily() {
    const openJobs = state.jobs.filter(j => !j.closed);
    const records = state.daily_records || [];
    return `
      <h1>Daily Record</h1>
      <p class="sub">Blank and ready. Create a record and attach it to an ongoing job’s packet. Tick “for history” is set automatically if the job is already closed.</p>
      <div class="card">
        <h2>New record</h2>
        <div class="form-inline">
          <select id="daily-job">
            <option value="">Select job…</option>
            ${openJobs.map(j => `<option value="${j.id}">${j.name}</option>`).join("")}
          </select>
          <input id="daily-notes" placeholder="What happened today" style="flex:1">
          <button class="btn" data-action="daily-save">Save & attach to packet</button>
        </div>
      </div>
      <div class="card">
        <h2>Recent records</h2>
        ${records.length ? records.slice().reverse().map(r => {
          const j = state.jobs.find(x => x.id === r.job_id);
          return `<div class="list-item">${r.at.slice(0,10)} · ${j ? j.name : r.job_id} · ${r.notes} ${r.for_history ? "· history" : ""}</div>`;
        }).join("") : "<p>None yet.</p>"}
      </div>
    `;
  },
  packetDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return "<p>Select a job from Packets.</p>";
    const records = (state.daily_records || []).filter(r => r.job_id === j.id);
    return `
      <div class="top"><button class="back" onclick="nav('packets')">← Packets</button></div>
      <h1>${j.name} packet</h1>
      <p class="sub">Daily details, attachments, and the contract field for pay applications.</p>
      <div class="card">
        <h2>Contract</h2>
        <p class="sub">Field for the signed contract. No sample contract is preloaded.</p>
        <div class="form-inline"><input placeholder="Contract file name (placeholder)" disabled style="flex:1"><button class="btn secondary" disabled>Upload</button></div>
      </div>
      <div class="card">
        <h2>Required items</h2>
        ${j.packet.map(p => `
          <div class="list-item">
            ${p.present ? "✓" : "✗"} ${p.label}
            ${!p.present ? `<button class="btn small pop" data-action="resolve" data-id="${j.id}" data-item="${p.id}">Mark present</button>` : ""}
            ${(p.attachments || []).map(a => `<div class="sub">Attached: ${a.name}</div>`).join("")}
            <div class="form-inline"><input id="attach-name" placeholder="File name"><button class="btn small" data-action="attach" data-id="${j.id}" data-item="${p.id}">Attach</button></div>
          </div>`).join("")}
      </div>
      <div class="card">
        <h2>Daily records</h2>
        ${records.length ? records.map(r => `<div class="list-item">${r.at.slice(0,10)} · ${r.notes}</div>`).join("") : "<p>None yet. Create one from Daily Record.</p>"}
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
        <div class="form-inline"><input placeholder="File name" id="co-attach"><button class="btn small secondary" onclick="alert('Attached locally for demo')">Attach</button></div>
      </div>
      <div class="card">
        <h2>Packet</h2>
        <button class="btn secondary" data-action="open-packet" data-job="${j.id}">Open job packet</button>
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
        <h2>Packet at send</h2>
        ${j.packet.map(p => `<div class="list-item">${p.present ? "✓" : "✗"} ${p.label}</div>`).join("")}
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
    state.jobs.forEach(j => {
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
          </tr>`).join("") || "<tr><td colspan=5>None</td></tr>"}
        </table>
      </div>
    `;
  },
  packets() {
    return `
      <h1>Packets</h1>
      <p class="sub">Critical status for each job. Click a job to open its packet — daily records, attachments, and the contract field.</p>
      <div class="table-wrap">
        <table>
          <tr><th>Job</th><th>Missing</th><th>Daily records</th><th>Contract</th></tr>
          ${state.jobs.map(j => {
            const missing = j.packet.filter(p => p.required && !p.present).length;
            const dr = (state.daily_records || []).filter(r => r.job_id === j.id).length;
            return `<tr>
              <td><a href="#" data-action="open-packet" data-job="${j.id}">${j.name}</a></td>
              <td>${missing ? `<span class="pill red">${missing}</span>` : `<span class="pill green">complete</span>`}</td>
              <td>${dr}</td>
              <td>${j.contract_doc ? "Attached" : "—"}</td>
            </tr>`;
          }).join("")}
        </table>
      </div>
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
            ${state.jobs.filter(j => !j.closed).map(j => `<option value="${j.id}">${j.name}</option>`).join("")}
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
          </tr>`).join("") || "<tr><td colspan=6>None</td></tr>"}
        </table>
      </div>
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
          }).join("") || "<tr><td colspan=6>None</td></tr>"}
        </table>
      </div>
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
            const paid = j.invoices.find(i => i.status === "paid");
            return `<tr>
              <td><a href="#" data-action="open-waiver" data-id="${j.id}">${j.name}</a></td>
              <td>${j.waiver_kind || "Conditional on progress"}</td>
              <td>${paid ? money(paid.amount_cents) : "—"}</td>
              <td>${j.waiver_sent ? "Sent" : "Due"}</td>
              <td>${!j.waiver_sent && j.waiver_due ? `<button class="btn small" data-action="waiver" data-id="${j.id}">Approve & send</button>` : "—"}</td>
            </tr>`;
          }).join("") || "<tr><td colspan=5>None</td></tr>"}
        </table>
      </div>
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
            const missing = j.packet.filter(p => p.required && !p.present);
            return `<tr>
              <td><a href="#" data-action="open-job" data-id="${j.id}">${j.name}</a></td>
              <td>${money(j.money.retainage_open_cents)}</td>
              <td>${missing.length ? missing.map(m => m.label).join(", ") : "Nothing in packet"}</td>
              <td>${missing.length ? `<button class="btn small" data-action="open-packet" data-job="${j.id}">Open packet</button>` : `<button class="btn small" data-action="retainage" data-id="${j.id}">Chase</button>`}</td>
              <td>
                ${j.payer.approver}<br>
                <button class="btn small" data-action="retainage" data-id="${j.id}">Auto follow-up</button>
                <a class="btn small secondary" href="mailto:${j.payer.ap_email}">Email</a>
                <a class="btn small secondary" href="tel:${(j.payer.phone || "").replace(/[^0-9+]/g, "")}">Call</a>
              </td>
            </tr>`;
          }).join("") || "<tr><td colspan=5>None</td></tr>"}
        </table>
      </div>
    `;
  },
  playbook() {
    return `
      <h1>Payer Playbook</h1>
      <p class="sub">These values drive follow-up cadence and expected-approval dates on the calendar. Edits persist. Rejected packets can teach the rejects-when field.</p>
      ${state.payers.map(p => `
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
        </div>`).join("")}
    `;
  },
  outbox() {
    let emails = [...(state.email_log || [])].reverse();
    if (activityJobFilter !== "all") emails = emails.filter(e => e.job_id === activityJobFilter);
    return `
      <h1>Outbox</h1>
      <p class="sub">Every approved send is logged here. Real delivery is stubbed; the human gate still fired.</p>
      <div class="filter-bar">
        <select id="activity-filter" data-action="activity-filter">
          <option value="all">All jobs</option>
          ${state.jobs.map(j => `<option value="${j.id}" ${activityJobFilter === j.id ? "selected" : ""}>${j.name}</option>`).join("")}
        </select>
      </div>
      <div class="table-wrap">
        <table>
          <tr><th>When</th><th>Kind</th><th>To</th><th>Subject</th><th>Status</th></tr>
          ${emails.map(e => `<tr><td>${e.at.slice(0,16)}</td><td>${e.kind}</td><td>${e.to}</td><td>${e.subject}</td><td>${e.status}</td></tr>`).join("") || "<tr><td colspan=5>None yet.</td></tr>"}
        </table>
      </div>
    `;
  },
  audit() {
    let events = [...(state.audit || [])].sort((a, b) => (b.at > a.at ? 1 : -1));
    if (activityJobFilter !== "all") events = events.filter(e => e.job_id === activityJobFilter);
    return `
      <h1>Audit</h1>
      <p class="sub">Every action: what was seen, what was drafted, who approved.</p>
      <div class="filter-bar">
        <select id="activity-filter" data-action="activity-filter">
          <option value="all">All jobs</option>
          ${state.jobs.map(j => `<option value="${j.id}" ${activityJobFilter === j.id ? "selected" : ""}>${j.name}</option>`).join("")}
        </select>
      </div>
      <div class="table-wrap">
        <table>
          <tr><th>When</th><th>Actor</th><th>Action</th><th>Detail</th></tr>
          ${events.map(e => `<tr><td>${e.at}</td><td>${e.actor}</td><td>${e.action}</td><td>${e.detail}</td></tr>`).join("")}
        </table>
      </div>
    `;
  },
};

function updateAiContext() {
  const replies = document.getElementById("ai-replies");
  if (!replies || !aiOpen) return;
  // context hint is shown in the panel subtitle via existing HTML; dynamic answers still rule-based
}

document.getElementById("ai-badge").addEventListener("click", () => {
  aiOpen = !aiOpen;
  document.getElementById("ai-panel").classList.toggle("open", aiOpen);
  if (aiOpen && currentJobId) {
    const j = state.jobs.find(x => x.id === currentJobId);
    const replies = document.getElementById("ai-replies");
    if (j && replies && !replies.dataset.shown) {
      replies.innerHTML = `<div class="reply">Current job: ${j.name}. ${j.blockers && j.blockers.length ? "Blocked by: " + j.blockers.join("; ") : "Status: " + j.status_label}</div>`;
      replies.dataset.shown = "1";
    }
  }
});

document.getElementById("ai-input").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const q = e.target.value.trim();
  if (!q) return;
  e.target.value = "";
  const replies = document.getElementById("ai-replies");
  replies.innerHTML += `<p><strong>You:</strong> ${q}</p>`;
  let ans = "I can help with blockers, money files, or drafting a short follow-up note. Try “what’s blocking Oak” or “draft follow-up for Turner”.";
  const lower = q.toLowerCase();
  if (lower.includes("block") || lower.includes("oak")) {
    const j = state.jobs.find(x => x.name.toLowerCase().includes("oak")) || (currentJobId && state.jobs.find(x => x.id === currentJobId)) || state.jobs[0];
    ans = j.blockers.length ? `${j.name} is blocked by:\n- ${j.blockers.join("\n- ")}` : `${j.name} has no packet blockers. Status: ${j.status_label}`;
  } else if (lower.includes("follow") || lower.includes("draft")) {
    const j = state.jobs.find(x => lower.includes(x.name.toLowerCase().split(" ")[0].toLowerCase())) || state.jobs.find(x => x.invoices.some(i => i.status === "sent"));
    if (j) {
      ans = `Draft follow-up for ${j.name}:\n\nHi ${j.payer.approver},\n\nChecking on ${j.invoices.slice(-1)[0]?.number || "the recent pay app"} for ${j.name}. Packet is complete on our side. Usual approval is ${j.payer.usual_approval_days} days.\n\nThanks,\nHartline`;
    }
  } else if (lower.includes("money") || lower.includes("outstanding")) {
    const t = state.totals;
    ans = `Totals: unbilled ${money(t.unbilled_cents)}, outstanding ${money(t.outstanding_cents)}, retainage ${money(t.retainage_cents)}.`;
  }
  replies.innerHTML += `<div class="reply">${ans}</div>`;
  replies.scrollTop = replies.scrollHeight;
});

window.nav = nav;
load();
