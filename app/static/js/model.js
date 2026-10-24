/* Derived numbers for the journey and the plan: chosen items, prices with knock-on extras,
   budget totals and the task list. Pure functions of the state. */
import { daysBetween, inr, shortDate } from './dom.js';

export const allOptions = (s) => [s.rec, ...s.options];

export function lineTotal(o, guests) {
  return o.per_guest ? o.price * guests : o.price;
}

// ------------------------------------------------------------------ honest prices (mirrors prices.py)

/** Where a stop's price comes from: 'none' (skipped), 'yours' (typed by the user: what they already
    pay, or what they booked it for), 'quoted' (a quote they chose) or 'estimate' (Waypoint's). */
export function priceSource(s) {
  if (s.status === 'skip') return 'none';
  if (s.status === 'have' || s.status === 'own' || s.paid != null) return 'yours';
  if (s.quote) return 'quoted';
  return 'estimate';
}

export const PRICE_LABEL = { estimate: 'Estimate', yours: 'Your number', quoted: 'Quoted' };
export const PRICE_HELP = {
  estimate: "An estimate from Waypoint, not a confirmed price. Confirm it with the vendor.",
  yours: 'A figure you typed in.',
  quoted: "From a quote you entered. Waypoint hasn't contacted the vendor.",
};

/** "≈ ₹12,000" for an estimate; a number the user typed or was quoted has no "≈". */
export function priceText(amount, src) {
  return (src === 'estimate' ? '≈ ' : '') + inr(amount);
}

/** The words a screen reader hears after the amount: ", an estimate: confirm it with the vendor". */
export function priceSays(src) {
  return src === 'estimate' ? ', an estimate: confirm it with the vendor' : src === 'quoted' ? ', quoted by the vendor'
    : src === 'yours' ? ', your number' : '';
}

/** What a stop costs: nothing when skipped; the user's own figure when they have it, do it their
    way or booked it; a chosen quote; otherwise the chosen option plus knock-on extras.
    Mirrors prices.stop_price. */
export function stopPrice(s, item, guests, extra) {
  const src = priceSource(s);
  if (src === 'none') return 0;
  if (src === 'yours') return (s.paid != null && (s.status || 'open') === 'open' ? s.paid : s.user_cost) || 0;
  if (src === 'quoted') return s.quote.price || 0;
  return Math.max(0, lineTotal(item, guests) + extra);
}

export function stopsModel(st) {
  const plan = st.plan, guests = plan.profile.guests || 0, j = st.j;
  return plan.stops.map((s, i) => {
    const item = allOptions(s).find((o) => o.id === j.chosen[s.key]) || s.rec;
    const rips = j.ripples.filter((r) => r.stop === s.key);
    const extra = rips.reduce((a, r) => a + (r.extra || 0), 0);
    const unacked = rips.find((r) => !r.ack);
    const note = unacked ? unacked.note : j.updated[s.key] || '';
    const status = s.status || 'open';
    return {
      s, i, key: s.key, label: s.label, item, extra, status, decided: !!j.decided[s.key] || status !== 'open',
      price: stopPrice(s, item, guests, extra), src: priceSource(s), freed: status === 'skip' ? Math.max(0, lineTotal(item, guests) + extra) : 0,
      flag: note ? { note } : null,
    };
  });
}

export function budgetModel(stops, profile, ticks = {}) {
  const total = stops.reduce((a, x) => a + x.price, 0);
  const budget = profile.budget || total;
  const booked = stops.filter((x) => ticks[x.key]).reduce((a, x) => a + x.price, 0);
  const decided = stops.filter((x) => x.decided).length;
  const freed = stops.reduce((a, x) => a + x.freed, 0);
  const est = stops.filter((x) => x.src === 'estimate').reduce((a, x) => a + x.price, 0);
  return { total, budget, booked, decided, freed, est, firm: total - est, count: stops.length, free: budget - total,
    denom: Math.max(budget, total, 1), over: total > budget };
}

/** Everything a screen shows about the plan, derived from one place. */
export function summary(st) {
  const stops = stopsModel(st);
  return { stops, budget: budgetModel(stops, st.plan.profile, st.ticks), profile: st.plan.profile };
}

/** The one wording of the budget line, used on every screen. It says how much is still an estimate. */
export function budgetLine(b) {
  const split = !b.firm ? 'all estimates' : !b.est ? 'all quoted or yours' : `${inr(b.est)} estimated, ${inr(b.firm)} quoted or yours`;
  return `Planned ${inr(b.total)} of ${inr(b.budget)} (${split}) · booked ${inr(b.booked)}`;
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
  const rows = st.plan.tasks.filter((t) => !(t.stop && byKey[t.stop] && byKey[t.stop].status === 'skip' && !t.mandatory))
    .map((t) => {
      const sv = t.stop ? byKey[t.stop] : null;
      const isStop = sv && !t.mandatory;
      const ticked = !!st.ticks[t.id];
      const days = t.due ? daysBetween(today, t.due) : null;
      let status = 'No date yet', late = false;
      if (ticked) status = isStop ? (sv.status === 'open' ? 'Booked' : 'Done') : 'Done';
      else if (t.late || (days !== null && days < 0)) { status = 'Late: do this today'; late = true; }
      else if (days === 0) status = 'Due today';
      else if (days !== null) status = days + (days === 1 ? ' day left' : ' days left');
      let text = t.text;
      if (isStop && sv.status === 'have') text = `Confirm with ${sv.s.note || 'your own'}: ${sv.label.toLowerCase()}`;
      else if (isStop && sv.status === 'own') text = sv.s.note || sv.label;
      else if (isStop) text = (t.verb || 'Book') + ': ' + sv.item.name;
      return {
        id: t.id, stop: t.stop, mandatory: t.mandatory, why: t.why, due: t.due, ticked, late, status, text,
        dueText: t.due ? (isStop ? (sv.status === 'open' ? 'Book by ' : 'By ') : 'By ') + shortDate(t.due) : 'Date to be set',
        price: isStop ? sv.price : 0, src: isStop ? sv.src : 'none', costText: isStop && sv.price ? priceText(sv.price, sv.src) : '',
      };
    });
  // stops added after the plan was made have no task yet: derive one
  for (const sv of stops) {
    if (sv.status === 'skip' || rows.some((r) => r.stop === sv.key && !r.mandatory)) continue;
    rows.push({ id: sv.key, stop: sv.key, mandatory: false, why: '', due: null, ticked: !!st.ticks[sv.key], late: false,
      status: st.ticks[sv.key] ? 'Done' : 'No date yet', text: (sv.s.verb || 'Book') + ': ' + sv.item.name,
      dueText: 'Date to be set', price: sv.price, src: sv.src, costText: sv.price ? priceText(sv.price, sv.src) : '' });
  }
  rows.sort((a, b) => (a.due || '9999').localeCompare(b.due || '9999') || (a.mandatory - b.mandatory));
  return rows;
}
