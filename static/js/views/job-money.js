// The job page's Money card: contract, change orders, billed, received, outstanding and retainage.
import { money } from "../format.js";
import { retainageNote, retainageAskWaits, closeoutMissing as closeoutMissingCount } from "../jobs.js";
import { pathFor } from "../routes.js";
import { nav } from "../app-core.js";


export function moneyCardHtml(j) {
  const m = j.money;
  const retNote = retainageNote(j);
  const closeoutMissing = closeoutMissingCount(j);
  return `<details class="card money-file"${window.innerWidth >= 800 ? " open" : ""}>
          <summary><h2>Money</h2><span class="sub">${money(m.contract_total_cents)} contract · ${money(m.billed_cents)} billed · ${money(m.collected_cents)} received · ${money(m.outstanding_cents)} owed</span></summary>
          <div class="money-row"><span class="label">Contract</span><span>${money(m.contract_cents)}</span></div>
          <div class="money-row"><span class="label">Signed change orders</span><span>${money(m.approved_co_cents)}</span></div>
          <div class="money-row"><span class="label">Unsigned change orders</span><span>${money(m.pending_co_cents)}</span></div>
          <div class="money-row strong"><span class="label">Contract total</span><span>${money(m.contract_total_cents)}</span></div>
          <div class="money-row"><span class="label">Billed</span><span>${money(m.billed_cents)}</span></div>
          <div class="money-row"><span class="label" data-help="collected">Received</span><span>${money(m.collected_cents)}</span></div>
          ${m.short_paid_cents ? `<div class="money-row"><span class="label">Short pay accepted</span><span>${money(m.short_paid_cents)}</span></div>` : ""}
          <div class="money-row strong"><span class="label">Outstanding</span><span>${money(m.outstanding_cents)}</span></div>
          <div class="money-row"><span class="label">Done, not billed</span><span>${money(m.unbilled_completed_cents)}</span></div>
          <div class="money-row"><span class="label">Retainage held</span><span>${money(m.retainage_held_cents)}${j.retainage_pct ? " · " + j.retainage_pct + "%" : ""}</span></div>
          <div class="money-row"><span class="label">Retainage released</span><span>${money(m.retainage_released_cents)}</span></div>
          <div class="money-row"><span class="label">Held back, still owed</span><span>${money(m.retainage_open_cents)}</span></div>
          ${retNote ? `<p class="sub retainage-note">${closeoutMissing && j.work_status === "complete" ? `<a href="${pathFor("packet-detail", j.id, "closeout")}" data-action="open-packet" data-job="${j.id}" data-inv="closeout">${retNote}</a>` : `${retNote}${j.work_status !== "complete" && !retainageAskWaits(j) ? ` <button class="linkish" data-action="nav" data-view="retainage">Ask for part of it</button>` : ""}`}</p>` : ""}
        </details>
      `;
}
