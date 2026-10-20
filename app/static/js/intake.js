/* Welcome screen and the one-question-at-a-time intake chat. */
import { api, errorText } from './api.js';
import { h, inr, parseISO, shortDate } from './dom.js';
import { freshChat, freshJourney, state } from './store.js';
import { announce, go, say, stopThinking, think, todayIso, update } from './ui.js';

const STARTERS = ['Birthday', 'Wedding', 'House-warming', 'Community event'];
const OPENING_Q = 'What are you celebrating?';
const TYPE_LABEL = { birthday: 'Birthday', wedding: 'Wedding', 'house-warming': 'House-warming', community: 'Community event', other: 'Celebration' };

const SAMPLES = {
  aditya: { type: 'birthday', title: "Aditya's 22nd birthday", guests: 30, budget: 40000, area: 'Gachibowli, Hyderabad',
    date: '2026-11-15', who: 'Aditya, 22 — loves cricket and retro Bollywood', food: 'Veg + 2 Jain guests', access: 'Lift or ground floor', vibe: '' },
  wedding: { type: 'wedding', title: 'A wedding in Jaipur', guests: 250, budget: 1200000, area: 'Jaipur, Rajasthan',
    date: '2026-12-12', who: 'The couple and both families — folk music, Rajasthani food', food: 'Vegetarian', access: 'Seating for elders', vibe: '' },
};

/** Keep sample dates in the future whenever the app is opened. */
function futureDate(iso) {
  const today = todayIso();
  let [y, m, d] = iso.split('-').map(Number);
  while (`${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}` <= today) y += 1;
  return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}

function newEvent() {
  Object.assign(state, { profile: {}, chat: freshChat(), plan: null, j: freshJourney(), ticks: {}, dayTab: 0,
    planDirty: false, diff: null, adjMsg: '', err: '', draft: '', editing: null });
}

// ------------------------------------------------------------------ actions

/** Send one message (or a chip edit) to the intake model and apply its reply. */
export async function sendChat(message, asked, edit = false) {
  const body = { profile: state.profile, message: message.slice(0, 500), asked: asked || null,
    transcript: state.chat.transcript.slice(-16), edit };
  update({ busy: true, err: '' });
  if (!edit) think('Reading that…');
  const r = await api.chat(body);
  stopThinking();
  if (!r.ok) {
    update({ busy: false, err: errorText(r) });
    return false;
  }
  const out = r.data;
  const transcript = state.chat.transcript.slice();
  if (!edit && message) {
    transcript.push({ field: asked || null, q: (asked ? state.chat.reply : OPENING_Q).slice(0, 300), a: message.slice(0, 300) });
  }
  const hadArea = state.profile.area;
  const changed = JSON.stringify(out.profile) !== JSON.stringify(state.profile);
  update({
    busy: false, draft: '', editing: null, profile: out.profile,
    planDirty: state.planDirty || (!!state.plan && changed),
    chat: { transcript: transcript.slice(-16), ask: out.ask, reply: out.reply, chips: out.chips || [],
      placeholder: out.placeholder || '', done: out.done, block: out.block, remaining: out.remaining, source: out.source },
  }, { focus: edit ? null : 'question' });
  if (out.profile.area && out.profile.area !== hadArea) api.prefetch(out.profile.area, out.profile.guests);
  announce(out.block ? out.block.title : out.done ? "That's everything I need." : out.reply);
  return true;
}

async function startChat(message) {
  newEvent();
  state.busy = true;
  go('chat', { focus: 'question' });
  await sendChat(message, null);
}

const PLAN_STEPS = (p) => [
  `Finding real venues near ${(p.area || 'you').split(',')[0]} on OpenStreetMap…`,
  'Asking the AI to draft every stop around your details…',
  'Checking the budget adds up and every place is real…',
  'Almost there: writing the run-sheet and the invite…',
];

/** Rules, real places and one model call on the server; then the journey starts. */
export async function buildPlan() {
  update({ busy: true, err: '' });
  think(PLAN_STEPS(state.profile));
  const r = await api.plan(state.profile, !state.demo.offline);
  stopThinking();
  if (r.status === 409 && r.data.block) {
    update({ busy: false, chat: Object.assign({}, state.chat, { block: r.data.block, done: false }) });
    go('chat', { focus: 'question' });
    return;
  }
  if (!r.ok) {
    update({ busy: false, err: errorText(r) });
    say(errorText(r));
    return;
  }
  update({ busy: false, plan: r.data, j: freshJourney(), ticks: {}, planDirty: false, diff: null }, { render: false });
  go('journey', { focus: 'stop' });
  const first = r.data.stops[0];
  announce(`Your route has ${r.data.stops.length} stops. Stop 1, ${first.label}: ${first.rec.name}.`);
}

export function loadSample(key) {
  newEvent();
  const p = Object.assign({}, SAMPLES[key]);
  p.date = futureDate(p.date);
  update({ profile: p, chat: Object.assign(freshChat(), { done: true }) }, { render: false });
  buildPlan();
}

export function startDemo(kind) {
  const lastYear = Number(todayIso().slice(0, 4)) - 1;
  if (kind === 'budget') startChat('A birthday for 100 people with ₹5,000 in Hyderabad on 15 Nov');
  else if (kind === 'date') startChat(`A birthday for 30 people, ₹40,000, in Gachibowli, Hyderabad on 3 Oct ${lastYear}`);
  else loadSample('aditya');
}

function pickWay(w) {
  if (w.apply) {
    state.profile = Object.assign({}, state.profile, w.apply);
    sendChat('', state.chat.ask, true);
  } else {
    state.profile = Object.assign({}, state.profile, { date: null });
    sendChat('', 'date', true);
  }
}

// ------------------------------------------------------------------ welcome

export function welcomeView() {
  const submit = (e) => {
    e.preventDefault();
    const text = state.welcomeDraft.trim();
    state.welcomeDraft = '';
    startChat(text || 'Birthday');
  };
  const dots = [[8, 18, 14, 0], [78, 12, 22, -4], [90, 56, 12, -8], [14, 72, 18, -2], [62, 84, 10, -6], [44, 8, 10, -10]];
  return h('main', { class: 'welcome', id: 'main' },
    h('div', { class: 'lights', 'aria-hidden': 'true' }, dots.map(([l, t, sz, d]) => h('span', {
      style: { left: l + '%', top: t + '%', width: sz + 'px', height: sz + 'px', animationDelay: d + 's' } }))),
    h('div', { class: 'wrap wcol' },
      h('p', { class: 'disp wmark' }, 'Waypoint'),
      h('h1', { class: 'disp wh', tabindex: '-1' }, "Tell me what you're celebrating"),
      h('form', { class: 'wform', onSubmit: submit },
        h('label', { class: 'sr', for: 'w-in' }, OPENING_Q),
        h('input', { id: 'w-in', class: 'wbig', type: 'text', autocomplete: 'off', maxlength: 500,
          placeholder: "Aditya's 22nd birthday, in November…", value: state.welcomeDraft,
          onInput: (e) => { state.welcomeDraft = e.target.value; } }),
        h('button', { class: 'btn mari wgo', type: 'submit', disabled: state.busy }, 'Begin')),
      h('div', { class: 'starters', role: 'group', 'aria-label': 'Or start with one of these' },
        STARTERS.map((label) => h('button', { class: 'chip', type: 'button', onClick: () => startChat(label) }, label))),
      h('p', { class: 'sample' }, 'Just looking around? Open a sample: ',
        h('button', { class: 'linkb', type: 'button', onClick: () => loadSample('aditya') }, "Aditya's 22nd birthday"), ' · ',
        h('button', { class: 'linkb', type: 'button', onClick: () => loadSample('wedding') }, 'A wedding in Jaipur')),
      state.plan ? h('div', { class: 'resume' },
        h('span', null, 'Saved on this device: ', h('b', null, state.plan.title)),
        h('button', { class: 'btn pri', type: 'button', onClick: () => go('journey', { focus: 'stop' }) }, 'Continue planning')) : null,
      state.err ? h('p', { class: 'err', role: 'alert' }, state.err) : null));
}

// ------------------------------------------------------------------ chat

function chipDefs(p) {
  const d = [];
  if (p.type) d.push({ key: 'type', label: 'Type', text: TYPE_LABEL[p.type] || p.type, fixed: true });
  if (p.guests) d.push({ key: 'guests', label: 'Guests', text: p.guests + ' guests', raw: String(p.guests) });
  if (p.budget) d.push({ key: 'budget', label: 'Budget', text: inr(p.budget), raw: String(p.budget) });
  if (p.area) d.push({ key: 'area', label: 'Where', text: p.area, raw: p.area });
  if (p.date) d.push({ key: 'date', label: 'When', text: shortDate(p.date) + (parseISO(p.date).getFullYear() !== Number(todayIso().slice(0, 4)) ? ' ' + p.date.slice(0, 4) : ''), raw: shortDate(p.date) + ' ' + p.date.slice(0, 4) });
  if (p.who) d.push({ key: 'who', label: 'For', text: 'For ' + p.who, raw: p.who });
  if (p.food) d.push({ key: 'food', label: 'Food', text: p.food, raw: p.food });
  if (p.access) d.push({ key: 'access', label: 'Access', text: p.access, raw: p.access });
  return d;
}

export { chipDefs };

function eventCard() {
  const chips = chipDefs(state.profile);
  const save = (key) => (e) => {
    if (e && e.preventDefault) e.preventDefault();
    if (state.editing !== key) return;
    const val = state.editDraft.trim();
    state.editing = null;
    if (val) sendChat(val, key, true);
    else update({});
  };
  return h('aside', { class: 'evcard', 'aria-labelledby': 'ev-h' },
    h('h2', { class: 'evh', id: 'ev-h' }, 'Your event'),
    h('p', { class: 'evsub' }, 'Fills in as we talk. Tap any detail to change it.'),
    chips.length ? h('ul', { class: 'evchips' }, chips.map((c) => h('li', null,
      state.editing === c.key
        ? h('form', { onSubmit: save(c.key) },
          h('label', { class: 'sr', for: 'ed-in' }, 'Edit ' + c.label),
          h('input', { id: 'ed-in', class: 'einp', type: 'text', maxlength: 160, value: state.editDraft,
            onInput: (e) => { state.editDraft = e.target.value; }, onBlur: save(c.key),
            onKeydown: (e) => { if (e.key === 'Escape') update({ editing: null }); } }))
        : c.fixed ? h('span', { class: 'estat' }, c.text)
          : h('button', { class: 'echip', type: 'button', 'aria-label': `Change ${c.label}: ${c.text}`,
            onClick: () => update({ editing: c.key, editDraft: c.raw }, { focus: 'ed-in' }) }, c.text))))
      : h('p', { class: 'evempty' }, 'Nothing yet. Your answers will appear here.'));
}

function blockView(b) {
  return h('div', { role: 'alert' },
    h('p', { class: 'step' }, b.type === 'budget' ? "Let's make this work" : 'One thing to check'),
    h('h2', { class: 'disp qq', id: 'q-h', tabindex: '-1' }, b.title),
    h('p', { class: 'lead' }, b.body),
    h('ol', { class: 'ways' }, b.ways.map((w) => h('li', null,
      h('button', { class: 'way', type: 'button', onClick: () => pickWay(w) },
        h('span', { class: 'wt' }, w.title), h('span', { class: 'wd' }, w.detail))))));
}

function questionView(c) {
  const asked = c.transcript.filter((t) => t.field).length;
  const num = asked + 1, total = asked + Math.max(1, c.remaining);
  const submit = (e) => {
    e.preventDefault();
    const text = state.draft.trim();
    if (!text) { update({ err: 'Type an answer, or tap one of the suggestions.' }, { focus: 'a-in' }); return; }
    sendChat(text, c.ask);
  };
  return h('div', null,
    h('p', { class: 'step' }, `Question ${num} of ${total}`, ' ',
      h('span', { class: 'aitag' + (c.source === 'ai' ? ' ai' : '') }, c.source === 'ai' ? 'Asked by AI' : 'Rule-based')),
    h('h2', { class: 'disp qq', id: 'q-h', tabindex: '-1' }, c.reply),
    c.chips.length ? h('div', { class: 'chips', role: 'group', 'aria-label': 'Quick answers' },
      c.chips.map((chip) => h('button', { class: 'chip', type: 'button', disabled: state.busy,
        onClick: () => sendChat(chip, c.ask) }, chip))) : null,
    h('form', { class: 'ans', onSubmit: submit },
      h('label', { class: 'sr', for: 'a-in' }, 'Your answer'),
      h('input', { id: 'a-in', type: 'text', autocomplete: 'off', maxlength: 300, placeholder: c.placeholder,
        value: state.draft, 'aria-describedby': state.err ? 'a-err' : null,
        onInput: (e) => { state.draft = e.target.value; } }),
      h('button', { class: 'btn pri', type: 'submit', disabled: state.busy }, 'Next')),
    state.err ? h('p', { class: 'err', id: 'a-err', role: 'alert' }, state.err) : null);
}

function doneView() {
  const p = state.plan;
  return h('div', null,
    h('h2', { class: 'disp qq', id: 'q-h', tabindex: '-1' },
      p && state.planDirty ? 'Your details changed.' : "That's everything I need."),
    h('p', { class: 'lead' }, p && state.planDirty
      ? 'I can re-plan every stop around the new details, or you can keep the current plan.'
      : "I'll walk you through one decision at a time, starting with the venue. Change any detail on the right whenever you like."),
    h('div', { class: 'pact' },
      !p || state.planDirty
        ? h('button', { class: 'btn mari', type: 'button', disabled: state.busy, onClick: buildPlan },
          p ? 'Re-plan with these details' : 'Start the journey') : null,
      p ? h('button', { class: 'btn', type: 'button', onClick: () => go('journey', { focus: 'stop' }) },
        state.planDirty ? 'Keep the current plan' : 'Back to the journey') : null),
    state.err ? h('p', { class: 'err', role: 'alert' }, state.err) : null);
}

export function chatView() {
  const c = state.chat;
  const body = c.block ? blockView(c.block) : c.done ? doneView()
    : c.reply ? questionView(c)
      : h('p', { class: 'lead', id: 'q-h', tabindex: '-1' }, state.busy ? 'Reading that…' : state.err || 'Starting…');
  return h('main', { class: 'wrap chat', id: 'main' },
    h('div', { class: 'chatgrid' },
      h('section', { 'aria-labelledby': 'chat-h' },
        h('h1', { id: 'chat-h', class: 'sr', tabindex: '-1' }, 'Tell me about your event'),
        c.transcript.map((t) => h('div', { class: 'tq' }, h('p', { class: 'tqq' }, t.q), h('p', { class: 'tqa' }, t.a))),
        body),
      eventCard()));
}
