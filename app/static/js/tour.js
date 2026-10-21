/* First-time tours: small popovers anchored to the thing they explain, placed beside it (never on
   top of it). Each screen's tour is shown once (localStorage); the header's "?" replays it. Tours
   stay out of the way under ?demo= and in automated browsers unless asked for with ?tour=1. */
import { h } from './dom.js';
import { state } from './store.js';

const SEEN = 'waypoint:tours';
const ASKED = 'waypoint:tour-asked';
const GAP = 14;

const TOURS = {
  journey: [
    { at: '.scenewrap, .slist, .mapwrap', title: 'This is your route',
      body: 'Each stop is one decision, from today to the big day. You are on the glowing stop; decided stops get a tick.' },
    { at: '.prop .pact, .fin .pact', title: 'Decide one stop at a time',
      body: 'Looks good keeps my suggestion and moves on. Show options compares the alternatives. More has skip, I already have this and write my own.' },
    { at: '.bud', title: 'Your budget, live',
      body: 'Every choice updates this bar. Solid parts are decided, striped ones are still suggestions, and the red line is your budget.' },
  ],
  plan: [
    { at: '.next', title: 'What to do next',
      body: 'The most urgent task, with its book-by date. Mark it done and the next one moves up.' },
    { at: '.adjust', title: 'Change anything in plain words',
      body: 'Type something like "no cake" or "add 15 guests". I show the before and after, and nothing changes until you apply it.' },
  ],
  invite: [
    { at: '.tones, #txt-h', title: 'Pick the words',
      body: 'Three versions written from your plan: warm, fun and formal. Tap one; you can still edit every line.' },
    { at: '#dl-btn', title: 'Save or share the card',
      body: 'The card is drawn on this device. Download it, print it or copy the text for WhatsApp.' },
  ],
};

let allowed = true;
let tour = null;  // { route, steps, i, back: element focused before the tour }

function seen() {
  try { return JSON.parse(localStorage.getItem(SEEN) || '{}') || {}; } catch (e) { return {}; }
}

function markSeen(route) {
  try { localStorage.setItem(SEEN, JSON.stringify(Object.assign(seen(), { [route]: true }))); } catch (e) { /* ignore */ }
}

/** Called once at boot. ``asked`` (?tour=1) lasts for the browser session, so a reload still counts. */
export function initTours({ demo = false, asked = false, reset = false } = {}) {
  try {
    if (reset) localStorage.removeItem(SEEN);
    if (asked) sessionStorage.setItem(ASKED, '1');
    asked = asked || sessionStorage.getItem(ASKED) === '1';
  } catch (e) { /* storage blocked: tours simply show */ }
  allowed = asked || (!demo && !navigator.webdriver);
  window.addEventListener('resize', () => { if (tour) place(true); });
}

/** One step for screens without a tour of their own: point at the screen's next step. */
function fallback() {
  const g = document.getElementById('guide');
  return [{ at: '#view [data-next]', title: 'Your next step', body: g ? g.textContent : 'This is the next thing to do here.' }];
}

const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));

function target(step) {
  return [...document.querySelectorAll(step.at)].find(visible) || null;
}

export function tourOpen() { return !!tour; }

/** Start the tour of ``route``. Steps whose target is not on screen are left out. */
export function startTour(route) {
  const steps = (TOURS[route] || fallback()).filter((st) => target(st));
  if (!steps.length) return false;
  const back = tour ? tour.back : document.activeElement;
  close(false);
  tour = { route, steps, i: 0, back };
  draw();
  return true;
}

/** After each render: show a screen's tour the first time, and keep an open one attached. */
export function afterRender() {
  if (tour) {
    if (state.route !== tour.route) close(false);
    else place(false);
    return;
  }
  const route = state.route;
  if (!allowed || !TOURS[route] || seen()[route] || state.busy || state.sheet || !state.plan) return;
  setTimeout(() => {
    if (tour || state.route !== route || state.sheet || seen()[route] || !document.getElementById('busy').hidden) return;
    if (startTour(route)) markSeen(route);
  }, 700);
}

function close(returnFocus = true) {
  if (!tour) return;
  const t = tour;
  tour = null;
  markSeen(t.route);
  document.querySelectorAll('.tour-on').forEach((el) => el.classList.remove('tour-on'));
  const box = document.getElementById('tour');
  if (box) box.remove();
  document.removeEventListener('pointerdown', outside, true);
  if (!returnFocus) return;
  const to = t.back && t.back.isConnected && t.back !== document.body ? t.back : document.querySelector('#prop-h, #fin-h, #view h1');
  if (to) to.focus({ preventScroll: true });
}

function outside(e) {
  const box = document.getElementById('tour');
  if (box && !box.contains(e.target)) close(false);
}

function move(d) {
  const i = tour.i + d;
  if (i < 0) return;
  if (i >= tour.steps.length) { close(); return; }
  tour.i = i;
  draw();
}

function keys(e) {
  if (e.key === 'Escape') { e.preventDefault(); close(); return; }
  if (e.key === 'ArrowRight') { e.preventDefault(); move(1); return; }
  if (e.key === 'ArrowLeft') { e.preventDefault(); move(-1); return; }
  if (e.key !== 'Tab') return;
  const f = [...e.currentTarget.querySelectorAll('button:not([disabled])')];
  if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
  else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
}

function draw() {
  const { steps, i } = tour, st = steps[i], last = i === steps.length - 1;
  const old = document.getElementById('tour');
  if (old) old.remove();
  const box = h('div', { id: 'tour', class: 'tour', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'tour-h',
    'aria-describedby': 'tour-b', onKeydown: keys },
  h('span', { class: 'tourarrow', 'aria-hidden': 'true' }),
  steps.length > 1 ? h('p', { class: 'step' }, `${i + 1} of ${steps.length}`) : null,
  h('h2', { class: 'disp', id: 'tour-h' }, st.title),
  h('p', { id: 'tour-b' }, st.body),
  h('div', { class: 'touract' },
    h('button', { class: 'linkb', id: 'tour-skip', type: 'button', onClick: () => close() }, last ? 'Close' : 'Skip tour'),
    h('span', { class: 'touram' },
      i > 0 ? h('button', { class: 'btn', id: 'tour-back', type: 'button', onClick: () => move(-1) }, 'Back') : null,
      h('button', { class: 'btn pri', id: 'tour-next', type: 'button', onClick: () => move(1) }, last ? 'Done' : 'Next'))));
  document.body.append(box);
  document.addEventListener('pointerdown', outside, true);
  place(true);
  document.getElementById('tour-next').focus({ preventScroll: true });
}

/** Put the popover above or below its target, whichever has room, so it never covers it. */
function place(scroll) {
  const box = document.getElementById('tour');
  const el = box && target(tour.steps[tour.i]);
  if (!el) { if (box) close(false); return; }
  document.querySelectorAll('.tour-on').forEach((x) => x !== el && x.classList.remove('tour-on'));
  el.classList.add('tour-on');
  if (scroll) el.scrollIntoView({ block: 'center', inline: 'nearest' });
  const r = el.getBoundingClientRect(), bw = box.offsetWidth, bh = box.offsetHeight;
  const below = innerHeight - r.bottom >= bh + GAP + 8 || r.top < bh + GAP + 8 && innerHeight - r.bottom >= r.top;
  const top = below ? r.bottom + GAP : r.top - GAP - bh;
  const left = Math.min(Math.max(12, r.left + r.width / 2 - bw / 2), document.documentElement.clientWidth - bw - 12);
  box.style.top = top + scrollY + 'px';
  box.style.left = left + scrollX + 'px';
  box.classList.toggle('above', !below);
  box.querySelector('.tourarrow').style.left = Math.min(Math.max(18, r.left + r.width / 2 - left), bw - 18) + 'px';
  if (scroll) {  // keep the popover itself in view, without hiding the target behind the fold
    const b = box.getBoundingClientRect();
    if (b.bottom > innerHeight) window.scrollBy(0, b.bottom - innerHeight + 12);
    else if (b.top < 0) window.scrollBy(0, b.top - 12);
  }
}
