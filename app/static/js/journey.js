/* The journey: Today → one stop at a time → the event day. Path, Map and List views,
   one proposal per stop with real photos, an options sheet, and a live budget bar. */
import { api } from './api.js';
import { landscape, mapBase, noPhotoIcon, toneArt } from './art.js';
import { h, inr, s, safeUrl } from './dom.js';
import { chipDefs } from './intake.js';
import { allOptions, budgetLine, lineTotal, maxReach, nextIndex, stopsModel, summary } from './model.js';
import { chooseOption, confirmStop, state } from './store.js';
import { announce, go, later, rerender, say, update } from './ui.js';

const PHOTO_HOSTS = ['upload.wikimedia.org'];
const PAGE_HOSTS = ['commons.wikimedia.org'];
const RELATION = {
  place: 'Photo of this place',
  near: 'Taken near this place, not the place itself',
  inspiration: 'Inspiration photo, not the actual item',
};
const LEVEL = { info: 'Note', warn: 'Heads-up', block: 'Blocker' };
const PIN_KIND = { food: ['F', 'Food'], park: ['P', 'Parking'], stay: ['S', 'Stay'] };

// ------------------------------------------------------------------ geometry
// One coordinate space (1200 × 300) for the landscape, the path and the stop buttons,
// so "Today" sits exactly on the path origin and the route always spans the panel.
const W = 1200, H = 300;
const START = [60, 232], END = [1135, 150];
const SY = [200, 112, 192, 108, 196, 118];

function points(n) {
  const step = n > 1 ? 830 / (n - 1) : 0;
  return [START, ...Array.from({ length: n }, (_, i) => [185 + i * step, SY[i % SY.length]]), END];
}

function pathD(pts) {
  let d = `M${pts[0][0]} ${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${c1[0].toFixed(1)} ${c1[1].toFixed(1)},${c2[0].toFixed(1)} ${c2[1].toFixed(1)},${p2[0]} ${p2[1]}`;
  }
  return d;
}

const fracCache = {};
/** Share of the path length at each stop, measured once per stop count. */
function fractions(n) {
  if (fracCache[n]) return fracCache[n];
  const pts = points(n);
  const svg = s('svg', { width: 0, height: 0, 'aria-hidden': 'true', style: { position: 'absolute' } });
  document.body.append(svg);
  const len = (k) => { const p = s('path', { d: pathD(pts.slice(0, k + 1)) }); svg.append(p); return p.getTotalLength(); };
  const total = len(pts.length - 1) || 1;
  fracCache[n] = Array.from({ length: n }, (_, i) => len(i + 1) / total);
  svg.remove();
  return fracCache[n];
}

let lastFrac = null, lastSegs = null;

// ------------------------------------------------------------------ actions

function current(m) {
  return Math.min(state.j.stopIdx, m.length);
}

function setJ(patch, opts) {
  update({ j: Object.assign({}, state.j, patch) }, opts);
}

function openStop(i) {
  update({ j: Object.assign({}, state.j, { stopIdx: i, pin: null }), slide: 0, why: false, change: false, changeMsg: '', sheet: false },
    { focus: 'stop' });
  const m = stopsModel(state), x = m[i];
  if (x) announce(`Stop ${i + 1}, ${x.label}: ${x.item.name}, ${inr(x.price)}.`);
}

function lookGood(m) {
  const cur = m[current(m)];
  if (!cur) return;
  const next = nextIndex(m, Object.assign({}, state.j.decided, { [cur.key]: true }), cur.i);
  confirmStop(cur.key, { stopIdx: next, pin: null });
  update({ slide: 0, why: false, change: false, sheet: false }, { focus: next >= m.length ? 'finale' : 'stop' });
  const after = stopsModel(state);
  announce(next >= after.length ? 'Every stop is decided.'
    : `${cur.label} confirmed. Stop ${next + 1}, ${after[next].label}: ${after[next].item.name}, ${inr(after[next].price)}.`);
}

function pick(stop, o) {
  const rip = chooseOption(stop, o);
  update({ sheet: false, slide: 0, why: false, change: false, changeMsg: '' }, { focus: 'stop' });
  const affected = rip.map((r) => (state.plan.stops.find((x) => x.key === r.stop) || {}).label).filter(Boolean);
  say(`${o.name} is now your ${stop.label.toLowerCase()} choice.`);
  announce(`${o.name} chosen for ${stop.label}.` + (affected.length ? ` This also changes: ${affected.join(', ')}.` : ''));
}

function planStep() {
  const m = stopsModel(state);
  const i = m.findIndex((x) => !x.decided);
  if (i < 0) {
    update({ planning: false, j: Object.assign({}, state.j, { stopIdx: m.length }) }, { focus: 'finale' });
    say('Every stop is decided.');
    return;
  }
  update({ j: Object.assign({}, state.j, { stopIdx: i }), slide: 0, why: false, change: false, sheet: false });
  announce(`${m[i].label}: ${m[i].item.name}.`);
  later(() => {
    confirmStop(m[i].key);
    update({});
    later(planStep, 350);
  }, 900);
}

function planRest() {
  if (state.planning) return;
  update({ planning: true, j: Object.assign({}, state.j, { view: 'path' }) });
  announce('Confirming my suggestions for the remaining stops.');
  planStep();
}

function changeBy(m, kind) {
  const cur = m[current(m)];
  const o = cur.s.options.find((x) => x.kind === kind);
  if (o && o.id !== cur.item.id) pick(cur.s, o);
  else update({ changeMsg: `I don't have a ${kind.toLowerCase()} version here. These are the options I have.`, sheet: true }, { focus: 'sheet' });
}

function submitChange(m) {
  const t = state.changeDraft.toLowerCase();
  if (!t.trim()) return;
  const kind = /cheap|less|save|budget|lower|afford/.test(t) ? 'Cheaper'
    : /close|near|walk|metro|distance|far/.test(t) ? 'Closer'
      : /vibe|different|style|fun|look|feel|theme/.test(t) ? 'Different vibe' : null;
  state.changeDraft = '';
  if (kind) changeBy(m, kind);
  else update({ changeMsg: "I couldn't pin that down, so here are all the alternatives I have.", sheet: true }, { focus: 'sheet' });
}

// ------------------------------------------------------------------ data fetches

function photosFor(item, key) {
  const hint = item.photo;
  if (!hint) return { status: 'none' };
  if (state.demo.nophoto && key === 'venue') return { status: 'done', photos: [] };
  const k = JSON.stringify(hint);
  if (!state.photos[k]) {
    state.photos[k] = { status: 'loading' };
    api.photos(hint).then((r) => {
      state.photos[k] = { status: 'done', photos: r.ok && Array.isArray(r.data.photos) ? r.data.photos : [] };
      if (state.route === 'journey') rerender();
    });
  }
  return state.photos[k];
}

function venuePoint(m) {
  const v = m.find((x) => x.key === 'venue');
  if (v && v.item.lat != null) return { lat: v.item.lat, lon: v.item.lon, named: true, name: v.item.name };
  const c = state.plan.area_point;
  return c ? { lat: c.lat, lon: c.lon, named: false, name: state.plan.profile.area } : null;
}

function nearbyFor(pt) {
  const k = pt.lat.toFixed(4) + ',' + pt.lon.toFixed(4);
  if (!state.nearby[k]) {
    state.nearby[k] = { status: 'loading', places: [] };
    api.nearby(pt.lat, pt.lon).then((r) => {
      state.nearby[k] = { status: 'done', places: r.ok && Array.isArray(r.data.places) ? r.data.places : [] };
      if (state.route === 'journey') rerender();
    });
  }
  return state.nearby[k];
}

// ------------------------------------------------------------------ pieces

function budgetBar(m, idx, reach) {
  const b = summary(state).budget;
  const widths = m.map((x) => (x.price / b.denom) * 100);
  const start = lastSegs && lastSegs.length === widths.length ? lastSegs : widths;
  lastSegs = widths;
  const segs = m.map((x, i) => {
    const tip = `${x.label}: ${inr(x.price)}, ${x.decided ? 'decided' : 'still a suggestion'}`;
    return h('button', { class: `seg ${x.decided ? 'd' : 'p'}${i === idx ? ' cur' : ''}`, type: 'button', title: tip,
      'aria-label': tip + (i > reach ? '. Decide the earlier stops first.' : '. Open this stop.'),
      style: { flexBasis: start[i].toFixed(2) + '%' },
      onClick: () => (i > reach ? say('Decide the earlier stops first, or use "Plan the rest for me".') : openStop(i)) });
  });
  requestAnimationFrame(() => requestAnimationFrame(() => segs.forEach((el, i) => { el.style.flexBasis = widths[i].toFixed(2) + '%'; })));
  return h('section', { class: 'bud', 'aria-label': 'Budget' },
    h('div', { class: 'budtop' },
      h('span', { 'data-budget-line': '' }, budgetLine(b)),
      h('span', { class: b.over ? 'over' : '' }, b.over ? `Over budget by ${inr(b.total - b.budget)}`
        : `${inr(b.free)} still free`)),
    h('div', { class: 'bar', role: 'group', 'aria-label': `Budget bar: planned ${inr(b.total)} against ${inr(b.budget)}` },
      segs, h('div', { class: 'mark', 'aria-hidden': 'true', style: { left: `calc(${((b.budget / b.denom) * 100).toFixed(2)}% - 2px)` } })),
    h('div', { class: 'legend', 'aria-hidden': 'true' },
      h('span', null, 'Solid: decided'), h('span', null, 'Striped: still a suggestion'), h('span', null, 'Red line: your budget')),
    h('ul', { class: 'budlist', 'aria-label': 'Budget by stop' }, m.map((x, i) => h('li', null,
      h('button', { type: 'button', 'aria-disabled': i > reach ? 'true' : null, 'data-stop': x.key, 'data-price': x.price,
        onClick: () => (i > reach ? say('Decide the earlier stops first.') : openStop(i)) },
        h('span', { class: `sw ${x.decided ? 'd' : 'p'}`, 'aria-hidden': 'true' }),
        `${x.label} ${inr(x.price)}`, h('span', { class: 'sr' }, x.decided ? ', decided' : ', suggestion'))))));
}

function rulesBox() {
  const ins = state.plan.insights || [];
  if (!ins.length) return null;
  return h('details', { class: 'rules' },
    h('summary', null, `How I'm deciding: ${ins.length} rules from your details`,
      h('span', { class: 'aitag' }, 'Rules engine, not AI')),
    h('ul', null, ins.map((i) => h('li', null,
      h('span', { class: 'lvl ' + i.level }, LEVEL[i.level] || 'Note'),
      h('div', null, h('p', { class: 'rt' }, i.title), h('p', null, i.why),
        i.suggestions && i.suggestions.length ? h('p', { class: 'tt2' }, 'Try: ' + i.suggestions.join(' ')) : null)))));
}

function pathView(m, idx, reach, draw) {
  const n = m.length, pts = points(n), d = pathD(pts);
  const fr = fractions(n);
  const target = 1 - (idx >= n ? 1 : fr[idx]);
  const from = draw || lastFrac === null ? target : lastFrac;
  lastFrac = target;
  const pg = s('path', { class: 'pg' + (draw ? ' draw' : ''), d, pathLength: 1, style: { strokeDashoffset: String(from) } });
  if (from !== target) requestAnimationFrame(() => requestAnimationFrame(() => { pg.style.strokeDashoffset = String(target); }));
  const type = state.plan.profile.type;
  const endLabel = type === 'wedding' ? 'Wedding day' : type === 'birthday' ? 'Party day' : 'Event day';
  const pct = (x, y) => ({ left: (x / W) * 100 + '%', top: (y / H) * 100 + '%' });
  const nodes = m.map((x, i) => {
    const off = i > reach;
    const cls = off ? 'later' : i === idx ? 'now' : x.decided ? 'done' : 'later';
    const status = x.decided ? 'decided' : off ? 'to be decided later' : i === idx ? 'in progress' : 'proposed';
    return h('button', { class: 'node ' + cls, type: 'button', style: pct(pts[i + 1][0], pts[i + 1][1]),
      'aria-label': `Stop ${i + 1}, ${x.label}, ${status}${x.flag && !off ? ', updated' : ''}`,
      'aria-current': i === idx ? 'step' : null, 'aria-disabled': off ? 'true' : null, 'data-i': i,
      onClick: () => (off ? say('Decide the earlier stops first, or use "Plan the rest for me".') : openStop(i)) },
    h('span', { class: 'nb', 'aria-hidden': 'true' }, x.decided && i !== idx ? '✓' : String(i + 1)),
    h('span', { class: 'nl', 'aria-hidden': 'true' }, x.label),
    x.flag && !off ? h('span', { class: 'nu', 'aria-hidden': 'true' }, 'Updated') : null);
  });
  const scroll = h('div', { class: 'scenescroll' },
    h('div', { class: 'scene' },
      s('svg', { class: 'scsvg', viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', 'aria-hidden': 'true' },
        landscape(),
        s('path', { class: 'pbase', d }),
        pg,
        s('circle', { class: 'porig', cx: START[0], cy: START[1], r: 7 }),
        s('circle', { class: 'porig', cx: END[0], cy: END[1], r: 7 })),
      h('div', { class: 'endl', 'aria-hidden': 'true', style: pct(START[0], START[1] + 14) }, 'Today'),
      nodes,
      h('div', { class: 'endl end', 'aria-hidden': 'true', style: pct(END[0], END[1] - 14) }, endLabel)));
  const wrap = h('div', { class: 'scenewrap dark', role: 'group',
    'aria-label': `Your route from today to the ${endLabel.toLowerCase()}. The List view has the same stops as plain text.` },
  scroll, h('div', { class: 'scrollcue', 'aria-hidden': 'true' }));
  requestAnimationFrame(() => {
    const over = scroll.scrollWidth > scroll.clientWidth + 2;
    wrap.classList.toggle('overflow', over);
    if (over) {
      const node = scroll.querySelector(`[data-i="${Math.min(idx, n - 1)}"]`);
      if (node) scroll.scrollLeft = node.offsetLeft - scroll.clientWidth / 2;
    }
  });
  return [wrap, h('p', { class: 'scrollhint' }, 'Swipe or scroll sideways to see every stop.')];
}

function mapView(m) {
  const pt = venuePoint(m);
  const j = state.j;
  const kids = [mapBase()];
  const list = [];
  if (!pt) {
    kids.push(h('p', { class: 'mapnote' }, "There's nothing to map yet: I couldn't place your area on OpenStreetMap."));
    return h('div', null, h('div', { class: 'mapwrap' }, kids));
  }
  const ny = j.nearby ? nearbyFor(pt) : null;
  const places = ny ? ny.places : [];
  const kmx = (lon) => (lon - pt.lon) * 111.32 * Math.cos((pt.lat * Math.PI) / 180);
  const kmy = (lat) => (lat - pt.lat) * 110.57;
  const area = state.plan.area_point;
  const others = places.map((p) => [kmx(p.lon), kmy(p.lat)]);
  if (area && pt.named) others.push([kmx(area.lon), kmy(area.lat)]);
  const maxX = Math.max(0.3, ...others.map((o) => Math.abs(o[0])));
  const maxY = Math.max(0.3, ...others.map((o) => Math.abs(o[1])));
  const sc = Math.min(30, 42 / maxX, 22 / maxY);
  const proj = (lat, lon) => [50 + kmx(lon) * sc, 26 - kmy(lat) * sc];
  const sel = places.find((p) => p.id === j.pin) || null;
  if (area && pt.named) {
    const [ax, ay] = proj(area.lat, area.lon);
    if (Math.hypot(ax - 50, (ay - 26) * 1.8) > 12) {  // only when the pins would not collide
      kids.push(s('svg', { class: 'routesvg', viewBox: '0 0 100 56', preserveAspectRatio: 'xMidYMid slice', 'aria-hidden': 'true' },
        s('path', { class: 'm-route', d: `M${ax.toFixed(1)} ${ay.toFixed(1)} C${((ax + 50) / 2).toFixed(1)} ${ay.toFixed(1)} ${((ax + 50) / 2).toFixed(1)} 26 50 26` })));
      kids.push(h('div', { class: 'pin me', style: { left: ax + '%', top: (ay / 56) * 100 + '%' } }, 'Area'));
    }
  }
  if (sel) {
    const [sx, sy] = proj(sel.lat, sel.lon);
    kids.push(s('svg', { viewBox: '0 0 100 56', preserveAspectRatio: 'xMidYMid slice', 'aria-hidden': 'true' },
      s('line', { class: 'm-line', x1: 50, y1: 26, x2: sx.toFixed(1), y2: sy.toFixed(1) })));
  }
  kids.push(h('div', { class: 'pin ven', style: { left: '50%', top: (26 / 56) * 100 + '%' } }, pt.named ? 'Venue' : 'Area'));
  places.forEach((p) => {
    const [x, y] = proj(p.lat, p.lon);
    const [letter, label] = PIN_KIND[p.group] || ['•', 'Place'];
    kids.push(h('button', { class: 'pin ' + p.group, type: 'button', 'aria-pressed': j.pin === p.id ? 'true' : 'false',
      'aria-label': `${label}: ${p.name}, ${p.dist_km} km`, style: { left: Math.min(97, Math.max(3, x)) + '%', top: (Math.min(53, Math.max(3, y)) / 56) * 100 + '%' },
      onClick: () => setJ({ pin: j.pin === p.id ? null : p.id }) }, letter));
    list.push(h('li', null, h('b', null, `${label}: ${p.name}`), `${p.dist_km} km from the ${pt.named ? 'venue' : 'area centre'} · ${p.label}`));
  });
  if (sel) kids.push(h('div', { class: 'pincap', role: 'status' }, h('b', null, sel.name), ` · ${sel.dist_km} km · ${sel.label}`));
  else if (!pt.named) kids.push(h('p', { class: 'mapnote' }, "Your venue is a description, not a named place yet, so the map centres on your area."));
  return h('div', null,
    h('div', { class: 'mapwrap', role: 'group', 'aria-label': 'Map of the venue and nearby places. The list below has the same places.' }, kids),
    j.nearby
      ? (ny.status === 'loading' ? h('p', { class: 'lead', role: 'status' }, 'Looking for food, parking and stays nearby…')
        : list.length ? h('ul', { class: 'nearl', 'aria-label': 'Places near the venue' }, list)
          : h('p', { class: 'lead', role: 'status' }, 'No food, parking or stays found within 1.5 km on OpenStreetMap.'))
      : h('p', { class: 'lead' }, 'Turn on Nearby to see food, parking and stays around the venue.'),
    h('p', { class: 'osm' }, 'Places © OpenStreetMap contributors (ODbL).'));
}

function listView(m, idx, reach) {
  return h('ol', { class: 'slist', 'aria-label': 'All stops' }, m.map((x, i) => {
    const off = i > reach;
    return h('li', { class: i === idx ? 'now' : null },
      h('div', null,
        h('span', { class: 'sltag' }, `${x.label} · ${x.decided ? 'Decided' : off ? 'To be decided' : 'Proposed'}`), h('br'),
        h('b', null, off ? 'Waiting on earlier stops' : x.item.name), off ? null : ' · ' + inr(x.price),
        x.flag && !off ? h('span', { class: 'tt2' }, ' · Updated: ' + x.flag.note) : null),
      h('button', { class: 'btn', type: 'button', disabled: off,
        onClick: () => { setJ({ view: 'path' }, { render: false }); openStop(i); } },
      off ? 'Locked' : x.decided ? 'Revisit' : 'Open', h('span', { class: 'sr' }, ' ' + x.label)));
  }));
}

function photoBlock(cur) {
  const st = cur.s, it = cur.item;
  const noPhoto = (msg) => h('div', { class: 'nophoto' }, h('div', null, noPhotoIcon(),
    h('p', { class: 'disp', style: { fontSize: '22px', color: 'var(--ink)' } }, 'No photos for this one yet'), h('p', null, msg)));
  if (st.nophoto_msg || !it.photo) return noPhoto(st.nophoto_msg || "There's nothing real to photograph for this choice, so I won't show a stand-in.");
  const res = photosFor(it, st.key);
  if (res.status === 'loading') {
    return h('div', { class: 'loadart', role: 'status' },
      toneArt(it.tone, cur.i % 3, { class: 'ph', role: 'img', 'aria-label': 'Illustration while real photos load' }),
      h('p', null, 'Looking for real photos…'));
  }
  const photos = (res.photos || []).filter((p) => safeUrl(p.thumb, PHOTO_HOSTS) && !p.failed);
  if (!photos.length) {
    return noPhoto(st.key === 'venue'
      ? "The listing has no photos. I won't show a stand-in as if it were the real place."
      : "I couldn't find a real photo for this idea. I'd rather show nothing than something that isn't real.");
  }
  const n = photos.length, k = ((state.slide % n) + n) % n, p = photos[k];
  const page = safeUrl(p.page, PAGE_HOSTS);
  return h('div', { role: 'group', 'aria-roledescription': 'carousel', 'aria-label': 'Photos for ' + it.name },
    h('figure', { class: 'fig' },
      h('span', { class: 'rel' }, RELATION[p.relation] || RELATION.inspiration),
      h('img', { class: 'ph', src: safeUrl(p.thumb, PHOTO_HOSTS), alt: p.title || it.name, decoding: 'async',
        referrerpolicy: 'no-referrer', width: 960, height: 720,
        onError: () => { p.failed = true; rerender(); } }),
      h('figcaption', null, h('span', { class: 'cap' }, p.title),
        h('span', { class: 'credit' }, 'Photo: ', p.author, ' · ',
          page ? h('a', { href: page, target: '_blank', rel: 'noopener noreferrer' }, p.licence, h('span', { class: 'sr' }, ' (opens Wikimedia Commons)')) : p.licence))),
    n > 1 ? h('div', { class: 'cctl' },
      h('button', { class: 'cbtn', type: 'button', 'aria-label': 'Previous photo', onClick: () => update({ slide: state.slide + n - 1 }) }, '‹'),
      photos.map((_, i) => h('button', { class: 'dot', type: 'button', 'aria-label': `Photo ${i + 1} of ${n}`,
        'aria-current': i === k ? 'true' : 'false', onClick: () => update({ slide: i }) }, h('span'))),
      h('button', { class: 'cbtn', type: 'button', 'aria-label': 'Next photo', onClick: () => update({ slide: state.slide + 1 }) }, '›')) : null);
}

function proposal(m, idx) {
  const cur = m[idx], it = cur.item, st = cur.s, p = state.plan.profile, guests = p.guests || 0;
  const alts = allOptions(st).filter((o) => o.id !== it.id);
  const ai = state.plan.source === 'ai';
  const unit = it.per_guest ? `${inr(it.price)} a guest × ${guests}`
    : cur.extra ? `includes ${cur.extra > 0 ? '+' : '−'}${inr(Math.abs(cur.extra))} from earlier choices` : '';
  const where = it.where ? `From ${it.where}${it.dist_km != null ? `, ${it.dist_km} km away` : ''}`
    : it.dist_km != null ? `${it.dist_km} km from the centre of ${(p.area || '').split(',')[0]}` : '';
  const kinds = [...new Set(st.options.map((o) => o.kind))];
  return h('section', { class: 'prop', 'aria-labelledby': 'prop-h' },
    photoBlock(cur),
    h('div', null,
      h('p', { class: 'step' }, `Stop ${idx + 1} of ${m.length} · ${st.label} `,
        h('span', { class: 'aitag' + (ai ? ' ai' : '') }, ai ? 'Proposed by AI · checked by rules' : 'Rule-based suggestion')),
      cur.flag ? h('p', { class: 'upd' }, 'Updated: ' + cur.flag.note) : null,
      h('h2', { id: 'prop-h', class: 'disp ph2', tabindex: '-1' }, it.name),
      where ? h('p', { class: 'pwhere' }, where) : null,
      h('p', { class: 'pprice' }, inr(cur.price), unit ? h('span', null, unit) : null),
      it.cap && it.cap < guests ? h('p', { class: 'err' }, `Fits about ${it.cap}, fewer than your ${guests} guests.`) : null,
      h('p', { class: 'preason' }, it.reason),
      h('div', { class: 'pact' },
        h('button', { class: 'btn mari', type: 'button', onClick: () => lookGood(m) }, cur.decided ? 'Keep and continue' : 'Looks good'),
        h('button', { class: 'btn', id: 'opt-btn', type: 'button', disabled: !alts.length,
          onClick: () => update({ sheet: true, changeMsg: '' }, { focus: 'sheet' }) }, 'Show options'),
        h('button', { class: 'btn', type: 'button', 'aria-expanded': String(state.change), 'aria-controls': 'chg',
          onClick: () => update({ change: !state.change, changeMsg: '' }, { focus: state.change ? null : 'c-in' }) }, 'Change')),
      h('button', { class: 'linkb', type: 'button', 'aria-expanded': String(state.why), 'aria-controls': 'why',
        onClick: () => update({ why: !state.why }) }, 'Why?'),
      state.why ? h('div', { class: 'why', id: 'why' },
        it.why ? h('p', null, h('b', null, ai ? 'Why the AI picked this' : 'Why this'), it.why) : null,
        st.rule ? h('p', null, h('b', null, 'The rule behind it'), st.rule) : null) : null,
      state.change ? h('div', { class: 'chg', id: 'chg' },
        h('p', { style: { margin: '0 0 4px', fontWeight: '600' } }, 'What would you change?'),
        kinds.length ? h('div', { class: 'chips', role: 'group', 'aria-label': 'Change this by' },
          kinds.map((k) => h('button', { class: 'chip', type: 'button', onClick: () => changeBy(m, k) }, k))) : null,
        h('form', { class: 'ans', onSubmit: (e) => { e.preventDefault(); submitChange(m); } },
          h('label', { class: 'sr', for: 'c-in' }, 'Tell me what to change'),
          h('input', { id: 'c-in', class: 'cin', type: 'text', autocomplete: 'off', maxlength: 200,
            placeholder: 'e.g. something closer, or cheaper', value: state.changeDraft,
            onInput: (e) => { state.changeDraft = e.target.value; } }),
          h('button', { class: 'btn pri', type: 'submit' }, 'Find it')),
        state.changeMsg ? h('p', { class: 'chgmsg', role: 'status' }, state.changeMsg) : null) : null));
}

function finale() {
  const b = summary(state).budget;
  return h('section', { class: 'fin', 'aria-labelledby': 'fin-h' },
    h('p', { class: 'step' }, 'The route is complete'),
    h('h2', { id: 'fin-h', class: 'disp h1', tabindex: '-1' }, 'Every stop is decided.'),
    h('p', { class: 'lead' }, `You have ${inr(b.total)} planned` + (b.over
      ? `, which is ${inr(b.total - b.budget)} over your budget. "Ask to adjust" on My plan can bring it back.`
      : `, with ${inr(b.budget - b.total)} of your budget still free.`)),
    h('div', { class: 'pact' },
      h('button', { class: 'btn mari', type: 'button', onClick: () => go('day') }, 'See the event day'),
      h('button', { class: 'btn', type: 'button', onClick: () => go('plan') }, 'Open my plan')));
}

export function sheetView() {
  if (!state.sheet || state.route !== 'journey' || !state.plan) return null;
  const m = stopsModel(state), idx = current(m), cur = m[idx];
  if (!cur) return null;
  const guests = state.plan.profile.guests || 0;
  const close = () => update({ sheet: false }, { focus: 'opt-btn' });
  const alts = allOptions(cur.s).filter((o) => o.id !== cur.item.id).map((o, i) => {
    const price = Math.max(0, lineTotal(o, guests) + cur.extra), d = price - cur.price;
    const small = o.cap && o.cap < guests;
    const rip = (o.ripple || []).map((r) => {
      const lab = (state.plan.stops.find((x) => x.key === r.stop) || {}).label || r.stop;
      return `${lab}: ${r.note}${r.extra ? ` (${r.extra > 0 ? '+' : '−'}${inr(Math.abs(r.extra))})` : ''}`;
    });
    return h('li', { class: 'alt' },
      toneArt(o.tone, i % 3, { role: 'img', 'aria-label': 'Illustration, not a photo' }),
      h('div', null,
        h('p', { class: 'step', style: { margin: '0' } }, o.id === cur.s.rec.id ? 'My first suggestion' : o.kind),
        h('b', null, o.name),
        o.where || o.dist_km != null ? h('p', { class: 'tt2' }, o.where ? 'From ' + o.where : `${o.dist_km} km away`) : null,
        h('p', { class: 'altp' }, inr(price), ' ',
          h('span', { class: d > 0 ? 'more' : d < 0 ? 'less' : '' }, '· ' + (d === 0 ? 'same price' : d > 0 ? inr(d) + ' more' : inr(-d) + ' less'))),
        h('p', { class: 'altr' }, small ? `Too small for ${guests} guests (fits ${o.cap}).` : o.reason),
        rip.length ? h('p', { class: 'altrip' }, 'Also changes ' + rip.join('; ')) : null,
        h('button', { class: 'btn', type: 'button', disabled: !!small, onClick: () => pick(cur.s, o) },
          small ? 'Not big enough' : 'Choose this', h('span', { class: 'sr' }, ': ' + o.name))));
  });
  const trap = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); close(); return; }
    if (e.key !== 'Tab') return;
    const f = [...e.currentTarget.querySelectorAll('button:not([disabled]), a[href], input')];
    if (!f.length) return;
    if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
    else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
  };
  return h('div', { class: 'sheetwrap' },
    h('button', { class: 'scrim', type: 'button', tabindex: '-1', 'aria-label': 'Close options', onClick: close }),
    h('div', { id: 'sheet', class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'sheet-h', tabindex: '-1', onKeydown: trap },
      h('div', { class: 'sheethd' },
        h('div', null, h('p', { class: 'step' }, cur.s.label + ' · other ways to do it'),
          h('h2', { id: 'sheet-h', class: 'disp', style: { fontSize: '30px' } }, 'Pick one to compare')),
        h('button', { id: 'sheet-x', class: 'btn', type: 'button', onClick: close }, 'Close')),
      h('div', { class: 'sheetcur' },
        h('div', null, h('p', { class: 'step', style: { margin: '0' } }, 'Your current choice'),
          h('b', null, cur.item.name), ' · ' + inr(cur.price)),
        h('button', { class: 'btn mari', type: 'button', onClick: () => { state.sheet = false; lookGood(m); } },
          cur.decided ? 'Keep and continue' : 'Looks good')),
      state.changeMsg ? h('p', { class: 'chgmsg', role: 'status' }, state.changeMsg) : null,
      h('ul', { class: 'alts' }, alts)));
}

export function journeyView(opts) {
  const plan = state.plan;
  const m = stopsModel(state), n = m.length;
  const idx = current(m), reach = maxReach(m);
  const j = state.j;
  const strip = chipDefs(plan.profile).filter((c) => c.key !== 'access');
  const setView = (v) => () => setJ({ view: v });
  const undecided = m.some((x) => !x.decided);
  return h('main', { class: 'wrap journey', id: 'main' },
    h('p', { class: 'step' }, 'The journey'),
    h('h1', { class: 'disp h1', tabindex: '-1' }, plan.title),
    h('ul', { class: 'strip', 'aria-label': 'Event details' },
      strip.map((c) => h('li', { class: 'schip' }, c.text)),
      h('li', null, h('button', { class: 'linkb', type: 'button', onClick: () => go('chat', { focus: 'question' }) }, 'Edit details'))),
    budgetBar(m, idx, reach),
    rulesBox(),
    h('div', { class: 'jbar' },
      h('div', { class: 'sgc', role: 'group', 'aria-label': 'How to see your plan' },
        ['path', 'map', 'list'].map((v) => h('button', { class: 'sg', type: 'button', 'aria-pressed': String(j.view === v), onClick: setView(v) },
          v[0].toUpperCase() + v.slice(1)))),
      h('button', { class: 'tgl', type: 'button', role: 'switch', 'aria-checked': String(!!j.nearby),
        onClick: () => setJ({ nearby: !j.nearby, view: !j.nearby ? 'map' : j.view, pin: null }) }, h('span', { class: 'knob', 'aria-hidden': 'true' }), 'Nearby'),
      h('button', { class: 'btn', type: 'button', disabled: state.planning || !undecided, onClick: planRest }, 'Plan the rest for me')),
    j.view === 'map' ? mapView(m) : j.view === 'list' ? listView(m, idx, reach) : pathView(m, idx, reach, !!(opts && opts.routeChanged)),
    idx >= n ? finale() : proposal(m, idx),
    plan.notes && plan.notes.length ? h('ul', { class: 'notes', 'aria-label': 'Notes about this plan' }, plan.notes.map((t) => h('li', null, t))) : null);
}
