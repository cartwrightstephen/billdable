// The job page's Money card: contract, change orders, billed, received, outstanding and retainage,
// and the Retainage card beside it.
import { money, fmtDate, INVOICE_WORDS, words } from "../format.js";
import { retainageNote, closeoutMissing as closeoutMissingCount, retainageFacts, daysAgoText, waiverKindLabel } from "../jobs.js";
import { pathFor } from "../routes.js";
import { state } from "../state.js";


export function moneyCardHtml(j) {
  const m = j.money;
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
          <div class="money-row"><span class="label">Held back, still owed</span><span>${money(m.retainage_open_cents)}</span></div>
        </details>
        ${retainageCardHtml(j)}
      `;
}

// The links to the closeout paperwork's files on the job record (M26).
function closeoutFileLinks(j) {
  const records = Object.fromEntries((j.records || []).map(r => [r.id, r]));
  const files = Object.fromEntries((state && state.files || []).map(f => [f.id, f]));
  const seen = new Set();
  return (j.closeout_packet || []).flatMap(p => (p.attachments || []).flatMap(a => ((records[a.record_id] || {}).file_ids || [])))
    .filter(fid => files[fid] && !seen.has(fid) && seen.add(fid))
    .map(fid => `<a href="/api/files/${fid}" target="_blank" rel="noopener">${files[fid].name}</a>`).join(" · ");
}

// The Retainage card (M23, M25, M26): always open, whenever the job holds retainage or has a rate.
export function retainageCardHtml(j, now = new Date()) {
  const m = j.money;
  if (!(Number(j.retainage_pct) > 0) && !(m.retainage_held_cents > 0)) return "";
  const f = retainageFacts(j, now);
  const note = retainageNote(j, now);
  const missing = closeoutMissingCount(j);
  const links = closeoutFileLinks(j);
  const waivers = j.retainage_waivers || [];
  return `<section class="card retainage-card" aria-labelledby="retainage-h-${j.id}">
      <h2 id="retainage-h-${j.id}">Retainage</h2>
      ${f.pct > 0 ? `<div class="money-row"><span class="label">Rate</span><span>${f.pct}%</span></div>` : ""}
      <div class="money-row"><span class="label">Retainage held</span><span>${money(f.held)}</span></div>
      <div class="money-row"><span class="label">Retainage released</span><span>${money(f.released)}</span></div>
      <div class="money-row strong"><span class="label">Still held</span><span>${money(f.open)}</span></div>
      ${f.doneDays != null ? `<div class="money-row"><span class="label">Job done</span><span>${daysAgoText(f.doneDays)}</span></div>` : ""}
      <div class="money-row"><span class="label">Times asked</span><span>${f.asked}</span></div>
      ${f.last ? `<div class="money-row"><span class="label">Last asked</span><span>${fmtDate(f.last.toISOString())}, ${daysAgoText(f.askedDays)}</span></div>
      <div class="money-row"><span class="label">Next ask allowed</span><span>${fmtDate(f.next.toISOString())}</span></div>` : ""}
      ${note ? `<p class="sub retainage-note">${note}</p>` : ""}
      <h3>Pay apps holding it</h3>
      ${f.payApps.length ? `<ul class="plain-list">${f.payApps.map(i => `<li><a href="${pathFor("payapp-detail", j.id, i.id)}" data-action="open-payapp" data-job="${j.id}" data-inv="${i.id}">${i.number}</a> · ${money(i.retainage_cents)} · ${INVOICE_WORDS[i.status] || words(i.status)}</li>`).join("")}</ul>` : `<p class="sub">${f.held > 0 ? "Not recorded on each pay app." : "None yet."}</p>`}
      <h3>Releases</h3>
      ${f.releases.length ? `<ul class="plain-list">${f.releases.map(r => {
        const w = waivers.find(x => x.at === r.at);
        return `<li>${money(r.amount_cents)} · ${r.reference} · ${fmtDate(r.at)} · ${r.recorded_by}${w ? ` · ${w.waiver_sent ? `waiver sent (${waiverKindLabel(w.kind)})` : `<a href="${pathFor("waiver-detail", j.id)}" data-action="open-waiver" data-id="${j.id}">waiver due</a>`}` : ""}</li>`;
      }).join("")}</ul>` : `<p class="sub">None yet.</p>`}
      ${(j.closeout_packet || []).length ? `<p class="retainage-closeout"><a href="${pathFor("packet-detail", j.id, "closeout")}" data-action="open-packet" data-job="${j.id}" data-inv="closeout">Closeout paperwork</a> · ${missing ? `${missing} missing` : "complete"}${links ? `<span class="sub row-sub">${links}</span>` : ""}</p>` : ""}
    </section>`;
}
