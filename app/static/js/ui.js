/* Shared UI plumbing: state updates, routing, the status pill, toasts and announcements. */
import { state, save } from './store.js';
import { reducedMotion } from './dom.js';

export const ROUTES = ['welcome', 'chat', 'journey', 'day', 'plan'];
let renderFn = () => {};
let toastTimer = 0;
let pillTimer = 0;
const timers = new Set();

export function onRender(fn) { renderFn = fn; }

/** Merge a patch into the state, persist, and re-render unless told not to. */
export function update(patch = {}, opts = {}) {
  Object.assign(state, patch);
  save();
  if (opts.render !== false) renderFn(opts);
}

export function rerender(opts = {}) { renderFn(opts); }

/** Switch screen; focus moves to the new screen's heading for keyboard and screen-reader users. */
export function go(route, opts = {}) {
  if (!ROUTES.includes(route)) route = 'welcome';
  state.route = route;
  save();
  if (location.hash !== '#' + route) history.pushState(null, '', '#' + route);
  renderFn({ routeChanged: true, focus: opts.focus || 'h1' });
  window.scrollTo(0, 0);
}

/** A timer that is shortened under reduced motion. */
export function later(fn, ms) {
  const t = setTimeout(() => { timers.delete(t); fn(); }, reducedMotion() ? Math.min(ms, 120) : ms);
  timers.add(t);
  return t;
}

export function cancelTimers() { timers.forEach(clearTimeout); timers.clear(); }

/** Show the "thinking" pill. Several messages rotate while a long call runs. */
export function think(messages) {
  const list = Array.isArray(messages) ? messages : [messages];
  const pill = document.getElementById('pill'), tx = document.getElementById('pill-tx');
  let i = 0;
  tx.textContent = list[0];
  pill.hidden = false;
  clearInterval(pillTimer);
  if (list.length > 1) {
    pillTimer = setInterval(() => { i = Math.min(i + 1, list.length - 1); tx.textContent = list[i]; }, 7000);
  }
}

export function stopThinking() {
  clearInterval(pillTimer);
  document.getElementById('pill').hidden = true;
}

export function say(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3800);
}

/** Polite screen-reader announcement (AI proposals, status changes). */
export function announce(msg) {
  const el = document.getElementById('live');
  el.textContent = '';
  setTimeout(() => { el.textContent = msg; }, 60);
}

/** Today's date as the server sees it (pinned for demos), falling back to the device. */
export function todayIso() {
  if (state.serverToday) return state.serverToday;
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}
