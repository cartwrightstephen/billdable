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

// Signed out (or the session ended): nothing of the company stays in the page. What a module
// keeps of it outside `state` (a job fetched whole, the Inbox addresses, Results, QuickBooks
// rows, filters on a job) it forgets here too, through onSessionEnd, so the next company to sign
// in in this tab never sees it, whether after Log out or a session that ran out.
const forgetters = [];
export function onSessionEnd(fn) { forgetters.push(fn); }
export function clearSession() {
  me = null;
  state = null;
  teamData = null;
  formOpen = {};
  forgetters.forEach(fn => fn());
}

export function setLocation(view, jobId = null, detailId = null) {
  currentView = view;
  currentJobId = jobId || null;
  currentDetailId = detailId || null;
}

export function closeForms() { formOpen = {}; }

// The shell and Today, reset on every page change (nav): the phone menu, and Today's "and N
// more" (tapped: all the rows; back to three on the next page).
export let drawerOpen = false;
export let queueExpanded = false;

export function setDrawerOpen(on) { drawerOpen = on; }

export function setQueueExpanded(on) { queueExpanded = on; }

// A page address opened while signed out, opened after sign-in, and its ?camera=1 or ?new=1
// from a home-screen shortcut. cameraAsk: opened from the Take a photo shortcut, so the Inbox's
// camera button gets the focus.
export let pendingPath = null;
export let pendingSearch = null;
export let cameraAsk = false;

export function setPending(path, search) { pendingPath = path; pendingSearch = search; }

export function setCameraAsk(on) { cameraAsk = on; }

// What /api/auth/me says about this session (escaped where shown).
export let helperAdmin = false;   // sees Settings, Helper answers (the Duracash team)
export let mailReady = false;     // the server sends mail: nothing goes out until the email is confirmed
export let readOnly = "";         // why this company can't be changed (the read-only demo, or a founder's hold)
export let billingInfo = null;    // billing.standing: configured, status, warning
export let heldShare = 0;         // items shared to the app from this browser before signing in, waiting (R6-10)
export let idleMinutes = 60;      // the server signs a session out after this long without use (G2)

// Sets the fields given: { helperAdmin, mailReady, readOnly, billingInfo, heldShare }.
export function setAccount(info) {
  if ("helperAdmin" in info) helperAdmin = info.helperAdmin;
  if ("mailReady" in info) mailReady = info.mailReady;
  if ("readOnly" in info) readOnly = info.readOnly;
  if ("billingInfo" in info) billingInfo = info.billingInfo;
  if ("heldShare" in info) heldShare = info.heldShare;
  if ("idleMinutes" in info && Number(info.idleMinutes) > 0) idleMinutes = Number(info.idleMinutes);
}

// Why the sign-in page is showing, when the server or the idle timer ended the session (G2):
// "Signed out after an hour without use." Shown once on Sign in.
export let signedOutNote = "";

export function setSignedOutNote(text) { signedOutNote = text || ""; }

// { email, verification } (escaped) right after sign-up: the "Check your email" panel.
export let checkEmail = null;

export function setCheckEmail(value) { checkEmail = value; }

// { users, invites } from /api/team (escaped); asked for again each time Team opens.
export let teamData = null;

export function setTeamData(data) { teamData = data; }
