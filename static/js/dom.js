// Small DOM helpers shared by the views.

export function moreButton(kind, page) {
  return page && page.entries.length < page.total
    ? `<p><button class="btn small secondary" data-action="list-more" data-kind="${kind}">Show older (${page.total - page.entries.length} more)</button></p>` : "";
}

// Tables become cards on a phone: each cell carries its column's name for the CSS to show.
export function labelTables(root) {
  root.querySelectorAll(".main table:not(.sov-table)").forEach(t => {
    const head = t.rows[0];
    if (!head || !head.querySelector("th")) return;
    t.classList.add("stack");
    head.classList.add("head-row");
    const labels = [...head.cells].map(c => c.textContent.replace(/[↑↓]/g, "").trim());
    [...t.rows].slice(1).forEach(r => [...r.cells].forEach((c, i) => { if (labels[i] && r.cells.length > 1) c.dataset.label = labels[i]; }));
  });
}

// An in-page confirmation instead of the browser's confirm(): styled like the app, the focus
// stays inside it, Escape cancels. The message is plain text. Resolves true for the main button.
export function askConfirm(message, okLabel = "Yes", danger = false) {
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
    row.append(cancel, ok);
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
