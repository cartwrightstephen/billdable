// The job page's client cards: what the client's playbook is missing, and the paperwork this job
// needs. The client's list is the default; a job can change its own copy, which applies from the
// next pay app and to a draft that hasn't been sent (domain.set_job_paperwork).
import { unesc, fmtDate } from "../format.js";
import { formErrorLine, refuseForm, formValues, field } from "../forms.js";
import { formOpen } from "../state.js";
import { post } from "../app-core.js";
import { paperworkHtml, paperworkValue, forgetPaperworkTicks } from "./clients.js";

const WORDS = { client_type: "the client type", approver: "who signs off", ap_email: "email for pay apps", phone: "phone" };

// What holds up this job's pay apps, with the way to fix it. A job made before a new job needed a
// complete playbook isn't held up, so it says nothing (Decision 3).
export function playbookMissingHtml(j) {
  const missing = (j.playbook_missing || []).map(k => WORDS[unesc(k)]).filter(Boolean);
  if (!j.playbook_blocks || !missing.length) return "";
  return `<section class="card playbook-missing" aria-label="Client playbook">
      <p><strong>${j.payer.name}'s Client playbook is missing ${missing.join(", ")}.</strong> A pay app can't start or go out until it is added.</p>
      <div class="form-inline"><button type="button" class="btn small secondary" data-action="open-client-card" data-id="${j.payer.id}">Open Client playbook</button></div>
    </section>`;
}

// The paperwork this job asks for with each pay app. A job on the older job-level paperwork has
// none of its own (the server refuses it).
// A fold whose summary is the list itself (Round 1, item 13); open while it is being changed.
// Hard and soft gates (Stephen, Nov 26): each document is Required (hard stop) or Check (soft).
export const GATE_WORDS = { hard: "Required (hard stop)", soft: "Check (soft)" };
export function gateRowsHtml(view) {
  const rows = view.gates || [];
  if (!rows.length) return "";
  return `<ul class="gate-list">${rows.map(g => `<li><span>${g.label}</span> <span class="pill ${g.gate === "soft" ? "gate-soft" : "gate-hard"}">${GATE_WORDS[unesc(g.gate)] || ""}</span>${g.question ? ` <span class="sub">${g.question}</span>` : ""}</li>`).join("")}</ul>`;
}
function gateFieldsHtml(key, view) {
  const rows = view.gates || [];
  if (!rows.length) return "";
  return `<fieldset class="span-2 gate-choices"><legend>Hard stop or check, on each pay app</legend>
    <p class="sub">Required: the pay app doesn't go without it, unless someone who can send pay apps overrides it with a reason. Check: whoever sends says it was checked or isn't needed, and it can go.</p>
    ${rows.map(g => field({ form: key, name: `gate_${unesc(g.key)}`, id: `${key}-gate-${unesc(g.key)}`, label: unesc(g.label), value: unesc(g.gate),
      options: [["hard", GATE_WORDS.hard], ["soft", GATE_WORDS.soft]] })).join("")}
  </fieldset>`;
}
// The gate boxes of a form, as the save sends them: {key: "hard" | "soft"}.
export function gateValues(v) {
  const out = {};
  for (const [k, val] of Object.entries(v)) if (k.startsWith("gate_") && (val === "hard" || val === "soft")) out[k.slice(5)] = val;
  return out;
}

export function jobPaperworkHtml(j, open = true) {
  const view = j.paperwork_view;
  if (!view || view.legacy) return "";
  const key = `jobpaperwork-${j.id}`;
  const list = view.none ? "Nothing needed" : (view.labels || []).join(", ");
  return `<details class="card job-paperwork" data-more="job-docs"${open || formOpen[key] ? " open" : ""}>
      <summary><h2 id="job-paperwork-h">Supporting documents for this job</h2><span class="sub">${list}</span></summary>
      ${view.waiver_skipped ? `<p class="override-note">Conditional waiver not needed on this job. Marked by ${view.waiver_skipped.by} on ${fmtDate(view.waiver_skipped.at)}: ${view.waiver_skipped.reason} <button type="button" class="btn small secondary" data-action="waiver-needed" data-id="${j.id}">Needed after all</button></p>` : ""}
      <p class="sub">${view.own ? "Changed for this job." : `${j.payer.name}'s list.`} Changes apply from the next pay app and to a draft that hasn't been sent.</p>
      ${formOpen[key] ? "" : gateRowsHtml(view)}
      ${formOpen[key] ? `<div data-form-key="${key}">
        ${formErrorLine(key)}
        <div class="form-grid">${paperworkHtml(key, { packet_items: view.labels, no_paperwork: view.none }, "Supporting documents needed on this job")}
          ${gateFieldsHtml(key, view)}</div>
        <div class="form-inline"><button type="button" class="btn small" data-action="save-job-paperwork" data-id="${j.id}">Save supporting documents</button>
          <button type="button" class="btn small secondary" data-action="toggle-form" data-form="${key}">Cancel</button></div>
      </div>` : `<div class="form-inline"><button type="button" class="btn small secondary" data-action="toggle-form" data-form="${key}" aria-expanded="false">Change for this job</button>
        ${view.own ? `<button type="button" class="btn small secondary" data-action="use-client-paperwork" data-id="${j.id}">Use ${j.payer.name}'s list</button>` : ""}</div>`}
    </details>`;
}

export const actions = {
  "save-job-paperwork": async (el, id) => {
    const key = `jobpaperwork-${id}`;
    const v = formValues(key);
    const paperwork = paperworkValue(key, v.packet_other);
    if (refuseForm(key, paperwork && !paperwork.none && !paperwork.text ? { packet: "Tick what this job needs with each pay app, or Nothing needed." } : {})) return;
    const gates = gateValues(v);
    const r = await post(`/api/jobs/${id}/paperwork`, { ...(paperwork && paperwork.none ? { no_paperwork: true } : { packet: paperwork ? paperwork.text : "" }), ...(Object.keys(gates).length ? { gates } : {}) }, { formKey: key, close: key });
    if (r && r.ok) forgetPaperworkTicks(key);
  },
  "use-client-paperwork": async (el, id) => { await post(`/api/jobs/${id}/paperwork`, { use_client: true }); },
};
