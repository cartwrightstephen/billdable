// The cursor goes to a form's first box when it opens, but never takes it from a box the person
// has already gone to: the frame can come late (the Change order form's amount went into its title).
import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { focusAfterDraw } from "../../static/js/dom.js";

function page() {
  const body = { name: "body", isConnected: true };
  const frames = [];
  const doc = { body, activeElement: body };
  const box = (name) => ({ name, isConnected: true, focus() { doc.activeElement = this; } });
  globalThis.document = doc;
  globalThis.requestAnimationFrame = (fn) => frames.push(fn);
  return { doc, box, frame: () => frames.splice(0).forEach(fn => fn()) };
}

test("focusAfterDraw focuses the box when nothing else took the cursor", () => {
  const { doc, box, frame } = page();
  const title = box("title");
  focusAfterDraw(() => title);
  frame();
  assert.equal(doc.activeElement, title);
});

test("focusAfterDraw moves on from the button that was tapped, even once it is gone", () => {
  const { doc, box, frame } = page();
  const button = box("button"), title = box("title");
  button.focus();
  focusAfterDraw(() => title);
  frame();
  assert.equal(doc.activeElement, title);
  button.focus();
  focusAfterDraw(() => title);
  button.isConnected = false;
  doc.activeElement = doc.body;
  frame();
  assert.equal(doc.activeElement, title);
});

test("a late frame doesn't take the cursor from the box the person went to", () => {
  const { doc, box, frame } = page();
  const title = box("title"), amount = box("amount");
  focusAfterDraw(() => title);
  amount.focus();
  frame();
  assert.equal(doc.activeElement, amount);
});

test("no page focuses a box from a bare animation frame", () => {
  const dir = new URL("../../static/js/views/", import.meta.url);
  for (const f of readdirSync(dir)) {
    assert.doesNotMatch(readFileSync(new URL(f, dir), "utf8"), /requestAnimationFrame\([^\n]*\.focus\(\)/, f);
  }
});
