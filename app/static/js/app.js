/* Waypoint front end: boot, routing, header, theme and focus management. */
import { api } from './api.js';
import { moonIcon, sunIcon } from './art.js';
import { dayView, planView } from './dayplan.js';
import { $, clear, h } from './dom.js';
import { chatView, startDemo, welcomeView } from './intake.js';
import { journeyView, sheetView } from './journey.js';
import { discardDetailEdits, load, resetAll, state } from './store.js';
import { cancelTimers, go, onRender, ROUTES, stopThinking } from './ui.js';

const NAV = [['chat', 'Details'], ['journey', 'Journey'], ['day', 'Event day'], ['plan', 'My plan']];
let shownRoute = null;

// ------------------------------------------------------------------ theme

function applyThemeButton() {
  const dark = document.documentElement.getAttribute('data-theme') === 'dark';
  const btn = $('#theme');
  btn.setAttribute('aria-pressed', String(dark));
  btn.setAttribute('aria-label', 'Dark mode');
  clear($('#theme-ic')).append(dark ? moonIcon() : sunIcon());
  $('#theme-tx').textContent = dark ? 'Dark' : 'Light';
}

function setTheme(theme, remember) {
  const root = document.documentElement;
  root.classList.add('theme-fade');
  root.setAttribute('data-theme', theme);
  if (remember) { try { localStorage.setItem('waypoint:theme', theme); } catch (e) { /* ignore */ } }
  applyThemeButton();
  setTimeout(() => root.classList.remove('theme-fade'), 400);
}

// ------------------------------------------------------------------ chrome

function navView() {
  const nav = clear($('#nav'));
  if (state.route === 'welcome') return;
  const t = state.plan && state.plan.profile.type;
  const dayLabel = t === 'wedding' ? 'Wedding days' : t === 'birthday' ? 'Party day' : 'Event day';
  for (const [route, label] of NAV) {
    if (route !== 'chat' && !state.plan) continue;
    nav.append(h('button', { class: 'navb', type: 'button', 'aria-current': state.route === route ? 'page' : null,
      onClick: () => go(route, { focus: route === 'journey' ? 'stop' : route === 'chat' ? 'question' : 'h1' }) },
    route === 'day' ? dayLabel : label));
  }
}

function bannerView() {
  const el = clear($('#banner'));
  if (!state.online) {
    el.append(h('div', { class: 'offb', role: 'status' },
      "You're offline. Everything you've decided is saved on this device; new AI suggestions wait until you're back."));
    return;
  }
  if (state.plan && state.planDirty && ['journey', 'day', 'plan'].includes(state.route)) {
    el.append(h('div', { class: 'offb warn dirty', role: 'status' },
      h('span', null, 'You changed your details, but this plan still uses the old ones. '),
      h('button', { class: 'linkb', type: 'button', onClick: () => go('chat', { focus: 'question' }) }, 'Re-plan with the new details'),
      h('button', { class: 'linkb', type: 'button', onClick: () => { discardDetailEdits(); render({ focus: null }); } }, 'Undo the edits')));
  }
  const src = state.plan && ['journey', 'day', 'plan'].includes(state.route) ? state.plan.source : null;
  if (src === 'fallback') {
    el.append(h('div', { class: 'offb warn', role: 'status' },
      'AI offline: this plan was made by the rules engine alone. It still uses your details and real places.'));
  } else if (src === 'mock') {
    el.append(h('div', { class: 'offb warn', role: 'status' },
      'Demo mode: no AI key is configured, so this plan comes from the rules engine.'));
  }
}

// ------------------------------------------------------------------ render

const FOCUS = {
  h1: () => $('#view h1'),
  stop: () => $('#prop-h') || $('#fin-h') || $('#view h1'),
  finale: () => $('#fin-h') || $('#prop-h'),
  question: () => $('#q-h') || $('#a-in'),
  sheet: () => $('#sheet-x'),
};

function render(opts = {}) {
  if (['journey', 'day', 'plan'].includes(state.route) && !state.plan) state.route = state.chat.reply ? 'chat' : 'welcome';
  if (state.route === 'chat' && !state.chat.reply && !state.chat.done && !state.chat.block && !state.busy) state.route = 'welcome';
  const before = document.activeElement && document.activeElement.id;
  const view = $('#view');
  const screen = { welcome: welcomeView, chat: chatView, journey: journeyView, day: dayView, plan: planView }[state.route];
  const node = screen(opts);
  const routeChanged = shownRoute !== state.route;
  if (routeChanged) node.classList.add('screen');
  clear(view).append(node);
  shownRoute = state.route;
  $('#logo').hidden = state.route === 'welcome';
  navView();
  bannerView();
  const sheet = sheetView();
  clear($('#sheet-root'));
  if (sheet) $('#sheet-root').append(sheet);
  document.title = (state.plan && state.route !== 'welcome' ? state.plan.title + ' · ' : '') + 'Waypoint';

  const want = opts.focus === null ? null : opts.focus;
  let target = null;
  if (want) target = FOCUS[want] ? FOCUS[want]() : document.getElementById(want);
  if (!target && before) target = document.getElementById(before);
  if (target) target.focus({ preventScroll: !routeChanged && want !== 'stop' });
}

// ------------------------------------------------------------------ boot

function fromHash() {
  const r = (location.hash || '').replace('#', '');
  if (ROUTES.includes(r) && r !== state.route) {
    cancelTimers();
    stopThinking();
    state.planning = false;
    state.sheet = false;
    go(r, { focus: r === 'journey' ? 'stop' : r === 'chat' ? 'question' : 'h1' });
  }
}

async function boot() {
  load();
  state.planning = false;
  onRender(render);
  applyThemeButton();
  $('#theme').addEventListener('click', () => {
    setTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark', true);
  });
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
    let saved = null;
    try { saved = localStorage.getItem('waypoint:theme'); } catch (err) { /* ignore */ }
    if (!saved) setTheme(e.matches ? 'dark' : 'light', false);
  });
  $('#logo').addEventListener('click', () => go('welcome'));
  window.addEventListener('hashchange', fromHash);
  window.addEventListener('popstate', fromHash);
  window.addEventListener('online', () => { state.online = true; render({ focus: null }); });
  window.addEventListener('offline', () => { state.online = false; render({ focus: null }); });

  const params = new URLSearchParams(location.search);
  const demo = params.get('demo');
  if (demo === 'offline') state.demo.offline = true;
  if (demo === 'nophoto') state.demo.nophoto = true;
  if (params.get('reset') === '1') resetAll();
  if (params.has('reset') || demo) history.replaceState(null, '', location.pathname + location.hash);

  const hash = (location.hash || '').replace('#', '');
  if (ROUTES.includes(hash)) state.route = hash;
  const health = await api.health();
  if (health.ok) { state.serverToday = health.data.today; state.llmMode = health.data.llm; }
  render({ focus: null });
  if (demo && ['budget', 'date', 'offline', 'nophoto'].includes(demo)) {
    startDemo(demo);
  }
}

boot();
