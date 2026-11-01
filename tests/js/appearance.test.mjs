// G9: the dark palette and the accents. Each accent reads against the page in both palettes, and
// the text on an accent button reads against it; theme.js keeps only the allowed choices.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { normalizeAppearance, THEMES, ACCENTS } from "../../static/js/theme.js";

const css = readFileSync(new URL("../../static/styles.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

// The tokens a selector's block sets (the last block with exactly this selector).
function tokens(selector) {
  const out = {};
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (m[1].trim() !== selector) continue;
    for (const t of m[2].matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out[t[1]] = t[2].trim();
  }
  return out;
}

function lum(hex) {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map(c => c + c).join("") : h;
  const [r, g, b] = [0, 2, 4].map(i => parseInt(full.slice(i, i + 2), 16) / 255).map(c => c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

const light = tokens(":root");
const dark = tokens(':root[data-theme="dark"]');
const palettes = {
  light: (accent) => ({ ...light, ...(accent === "copper" ? {} : tokens(`:root[data-accent="${accent}"]`)) }),
  dark: (accent) => ({ ...light, ...dark, ...(accent === "copper" ? {} : tokens(`:root[data-theme="dark"][data-accent="${accent}"]`)) }),
};

test("the dark palette sets every colour the light one does", () => {
  const missing = Object.keys(light).filter(k => !(k in dark));
  assert.deepEqual(missing, []);
  assert.deepEqual(tokens(':root:not([data-theme="light"])'), dark, "the device-dark block is the same palette");
});

test("each accent reads against the page, and text on it reads, in light and dark", () => {
  for (const [name, pick] of Object.entries(palettes)) {
    for (const [accent] of ACCENTS) {
      const t = pick(accent);
      for (const bg of ["--card", "--paper"]) assert.ok(ratio(t["--copper"], t[bg]) >= 4.5, `${name} ${accent} on ${bg}: ${ratio(t["--copper"], t[bg]).toFixed(2)}`);
      assert.ok(ratio(t["--on-accent"], t["--copper"]) >= 4.5, `${name} ${accent} button text`);
      assert.ok(ratio(t["--ink"], t["--paper"]) >= 7, `${name} ink`);
      assert.ok(ratio(t["--muted"], t["--card"]) >= 4.5, `${name} muted`);
    }
  }
});

test("only the listed themes and accents are kept", () => {
  assert.deepEqual(normalizeAppearance({ theme: "dark", accent: "blue" }), { theme: "dark", accent: "blue" });
  assert.deepEqual(normalizeAppearance({ theme: "neon", accent: "pink" }), { theme: "system", accent: "copper" });
  assert.deepEqual(THEMES.map(([k]) => k), ["system", "light", "dark"]);
});
