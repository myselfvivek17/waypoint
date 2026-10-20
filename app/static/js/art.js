/* Illustrations from the design. None of these is ever presented as a photo of a real place. */
import { s } from './dom.js';

export const TONES = {
  dusk: { sky: '#F2C27B', sun: '#E8A317', fg: '#16202A', ground: '#2A3A47' },
  garden: { sky: '#CFE0C3', sun: '#F6E6A8', fg: '#3E5C45', ground: '#5E7A5A' },
  blush: { sky: '#F3C9BD', sun: '#FBF4E6', fg: '#8C3B2B', ground: '#B8604A' },
  night: { sky: '#22313D', sun: '#E8A317', fg: '#0F171E', ground: '#16202A' },
  mustard: { sky: '#E8A317', sun: '#FBF4E6', fg: '#6B2E10', ground: '#9A4A0C' },
  sea: { sky: '#BFD8DC', sun: '#FBF4E6', fg: '#245660', ground: '#2F6F7B' },
};

const tone = (t) => TONES[t] || TONES.dusk;

/** Skyline / arches / bunting stand-in, used only while real photos load and in the options sheet. */
export function toneArt(t, motif, props) {
  const T = tone(t);
  const g = [];
  if (motif === 0) {
    g.push(s('g', { fill: T.fg },
      s('rect', { x: 20, y: 130, width: 60, height: 110 }), s('rect', { x: 92, y: 90, width: 50, height: 150 }),
      s('rect', { x: 152, y: 150, width: 70, height: 90 }), s('rect', { x: 232, y: 110, width: 44, height: 130 }),
      s('rect', { x: 288, y: 160, width: 90, height: 80 })));
  } else if (motif === 1) {
    g.push(s('g', { fill: T.fg },
      s('path', { d: 'M30 240 V150 a45 45 0 0 1 90 0 V240Z' }), s('path', { d: 'M155 240 V120 a45 45 0 0 1 90 0 V240Z' }),
      s('path', { d: 'M280 240 V150 a45 45 0 0 1 90 0 V240Z' })));
  } else {
    g.push(s('g', null,
      s('path', { d: 'M0 40 Q100 110 200 50 T400 40', fill: 'none', stroke: T.fg, 'stroke-width': 3 }),
      s('path', { d: 'M40 62 l14 28 l14 -28Z M110 80 l14 28 l14 -28Z M180 62 l14 28 l14 -28Z M250 56 l14 28 l14 -28Z M320 50 l14 28 l14 -28Z', fill: T.fg }),
      s('rect', { x: 0, y: 200, width: 400, height: 40, fill: T.fg, opacity: '.35' })));
  }
  return s('svg', Object.assign({ viewBox: '0 0 400 300', preserveAspectRatio: 'xMidYMid slice' }, props),
    s('rect', { width: 400, height: 300, fill: T.sky }),
    s('circle', { cx: 310, cy: 86, r: 36, fill: T.sun }),
    g,
    s('rect', { y: 236, width: 400, height: 64, fill: T.ground }));
}

export function noPhotoIcon() {
  return s('svg', { viewBox: '0 0 80 60', width: 96, height: 72, 'aria-hidden': 'true', fill: 'none', 'stroke-width': 2.5 },
    s('rect', { x: 6, y: 6, width: 68, height: 48, rx: 6, 'stroke-dasharray': '5 4' }),
    s('path', { d: 'M14 46 L30 28 L42 40 L52 30 L66 46', 'stroke-linejoin': 'round' }),
    s('circle', { cx: 54, cy: 18, r: 5 }));
}

const INK = '#16202A';
const ART = {
  arrive: () => [s('path', { d: 'M18 52V28a14 14 0 0 1 28 0v24z', fill: '#FBF4E6', stroke: INK, 'stroke-width': 2.5 }),
    s('circle', { cx: 38, cy: 38, r: 2.5, fill: INK })],
  games: () => [s('rect', { x: 26, y: 10, width: 12, height: 30, rx: 3, transform: 'rotate(30 32 32)', fill: '#FBF4E6', stroke: INK, 'stroke-width': 2.5 }),
    s('rect', { x: 30, y: 38, width: 4, height: 14, transform: 'rotate(30 32 32)', fill: INK }),
    s('circle', { cx: 46, cy: 46, r: 6, fill: '#B8441E', stroke: INK, 'stroke-width': 2.5 })],
  cake: () => [s('rect', { x: 14, y: 36, width: 36, height: 14, rx: 3, fill: '#FBF4E6', stroke: INK, 'stroke-width': 2.5 }),
    s('rect', { x: 20, y: 26, width: 24, height: 10, rx: 3, fill: '#FBF4E6', stroke: INK, 'stroke-width': 2.5 }),
    s('path', { d: 'M32 12v12M26 16v8M38 16v8', stroke: INK, 'stroke-width': 2.5, 'stroke-linecap': 'round' })],
  dinner: () => [s('circle', { cx: 32, cy: 32, r: 16, fill: '#FBF4E6', stroke: INK, 'stroke-width': 2.5 }),
    s('circle', { cx: 26, cy: 29, r: 4, fill: '#E8A317', stroke: INK, 'stroke-width': 2 }),
    s('circle', { cx: 38, cy: 29, r: 4, fill: '#B8441E', stroke: INK, 'stroke-width': 2 }),
    s('circle', { cx: 32, cy: 38, r: 4, fill: '#6E8F5E', stroke: INK, 'stroke-width': 2 })],
  music: () => [s('ellipse', { cx: 32, cy: 32, rx: 18, ry: 11, fill: '#FBF4E6', stroke: INK, 'stroke-width': 2.5 }),
    s('path', { d: 'M14 32h36M24 22l4 20M40 22l-4 20', stroke: INK, 'stroke-width': 2 })],
  ritual: () => [s('path', { d: 'M32 12c6 8 10 12 10 18a10 10 0 0 1-20 0c0-6 4-8 4-14 3 2 4 4 6-4z', fill: '#E8A317', stroke: INK, 'stroke-width': 2.5, 'stroke-linejoin': 'round' }),
    s('path', { d: 'M18 46h28l-4 8H22z', fill: '#FBF4E6', stroke: INK, 'stroke-width': 2.5, 'stroke-linejoin': 'round' })],
};

export function dayIcon(art) {
  return s('svg', { viewBox: '0 0 64 64', 'aria-hidden': 'true' },
    s('circle', { cx: 32, cy: 32, r: 30, fill: '#F6D58A' }), (ART[art] || ART.arrive)());
}

/** Soft illustrated map tiles (viewBox 0 0 100 56). */
export function mapBase() {
  const blocks = [[4, 12, 20, 12], [28, 12, 14, 12], [62, 12, 16, 12], [82, 12, 14, 12], [4, 32, 14, 10], [22, 32, 20, 10],
    [46, 31, 14, 12], [64, 32, 12, 8]];
  return s('svg', { viewBox: '0 0 100 56', preserveAspectRatio: 'xMidYMid slice', 'aria-hidden': 'true' },
    s('path', { class: 'm-park', d: 'M0 0 H100 V9 C80 13 70 5 50 9 S20 12 0 8Z' }),
    s('ellipse', { class: 'm-water', cx: 84, cy: 46, rx: 15, ry: 7 }),
    blocks.map(([x, y, w, hh]) => s('rect', { class: 'm-block', x, y, width: w, height: hh, rx: 2 })),
    s('rect', { class: 'm-park', x: 4, y: 46, width: 30, height: 7, rx: 2 }),
    s('path', { class: 'm-road', d: 'M0 28 H100 M26 10 V56 M44 10 V56 M80 10 V40' }),
    s('path', { class: 'm-edge', d: 'M0 28 H100 M26 10 V56 M44 10 V56 M80 10 V40' }));
}

/** The night landscape behind the journey path, drawn edge to edge (viewBox 0 0 1200 300). */
export function landscape() {
  const stars = [[300, 46], [490, 30], [670, 54], [820, 28], [180, 28], [980, 42], [1100, 60]];
  const bulbs = [100, 290, 500, 700, 890, 1090];
  return [
    s('path', { class: 'hill1', d: 'M0 214 C140 160 265 232 445 188 S770 142 905 206 S1115 168 1200 196 V300 H0Z' }),
    s('path', { class: 'hill2', d: 'M0 252 C180 216 362 272 572 234 S920 216 1200 252 V300 H0Z' }),
    s('circle', { class: 'sun', cx: 935, cy: 60, r: 26 }),
    stars.map(([cx, cy], i) => s('circle', { class: 'star', cx, cy, r: i % 2 ? 2 : 2.6 })),
    s('path', { class: 'wire', d: 'M0 14 C210 42 390 4 600 28 S980 8 1200 26' }),
    bulbs.map((cx, i) => s('circle', { class: 'bulb', cx, cy: [26, 24, 18, 24, 14, 20][i], r: 5 })),
  ];
}

export function sunIcon() {
  return s('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, 'stroke-linecap': 'round' },
    s('circle', { cx: 12, cy: 12, r: 4.5 }),
    s('path', { d: 'M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8' }));
}

export function moonIcon() {
  return s('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, 'stroke-linejoin': 'round' },
    s('path', { d: 'M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5z' }));
}
