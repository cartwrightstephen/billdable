// Communications: the Outbox (every approved send and follow-up, with what became of it, its
// attachments and a Print), and the print sheet the Outbox and the Inbox share. The Inbox is in
// inbox.js.
import { fmtDateTime, words, emailStatusHtml, unesc } from "../format.js";
import { pathFor } from "../routes.js";
import { jobFilterOptions, jobFilterShown } from "../jobs.js";
import { moreButton } from "../dom.js";
import { termText } from "../terms.js";
import { state, me, onSessionEnd, mailReady } from "../state.js";
import { post, emptyState, listPage, can } from "../app-core.js";

let outboxJobFilter = "all";

// The per-address lines show once an email went out (or bounced); before that its status says it all.
export function shownDelivery(e) {
  const st = unesc(e.status || "");
  return (e.recipients || []).length > 0 || st === "sent" || st.startsWith("bounced");
}
export function setOutboxJobFilter(value) { outboxJobFilter = value; }

// Signed out: a job picked in the filter is that company's.
onSessionEnd(() => { outboxJobFilter = "all"; });

// What an email carried: the pay app PDF and each file, by name, opening in a new tab; a file
// taken off the job record since it went says so (M27). files: the company's (state.files).
export function attachmentsHtml(e, files = (state && state.files) || []) {
  const parts = attachmentNames(e, files, true);
  const missed = (e.not_attached || []).length ? `; not attached: ${e.not_attached.join("; ")}` : "";
  return parts.length || missed ? `<div class="sub email-files">Attached: ${parts.join(" · ") || "nothing"}${missed}</div>` : "";
}

function attachmentNames(e, files, links) {
  const byId = new Map(files.map(f => [f.id, f]));
  const out = [];
  if (e.payapp_invoice_id) {
    out.push(links ? `<a href="/api/jobs/${e.job_id}/payapp/${e.payapp_invoice_id}.pdf" target="_blank" rel="noopener">Pay app PDF</a>` : "Pay app PDF");
  }
  for (const fid of e.attachments || []) {
    const f = byId.get(fid);
    out.push(!f ? "A file no longer in the job records" : links ? `<a href="/api/files/${f.id}" target="_blank" rel="noopener">${f.name}</a>` : f.name);
  }
  return out;
}

// One message on paper (M27): the sheet is drawn outside the page, the body is marked printing,
// and the print rule in styles.css shows only the sheet. Values arrive escaped, as on the page.
export function printSheet(fields, title, body, attachments = []) {
  let sheet = document.getElementById("print-sheet");
  if (!sheet) {
    sheet = document.createElement("div");
    sheet.id = "print-sheet";
    sheet.className = "print-sheet";
    document.body.appendChild(sheet);
  }
  sheet.innerHTML = `<h1>${title}</h1>
    <dl>${fields.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>
    ${body ? `<div class="print-body">${body}</div>` : ""}
    ${attachments.length ? `<h2>Attachments</h2><ul>${attachments.map(a => `<li>${a}</li>`).join("")}</ul>` : ""}`;
  document.body.classList.add("printing");
  window.addEventListener("afterprint", () => document.body.classList.remove("printing"), { once: true });
  if (typeof window.print === "function") window.print();
}

// What became of an email at each address (Siteline comparison, item 5; delivery.py on the
// server): sent, delivered, opened (when it can be told) or bounced, with when. An email from
// before these lines were kept reads from its own status. Values arrive escaped.
const DELIVERY_WORDS = { queued: "Waiting", sending: "Sending", sent: "Sent", delivered: "Delivered", opened: "Opened", bounced: "Bounced", failed: "Failed", "not sent": "Not sent" };
const DELIVERY_PILL = { delivered: "green", opened: "green", bounced: "red", failed: "red", "not sent": "amber" };
// Who may send an email again (outbox.KIND_PERMS): whoever may send that kind.
const RESEND_PERM = { pay_app: "send_payapps", waiver: "send_waivers", retainage: "ask_retainage", pay_app_withdrawn: "void" };

export function recipientRows(e) {
  if ((e.recipients || []).length) return e.recipients;
  const st = unesc(e.status || "");
  const base = st.startsWith("bounced") ? "sent" : st.startsWith("failed") ? "failed" : st.startsWith("not sent") ? "not sent" : st;
  const refused = (e.refused || []).map(r => unesc(r).split(" (")[0].toLowerCase());
  const rows = [{ email: e.to, role: "to", status: st.startsWith("bounced") ? "bounced" : base, reason: st.startsWith("bounced") ? st.replace(/^bounced:\s*/, "") : "" }];
  for (const c of e.cc || []) {
    if (unesc(c).toLowerCase() !== unesc(e.to || "").toLowerCase()) rows.push({ email: c, role: "cc", status: refused.includes(unesc(c).toLowerCase()) ? "failed" : base === "bounced" ? "sent" : base });
  }
  return rows;
}

export function mayResend(e) {
  const perm = RESEND_PERM[unesc(e.kind || e.email_kind || "")];
  const st = unesc(e.status || "");
  return mailReady && !e.moot && !e.resend_moot && !e.sample && !st.includes("demo account") && st !== "queued" && st !== "sending" && (!perm || can(perm)) && !!me;
}

export function deliveryHtml(e) {
  const rows = recipientRows(e);
  const resend = mayResend(e);
  return `<ul class="plain-list delivery-list">${rows.map(r => {
    const st = String(r.status || "");
    const when = r.opened_at || r.bounced_at || r.delivered_at || r.sent_at || "";
    return `<li class="delivery-row"><span class="delivery-to">${r.email}${r.role === "cc" ? ' <span class="sub">(copy)</span>' : ""}</span>
      <span class="pill ${DELIVERY_PILL[st] || ""}">${DELIVERY_WORDS[st] || words(st)}</span>${when ? ` <span class="sub">${fmtDateTime(when)}</span>` : ""}${r.reason ? ` <span class="sub">${r.reason}</span>` : ""}${resend ? ` <button class="linkish" data-action="email-resend" data-email="${e.id}" data-to="${r.email}">Resend to this recipient</button>` : ""}</li>`;
  }).join("")}</ul>`;
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  outbox() {
    const page = listPage("outbox", outboxJobFilter);
    const emails = page ? page.entries : [];
    return `
      <h1>Outbox</h1>
      <p class="sub" id="outbox-mode"></p>
      ${jobFilterShown(state.jobs) ? `<div class="filter-bar">
        <label for="outbox-filter" class="inline-label">Job</label>
        <select id="outbox-filter" data-filter-for="outbox">
          <option value="all">All jobs</option>
          ${jobFilterOptions(state.jobs, outboxJobFilter)}
        </select>
      </div>` : ""}
      ${emails.length ? `<div class="table-wrap">
        <table>
          <tr><th>Subject</th><th>When</th><th>Kind</th><th>To</th><th>Status</th></tr>
          ${emails.map(e => `<tr><td><a href="${pathFor("job", e.job_id)}" data-action="open-job" data-id="${e.job_id}">${e.subject}</a><details class="email-details"><summary>Show message</summary><div class="sub email-body">${e.body || ""}</div></details>${attachmentsHtml(e)}<div class="row-actions"><button class="linkish" data-action="print-email" data-email="${e.id}">Print</button></div></td><td>${fmtDateTime(e.at)}</td><td>${words(e.kind)}</td><td>${e.to}${(e.cc || []).length ? `<div class="sub">Copy: ${e.cc.join(", ")}</div>` : ""}</td><td>${emailStatusHtml(e)}${shownDelivery(e) ? deliveryHtml(e) : ""}</td></tr>`).join("")}
        </table>
      </div>${moreButton("outbox", page)}` : !page ? `<p class="sub">Loading…</p>` : emptyState("No emails sent yet", "", `<button class="btn" data-action="nav" data-view="payapps">${termText("Pay apps")}</button>`)}
    `;
  },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  "email-retry": (el) => post(`/api/outbox/${el.dataset.email}/retry`),
  // One address only, as a new Outbox row (outbox.resend_to).
  "email-resend": (el) => post(`/api/outbox/${el.dataset.email}/resend`, { email: unesc(el.dataset.to || "") }),
  "print-email": (el) => {
    const e = ((listPage("outbox", outboxJobFilter) || {}).entries || []).find(x => x.id === el.dataset.email);
    if (!e) return;
    const fields = [["To", e.to], ...((e.cc || []).length ? [["Cc", e.cc.join(", ")]] : []), ["When", fmtDateTime(e.at)], ["Status", words(e.status || "")]];
    printSheet(fields, e.subject, e.body || "", attachmentNames(e, (state && state.files) || [], false));
  },
};
