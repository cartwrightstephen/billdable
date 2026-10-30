// Every function a page module calls is imported or defined in it (the modules were split, and
// later work moves code between them: a missed import only fails when that page is drawn).
// node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";

const dir = new URL("../../static/js/views/", import.meta.url);
const files = readdirSync(dir).filter(f => f.endsWith(".js")).map(f => `views/${f}`)
  .concat(readdirSync(new URL("../", dir)).filter(f => f.endsWith(".js")));
const GLOBAL = new Set(["if", "for", "while", "switch", "catch", "return", "function", "typeof", "await", "new", "import",
  "String", "Number", "Boolean", "Object", "Array", "Math", "Date", "JSON", "Promise", "Set", "Map", "RegExp", "Error", "Symbol",
  "parseInt", "parseFloat", "isNaN", "encodeURIComponent", "decodeURIComponent", "setTimeout", "clearTimeout", "setInterval",
  "requestAnimationFrame", "fetch", "btoa", "atob", "alert", "confirm", "structuredClone", "queueMicrotask", "Uint8Array",
  "URL", "URLSearchParams", "FormData", "Blob", "File", "FileReader", "AbortController", "CSS", "Intl", "super", "catch", "async", "PopStateEvent", "Event", "CustomEvent"]);

// The code without what strings say: quotes and template text become spaces; ${...} stays.
function codeOnly(src) {
  let out = "", i = 0;
  const stack = [];   // "`" inside a template, "{" inside its ${ }
  while (i < src.length) {
    const c = src[i];
    const inTemplate = stack[stack.length - 1] === "`";
    if (inTemplate) {
      if (c === "\\") { i += 2; out += "  "; continue; }
      if (c === "`") { stack.pop(); out += c; i++; continue; }
      if (c === "$" && src[i + 1] === "{") { stack.push("{"); out += "${"; i += 2; continue; }
      out += c === "\n" ? "\n" : " "; i++; continue;
    }
    if (c === "/" && src[i + 1] === "/") { while (i < src.length && src[i] !== "\n") i++; continue; }
    if (c === "/" && src[i + 1] === "*") { const e = src.indexOf("*/", i + 2); i = e < 0 ? src.length : e + 2; continue; }
    if (c === '"' || c === "'") {
      out += c; i++;
      while (i < src.length && src[i] !== c) { if (src[i] === "\\") i++; i++; out += " "; }
      out += c; i++; continue;
    }
    if (c === "/" && /[=(,:!&|?{};]\s*$/.test(out.slice(-20))) {   // a regex literal
      out += " "; i++;
      while (i < src.length && src[i] !== "/") { if (src[i] === "\\") i++; if (src[i] === "[") { while (i < src.length && src[i] !== "]") { if (src[i] === "\\") i++; i++; } } i++; }
      i++; continue;
    }
    if (c === "`") { stack.push("`"); out += c; i++; continue; }
    if (c === "{" && stack.length) stack.push("{");
    if (c === "}" && stack[stack.length - 1] === "{") stack.pop();
    out += c; i++;
  }
  return out;
}

function defined(src) {
  const names = new Set();
  for (const m of src.matchAll(/import \{([^}]*)\} from/g)) for (const spec of m[1].split(",")) names.add(spec.trim().split(/\s+as\s+/).pop());
  for (const m of src.matchAll(/import \* as (\w+)/g)) names.add(m[1]);
  for (const m of src.matchAll(/(?:function\*?|class)\s+(\w+)/g)) names.add(m[1]);
  for (const m of src.matchAll(/(?:const|let|var)\s+(\w+)/g)) names.add(m[1]);
  for (const m of src.matchAll(/,\s*(\w+)\s*=(?![=>])/g)) names.add(m[1]);   // const a = 1, b = 2
  for (const m of src.matchAll(/(?:const|let|var)\s+\{([^}]*)\}/g)) for (const n of m[1].split(",")) names.add(n.split(":").pop().trim());
  for (const m of src.matchAll(/(?:const|let|var)\s+\[([^\]]*)\]/g)) for (const n of m[1].split(",")) names.add(n.trim());
  // parameters: (a, b) =>, a =>, function f(a, b)
  for (const m of src.matchAll(/\(([^()]*)\)\s*=>|(\w+)\s*=>|function\s*\w*\s*\(([^)]*)\)/g)) {
    for (const n of (m[1] || m[2] || m[3] || "").split(",")) names.add(n.replace(/=.*/, "").replace(/[{}[\].\s]/g, ""));
  }
  return names;
}

test("every called function is imported or defined in its module", () => {
  const missing = [];
  for (const f of files) {
    const src = readFileSync(new URL(f.startsWith("views/") ? f.slice(6) : `../${f}`, dir), "utf8")
      .replace(/^\s*\/\/.*$/gm, "");
    const have = defined(src);
    const code = codeOnly(src);
    // a call: name( not preceded by . or a word character; object-literal methods name() { are definitions
    for (const m of code.matchAll(/(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(/g)) {
      const name = m[1];
      const after = code.slice(m.index + m[0].length);
      if (GLOBAL.has(name) || have.has(name)) continue;
      if (/^[^)]*\)\s*\{/.test(after) && /(^|\n|[{,])\s*$/.test(code.slice(Math.max(0, m.index - 40), m.index).replace(/["\w-]+$/, ""))) continue;   // a method definition
      missing.push(`${f}: ${name}`);
    }
  }
  assert.deepEqual([...new Set(missing)], []);
});
