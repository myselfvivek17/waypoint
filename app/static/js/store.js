/* App state. The durable part lives in localStorage on this device only: the server keeps nothing.

   The plan state (plan, profile, j, ticks) has ONE owner: the functions at the bottom of this
   file. Screens never patch it themselves; they derive every number from it through model.js,
   so the journey, the event day, My plan and the details page can never disagree. */

const KEY = 'waypoint:v1';
const DURABLE = ['route', 'profile', 'chat', 'plan', 'j', 'ticks', 'dayTab', 'planDirty'];

export function freshJourney() {
  return { stopIdx: 0, decided: {}, chosen: {}, ripples: [], updated: {}, view: 'path', nearby: false, pin: null };
}

export function freshChat() {
  return { transcript: [], ask: null, reply: '', chips: [], placeholder: '', done: false, block: null, remaining: 0, source: null };
}

function initial() {
  return {
    route: 'welcome', profile: {}, chat: freshChat(), plan: null, j: freshJourney(), ticks: {}, dayTab: 0,
    planDirty: false,
    // ephemeral
    welcomeDraft: '', draft: '', err: '', editing: null, editDraft: '', busy: false,
    sheet: false, why: false, change: false, changeDraft: '', changeMsg: '', slide: 0,
    planning: false, adjDraft: '', adjMsg: '', adjSource: null, diff: null, inviteShown: false, online: navigator.onLine !== false,
    demo: {}, serverToday: null, llmMode: null, photos: {}, nearby: {},
  };
}

export const state = initial();

export function load() {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (saved && typeof saved === 'object') {
      for (const k of DURABLE) if (k in saved) state[k] = saved[k];
      state.j = Object.assign(freshJourney(), state.j || {});
      state.chat = Object.assign(freshChat(), state.chat || {});
    }
  } catch (e) { /* corrupt or blocked storage: start fresh */ }
}

export function save() {
  try {
    const out = {};
    for (const k of DURABLE) out[k] = state[k];
    localStorage.setItem(KEY, JSON.stringify(out));
  } catch (e) { /* storage full or blocked: the session still works */ }
}

export function resetAll() {
  const keep = { online: state.online, demo: state.demo, serverToday: state.serverToday, llmMode: state.llmMode };
  Object.assign(state, initial(), keep);
  try { localStorage.removeItem(KEY); } catch (e) { /* ignore */ }
}

// ------------------------------------------------------------------ plan state mutations

/** A fresh plan from the server: the journey starts over and the details follow the plan. */
export function adoptPlan(plan) {
  Object.assign(state, { plan, profile: plan.profile, j: freshJourney(), ticks: {}, planDirty: false, diff: null });
}

/** Drop task ticks for stops whose chosen item changed: a booking was for the old item. */
function untick(keys) {
  if (!keys.length) return;
  const ticks = Object.assign({}, state.ticks);
  for (const k of keys) delete ticks[k];
  state.ticks = ticks;
}

/** Choose an option for a stop; its knock-on effects replace the stop's earlier ones. */
export function chooseOption(stop, o) {
  const j = state.j;
  const rip = (o.ripple || []).map((r) => ({ src: stop.key, stop: r.stop, note: r.note, extra: r.extra || 0, ack: false }));
  const changed = (j.chosen[stop.key] || stop.rec.id) !== o.id;
  state.j = Object.assign({}, j, {
    chosen: Object.assign({}, j.chosen, { [stop.key]: o.id }),
    ripples: j.ripples.filter((r) => r.src !== stop.key).concat(rip),
    decided: Object.assign({}, j.decided, { [stop.key]: false }),
  });
  if (changed) untick([stop.key]);
  return rip;
}

/** Mark a stop decided; its "updated" note and pending knock-on notes are acknowledged. */
export function confirmStop(key, patch = {}) {
  const j = state.j;
  const updated = Object.assign({}, j.updated);
  delete updated[key];
  state.j = Object.assign({}, j, {
    decided: Object.assign({}, j.decided, { [key]: true }), updated,
    ripples: j.ripples.map((r) => (r.stop === key ? Object.assign({}, r, { ack: true }) : r)),
  }, patch);
}

/** Apply an "Ask to adjust" result. ``changedKeys`` are stops whose item or price moved. */
export function applyAdjustment(d, changes) {
  const j = state.j;
  const updated = Object.assign({}, j.updated), decided = Object.assign({}, j.decided);
  for (const c of changes) {
    updated[c.key] = `changed by "${d.request}"`;
    if (c.itemChanged) decided[c.key] = false;
  }
  state.plan = d.plan;
  state.profile = d.plan.profile;
  state.diff = null;
  state.j = Object.assign({}, j, { chosen: d.chosen, ripples: d.ripples, updated, decided });
  untick(changes.filter((c) => c.itemChanged).map((c) => c.key));
}

export function toggleTick(id) {
  state.ticks = Object.assign({}, state.ticks, { [id]: !state.ticks[id] });
}

/** Detail edits that were not re-planned are dropped, so the details match the plan again. */
export function discardDetailEdits() {
  if (state.plan) state.profile = state.plan.profile;
  state.planDirty = false;
}
