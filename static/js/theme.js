// Appearance (G9): each user picks a theme (System, Light or Dark) and an accent colour, kept on
// their account (POST /api/me/appearance) and shown through data-theme and data-accent on <html>,
// which styles.css reads; no inline style, as the CSP requires. A copy in this browser's
// localStorage is applied as soon as this module loads, so a dark page doesn't flash light while
// /api/auth/me is asked. The pay app PDF and the client's page are not affected.

export const THEMES = [["system", "Same as this device"], ["light", "Light"], ["dark", "Dark"]];
export const ACCENTS = [["copper", "Copper"], ["blue", "Blue"], ["green", "Green"], ["slate", "Slate"]];
export const DEFAULT_APPEARANCE = { theme: "system", accent: "copper" };
const KEY = "duracash-appearance";
// The browser bar's colour (index.html's theme-color): the accent in light, the page in dark.
const BAR = { copper: "#8c3f1d", blue: "#1f4f8a", green: "#1f6b45", slate: "#3d4a5c", dark: "#1a1714" };

export function normalizeAppearance(a) {
  const theme = a && THEMES.some(([k]) => k === a.theme) ? a.theme : DEFAULT_APPEARANCE.theme;
  const accent = a && ACCENTS.some(([k]) => k === a.accent) ? a.accent : DEFAULT_APPEARANCE.accent;
  return { theme, accent };
}

function prefersDark() {
  try { return !!(globalThis.matchMedia && globalThis.matchMedia("(prefers-color-scheme: dark)").matches); } catch (_) { return false; }
}

export function applyAppearance(a, { remember = true } = {}) {
  const { theme, accent } = normalizeAppearance(a);
  const root = globalThis.document && document.documentElement;
  if (root && root.dataset) {
    if (theme === "system") delete root.dataset.theme; else root.dataset.theme = theme;
    if (accent === "copper") delete root.dataset.accent; else root.dataset.accent = accent;
  }
  const bar = globalThis.document && typeof document.querySelector === "function" && document.querySelector('meta[name="theme-color"]');
  const dark = theme === "dark" || (theme === "system" && prefersDark());
  if (bar && bar.setAttribute) bar.setAttribute("content", dark ? BAR.dark : BAR[accent]);
  if (remember) {
    try { localStorage.setItem(KEY, JSON.stringify({ theme, accent })); } catch (_) { /* private window */ }
  }
  return { theme, accent };
}

export function storedAppearance() {
  try { return normalizeAppearance(JSON.parse(localStorage.getItem(KEY) || "null")); } catch (_) { return { ...DEFAULT_APPEARANCE }; }
}

// At load, before the first page is drawn.
applyAppearance(storedAppearance(), { remember: false });
