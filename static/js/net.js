// Talking to the server: JSON replies, the offline banner, and links that carry a token.

export function setNetBanner(on) {
  let el = document.getElementById("net-banner");
  if (!el && on) {
    el = document.createElement("div");
    el.id = "net-banner";
    el.className = "net-banner";
    el.textContent = "Can't reach Duracash. Your last change may not be saved. Check your connection and try again.";
    document.body.prepend(el);
  }
  if (el) el.style.display = on ? "block" : "none";
}

export async function readJson(res) {
  try { return await res.json(); } catch (_) { return {}; }
}

export function readTokenFromUrl() {
  const m = /^#(reset|verify|invite)=([A-Za-z0-9_-]{20,200})$/.exec(location.hash || "");
  if (!m) return null;
  history.replaceState(null, "", location.pathname);  // keep the token out of the address bar and history
  return { kind: m[1], token: m[2] };
}

export async function postJson(path, body) {
  try {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    return { ok: res.ok, status: res.status, data: await readJson(res), res };
  } catch (_) {
    setNetBanner(true);
    return { ok: false, status: 0, data: { error: "Can't reach Duracash." } };
  }
}

// Runs fn on each item with at most `limit` running at once (R6-04: a phone's five photos go up
// two or three at a time, not one by one). onDone(done, total) is told after each one finishes.
// Resolves with fn's results in the items' order.
export async function inBatches(items, limit, fn, onDone = () => {}) {
  const out = new Array(items.length);
  let next = 0, done = 0;
  async function worker() {
    while (next < items.length) {
      const i = next++;
      out[i] = await fn(items[i], i);
      onDone(++done, items.length);
    }
  }
  await Promise.all(Array.from({ length: Math.max(1, Math.min(limit, items.length)) }, worker));
  return out;
}

// A file (or text, sent as a .txt) as the raw body, its name in X-Filename, as the job record's
// uploads and the contract reader take it. {ok, status, data}, like postJson.
export async function sendFile(path, body, name, type = "") {
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": type || (body && body.type) || "application/octet-stream", "X-Filename": encodeURIComponent(name || "file") },
      body,
    });
    return { ok: res.ok, status: res.status, data: await readJson(res), res };
  } catch (_) {
    setNetBanner(true);
    return { ok: false, status: 0, data: { error: "Can't reach Duracash." } };
  }
}
