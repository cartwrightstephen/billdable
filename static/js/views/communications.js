// Communications: the Outbox (every approved send and follow-up, with what became of it, its
// attachments and a Print), and the print sheet the Outbox and the Inbox share. The Inbox is in
// inbox.js.
import { fmtDateTime, words, emailStatusHtml } from "../format.js";
import { pathFor } from "../routes.js";
import { moreButton } from "../dom.js";
import { state, onSessionEnd } from "../state.js";
import { post, emptyState, listPage } from "../app-core.js";

let outboxJobFilter = "all";
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
    out.push(!f ? "A file no longer on the job record" : links ? `<a href="/api/files/${f.id}" target="_blank" rel="noopener">${f.name}</a>` : f.name);
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

// This module's pages, by the name their address opens (routes.js).
export const views = {
  outbox() {
    const page = listPage("outbox", outboxJobFilter);
    const emails = page ? page.entries : [];
    return `
      <h1>Outbox</h1>
      <p class="sub" id="outbox-mode"></p>
      <div class="filter-bar">
        <label for="outbox-filter" class="inline-label">Job</label>
        <select id="outbox-filter" data-filter-for="outbox">
          <option value="all">All jobs</option>
          ${state.jobs.map(j => `<option value="${j.id}" ${outboxJobFilter === j.id ? "selected" : ""}>${j.name}</option>`).join("")}
        </select>
      </div>
      ${emails.length ? `<div class="table-wrap">
        <table>
          <tr><th>Subject</th><th>When</th><th>Kind</th><th>To</th><th>Status</th></tr>
          ${emails.map(e => `<tr><td><a href="${pathFor("job", e.job_id)}" data-action="open-job" data-id="${e.job_id}">${e.subject}</a><details class="email-details"><summary>Show message</summary><div class="sub email-body">${e.body || ""}</div></details>${attachmentsHtml(e)}<div class="row-actions"><button class="linkish" data-action="print-email" data-email="${e.id}">Print</button></div></td><td>${fmtDateTime(e.at)}</td><td>${words(e.kind)}</td><td>${e.to}</td><td>${emailStatusHtml(e)}</td></tr>`).join("")}
        </table>
      </div>${moreButton("outbox", page)}` : !page ? `<p class="sub">Loading…</p>` : emptyState("Outbox is empty")}
    `;
  },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  "email-retry": (el) => post(`/api/outbox/${el.dataset.email}/retry`),
  "print-email": (el) => {
    const e = ((listPage("outbox", outboxJobFilter) || {}).entries || []).find(x => x.id === el.dataset.email);
    if (!e) return;
    const fields = [["To", e.to], ...((e.cc || []).length ? [["Cc", e.cc.join(", ")]] : []), ["When", fmtDateTime(e.at)], ["Status", words(e.status || "")]];
    printSheet(fields, e.subject, e.body || "", attachmentNames(e, (state && state.files) || [], false));
  },
};
