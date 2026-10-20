/* App state. The durable part lives in localStorage on this device only: the server keeps nothing. */

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
    planning: false, adjDraft: '', adjMsg: '', diff: null, inviteShown: false, online: navigator.onLine !== false,
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
  const keep = { online: state.online };
  Object.assign(state, initial(), keep);
  try { localStorage.removeItem(KEY); } catch (e) { /* ignore */ }
}
