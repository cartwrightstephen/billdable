// Money, Unpaid: what is unpaid, who to contact, and the follow-up.
import { unesc, money, toDate, fmtDate, fmtDateTime } from "../format.js";
import { statusPill, followUpDue, payAppList } from "../jobs.js";
import { pathFor } from "../routes.js";
import { askConfirm } from "../dom.js";
import { termText } from "../terms.js";
import { state } from "../state.js";
import { post, emptyState } from "../app-core.js";
import { owedText, shortPayButtons, sendsTo } from "./job.js";


// Recent contact with the client (Stephen's Oct 29 review, M11): the last emails, calls and
// client page visits, newest first, from the server (domain.contact_log), closed until opened.
export function recentContactHtml(j) {
  const rows = j.recent_contact || [];
  if (!rows.length) return "";
  return `<details class="recent-contact"><summary>Recent contact (${j.contact_total || rows.length})</summary>
    <ul>${rows.map(c => `<li><span class="sub">${fmtDateTime(c.at)}</span> <strong>${c.label}</strong>${c.status ? ` · ${c.status}` : ""}${c.text ? `<div>${c.text}</div>` : ""}${c.by ? `<div class="sub">${c.by}</div>` : ""}</li>`).join("")}</ul>
    <a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}" data-focus=".job-history">See all on the job</a>
  </details>`;
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  collections() {
    const open = state.jobs.filter(j => j.money.outstanding_cents > 0);
    const today = new Date();
    return `
      <h1>Unpaid</h1>
      ${open.length ? `<div class="table-wrap">
        <table>
          <tr><th>Job</th><th>Unpaid</th><th>Days since sent</th><th>Due</th><th>Expected</th><th>Contact</th><th>Follow up</th></tr>
          ${open.map(j => {
            const sent = j.invoices.find(i => ["sent", "partial", "approved"].includes(i.status) && i.sent_at);
            const days = sent ? Math.floor((today - toDate(sent.sent_at)) / 86400000) : "—";
            const unpaid = j.invoices.filter(i => ["sent", "partial", "approved"].includes(i.status)).map(i => i.number + " " + owedText(i)).join(", ") || money(j.money.outstanding_cents);
            // Inside the client's days between follow-ups the button waits, and says until when (R3-03).
            const follow = followUpDue(j);
            const early = follow && today < follow.due;
            // From the client's payment terms, on the oldest unpaid pay app that has them (R4-04).
            const dueInv = j.invoices.find(i => ["sent", "partial", "approved"].includes(i.status) && i.due_on);
            const late = dueInv && toDate(dueInv.due_on) < new Date(today.toDateString());
            // When the money should come: the date given with the client's approval (R4-08), or
            // the one noted after a call (R4-06).
            const approved = j.invoices.find(i => i.status === "approved" && i.expected_payment_on);
            const expected = approved ? `${fmtDate(approved.expected_payment_on)}<div class="sub row-sub">${approved.number} approved by ${approved.approved_by_payer || "the client"}</div>`
              : j.expected_payment_on ? `${fmtDate(j.expected_payment_on)}<div class="sub row-sub">they said on the phone</div>` : "—";
            return `<tr>
              <td><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a><div class="sub row-sub">${statusPill(j)}</div>${recentContactHtml(j)}</td>
              <td>${unpaid}</td>
              <td class="num">${days}</td>
              <td>${dueInv ? (late ? `<span class="pill red">Overdue</span><div class="sub row-sub">due ${fmtDate(dueInv.due_on)}</div>` : fmtDate(dueInv.due_on)) : "—"}</td>
              <td>${expected}</td>
              <td>${j.payer.approver}<div class="sub row-sub">${j.payer.ap_email}${j.payer.phone ? "<br>" + j.payer.phone : ""}</div></td>
              <td class="actions-cell">
                ${early ? `<button class="btn small" disabled>Next follow-up ${fmtDate(follow.day)}</button>` : `<button class="btn small" data-action="follow-up" data-id="${j.id}">Send follow-up</button>`}
                ${shortPayButtons(j)}
                <a class="btn small secondary" href="mailto:${j.payer.ap_email}">Email</a>
                ${j.payer.phone ? `<a class="btn small secondary" href="tel:${(j.payer.phone || "").replace(/[^0-9+]/g, "")}">Call</a>` : ""}
              </td>
            </tr>`;
          }).join("")}
        </table>
      </div>` : emptyState("Nothing unpaid")}
    `;
  },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  "follow-up": async (el, id) => {
    const j = state.jobs.find(x => x.id === id);
    const follow = j && followUpDue(j);
    // "Follow up now" on the job page goes before the client's days between follow-ups are up (R3-04).
    const early = !!el.dataset.early && follow && follow.due > new Date();
    if (j && !(await askConfirm(`Email a follow-up on ${unesc(follow ? payAppList(follow.invoices) : "the pay app")} to ${sendsTo(j)}?${early ? ` It's before the follow-up date, ${fmtDate(follow.day)}.` : ""}`, "Send follow-up"))) return;
    await post(`/api/jobs/${id}/follow-up`, early ? { early: true } : {});
  },
};
