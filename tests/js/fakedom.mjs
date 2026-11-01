// Just enough of a browser to load the page's view modules under node and run their actions:
// a document whose elements are stubs (named ones can be set), an in-memory localStorage, and
// a fetch that answers from a list of handlers. Install it before importing a view module.

export function stubEl(props = {}) {
  return {
    style: {}, dataset: {}, isConnected: true, innerHTML: "", textContent: "", value: "", disabled: false,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute() {}, getAttribute() { return null; }, removeAttribute() {}, addEventListener() {},
    append() {}, prepend() {}, appendChild() {}, focus() {}, scrollIntoView() {}, insertAdjacentElement() {},
    querySelector() { return null; }, querySelectorAll() { return []; }, closest() { return null; }, contains() { return false; },
    ...props,
  };
}

const NAMED = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: "\u00a0" };
const decode = (html) => String(html).replace(/&(?:#(\d+)|#x([0-9a-f]+)|([a-z]+));/gi, (m, dec, hex, name) =>
  dec || hex ? String.fromCodePoint(parseInt(dec || hex, dec ? 10 : 16)) : NAMED[name.toLowerCase()] ?? m);

export function installFakeDom() {
  const dom = {
    byId: {},          // id -> element: getElementById and querySelector("#id")
    bySelector: {},    // selector -> element
    all: {},           // selector -> [elements] for querySelectorAll
    requests: [],      // [{ path, method, body }]
    answers: [],       // [(path, init) => {status, body} | undefined], tried in order
  };
  const storage = new Map();
  globalThis.localStorage = {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => { storage.set(k, String(v)); },
    removeItem: (k) => { storage.delete(k); },
  };
  globalThis.document = {
    body: stubEl(),
    activeElement: null,
    getElementById: (id) => dom.byId[id] || stubEl(),
    querySelector: (sel) => dom.bySelector[sel] || (sel.startsWith("#") ? dom.byId[sel.slice(1)] : null) || null,
    querySelectorAll: (sel) => dom.all[sel] || [],
    // A textarea decodes what is put in its innerHTML, as format.js's unesc() relies on.
    createElement: (tag) => (tag === "textarea" ? { value: "", set innerHTML(html) { this.value = decode(html); } } : stubEl()),
    addEventListener() {},
  };
  globalThis.window = { scrollTo() {}, addEventListener() {}, innerWidth: 1024, innerHeight: 768 };
  globalThis.location = { pathname: "/", search: "", hash: "", hostname: "localhost" };
  globalThis.history = { replaceState() {}, pushState() {} };
  globalThis.CSS = { escape: (s) => String(s) };
  globalThis.fetch = async (path, init = {}) => {
    dom.requests.push({ path, method: init.method || "GET", body: init.body ? JSON.parse(init.body) : null });
    for (const answer of dom.answers) {
      const a = await answer(path, init);
      if (a === "offline") throw new TypeError("Failed to fetch");
      if (a) return { ok: a.status >= 200 && a.status < 300, status: a.status, json: async () => a.body || {} };
    }
    return { ok: false, status: 404, json: async () => ({ error: "Not found." }) };
  };
  return dom;
}
