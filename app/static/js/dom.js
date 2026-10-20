/* Safe DOM building. Text is only ever set with textContent / text nodes, never innerHTML,
   so model and place text cannot inject markup. null, undefined and false children are skipped. */

const SVG_NS = 'http://www.w3.org/2000/svg';

function append(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k === null || k === undefined || k === false || k === '') continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
}

function setProps(el, props) {
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.setAttribute('class', v);
    else if (k === 'text') el.textContent = String(v);
    else if (k === 'style') Object.assign(el.style, v); // CSSOM: allowed by the CSP, unlike style=""
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, String(v));
  }
}

/** h('button', {class: 'btn', onClick}, 'Label') */
export function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  setProps(el, props);
  append(el, kids);
  return el;
}

/** SVG element with the same calling convention. */
export function s(tag, props, ...kids) {
  const el = document.createElementNS(SVG_NS, tag);
  setProps(el, props);
  append(el, kids);
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

/** Only https links to known hosts become hrefs; anything else is dropped. */
export function safeUrl(url, hosts) {
  try {
    const u = new URL(String(url));
    return u.protocol === 'https:' && hosts.includes(u.hostname) ? u.href : null;
  } catch (e) {
    return null;
  }
}

export const $ = (sel, root = document) => root.querySelector(sel);

// ---------- formatting ----------
export const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function inr(n) {
  return '₹' + Math.round(Number(n) || 0).toLocaleString('en-IN');
}

export function parseISO(iso) {
  if (!iso) return null;
  const [y, m, d] = String(iso).split('-').map(Number);
  return y && m && d ? new Date(y, m - 1, d) : null;
}

export function shortDate(iso) {
  const d = parseISO(iso);
  return d ? d.getDate() + ' ' + MON[d.getMonth()] : '';
}

export function longDate(iso) {
  const d = parseISO(iso);
  return d ? d.toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }) : '';
}

export function daysBetween(fromIso, toIso) {
  const a = parseISO(fromIso), b = parseISO(toIso);
  return a && b ? Math.round((b - a) / 86400000) : null;
}

export const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
