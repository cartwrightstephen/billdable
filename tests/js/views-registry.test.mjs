// The page modules' registry (static/js/views/index.js): every page module is in it, no two use
// the same view or action name, and every address opens a view. node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const reg = await import("../../static/js/views/index.js");
const { VIEW_PATHS, DETAIL_PATHS, PATH_ALIASES } = await import("../../static/js/routes.js");

// Shared helpers and the helper panel: not pages.
const NOT_PAGES = new Set(["index.js", "money.js", "helper.js", "job-money.js"]);

test("every page module is in the registry", async () => {
  const files = readdirSync(new URL("../../static/js/views/", import.meta.url)).filter(f => f.endsWith(".js") && !NOT_PAGES.has(f));
  const listed = new Set();
  for (const m of Object.values(reg.MODULES)) for (const f of files) {
    const mod = await import(`../../static/js/views/${f}`);
    if (mod === m) listed.add(f);
  }
  assert.deepEqual(files.filter(f => !listed.has(f)), []);
});

test("no two modules use the same view or action name", () => {
  const seen = { views: new Map(), actions: new Map() };
  const clashes = [];
  for (const [name, m] of Object.entries(reg.MODULES)) for (const kind of ["views", "actions"]) {
    for (const key of Object.keys(m[kind] || {})) {
      if (seen[kind].has(key)) clashes.push(`${kind} ${key}: ${seen[kind].get(key)} and ${name}`);
      seen[kind].set(key, name);
    }
  }
  assert.deepEqual(clashes, []);
});

test("every address opens a view", () => {
  const routed = [...Object.keys(VIEW_PATHS), ...Object.keys(DETAIL_PATHS), ...Object.values(PATH_ALIASES), "job"];
  assert.deepEqual(routed.filter(v => typeof reg.views[v] !== "function"), []);
});
