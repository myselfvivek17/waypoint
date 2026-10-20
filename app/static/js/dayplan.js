/* The event-day run-sheet and "My plan": tasks, budget, "Ask to adjust", exports. */
import { api, errorText } from './api.js';
import { dayIcon } from './art.js';
import { h, inr, longDate } from './dom.js';
import { budgetLine, stopsModel, summary, taskRows } from './model.js';
import { applyAdjustment, state, toggleTick } from './store.js';
import { announce, go, say, stopThinking, think, todayIso, update } from './ui.js';

const ADJ_CHIPS = ['Cut the budget by 20%', 'Add 15 guests', 'Move it outdoors', 'Make the food cheaper'];

// ------------------------------------------------------------------ day

export function dayView() {
  const plan = state.plan, days = plan.days;
  const di = Math.min(state.dayTab, days.length - 1), day = days[di];
  const type = plan.profile.type;
  return h('main', { class: 'wrap day', id: 'main' },
    h('p', { class: 'step' }, (type === 'wedding' ? 'Wedding days' : type === 'birthday' ? 'Party day' : 'Event day') +
      ' · ' + (longDate(plan.profile.date) || 'date to be set')),
    h('h1', { class: 'disp h1', tabindex: '-1' }, day.title),
    days.length > 1 ? h('div', { class: 'tabs', role: 'group', 'aria-label': 'Which function' },
      days.map((d, i) => h('button', { class: 'tab', type: 'button', 'aria-pressed': String(i === di),
        onClick: () => update({ dayTab: i }, { focus: 'h1' }) }, d.label))) : null,
    h('ol', { class: 'tl' }, day.items.map((it, i) => h('li', { class: 'tli', style: { animationDelay: i * 160 + 'ms' } },
      h('div', { class: 'tart' }, dayIcon(it.art)),
      h('div', { class: 'tbody' }, h('p', { class: 'ttime' }, it.time), h('h2', { class: 'disp tt' }, it.title),
        it.note ? h('p', null, it.note) : null)))),
    h('div', { class: 'acts' },
      h('button', { class: 'btn', type: 'button', onClick: () => go('journey', { focus: 'stop' }) }, 'Back to the journey'),
      h('button', { class: 'btn mari', type: 'button', onClick: () => go('plan') }, 'Open my plan')));
}

// ------------------------------------------------------------------ plan actions

async function runAdjust(text) {
  const request = String(text || '').trim().slice(0, 300);
  if (!request) return;
  update({ busy: true, adjMsg: '', diff: null });
  think('Working out what changes…');
  const booked = state.plan.stops.map((s) => s.key).filter((k) => state.ticks[k]);
  const r = await api.adjust({ plan: state.plan, chosen: state.j.chosen, ripples: state.j.ripples, booked, request });
  stopThinking();
  if (!r.ok) { update({ busy: false, adjMsg: errorText(r) }); return; }
  if (!r.data.understood) {
    update({ busy: false, adjMsg: r.data.note, adjSource: r.data.source }, { focus: 'adj-msg' });
    announce(r.data.note);
    return;
  }
  update({ busy: false, diff: Object.assign({ request }, r.data), adjDraft: '', adjSource: r.data.source }, { focus: 'diff-h' });
  announce(`${r.data.title}. ${r.data.note}`);
}

function applyDiff() {
  const d = state.diff;
  const before = Object.fromEntries(stopsModel(state).map((x) => [x.key, x]));
  const next = Object.assign({}, state, { plan: d.plan, j: Object.assign({}, state.j, { chosen: d.chosen, ripples: d.ripples }) });
  const changes = stopsModel(next).filter((a) => !before[a.key] || a.item.id !== before[a.key].item.id || a.price !== before[a.key].price)
    .map((a) => ({ key: a.key, itemChanged: !before[a.key] || a.item.id !== before[a.key].item.id }));
  applyAdjustment(d, changes);
  update({});
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

/** The badge says who reads the request: the model when one is live, otherwise the rules parser.
    After a request it reflects who actually answered (a live model can still fail over to the rules). */
function adjustTag() {
  const last = state.diff ? state.diff.source : state.adjSource;
  const ai = last ? last === 'ai' : state.llmMode === 'live' && !state.demo.offline;
  return h('span', { class: 'aitag' + (ai ? ' ai' : ''), 'data-source': ai ? 'ai' : 'rules' },
    ai ? 'AI' : 'Rules parser · AI offline');
}

function diffView(d) {
  return h('div', { role: 'region', 'aria-labelledby': 'diff-h' },
    h('h3', { class: 'disp h2', id: 'diff-h', tabindex: '-1', style: { fontSize: '22px' } }, d.title),
    h('table', { class: 'diff' },
      h('caption', { class: 'sr' }, d.title + ': before and after'),
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'What'), h('th', { scope: 'col' }, 'Before'), h('th', { scope: 'col' }, 'After'))),
      h('tbody', null, d.rows.map((r) => h('tr', null, h('td', null, r.label), h('td', null, r.before), h('td', null, h('b', null, r.after)))))),
    h('p', { class: 'chgmsg' }, d.note,
      d.source !== 'ai' ? h('span', { class: 'tt2' }, ' (Understood by the rules parser, not the AI.)') : null),
    h('div', { class: 'pact' },
      h('button', { class: 'btn mari', type: 'button', onClick: applyDiff }, 'Apply changes'),
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
  return h('main', { class: 'wrap plan', id: 'main' },
    h('p', { class: 'step' }, 'My plan'),
    h('h1', { class: 'disp h1', tabindex: '-1' }, plan.title),
    h('p', { class: 'lead' }, (allDecided ? 'Every stop is decided. ' : 'Some stops are still suggestions. ') +
      (late ? `${late} task${late > 1 ? 's are' : ' is'} late: do ${late > 1 ? 'them' : 'it'} first.` : 'Nothing is late.')),
    h('div', { class: 'plangrid' },
      h('section', { 'aria-labelledby': 'tasks-h' },
        next ? h('div', { class: 'next dark' },
          h('p', { class: 'step' }, 'Next up'),
          h('h2', { class: 'disp' }, next.text),
          h('p', null, [next.dueText, next.status, next.costText].filter(Boolean).join(' · ')),
          h('button', { class: 'btn mari', type: 'button', onClick: () => tick(next.id) }, next.price ? 'Mark as booked' : 'Mark as done'))
          : h('div', { class: 'next dark' }, h('h2', { class: 'disp' }, 'Everything is booked.'), h('p', null, 'Enjoy the day.')),
        h('h2', { class: 'disp h2', id: 'tasks-h' }, 'All tasks'),
        h('ul', { class: 'tasks' }, rows.map((t) => h('li', { class: 'task' + (t.mandatory ? ' must' : '') },
          h('input', { type: 'checkbox', id: 'tk-' + t.id, checked: t.ticked, onChange: () => tick(t.id),
            'aria-describedby': 'tkd-' + t.id }),
          h('label', { for: 'tk-' + t.id }, h('span', { class: 'tt1' }, t.text),
            h('span', { class: 'tt2' + (t.late && !t.ticked ? ' late' : ''), id: 'tkd-' + t.id }, `${t.dueText} · ${t.status}`)),
          t.costText ? h('span', { class: 'tcost' }, t.costText) : null)))),
      h('section', { 'aria-labelledby': 'bud-h' },
        h('h2', { class: 'disp h2', id: 'bud-h', style: { marginTop: '0' } }, 'Budget'),
        h('p', { style: { margin: '0 0 8px' } }, h('span', { 'data-budget-line': '' }, budgetLine(b)),
          b.over ? h('span', { class: 'over' }, ` · ${inr(b.total - b.budget)} over`) : null),
        h('div', null, m.map((x) => h('div', { class: 'brow', 'data-stop': x.key, 'data-price': x.price },
          h('button', { class: 'linkb', type: 'button', onClick: () => { update({ j: Object.assign({}, state.j, { stopIdx: x.i, view: 'path' }) }, { render: false }); go('journey', { focus: 'stop' }); } },
            x.label, h('span', { class: 'sr' }, ': open this stop')),
          h('div', { class: 'btrack', 'aria-hidden': 'true' },
            h('div', { class: 'bplan', style: { width: ((x.price / maxP) * 100).toFixed(1) + '%' } }),
            h('div', { class: 'bspent', style: { width: spentKeys.has(x.key) ? ((x.price / maxP) * 100).toFixed(1) + '%' : '0' } })),
          h('span', null, inr(x.price), h('span', { class: 'sr' }, spentKeys.has(x.key) ? ', booked' : ', not booked yet'))))),
        h('div', { class: 'adjust' },
          h('h2', { class: 'disp h2' }, 'Ask to adjust ', adjustTag()),
          h('form', { class: 'ans', onSubmit: (e) => { e.preventDefault(); runAdjust(state.adjDraft); } },
            h('label', { class: 'sr', for: 'adj-in' }, 'What should change?'),
            h('input', { id: 'adj-in', class: 'cin', type: 'text', autocomplete: 'off', maxlength: 300,
              placeholder: 'e.g. cut the budget by 20%', value: state.adjDraft, onInput: (e) => { state.adjDraft = e.target.value; } }),
            h('button', { class: 'btn pri', type: 'submit', disabled: state.busy }, 'Show me')),
          h('div', { class: 'chips', role: 'group', 'aria-label': 'Suggestions' },
            ADJ_CHIPS.map((c) => h('button', { class: 'chip', type: 'button', disabled: state.busy, onClick: () => runAdjust(c) }, c))),
          state.adjMsg ? h('p', { class: 'chgmsg', id: 'adj-msg', role: 'status', tabindex: '-1' }, state.adjMsg) : null,
          state.diff ? diffView(state.diff) : null))),
    h('div', { class: 'acts' },
      h('button', { class: 'btn mari', type: 'button', onClick: () => addCal(rows) }, 'Add deadlines to calendar'),
      h('button', { class: 'btn', type: 'button', onClick: copyInvite }, 'Copy invite'),
      h('button', { class: 'btn', type: 'button', onClick: () => window.print() }, 'Print')),
    state.inviteShown ? h('div', { class: 'inv', role: 'region', 'aria-label': 'Invite text' }, plan.invite) : null,
    plan.notes && plan.notes.length ? h('ul', { class: 'notes' }, plan.notes.map((t) => h('li', null, t))) : null);
}
