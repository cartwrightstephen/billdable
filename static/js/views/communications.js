// Communications: the Outbox (every approved send and follow-up, with what became of it). The
// Inbox is in inbox.js.
import { fmtDateTime, words, emailStatusHtml } from "../format.js";
import { pathFor } from "../routes.js";
import { moreButton } from "../dom.js";
import { state, onSessionEnd } from "../state.js";
import { post, emptyState, listPage } from "../app-core.js";

let outboxJobFilter = "all";
export function setOutboxJobFilter(value) { outboxJobFilter = value; }

// Signed out: a job picked in the filter is that company's.
onSessionEnd(() => { outboxJobFilter = "all"; });

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
          ${emails.map(e => `<tr><td><a href="${pathFor("job", e.job_id)}" data-action="open-job" data-id="${e.job_id}">${e.subject}</a><details class="email-details"><summary>Show message</summary><div class="sub email-body">${e.body || ""}</div></details>${e.payapp_invoice_id ? `<div class="sub">Pay app PDF attached</div>` : ""}${(e.attachments || []).length ? `<div class="sub">${e.attachments.length} attachment${e.attachments.length === 1 ? "" : "s"}${(e.not_attached || []).length ? "; not attached: " + e.not_attached.join("; ") : ""}</div>` : ""}</td><td>${fmtDateTime(e.at)}</td><td>${words(e.kind)}</td><td>${e.to}</td><td>${emailStatusHtml(e)}</td></tr>`).join("")}
        </table>
      </div>${moreButton("outbox", page)}` : !page ? `<p class="sub">Loading…</p>` : emptyState("Outbox is empty")}
    `;
  },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  "email-retry": (el) => post(`/api/outbox/${el.dataset.email}/retry`),
};
