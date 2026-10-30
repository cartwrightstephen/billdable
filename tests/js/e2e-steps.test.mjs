// The browser smoke test's step files (tests/e2e/steps/*.mjs, run by tests/e2e/smoke.mjs): each
// loads without Playwright, exports one default function, and no two steps share a name.
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync } from "node:fs";

const dir = new URL("../e2e/steps/", import.meta.url);

test("every steps file exports a function and step names are unique", async () => {
  const names = [];
  const ctx = { step: async (name) => { names.push(name); }, browser: null, page: null, BASE: "", signedInPage: null, confirmDialog: null, confirmIfAsked: null, errors: [] };
  const files = readdirSync(dir).filter(f => f.endsWith(".mjs")).sort();
  assert.ok(files.includes("10-core.mjs") && files.includes("20-foundation.mjs"));
  for (const f of files) {
    const mod = await import(new URL(f, dir));
    assert.equal(typeof mod.default, "function", f);
    await mod.default(ctx);
  }
  assert.ok(names.length > 20, `found the steps (${names.length})`);
  assert.deepEqual(names.filter((n, i) => names.indexOf(n) !== i), []);
});
