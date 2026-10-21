/* "Pin on map": a small OpenStreetMap map where the user taps the exact spot of the event.

   Tiles come from tile.openstreetmap.org (the one extra host in the CSP's img-src). Following
   the OSM tile usage policy, they load only after the user opens the map, a dozen or so per
   view, with the browser's own caching, a Referer for this site and the required attribution.
   Nothing is drawn with innerHTML; the map is plain positioned <img> elements. */
import { h } from './dom.js';

const TILE = 256;
export const MIN_Z = 10, MAX_Z = 18;
const STEP = 24; // arrow keys move the pin this many pixels
const SPAN = { x: 420, y: 180 }; // half the widest frame plus a margin: tiles outside are not loaded

function world(lat, lon, z) {
  const n = TILE * 2 ** z, r = (lat * Math.PI) / 180;
  return { x: ((lon + 180) / 360) * n, y: ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n };
}

function unworld(x, y, z) {
  const n = TILE * 2 ** z;
  return { lat: (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / n))) * 180) / Math.PI, lon: (x / n) * 360 - 180 };
}

const round = (v) => Math.round(v * 1e4) / 1e4; // about 10 m: the venue's spot, not a device position

/** A map view centred between the points, zoomed so they all fit in the frame. */
export function viewFor(points) {
  const lat = points.reduce((a, p) => a + p.lat, 0) / points.length;
  const lon = points.reduce((a, p) => a + p.lon, 0) / points.length;
  let z = 15;
  while (z > MIN_Z) {
    const c = world(lat, lon, z);
    if (points.every((p) => { const w = world(p.lat, p.lon, z); return Math.abs(w.x - c.x) < 130 && Math.abs(w.y - c.y) < 90; })) break;
    z -= 1;
  }
  return { lat, lon, z, pin: null };
}

function place(el, w, c) {
  Object.assign(el.style, { left: `calc(50% + ${Math.round(w.x - c.x)}px)`, top: `calc(50% + ${Math.round(w.y - c.y)}px)` });
  return el;
}

/** The map. ``m`` is { lat, lon, z, pin }; ``marks`` are the numbered options; ``onChange`` gets
    the next view, ``onUse`` the chosen pin. Keyboard: arrows move the pin, + and - zoom, Enter uses it. */
export function pinMap(m, { marks = [], onChange, onUse, onCancel }) {
  const c = world(m.lat, m.lon, m.z);
  const n = 2 ** m.z;
  const tiles = [];
  for (let tx = Math.floor((c.x - SPAN.x) / TILE); tx <= Math.floor((c.x + SPAN.x) / TILE); tx++) {
    for (let ty = Math.floor((c.y - SPAN.y) / TILE); ty <= Math.floor((c.y + SPAN.y) / TILE); ty++) {
      if (tx < 0 || ty < 0 || tx >= n || ty >= n) continue;
      tiles.push(place(h('img', { class: 'pmtile', alt: '', draggable: 'false', width: TILE, height: TILE,
        referrerpolicy: 'strict-origin-when-cross-origin', src: `https://tile.openstreetmap.org/${m.z}/${tx}/${ty}.png` }),
      { x: tx * TILE, y: ty * TILE }, c));
    }
  }
  const zoom = (dz) => {
    const z = Math.min(MAX_Z, Math.max(MIN_Z, m.z + dz));
    const at = m.pin || m;
    onChange({ lat: at.lat, lon: at.lon, z, pin: m.pin });
  };
  const movePin = (dx, dy) => {
    const p = world((m.pin || m).lat, (m.pin || m).lon, m.z);
    const ll = unworld(p.x + dx, p.y + dy, m.z);
    onChange(Object.assign({}, m, { pin: { lat: round(ll.lat), lon: round(ll.lon) } }));
  };
  const frame = h('div', {
    class: 'pmframe', id: 'pm-frame', tabindex: '0', role: 'application',
    'aria-label': 'Map of the area. Tap the spot, or use the arrow keys to move the pin, plus and minus to zoom, Enter to use it.',
    'aria-describedby': 'pm-state',
    onClick: (e) => {
      const r = e.currentTarget.getBoundingClientRect();
      const ll = unworld(c.x + e.clientX - r.left - r.width / 2, c.y + e.clientY - r.top - r.height / 2, m.z);
      onChange(Object.assign({}, m, { pin: { lat: round(ll.lat), lon: round(ll.lon) } }));
    },
    onKeydown: (e) => {
      const d = { ArrowLeft: [-STEP, 0], ArrowRight: [STEP, 0], ArrowUp: [0, -STEP], ArrowDown: [0, STEP] }[e.key];
      if (d) { e.preventDefault(); movePin(d[0], d[1]); } else if (e.key === '+' || e.key === '=') { e.preventDefault(); zoom(1); } else if (e.key === '-') { e.preventDefault(); zoom(-1); } else if (e.key === 'Enter' && m.pin) { e.preventDefault(); onUse(m.pin); }
    },
  }, tiles,
  marks.map((k, i) => place(h('span', { class: 'pmmark', 'aria-hidden': 'true', title: k.label }, String(i + 1)), world(k.lat, k.lon, m.z), c)),
  m.pin ? place(h('span', { class: 'pmpin', 'aria-hidden': 'true' }), world(m.pin.lat, m.pin.lon, m.z), c) : null,
  h('span', { class: 'pmattr' }, '© ', h('a', { href: 'https://www.openstreetmap.org/copyright', target: '_blank', rel: 'noopener',
    onClick: (e) => e.stopPropagation() }, 'OpenStreetMap'), ' contributors'));
  return h('div', { class: 'pinmap' },
    frame,
    h('p', { class: 'tt2', id: 'pm-state', role: 'status' }, m.pin
      ? 'Pin placed. Tap again to move it, or use this spot.'
      : marks.length ? 'The numbers are the places listed above. Tap the exact spot of the event.' : 'Tap the exact spot of the event.'),
    h('div', { class: 'pmact' },
      h('button', { class: 'btn', type: 'button', id: 'pm-out', 'aria-label': 'Zoom out', disabled: m.z <= MIN_Z, onClick: () => zoom(-1) }, '−'),
      h('button', { class: 'btn', type: 'button', id: 'pm-in', 'aria-label': 'Zoom in', disabled: m.z >= MAX_Z, onClick: () => zoom(1) }, '+'),
      h('button', { class: 'btn pri', type: 'button', id: 'pm-use', disabled: !m.pin, onClick: () => onUse(m.pin) }, 'Use this spot'),
      h('button', { class: 'linkb', type: 'button', id: 'pm-cancel', onClick: onCancel }, 'Cancel')));
}
