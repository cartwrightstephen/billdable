// Who approves a pay app before it goes (Stephen, 10 Nov 2026, decision 8): the company's list on
// Settings, Company, and a client's own list on its Client playbook card, which replaces it
// (domain/jobrules.py). Only people whose profile holds "Approve pay apps before they go" can be
// named; the server refuses anyone else. Ticks are kept here through a redraw until Save.

import { escDeep } from "./format.js";
import { readJson } from "./net.js";
import { teamData, setTeamData, onSessionEnd } from "./state.js";

export const APPROVE_PERM = "approve_payapps";

let drafts = {};
let teamAsked = false;
onSessionEnd(() => { drafts = {}; teamAsked = false; });

// The team, asked for once when a picker is drawn (the Team page and To Do share it).
function askTeam(render) {
  if (teamData || teamAsked || typeof fetch !== "function") return;
  teamAsked = true;
  fetch("/api/team").then(r => (r.ok ? readJson(r) : null)).then(data => { if (data) { setTeamData(escDeep(data)); render(); } }).catch(() => {});
}

// [{id, name}] of the team members whose profile holds the approve permission.
export function approverPool(team) {
  const profiles = Object.fromEntries(((team && team.profiles) || []).map(p => [p.id, p.perms || []]));
  return ((team && team.users) || [])
    .filter(u => (profiles[u.profile_id || u.role] || []).includes(APPROVE_PERM))
    .map(u => ({ id: u.id, name: u.name || u.email || "" }));
}

// A box ticked (app.js's change listener): every box of its list, as they are now.
export function approverTick(box) {
  const scope = box.dataset.approver;
  const boxes = typeof document !== "undefined" ? document.querySelectorAll(`[data-approver="${scope}"]`) : [];
  drafts[scope] = [...boxes].filter(b => b.checked).map(b => b.dataset.user);
}

// The ids to save for a list: the ticks, or what is saved when nothing was ticked.
export function approverIds(scope, saved = []) {
  return drafts[scope] ? [...drafts[scope]] : saved.map(p => p.id);
}

export function forgetApprovers(scope) { delete drafts[scope]; }

// The boxes for one list. saved: [{id, name}] as stored. Someone named who no longer holds the
// permission (or left) still shows, ticked, so saving takes them off on purpose.
export function approverBoxes(scope, saved, render, disabled = false) {
  askTeam(render);
  const pool = approverPool(teamData);
  const ticked = new Set(approverIds(scope, saved || []));
  const people = pool.concat((saved || []).filter(p => !pool.some(x => x.id === p.id)));
  if (!people.length) return `<p class="sub">Loading the team…</p>`;
  return people.map(p => `<label class="terms-check approver-row"><input type="checkbox" data-approver="${scope}" data-user="${p.id}"${ticked.has(p.id) ? " checked" : ""}${disabled ? " disabled" : ""}> ${p.name}</label>`).join("");
}

// "Ana Ruiz and Ben Ode", or nobody.
export function approverNames(list) {
  const names = (list || []).map(p => p.name);
  return names.length ? names.join(", ") : "Nobody";
}
