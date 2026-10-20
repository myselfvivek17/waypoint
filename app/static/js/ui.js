/* Shared UI plumbing: state updates, routing, the "working on it" overlay, toasts and announcements. */
import { busyMotif } from './art.js';
import { state, save } from './store.js';
import { clear, reducedMotion } from './dom.js';

export const ROUTES = ['welcome', 'chat', 'journey', 'day', 'plan'];
let renderFn = () => {};
let toastTimer = 0;
let busyTimer = 0, busyShow = 0, busyFrom = null;
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

const BEHIND = ['view', 'sheet-root', 'banner'];

/** AI work in progress: a calm, centred overlay with the journey motif and one line saying what
    is happening. Several lines rotate while a long call runs. It appears after a short delay so
    quick answers never flash; the page behind is inert, and focus comes back afterwards. */
export function think(messages, sub = '') {
  const list = Array.isArray(messages) ? messages : [messages];
  const box = document.getElementById('busy'), tx = document.getElementById('busy-tx');
  let i = 0;
  clearInterval(busyTimer);
  clearTimeout(busyShow);
  busyFrom = document.activeElement;
  for (const id of BEHIND) document.getElementById(id).inert = true;
  document.querySelector('.hd').inert = true;
  busyShow = setTimeout(() => {
    const art = document.getElementById('busy-art');
    if (!art.firstChild) art.append(busyMotif());
    tx.textContent = list[0];
    document.getElementById('busy-sub').textContent = sub;
    box.hidden = false;
    document.getElementById('busy-box').focus({ preventScroll: true });
  }, 250);
  if (list.length > 1) {
    busyTimer = setInterval(() => { i = Math.min(i + 1, list.length - 1); tx.textContent = list[i]; }, 7000);
  }
}

export function stopThinking() {
  clearInterval(busyTimer);
  clearTimeout(busyShow);
  for (const id of BEHIND) document.getElementById(id).inert = false;
  document.querySelector('.hd').inert = false;
  const box = document.getElementById('busy');
  const hadFocus = box.contains(document.activeElement);
  box.hidden = true;
  clear(document.getElementById('busy-tx'));
  // The next render usually moves focus on purpose; otherwise return it to where it was.
  if (hadFocus && busyFrom && busyFrom.isConnected) busyFrom.focus({ preventScroll: true });
  busyFrom = null;
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
