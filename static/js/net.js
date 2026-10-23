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
