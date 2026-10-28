// Small DOM helpers shared by the views.

export function moreButton(kind, page) {
  return page && page.entries.length < page.total
    ? `<p><button class="btn small secondary" data-action="list-more" data-kind="${kind}">Show older (${page.total - page.entries.length} more)</button></p>` : "";
}

// A money cell that reads $0.00: hidden on a phone, where a card of noughts says nothing
// (R6-02). The contract total and the next step always show.
export const KEEP_ZERO = new Set(["Contract total", "Next"]);
export function isZeroMoney(text) { return /^-?\$0\.00$/.test(String(text ?? "").trim()); }

// Tables become cards on a phone: each cell carries its column's name for the CSS to show.
// A schedule of values keeps its own layout (sov-table: cards only below 600 px), but its
// cells get the same names, except where the markup set a better one ("% done").
export function labelTables(root) {
  root.querySelectorAll(".main table").forEach(t => {
    const head = t.rows[0];
    if (!head || !head.querySelector("th")) return;
    if (!t.classList.contains("sov-table")) t.classList.add("stack");
    head.classList.add("head-row");
    const labels = [...head.cells].map(c => c.textContent.replace(/[↑↓]/g, "").trim());
    [...t.rows].slice(1).forEach(r => [...r.cells].forEach((c, i) => {
      if (labels[i] && r.cells.length > 1 && !c.hasAttribute("data-label")) c.dataset.label = labels[i];
      if (c.classList.contains("num") && isZeroMoney(c.textContent) && !KEEP_ZERO.has(labels[i])) c.classList.add("zero");
    }));
  });
}

// An in-page confirmation instead of the browser's confirm(): styled like the app, the focus
// stays inside it, Escape cancels. The message is plain text. Resolves true for the main button,
// false for Cancel, and "alt" for altLabel's button when there is one (a second, gentler way out).
export function askConfirm(message, okLabel = "Yes", danger = false, altLabel = "") {
  return new Promise((resolve) => {
    const back = document.activeElement;
    const dlg = document.createElement("dialog");
    dlg.className = "confirm-dialog";
    dlg.setAttribute("aria-labelledby", "confirm-text");
    const text = document.createElement("p");
    text.id = "confirm-text";
    text.textContent = message;
    const row = document.createElement("div");
    row.className = "confirm-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "btn secondary";
    cancel.textContent = "Cancel";
    const ok = document.createElement("button");
    ok.type = "button";
    ok.className = danger ? "btn danger" : "btn";
    ok.textContent = okLabel;
    row.append(cancel);
    if (altLabel) {
      const alt = document.createElement("button");
      alt.type = "button";
      alt.className = "btn secondary";
      alt.textContent = altLabel;
      alt.addEventListener("click", () => done("alt"));
      row.append(alt);
    }
    row.append(ok);
    dlg.append(text, row);
    document.body.append(dlg);
    const done = (answer) => {
      dlg.close();
      dlg.remove();
      if (back && back.focus) back.focus();
      resolve(answer);
    };
    cancel.addEventListener("click", () => done(false));
    ok.addEventListener("click", () => done(true));
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); done(false); });
    dlg.showModal();
    cancel.focus();  // the safe choice has the focus
  });
}
