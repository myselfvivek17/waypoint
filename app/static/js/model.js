/* Derived numbers for the journey and the plan: chosen items, prices with knock-on extras,
   budget totals and the task list. Pure functions of the state. */
import { daysBetween, inr, shortDate } from './dom.js';

export const allOptions = (s) => [s.rec, ...s.options];

export function lineTotal(o, guests) {
  return o.per_guest ? o.price * guests : o.price;
}

export function stopsModel(st) {
  const plan = st.plan, guests = plan.profile.guests || 0, j = st.j;
  return plan.stops.map((s, i) => {
    const item = allOptions(s).find((o) => o.id === j.chosen[s.key]) || s.rec;
    const rips = j.ripples.filter((r) => r.stop === s.key);
    const extra = rips.reduce((a, r) => a + (r.extra || 0), 0);
    const unacked = rips.find((r) => !r.ack);
    const note = unacked ? unacked.note : j.updated[s.key] || '';
    return {
      s, i, key: s.key, label: s.label, item, extra, decided: !!j.decided[s.key],
      price: Math.max(0, lineTotal(item, guests) + extra), flag: note ? { note } : null,
    };
  });
}

export function budgetModel(stops, profile) {
  const total = stops.reduce((a, x) => a + x.price, 0);
  const budget = profile.budget || total;
  const locked = stops.filter((x) => x.decided).reduce((a, x) => a + x.price, 0);
  return { total, budget, locked, denom: Math.max(budget, total, 1), over: total > budget };
}

/** First stop not yet decided, or n when all are. */
export function maxReach(stops) {
  const i = stops.findIndex((x) => !x.decided);
  return i < 0 ? stops.length : i;
}

export function nextIndex(stops, decided, from) {
  for (let i = from + 1; i < stops.length; i++) if (!decided[stops[i].key]) return i;
  for (let i = 0; i < stops.length; i++) if (!decided[stops[i].key]) return i;
  return stops.length;
}

/** Tasks with live names and costs from the current choices, sorted by deadline. */
export function taskRows(st, stops, today) {
  const byKey = Object.fromEntries(stops.map((x) => [x.key, x]));
  const rows = st.plan.tasks.map((t) => {
    const sv = t.stop ? byKey[t.stop] : null;
    const isStop = sv && !t.mandatory;
    const ticked = !!st.ticks[t.id];
    const days = t.due ? daysBetween(today, t.due) : null;
    let status = 'No date yet', late = false;
    if (ticked) status = isStop ? 'Booked' : 'Done';
    else if (t.late || (days !== null && days < 0)) { status = 'Late: do this today'; late = true; }
    else if (days === 0) status = 'Due today';
    else if (days !== null) status = days + (days === 1 ? ' day left' : ' days left');
    return {
      id: t.id, stop: t.stop, mandatory: t.mandatory, why: t.why, due: t.due, ticked, late, status,
      text: isStop ? (t.verb || 'Book') + ': ' + sv.item.name : t.text,
      dueText: t.due ? (isStop ? 'Book by ' : 'By ') + shortDate(t.due) : 'Date to be set',
      price: isStop ? sv.price : 0, costText: isStop ? inr(sv.price) : '',
    };
  });
  rows.sort((a, b) => (a.due || '9999').localeCompare(b.due || '9999') || (a.mandatory - b.mandatory));
  return rows;
}
