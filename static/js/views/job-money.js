// The job page's Money card: contract, change orders, billed, received, outstanding and retainage,
// with the retainage rate as its last line.
import { money } from "../format.js";


// open: unfolded (a phone starts it folded, Round 1 item 13); the summary line has the four figures.
export function moneyCardHtml(j, open = true) {
  const m = j.money;
  return `<details class="card money-file" data-more="job-money"${open ? " open" : ""}>
          <summary><h2>Money</h2><span class="sub">${money(m.contract_total_cents)} contract · ${money(m.billed_cents)} billed · ${money(m.collected_cents)} received · ${money(m.outstanding_cents)} owed</span></summary>
          <div class="money-row"><span class="label">Contract</span><span>${money(m.contract_cents)}</span></div>
          <div class="money-row"><span class="label">Signed change orders</span><span>${money(m.approved_co_cents)}</span></div>
          <div class="money-row"><span class="label">Unsigned change orders</span><span>${money(m.pending_co_cents)}</span></div>
          <div class="money-row strong"><span class="label">Contract total</span><span>${money(m.contract_total_cents)}</span></div>
          <div class="money-row"><span class="label">Billed</span><span>${money(m.billed_cents)}</span></div>
          <div class="money-row"><span class="label" data-help="collected">Received</span><span>${money(m.collected_cents)}</span></div>
          ${m.refunded_cents ? `<div class="money-row"><span class="label">Refunded or credited (taken off Received)</span><span>${money(m.refunded_cents)}</span></div>` : ""}
          ${m.short_paid_cents ? `<div class="money-row"><span class="label">Short pay accepted</span><span>${money(m.short_paid_cents)}</span></div>` : ""}
          <div class="money-row strong"><span class="label">Outstanding</span><span>${money(m.outstanding_cents)}</span></div>
          <div class="money-row"><span class="label">Done, not billed</span><span>${money(m.unbilled_completed_cents)}</span></div>
          ${m.retainage_credited_cents ? `<div class="money-row"><span class="label">Signed credit taken off retainage</span><span>${money(m.retainage_credited_cents)}</span></div>` : ""}
          <div class="money-row"><span class="label">Held back, still owed</span><span>${money(m.retainage_open_cents)}</span></div>
          <div class="money-row retainage-rate"><span class="label">Retainage</span><span>${retainagePctText(j)}</span></div>
        </details>
      `;
}

// The job's retainage rate: "10%", "0%" (Yusuf, 10 Nov: one line in Money, no Retainage card).
export function retainagePctText(j) {
  return `${+(Number(j.retainage_pct) || 0).toFixed(2)}%`;
}
