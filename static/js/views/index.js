// Every page module's views and actions, merged in one place. app.js draws views[currentView]()
// and runs actions[name](el, el.dataset.id, event); a new page module is added to MODULES here
// and app.js does not change. Two modules may not use the same view or action name (the JS
// tests check it).
import * as dashboard from "./today.js";
import * as daily from "./daily.js";
import * as jobs from "./jobs.js";
import * as job from "./job.js";
import * as jobCos from "./job-cos.js";
import * as jobSov from "./job-sov.js";
import * as jobRecord from "./job-record.js";
import * as clients from "./clients.js";
import * as inbox from "./inbox.js";
import * as communications from "./communications.js";
import * as data from "./data.js";
import * as payapps from "./payapps.js";
import * as paperwork from "./paperwork.js";
import * as changeorders from "./changeorders.js";
import * as collections from "./collections.js";
import * as payments from "./payments.js";
import * as closeout from "./closeout.js";
import * as automations from "./automations.js";
import * as settings from "./settings.js";
import * as auth from "./auth.js";

// name -> module, in the order the menu lists them. A module may export views, actions or both.
export const MODULES = {
  dashboard, daily, jobs, job, jobCos, jobSov, jobRecord, clients,
  inbox, communications, data,
  payapps, paperwork, changeorders, collections, payments, closeout,
  automations, settings, auth,
};

export const views = Object.assign({}, ...Object.values(MODULES).map(m => m.views || {}));
export const actions = Object.assign({}, ...Object.values(MODULES).map(m => m.actions || {}));
