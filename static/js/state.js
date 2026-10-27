// The page's shared state: the company's data, who is signed in, which page is open and which
// forms are open. Other modules import these as live bindings (an import always sees the current
// value) and change them only through the setters here, so a view moved out of app.js reads and
// writes the same state as the rest of the page.

export let state = null;            // the company's data from /api/state, escaped (escDeep)
export let me = null;               // the signed-in user, escaped
export let currentView = "dashboard";
export let currentJobId = null;
export let currentDetailId = null;  // for packet, co, payapp, waiver detail
export let formOpen = {};           // form key -> shown

export function setData(data) { state = data; }

export function setMe(user) { me = user; }

// Signed out (or the session ended): nothing of the company stays in the page.
export function clearSession() { me = null; state = null; }

export function setLocation(view, jobId = null, detailId = null) {
  currentView = view;
  currentJobId = jobId || null;
  currentDetailId = detailId || null;
}

export function closeForms() { formOpen = {}; }
