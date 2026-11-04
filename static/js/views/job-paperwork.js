// The job page's client cards: what the client's playbook is missing, and the paperwork this job
// needs. The client's list is the default; a job can change its own copy, which applies from the
// next pay app and to a draft that hasn't been sent (domain.set_job_paperwork).
import { unesc } from "../format.js";
import { formErrorLine, refuseForm, formValues } from "../forms.js";
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
export function jobPaperworkHtml(j) {
  const view = j.paperwork_view;
  if (!view || view.legacy) return "";
  const key = `jobpaperwork-${j.id}`;
  const list = view.none ? "Nothing needed." : (view.labels || []).join(", ");
  return `<section class="card job-paperwork" aria-labelledby="job-paperwork-h">
      <h2 id="job-paperwork-h">Supporting documents for this job</h2>
      <p>${list}</p>
      <p class="sub">${view.own ? "Changed for this job." : `${j.payer.name}'s list.`} Changes apply from the next pay app and to a draft that hasn't been sent.</p>
      ${formOpen[key] ? `<div data-form-key="${key}">
        ${formErrorLine(key)}
        <div class="form-grid">${paperworkHtml(key, { packet_items: view.labels, no_paperwork: view.none }, "Supporting documents needed on this job")}</div>
        <div class="form-inline"><button type="button" class="btn small" data-action="save-job-paperwork" data-id="${j.id}">Save supporting documents</button>
          <button type="button" class="btn small secondary" data-action="toggle-form" data-form="${key}">Cancel</button></div>
      </div>` : `<div class="form-inline"><button type="button" class="btn small secondary" data-action="toggle-form" data-form="${key}" aria-expanded="false">Change for this job</button>
        ${view.own ? `<button type="button" class="btn small secondary" data-action="use-client-paperwork" data-id="${j.id}">Use ${j.payer.name}'s list</button>` : ""}</div>`}
    </section>`;
}

export const actions = {
  "save-job-paperwork": async (el, id) => {
    const key = `jobpaperwork-${id}`;
    const paperwork = paperworkValue(key, formValues(key).packet_other);
    if (refuseForm(key, paperwork && !paperwork.none && !paperwork.text ? { packet: "Tick what this job needs with each pay app, or Nothing needed." } : {})) return;
    const r = await post(`/api/jobs/${id}/paperwork`, paperwork && paperwork.none ? { no_paperwork: true } : { packet: paperwork ? paperwork.text : "" }, { formKey: key, close: key });
    if (r && r.ok) forgetPaperworkTicks(key);
  },
  "use-client-paperwork": async (el, id) => { await post(`/api/jobs/${id}/paperwork`, { use_client: true }); },
};
