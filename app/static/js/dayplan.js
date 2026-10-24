/* The event-day run-sheet and "My plan": tasks, budget, "Ask to adjust", exports. */
import { api, errorText } from './api.js';
import { dayIcon } from './art.js';
import { h, inr, longDate } from './dom.js';
import { budgetLine, priceSays, priceText, stopsModel, summary, taskRows } from './model.js';
import { moreMenu } from './menu.js';
import { applyAdjustment, pickItemAlt, planBody, setItemStatus, setPaid, state, toggleTick } from './store.js';
import { announce, empty, go, guide, priceTag, primary, say, stopThinking, think, todayIso, update } from './ui.js';

/** Suggested requests, chosen from what this plan actually has. */
function adjustChips(plan) {
  const has = (k) => plan.stops.some((s) => s.key === k && s.status === 'open');
  return [
    has('cake') ? "I don't want a cake" : null,
    has('puja') ? 'We already have a poojari' : null,
    has('food') ? 'We will cook at home' : null,
    plan.stops.some((s) => s.key === 'photographer') ? null : 'Add a photographer',
    has('invites') ? 'Skip the invites' : null,
    'Cut the budget by 20%', 'Add 15 guests',
  ].filter(Boolean).slice(0, 6);
}
const ITEM_WORD = { skip: 'Skipped', have: 'Sorted', own: 'Your words' };

// ------------------------------------------------------------------ day

function itemControls(it) {
  const set = (status, mine) => { setItemStatus(it.id, status, mine); update({ dayForm: null, dayDraft: '', dayAlts: null }, { focus: 'di-' + it.id }); };
  const form = state.dayForm && state.dayForm.id === it.id ? state.dayForm.kind : null;
  const ctl = [];
  if (it.status !== 'keep') {
    ctl.push(h('button', { class: 'chip', id: 'di-' + it.id, type: 'button', onClick: () => set('keep') }, 'Keep it as planned',
      h('span', { class: 'sr' }, ': ' + it.title)));
  } else {
    // a moment shows only "Show options"; skip, already sorted and my own words are under More
    ctl.push(
      it.alts && it.alts.length ? h('button', { class: 'chip', type: 'button', 'aria-expanded': String(state.dayAlts === it.id),
        onClick: () => update({ dayAlts: state.dayAlts === it.id ? null : it.id, dayForm: null }, { focus: null }) },
      'Show options', h('span', { class: 'sr' }, ' for ' + it.title)) : null,
      moreMenu('di-' + it.id, [
        { label: 'Skip this moment', onSelect: () => set('skip') },
        { label: 'I already have this', onSelect: () => update({ dayForm: { id: it.id, kind: 'have' }, dayDraft: '', dayAlts: null }, { focus: 'dd-in' }) },
        { label: 'Write my own', onSelect: () => update({ dayForm: { id: it.id, kind: 'own' }, dayDraft: '', dayAlts: null }, { focus: 'dd-in' }) },
      ], { cls: 'chip', describe: 'for ' + it.title }));
  }
  return [h('div', { class: 'dctl', role: 'group', 'aria-label': 'Change this moment' }, ctl),
    state.dayAlts === it.id ? h('ul', { class: 'dalts', 'aria-label': 'Other ways to do this' }, it.alts.map((a) => h('li', null,
      h('button', { class: 'chip', type: 'button', onClick: () => { pickItemAlt(it.id, a); update({ dayAlts: null }, { focus: 'di-' + it.id }); say(`Changed to: ${a}.`); } }, a)))) : null,
    form ? h('form', { class: 'dform', onSubmit: (e) => { e.preventDefault(); const v = state.dayDraft.trim(); if (form === 'own' && !v) return; set(form, v); } },
      h('label', { class: 'sr', for: 'dd-in' }, form === 'have' ? 'Who or what? (optional)' : 'What will you do instead?'),
      h('input', { id: 'dd-in', class: 'cin', type: 'text', maxlength: 160, autocomplete: 'off', value: state.dayDraft,
        placeholder: form === 'have' ? 'Who or what? (optional)' : 'What will you do instead?', onInput: (e) => { state.dayDraft = e.target.value; } }),
      h('button', { class: 'btn pri', type: 'submit' }, 'Save'),
      h('button', { class: 'btn', type: 'button', onClick: () => update({ dayForm: null }, { focus: 'di-' + it.id }) }, 'Cancel')) : null];
}

/** "6 moments, 5 kept as planned and 1 skipped. Change any of them, then tap Open my plan." */
function dayGuide(day, many) {
  const n = day.items.length;
  if (!n) return 'Nothing is on this run-sheet yet. Tap Open my plan and use Ask to adjust to add a moment.';
  const count = (st) => day.items.filter((it) => it.status === st).length;
  const parts = [[count('keep'), 'kept as planned'], [count('skip'), 'skipped'], [count('have'), 'sorted by you'], [count('own'), 'in your words']]
    .filter(([c]) => c).map(([c, w]) => `${c} ${w}`);
  const list = parts.length > 1 ? parts.slice(0, -1).join(', ') + ' and ' + parts[parts.length - 1] : parts[0];
  return `${many ? day.label + ': ' : ''}${n} moment${n === 1 ? '' : 's'}, ${list}. Change any of them, then tap Open my plan.`;
}

export function dayView(opts = {}) {
  const plan = state.plan, days = plan.days;
  const di = Math.min(state.dayTab, days.length - 1), day = days[di];
  const type = plan.profile.type;
  return h('main', { class: 'wrap day', id: 'main' },
    h('p', { class: 'step' }, (type === 'wedding' ? 'Wedding days' : type === 'birthday' ? 'Party day' : 'Event day') +
      ' · ' + (longDate(plan.profile.date) || 'date to be set')),
    h('h1', { class: 'disp h1', tabindex: '-1' }, day.title),
    guide(dayGuide(day, days.length > 1)),
    h('p', { class: 'lead' }, 'Every moment is a suggestion: keep it, skip it, say you have it covered, pick another way, or write your own.'),
    days.length > 1 ? h('div', { class: 'tabs', role: 'group', 'aria-label': 'Which function' },
      days.map((d, i) => h('button', { class: 'tab', type: 'button', 'aria-pressed': String(i === di),
        onClick: () => update({ dayTab: i }, { focus: 'h1' }) }, d.label))) : null,
    !day.items.length ? empty('Nothing is on this run-sheet yet. On My plan, ask for a moment, like "cake at 8 pm", and it appears here.') : null,
    h('ol', { class: 'tl' + (opts.routeChanged ? ' anim' : '') }, day.items.map((it, i) => h('li', { class: 'tli' + (it.status === 'skip' ? ' skip' : ''), style: { animationDelay: i * 160 + 'ms' } },
      h('div', { class: 'tart' }, dayIcon(it.art)),
      h('div', { class: 'tbody' }, h('p', { class: 'ttime' }, it.time),
        h('h2', { class: 'disp tt' }, it.status === 'own' && it.mine ? it.mine : it.title),
        it.status !== 'keep' ? h('p', { class: 'mine' }, ITEM_WORD[it.status] + (it.status === 'have' && it.mine ? ': ' + it.mine : it.status === 'own' ? ` (instead of “${it.title}”)` : '')) : null,
        it.note && it.status === 'keep' ? h('p', null, it.note) : null,
        itemControls(it))))),
    h('div', { class: 'acts' },
      h('button', { class: 'btn', type: 'button', onClick: () => go('journey', { focus: 'stop' }) }, 'Back to the journey'),
      primary({ onClick: () => go('plan') }, 'Open my plan')));
}

// ------------------------------------------------------------------ plan actions

/** One line for the overlay that says what is being re-planned, in the user's own terms. */
export function adjustLine(request) {
  const t = request.toLowerCase();
  const parts = [/guest|people|pax|friends/.test(t) && 'guests', /budget|₹|\d\s*(k|lakh)\b|%/.test(t) && 'budget',
    /earlier|later|postpone|prepone|date|\b\d{1,2}\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/.test(t) && 'date'].filter(Boolean);
  if (parts.length > 1) return `Revising the ${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}, and checking every deadline…`;
  const without = t.match(/(?:no|don'?t want(?: a| the| any)?|without(?: a| the)?|remove(?: the)?|skip(?: the)?|drop(?: the)?|cancel(?: the)?)\s+([a-z][a-z -]{1,30})/);
  if (without) return `Re-planning without ${without[1].replace(/\s+(please|thanks?)$/, '').trim()}…`;
  const add = t.match(/\badd(?: a| an)?\s+([a-z][a-z -]{1,30})/);
  if (add && !/\d/.test(add[1])) return `Adding ${add[1].trim()} to the plan…`;
  if (/budget|cheap|cost|save/.test(t)) return 'Re-balancing the budget…';
  if (/guest|people/.test(t)) return 'Re-sizing the plan for the new guest count…';
  if (/date|move|postpone|prepone/.test(t)) return 'Checking the new date and deadlines…';
  return `Working out: “${request.slice(0, 60)}”…`;
}

/** "Yes, do that": the closest operation offered for a request I couldn't read. */
async function runSuggestion(sug) {
  update({ busy: true, adjMsg: '', adjSuggest: null, diff: null });
  think(sug.text.replace(/\?$/, '…'));
  const r = await api.apply(planBody({ ops: [sug.op] }));
  stopThinking();
  if (!r.ok || !r.data.understood) { update({ busy: false, adjMsg: r.ok ? r.data.note : errorText(r) }); return; }
  update({ busy: false, diff: Object.assign({ request: sug.request }, r.data), adjSource: 'rules' }, { focus: 'diff-h' });
  announce(`${r.data.title}. ${r.data.note}`);
}

async function runAdjust(text) {
  const request = String(text || '').trim().slice(0, 300);
  if (!request) return;
  update({ busy: true, adjMsg: '', adjSuggest: null, diff: null });
  think(adjustLine(request));
  const r = await api.adjust(planBody({ request }));
  stopThinking();
  if (!r.ok) { update({ busy: false, adjMsg: errorText(r) }); return; }
  if (!r.data.understood) {
    update({ busy: false, adjMsg: r.data.note, adjSource: r.data.source,
      adjSuggest: r.data.suggest ? { op: r.data.suggest, text: r.data.suggest_text, request } : null }, { focus: 'adj-msg' });
    announce(r.data.note + (r.data.suggest_text ? ' ' + r.data.suggest_text : ''));
    return;
  }
  update({ busy: false, diff: Object.assign({ request }, r.data), adjDraft: '', adjSource: r.data.source }, { focus: 'diff-h' });
  announce(`${r.data.title}. ${r.data.note}`);
}

/** "Let's make this work": a way out re-runs the whole revision with its own numbers, so the diff
    still compares against the plan as it was. */
async function runWay(w) {
  const d = state.diff;
  update({ busy: true });
  think(`Trying: ${w.title.toLowerCase()}…`);
  const r = await api.apply(planBody({ ops: w.ops }));
  stopThinking();
  if (!r.ok || !r.data.understood) { update({ busy: false, adjMsg: r.ok ? r.data.note : errorText(r) }, { focus: 'adj-msg' }); return; }
  update({ busy: false, diff: Object.assign({ request: d.request }, r.data) }, { focus: 'diff-h' });
  announce(`${w.title}. ${r.data.note}`);
}

/** Guests, budget or date edited on the Details page: the same revision as saying it, never a fresh plan. */
export async function reviseFromDetails(fields) {
  const p = state.profile;
  const ops = fields.map((f) => (f === 'date' ? { op: 'move_date', date: p.date }
    : { op: f === 'guests' ? 'set_guests' : 'set_budget', amount: p[f] }));
  update({ busy: true, err: '' });
  think('Revising the plan around your new details…');
  const r = await api.apply(planBody({ ops }));
  stopThinking();
  if (!r.ok || !r.data.understood) { update({ busy: false, err: r.ok ? r.data.note : errorText(r) }); return; }
  update({ busy: false, diff: Object.assign({ request: 'your new details' }, r.data), adjMsg: '', adjSuggest: null }, { render: false });
  go('plan', { focus: 'diff-h' });
  announce(`${r.data.title}. ${r.data.note}`);
}

// ------------------------------------------------------------------ "Where should the money go?"

/** Over budget: open the trade-off step. ``base`` is 'diff' when it rebalances a pending revision. */
export async function openTrade(base) {
  update({ trade: { base, prio: [], res: null, busy: true } }, { focus: 'trade-h' });
  await fetchTrade();
}

async function fetchTrade() {
  const t = state.trade, d = state.diff;
  const body = t.base === 'diff' && d
    ? planBody({ plan: d.plan, chosen: d.chosen, ripples: d.ripples, priorities: t.prio })
    : planBody({ priorities: t.prio });
  update({ trade: Object.assign({}, t, { busy: true }) }, { focus: null });
  const r = await api.tradeoffs(body);
  if (!state.trade) return;
  update({ trade: Object.assign({}, state.trade, { busy: false, res: r.ok ? r.data : null, err: r.ok ? '' : errorText(r) }) },
    { focus: null });
  if (r.ok) announce(r.data.options.length ? `${r.data.options.length} ways to fit the budget.` : 'The plan already fits the budget.');
}

function togglePrio(id) {
  const t = state.trade;
  const prio = t.prio.includes(id) ? t.prio.filter((x) => x !== id) : t.prio.concat(id);
  update({ trade: Object.assign({}, t, { prio, stale: true }) }, { focus: 'pr-' + id });
}

/** An option becomes a diff like any other change: with the pending revision when there is one. */
async function useTrade(o) {
  const t = state.trade, d = state.diff;
  const ops = t.base === 'diff' && d ? d.ops.concat(o.ops) : o.ops;
  update({ busy: true });
  think(`Rebalancing: ${o.title.toLowerCase()}…`);
  const r = await api.apply(planBody({ ops }));
  stopThinking();
  if (!r.ok || !r.data.understood) { update({ busy: false, adjMsg: r.ok ? r.data.note : errorText(r) }, { focus: 'adj-msg' }); return; }
  update({ busy: false, trade: null, diff: Object.assign({ request: d && t.base === 'diff' ? d.request : o.title }, r.data) }, { focus: 'diff-h' });
  announce(`${o.title}. ${r.data.note}`);
}

function tradeView() {
  const t = state.trade, res = t.res;
  const close = () => update({ trade: null }, { focus: state.diff ? 'diff-h' : 'adj-in' });
  return h('section', { class: 'chg trade', 'aria-labelledby': 'trade-h' },
    h('div', { class: 'chghd' },
      h('h3', { class: 'disp h2', id: 'trade-h', tabindex: '-1', style: { fontSize: '22px', margin: '0' } }, 'Where should the money go?'),
      h('button', { class: 'linkb', type: 'button', onClick: close }, 'Close')),
    res ? h('p', { class: 'lead', style: { margin: '6px 0 0' } },
      `The plan is ${inr(res.over)} over your ${inr(res.budget)}. Tap what matters most, in order: I protect those and take the money from the rest, instead of cutting everything a little.`) : null,
    res && res.categories.length ? h('div', { class: 'chips', role: 'group', 'aria-label': 'What matters most, in order' },
      res.categories.map((c) => {
        const rank = t.prio.indexOf(c.id);
        return h('button', { class: 'chip prio', id: 'pr-' + c.id, type: 'button', 'aria-pressed': String(rank >= 0), disabled: t.busy,
          onClick: () => togglePrio(c.id) }, rank >= 0 ? h('span', { class: 'apn', 'aria-hidden': 'true' }, String(rank + 1)) : null,
        c.label, rank >= 0 ? h('span', { class: 'sr' }, `, priority ${rank + 1}`) : null);
      })) : null,
    t.stale ? h('div', { class: 'pact' }, h('button', { class: 'btn', type: 'button', disabled: t.busy, onClick: () => {
      state.trade = Object.assign({}, t, { stale: false }); fetchTrade(); } }, 'Update the options')) : null,
    t.busy ? h('p', { class: 'chgmsg', role: 'status' }, 'Working out the options…') : null,
    t.err ? h('p', { class: 'err', role: 'alert' }, t.err) : null,
    res && !res.options.length && !t.busy ? h('p', { class: 'chgmsg' }, res.over ? 'Nothing left that I can change: the rest is booked, quoted or yours. Try fewer guests or a higher budget.'
      : 'The plan fits the budget.') : null,
    res && res.options.length ? h('ol', { class: 'tradeopts' }, res.options.map((o, i) => h('li', { class: 'tradeo' },
      h('p', { class: 'wt' }, o.title, ' ', h('span', { class: 'aitag' + (res.source === 'ai' ? ' ai' : '') }, res.source === 'ai' ? 'Worded by AI · maths by code' : 'Maths by code')),
      h('p', { class: 'tt2' }, o.line),
      h('p', { class: 'flab' }, 'Gives up'), h('ul', null, o.gives_up.map((g) => h('li', null, g))),
      o.keeps.length ? h('p', { class: 'tt2' }, 'Keeps: ' + o.keeps.join(', ').toLowerCase()) : null,
      h('p', { class: 'tradet' }, `≈ ${inr(o.total)} of ${inr(res.budget)}`, ' · ', o.fits ? `${inr(res.budget - o.total)} to spare` : h('span', { class: 'over' }, 'still over')),
      i === 0 ? primary({ disabled: state.busy || t.busy, onClick: () => useTrade(o) }, 'Use this', h('span', { class: 'sr' }, ': ' + o.title))
        : h('button', { class: 'btn', type: 'button', disabled: state.busy || t.busy, onClick: () => useTrade(o) }, 'Use this', h('span', { class: 'sr' }, ': ' + o.title))))) : null);
}

function applyDiff() {
  const d = state.diff;
  const before = Object.fromEntries(stopsModel(state).map((x) => [x.key, x]));
  const next = Object.assign({}, state, { plan: d.plan, j: Object.assign({}, state.j, { chosen: d.chosen, ripples: d.ripples }) });
  const changes = stopsModel(next).filter((a) => !before[a.key] || a.item.id !== before[a.key].item.id || a.price !== before[a.key].price)
    .map((a) => ({ key: a.key, itemChanged: !before[a.key] || a.item.id !== before[a.key].item.id }));
  applyAdjustment(d, changes);
  update({ planDirty: false });
  say('Plan updated. Changed stops are marked on the journey.');
}

async function addCal(rows) {
  const tasks = rows.filter((t) => t.due && !t.ticked).slice(0, 40).map((t) => ({ text: t.text.slice(0, 200), due: t.due }));
  if (!tasks.length) { say('No open deadlines to add.'); return; }
  const r = await api.ics({ title: state.plan.title.slice(0, 80), tasks });
  if (!r.ok) { say(errorText(r)); return; }
  const url = URL.createObjectURL(r.data);
  const a = h('a', { href: url, download: 'waypoint-deadlines.ics' });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  say(`${tasks.length} deadlines saved as a calendar file.`);
}

async function copyInvite() {
  update({ inviteShown: true }, { focus: null });
  try {
    await navigator.clipboard.writeText(state.plan.invite);
    say('Invite text copied.');
  } catch (e) {
    say('Copy is blocked here. Select the text below instead.');
  }
}

// ------------------------------------------------------------------ plan view

/** A booked stop can carry what the user actually paid: their number replaces the estimate. */
function paidControl(t, sv) {
  if (!t.ticked || !sv || t.mandatory || sv.status !== 'open') return null;
  if (state.paidFor !== t.id) {
    return h('button', { class: 'linkb paidb', id: 'pd-' + t.id, type: 'button',
      onClick: () => update({ paidFor: t.id, paidDraft: sv.s.paid == null ? '' : String(sv.s.paid) }, { focus: 'pd-in' }) },
    sv.s.paid == null ? 'Add what you booked it for' : 'Change the booked amount', h('span', { class: 'sr' }, ': ' + sv.label));
  }
  const done = (v) => { setPaid(sv.key, v); update({ paidFor: null, paidDraft: '' }, { focus: 'pd-' + t.id }); };
  return h('form', { class: 'paidf', onSubmit: (e) => { e.preventDefault(); done(state.paidDraft.trim()); } },
    h('label', { for: 'pd-in', class: 'flab' }, `Booked the ${sv.label.toLowerCase()} for, in rupees`),
    h('input', { id: 'pd-in', class: 'cin', type: 'number', min: 0, max: 500000000, step: 1, inputmode: 'numeric',
      value: state.paidDraft, onInput: (e) => { state.paidDraft = e.target.value; } }),
    h('button', { class: 'btn pri', type: 'submit' }, 'Save'),
    sv.s.paid != null ? h('button', { class: 'btn', type: 'button', onClick: () => done(null) }, 'Back to the estimate') : null,
    h('button', { class: 'btn', type: 'button', onClick: () => update({ paidFor: null }, { focus: 'pd-' + t.id }) }, 'Cancel'));
}

/** The badge says who reads the request: the model when one is live, otherwise the rules parser.
    After a request it reflects who actually answered (a live model can still fail over to the rules). */
function adjustTag() {
  const last = state.diff ? state.diff.source : state.adjSource;
  const live = state.llmMode === 'live' && !state.demo.offline;
  const ai = last ? last === 'ai' : live;
  return h('span', { class: 'aitag' + (ai ? ' ai' : ''), 'data-source': ai ? 'ai' : 'rules' },
    ai ? 'AI' : last === 'rules' && live ? 'Read by code, AI on standby' : 'Rules parser · AI offline');
}

/** The numbers no longer work: the rules' three ways out, the first one marked as the next step. */
function waysView(d) {
  return h('section', { class: 'chg ways-out', 'aria-labelledby': 'ways-h' },
    h('p', { class: 'step' }, "Let's make this work"),
    h('h4', { class: 'disp h2', id: 'ways-h', style: { fontSize: '22px', margin: '0' } }, d.block_title),
    h('p', { class: 'lead', style: { margin: '6px 0 0' } }, d.block_body),
    h('ol', { class: 'ways' }, d.ways.map((w, i) => h('li', null,
      h('button', { class: 'way', type: 'button', 'data-next': i === 0 ? true : null, disabled: state.busy, onClick: () => runWay(w) },
        h('span', { class: 'wt' }, w.title), h('span', { class: 'wd' }, w.detail))))));
}

function diffView(d) {
  const blocked = !!(d.ways && d.ways.length);
  return h('div', { role: 'region', 'aria-labelledby': 'diff-h' },
    h('h3', { class: 'disp h2', id: 'diff-h', tabindex: '-1', style: { fontSize: '22px' } }, d.title),
    blocked ? h('p', { class: 'warnline', id: 'diff-warn' }, "With these changes the numbers don't work yet. " +
      'The ways to make it work are under this preview.') : null,
    h('table', { class: 'diff' },
      h('caption', { class: 'sr' }, d.title + ': before and after'),
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'What'), h('th', { scope: 'col' }, 'Before'), h('th', { scope: 'col' }, 'After'))),
      h('tbody', null, d.rows.map((r) => h('tr', null, h('td', null, r.label), h('td', null, r.before), h('td', null, h('b', null, r.after)))))),
    h('p', { class: 'chgmsg' }, d.note,
      d.source === 'rules' ? null : d.source !== 'ai' ? h('span', { class: 'tt2' }, ' (Understood by the rules parser, not the AI.)') : null),
    d.explain && d.explain.length ? h('div', { class: 'explain' }, h('p', { class: 'flab' }, 'What I kept, changed and assumed'),
      h('ul', null, d.explain.map((t) => h('li', null, t)))) : null,
    blocked ? waysView(d) : null,
    h('div', { class: 'pact' },
      blocked || state.trade ? h('button', { class: 'btn', type: 'button', onClick: applyDiff }, blocked ? 'Apply anyway' : 'Apply changes')
        : primary({ onClick: applyDiff }, 'Apply changes'),
      d.over > 0 && !blocked && !state.trade ? h('button', { class: 'btn', id: 'trade-btn', type: 'button', onClick: () => openTrade('diff') },
        'Where should the money go?') : null,
      h('button', { class: 'btn', type: 'button', onClick: () => update({ diff: null }, { focus: 'adj-in' }) }, 'Keep as it is')));
}

export function planView() {
  const plan = state.plan, p = plan.profile;
  const { stops: m, budget: b } = summary(state);
  const rows = taskRows(state, m, todayIso());
  const next = rows.find((t) => !t.ticked) || null;
  const spentKeys = new Set(m.filter((x) => state.ticks[x.key]).map((x) => x.key));
  const maxP = Math.max(1, ...m.map((x) => x.price));
  const late = rows.filter((t) => !t.ticked && t.late).length;
  const allDecided = m.every((x) => x.decided);
  const tick = (id) => { toggleTick(id); update({}, { focus: 'tk-' + id }); };
  // one next step: the diff waiting for a decision, else the next task, else the invitation
  const step = state.trade ? 'trade' : state.diff ? 'diff' : next ? 'task' : 'invite';
  const doneWord = next && next.price ? 'Mark as booked' : 'Mark as done';
  const guideText = step === 'trade' ? (state.trade.res && state.trade.res.options.length
    ? 'Tap what matters most, then compare the options and tap Use this on the one you like.'
    : 'Tap what matters most; the options appear here.')
    : step === 'diff' && state.diff.ways && state.diff.ways.length
    ? `With these changes the numbers don't work yet. Pick one of the ${state.diff.ways.length} ways under the preview, or tap Keep as it is.`
    : step === 'diff' ? 'Check the before and after, then tap Apply changes, or Keep as it is.'
    : step === 'task' ? `${late ? `${late} task${late > 1 ? 's are' : ' is'} late. ` : ''}Next up: ${next.text}${next.due ? ` (${next.dueText[0].toLowerCase() + next.dueText.slice(1)})` : ''}. Tap ${doneWord} once it is done.`
      : rows.length ? 'Everything is booked. Tap Design the invitation card next.'
        : 'Nothing to book yet. Add stops on the Journey, or tap Design the invitation card.';
  return h('main', { class: 'wrap plan', id: 'main' },
    h('p', { class: 'step' }, 'My plan'),
    h('h1', { class: 'disp h1', tabindex: '-1' }, plan.title),
    guide(guideText),
    // while a preview is open the lead is about the preview, never the current plan beside it
    h('p', { class: 'lead', id: 'plan-lead' }, step === 'diff' || step === 'trade'
      ? 'You are looking at a preview of a change. Your plan stays as it is until you tap Apply.'
      : (allDecided ? 'Every stop is decided. ' : 'Some stops are still suggestions: decide them on the Journey. ') +
        (late ? `Do the late task${late > 1 ? 's' : ''} first.` : 'Nothing is late.')),
    h('div', { class: 'plangrid' },
      h('section', { 'aria-labelledby': 'tasks-h' },
        next ? h('div', { class: 'next dark' },
          h('p', { class: 'step' }, 'Next up'),
          h('h2', { class: 'disp' }, next.text),
          h('p', null, [next.dueText, next.status, next.costText].filter(Boolean).join(' · ')),
          step === 'task' ? primary({ onClick: () => tick(next.id) }, doneWord) : h('button', { class: 'btn', type: 'button', onClick: () => tick(next.id) }, doneWord))
          : rows.length ? h('div', { class: 'next dark' }, h('h2', { class: 'disp' }, 'Everything is booked.'), h('p', null, 'Enjoy the day.'))
            : h('div', { class: 'next dark' }, h('h2', { class: 'disp' }, 'Nothing to book yet.'), h('p', null, 'Add a stop on the Journey and its task appears here.')),
        h('h2', { class: 'disp h2', id: 'tasks-h' }, 'All tasks'),
        !rows.length ? empty('No tasks yet. Decide a stop on the Journey and its booking task, with a book-by date, appears here.') : null,
        h('ul', { class: 'tasks' }, rows.map((t) => h('li', { class: 'task' + (t.mandatory ? ' must' : '') },
          h('input', { type: 'checkbox', id: 'tk-' + t.id, checked: t.ticked, onChange: () => tick(t.id),
            'aria-describedby': 'tkd-' + t.id }),
          h('label', { for: 'tk-' + t.id }, h('span', { class: 'tt1' }, t.text),
            h('span', { class: 'tt2' + (t.late && !t.ticked ? ' late' : ''), id: 'tkd-' + t.id }, `${t.dueText} · ${t.status}`)),
          t.costText ? h('span', { class: 'tcost', title: t.src === 'estimate' ? 'An estimate: confirm it with the vendor.' : null },
            t.costText, h('span', { class: 'sr' }, priceSays(t.src))) : null,
          paidControl(t, m.find((x) => x.key === t.stop)))))),
      h('section', { 'aria-labelledby': 'bud-h' },
        h('h2', { class: 'disp h2', id: 'bud-h', style: { marginTop: '0' } }, 'Budget'),
        h('p', { style: { margin: '0 0 8px' } }, h('span', { 'data-budget-line': '' }, budgetLine(b)),
          b.over ? h('span', { class: 'over' }, ` · ${inr(b.total - b.budget)} over`) : null),
        b.over && !state.diff && !state.trade ? h('button', { class: 'btn', id: 'trade-btn', type: 'button', onClick: () => openTrade('plan') },
          'Where should the money go?') : null,
        !m.length ? empty('No stops in the budget yet. Add one on the Journey to see where the money goes.') : null,
        h('div', null, m.map((x) => h('div', { class: 'brow', 'data-stop': x.key, 'data-price': x.price },
          h('button', { class: 'linkb', type: 'button', onClick: () => { update({ j: Object.assign({}, state.j, { stopIdx: x.i, view: 'path' }) }, { render: false }); go('journey', { focus: 'stop' }); } },
            x.label, h('span', { class: 'sr' }, ': open this stop')),
          h('div', { class: 'btrack', 'aria-hidden': 'true' },
            h('div', { class: 'bplan', style: { width: ((x.price / maxP) * 100).toFixed(1) + '%' } }),
            h('div', { class: 'bspent', style: { width: spentKeys.has(x.key) ? ((x.price / maxP) * 100).toFixed(1) + '%' : '0' } })),
          h('span', { class: 'bamt' }, priceText(x.price, x.src), ' ', priceTag(x.price ? x.src : 'none'),
            h('span', { class: 'sr' }, spentKeys.has(x.key) ? ', booked' : ', not booked yet'))))),
        h('div', { class: 'adjust' },
          h('h2', { class: 'disp h2' }, 'Ask to adjust ', adjustTag()),
          h('form', { class: 'ans', onSubmit: (e) => { e.preventDefault(); runAdjust(state.adjDraft); } },
            h('label', { class: 'sr', for: 'adj-in' }, 'What should change?'),
            h('input', { id: 'adj-in', class: 'cin', type: 'text', autocomplete: 'off', maxlength: 300,
              placeholder: 'e.g. cut the budget by 20%', value: state.adjDraft, onInput: (e) => { state.adjDraft = e.target.value; } }),
            h('button', { class: 'btn pri', type: 'submit', disabled: state.busy }, 'Show me')),
          h('div', { class: 'chips', role: 'group', 'aria-label': 'Suggestions' },
            adjustChips(plan).map((c) => h('button', { class: 'chip', type: 'button', disabled: state.busy, onClick: () => runAdjust(c) }, c))),
          state.adjMsg ? h('p', { class: 'chgmsg', id: 'adj-msg', role: 'status', tabindex: '-1' }, state.adjMsg) : null,
          state.adjSuggest ? h('div', { class: 'pact' }, h('button', { class: 'btn', type: 'button', disabled: state.busy,
            onClick: () => runSuggestion(state.adjSuggest) }, 'Closest I can do: ' + state.adjSuggest.text)) : null,
          state.diff ? diffView(state.diff) : null,
          state.trade ? tradeView() : null))),
    h('div', { class: 'acts' },
      h('button', { class: 'btn', type: 'button', onClick: () => addCal(rows) }, 'Add deadlines to calendar'),
      h('button', { class: 'btn', type: 'button', onClick: copyInvite }, 'Copy invite'),
      step === 'invite' ? primary({ onClick: () => go('invite') }, 'Design the invitation card')
        : h('button', { class: 'btn', type: 'button', onClick: () => go('invite') }, 'Design the invitation card'),
      h('button', { class: 'btn', type: 'button', onClick: () => window.print() }, 'Print')),
    state.inviteShown ? h('div', { class: 'inv', role: 'region', 'aria-label': 'Invite text' }, plan.invite) : null,
    plan.notes && plan.notes.length ? h('ul', { class: 'notes' }, plan.notes.map((t) => h('li', null, t))) : null);
}
