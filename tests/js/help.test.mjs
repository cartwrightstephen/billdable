// Field help (R3-10): every FIELD_HELP entry is used by the page, and every key the page asks
// for has an entry. node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { FIELD_HELP, helpFor } from "../../static/js/help.js";
import { field } from "../../static/js/forms.js";

const root = new URL("../../static/", import.meta.url);
const sources = ["app.js", ...readdirSync(new URL("js/", root)).filter(f => f.endsWith(".js") && f !== "help.js").map(f => `js/${f}`)]
  .map(f => readFileSync(new URL(f, root), "utf8")).join("\n");
const lower = sources.toLowerCase();

// Keys the page names on purpose: data-help="x" in a template, helpKey: "x" in a field().
const asked = [...sources.matchAll(/data-help="([^"$]+)"|helpKey: "([^"]+)"/g)].map(m => m[1] || m[2]);

// A key is used when the page names it, or shows it as a label, header or tile: >Text<, or a
// string "Text" (labels in field() calls and header lists).
function used(key) {
  if (asked.includes(key)) return true;
  const k = key.toLowerCase();
  return lower.includes(`>${k}<`) || lower.includes(`"${k}"`);
}

test("every FIELD_HELP entry is used by a template or data-help", () => {
  assert.deepEqual(Object.keys(FIELD_HELP).filter(k => !used(k)), []);
});

test("every data-help and helpKey the page uses has an entry", () => {
  assert.ok(asked.length > 10, "field() calls name their help");
  assert.deepEqual(asked.filter(k => !(k in FIELD_HELP)), []);
});

test("field() marks its label with the help key, and helpFor still reads label text", () => {
  const html = field({ form: "t", name: "contract_cents", id: "t-amt", label: "Contract amount ($)", helpKey: "contract_amount" });
  assert.match(html, /<label for="t-amt" data-help="contract_amount">Contract amount \(\$\)<\/label>/);
  assert.doesNotMatch(field({ form: "t", name: "x", id: "t-x", label: "X" }), /data-help/);
  assert.equal(helpFor("Contract total ↓"), FIELD_HELP["contract total"]);
});
