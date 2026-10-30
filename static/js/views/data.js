// Import/Export: every manual import and export of data, one tab each (I1). A stub until the
// comms-data stream builds it.

function underConstruction(title) {
  return `<h1>${title}</h1>
    <div class="card under-construction"><h2>Under construction</h2></div>`;
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  import() { return underConstruction("Import"); },
  export() { return underConstruction("Export"); },
};
