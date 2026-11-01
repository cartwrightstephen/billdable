// Where the person has been in this tab (G3): the pages nav() opened, oldest first, so the
// in-app Back button can name the page they came from and go back to it (history.back()).
// It holds only what the app itself pushed: a page opened from a link or a refresh starts it again,
// and Back then falls back to the page's fixed parent. No DOM here, so node can test it.

const MAX = 50;
let trail = [];   // [{view, jobId, detailId}]

const same = (a, b) => !!a && !!b && a.view === b.view && (a.jobId || null) === (b.jobId || null) && (a.detailId || null) === (b.detailId || null);
const entry = (view, jobId = null, detailId = null) => ({ view, jobId: jobId || null, detailId: detailId || null });

// Start again from one page (arrived from outside the app, or signed out).
export function resetTrail(view, jobId = null, detailId = null) {
  trail = view ? [entry(view, jobId, detailId)] : [];
}

// A page opened by nav(). how: "push" (a new history entry), "replace" (the same entry, another
// page) or "history" (Back or Forward moved the address).
export function stepTrail(view, jobId = null, detailId = null, how = "push") {
  const e = entry(view, jobId, detailId);
  if (same(trail[trail.length - 1], e)) return;
  if (how === "history") {
    // Back to the page before: drop the one left. Anything else (Forward, a jump of several
    // pages) is not known here, so the trail starts again at this page.
    if (same(trail[trail.length - 2], e)) trail.pop();
    else trail = [e];
    return;
  }
  if (how === "replace" && trail.length) trail[trail.length - 1] = e;
  else trail.push(e);
  if (trail.length > MAX) trail = trail.slice(-MAX);
}

// The page before this one in the trail, or null when the person arrived here from outside.
export function previousPage() {
  return trail.length > 1 ? { ...trail[trail.length - 2] } : null;
}

export function trailLength() { return trail.length; }
