/* The invitation card: AI-written text options, drawn client-side on a canvas in the Waypoint
   style. Templates per occasion, a colour, an optional photo that never leaves the browser,
   then Download PNG, Print / PDF, Copy text and Share. */
import { api, errorText } from './api.js';
import { $, h, longDate } from './dom.js';
import { stopsModel } from './model.js';
import { state } from './store.js';
import { announce, guide, primary, say, stopThinking, think, update } from './ui.js';

const W = 1080, H = 1350, M = 90;
const LANGS = [['en', 'English'], ['hi', 'हिन्दी'], ['te', 'తెలుగు']];
const TONE_LABEL = { warm: 'Warm', fun: 'Fun', formal: 'Formal' };
const PALETTES = {
  night: { name: 'Night and marigold', bg: '#16202A', ink: '#FBF4E6', accent: '#E8A317', soft: '#26394A' },
  cream: { name: 'Cream and saffron', bg: '#FBF4E6', ink: '#16202A', accent: '#9A4A0C', soft: '#F1E5CB' },
  rose: { name: 'Rose and terracotta', bg: '#F6E1D8', ink: '#4A1F18', accent: '#B8604A', soft: '#EFC9BB' },
  sea: { name: 'Sea and cream', bg: '#DCEBEC', ink: '#173E45', accent: '#2F6F7B', soft: '#BFD8DC' },
  marigold: { name: 'Marigold and ink', bg: '#E8A317', ink: '#16202A', accent: '#6B2E10', soft: '#F2C27B' },
};
const TEMPLATES = {
  bunting: { name: 'Bunting and balloons', for: ['birthday', 'other'] },
  toran: { name: 'Mango-leaf toran', for: ['house-warming'] },
  mandap: { name: 'Marigold mandap', for: ['wedding'] },
  diya: { name: 'Diyas and rangoli', for: ['house-warming', 'wedding', 'community'] },
  lanterns: { name: 'Paper lanterns', for: ['community', 'birthday'] },
  path: { name: 'The Waypoint route', for: ['other', 'birthday', 'wedding', 'house-warming', 'community'] },
};
const SERIF = "Fraunces, 'Noto Serif Devanagari', 'Noto Sans Devanagari', 'Noto Sans Telugu', Georgia, serif";
const SANS = "'Hanken Grotesk', 'Noto Sans', 'Noto Sans Devanagari', 'Noto Sans Telugu', system-ui, sans-serif";

let fetching = null;   // the language being written right now
let photo = null;      // the user's photo: an in-memory image only, never stored or uploaded
let photoUrl = null;

// ------------------------------------------------------------------ state

function plan() { return state.plan; }

/** The facts the card and the AI text are written from, all derived from the plan. */
export function inviteFacts() {
  const p = plan().profile;
  const venue = stopsModel(state).find((x) => x.key === 'venue');
  const atHome = !!venue && (venue.s.mode === 'home' || /\bhome\b/i.test(venue.item.name) && !venue.item.place_id);
  const area = (p.area || '').split(',')[0].trim();
  const place = !venue || atHome ? '' : venue.status !== 'open' ? (venue.s.note || area)
    : venue.item.place_id ? `${venue.item.name}, ${area}` : area;
  const first = plan().days[0] && plan().days[0].items.find((it) => it.status !== 'skip');
  return { title: plan().title, type: p.type, date: p.date, time: first ? first.time : '', place, at_home: atHome };
}

function inv() {
  if (!state.inv || state.inv.plan !== plan().title + plan().today) {
    const tpl = Object.keys(TEMPLATES).find((k) => TEMPLATES[k].for.includes(plan().profile.type)) || 'path';
    state.inv = { plan: plan().title + plan().today, lang: 'en', options: {}, source: {}, tone: 'warm', headline: plan().title,
      message: '', details: '', template: tpl, palette: plan().profile.type === 'wedding' ? 'marigold' : 'night', host: '', rsvp: '' };
  }
  return state.inv;
}

function detailsLine() {
  const f = inviteFacts();
  return [longDate(f.date), f.time, f.at_home ? 'At our home' : f.place].filter(Boolean).join(' · ');
}

async function fetchTexts(lang) {
  if (fetching) return;
  fetching = lang;
  const s = inv();
  think('Writing three invitations from your plan…');
  const r = await api.invite(Object.assign(inviteFacts(), { host: s.host, rsvp: s.rsvp, language: lang }));
  stopThinking();
  fetching = null;
  if (!r.ok) { say(errorText(r)); return; }
  s.options = Object.assign({}, s.options, { [lang]: r.data.options });
  s.source = Object.assign({}, s.source, { [lang]: r.data.source });
  const opt = r.data.options.find((o) => o.tone === s.tone) || r.data.options[0];
  update({ inv: Object.assign({}, s, { lang, message: opt.text }) }, { focus: null });
  announce('Three invitations are ready. The card shows the ' + TONE_LABEL[opt.tone].toLowerCase() + ' one.');
}

// ------------------------------------------------------------------ drawing

const segmenter = typeof Intl !== 'undefined' && Intl.Segmenter ? new Intl.Segmenter(undefined, { granularity: 'grapheme' }) : null;
const graphemes = (s) => (segmenter ? [...segmenter.segment(s)].map((g) => g.segment) : [...s]);

/** Greedy word wrap; a word wider than the line (a very long name) is broken between graphemes,
    so emoji and Indic clusters are never split. */
export function wrap(ctx, text, width) {
  const lines = [];
  lines.broke = false;
  for (const para of String(text || '').split(/\n+/)) {
    let line = '';
    for (const word of para.split(/\s+/).filter(Boolean)) {
      const trial = line ? line + ' ' + word : word;
      if (ctx.measureText(trial).width <= width) { line = trial; continue; }
      if (line) lines.push(line);
      if (ctx.measureText(word).width <= width) { line = word; continue; }
      line = '';
      lines.broke = true;
      for (const g of graphemes(word)) {
        if (ctx.measureText(line + g).width > width && line) { lines.push(line); line = g; } else line += g;
      }
    }
    if (line) lines.push(line);
  }
  return lines;
}

/** The largest size (down to ``min``) at which the text fits ``maxLines`` lines and ``maxH``. */
function fit(ctx, text, font, width, from, min, maxLines, maxH, lh) {
  let fallback = null;  // fits, but only by splitting a long word: used if nothing better is found
  for (let size = from; size >= min; size -= 2) {
    ctx.font = font(size);
    const lines = wrap(ctx, text, width);
    if (lines.length <= maxLines && lines.length * size * lh <= maxH) {
      if (!lines.broke) return { size, lines, overflow: false };
      fallback = fallback || { size, lines, overflow: false };
    }
  }
  if (fallback) { ctx.font = font(fallback.size); return fallback; }
  ctx.font = font(min);
  let lines = wrap(ctx, text, width);
  const keep = Math.max(1, Math.min(maxLines, Math.floor(maxH / (min * lh))));
  const overflow = lines.length > keep;
  if (overflow) {
    lines = lines.slice(0, keep);
    let last = lines[keep - 1];
    while (last && ctx.measureText(last + '…').width > width) last = graphemes(last).slice(0, -1).join('');
    lines[keep - 1] = last + '…';
  }
  return { size: min, lines, overflow };
}

function drawLines(ctx, lines, y, size, lh) {
  lines.forEach((ln, i) => ctx.fillText(ln, W / 2, y + i * size * lh));
  return y + lines.length * size * lh;
}

function leaf(ctx, x, y, r, rot, fill) {
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot); ctx.fillStyle = fill;
  ctx.beginPath(); ctx.moveTo(0, 0); ctx.quadraticCurveTo(r * .6, r * .5, 0, r * 1.5); ctx.quadraticCurveTo(-r * .6, r * .5, 0, 0); ctx.fill();
  ctx.restore();
}

function diyaShape(ctx, x, y, s, pal) {
  ctx.fillStyle = pal.accent;
  ctx.beginPath(); ctx.moveTo(x - 40 * s, y); ctx.quadraticCurveTo(x, y + 44 * s, x + 40 * s, y); ctx.closePath(); ctx.fill();
  ctx.fillStyle = '#E8A317';
  ctx.beginPath(); ctx.moveTo(x, y - 46 * s); ctx.quadraticCurveTo(x + 16 * s, y - 16 * s, x, y - 4 * s); ctx.quadraticCurveTo(x - 16 * s, y - 16 * s, x, y - 46 * s); ctx.fill();
}

/** Decor for each template. Returns the y where the text area may start. */
const DECOR = {
  bunting(ctx, pal) {
    ctx.strokeStyle = pal.ink; ctx.lineWidth = 4;
    ctx.beginPath(); ctx.moveTo(0, 90); ctx.quadraticCurveTo(W / 2, 230, W, 90); ctx.stroke();
    const cols = [pal.accent, pal.soft, pal.ink];
    for (let i = 0; i < 11; i++) {
      const t = (i + .5) / 11, x = t * W, y = 90 + 140 * 4 * t * (1 - t) * .5 + 2;
      ctx.fillStyle = cols[i % 3];
      ctx.beginPath(); ctx.moveTo(x - 30, y); ctx.lineTo(x + 30, y); ctx.lineTo(x, y + 62); ctx.closePath(); ctx.fill();
    }
    [[120, H - 330, pal.accent], [W - 130, H - 360, pal.soft]].forEach(([x, y, c]) => {
      ctx.fillStyle = c; ctx.beginPath(); ctx.ellipse(x, y, 46, 58, 0, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = pal.ink; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(x, y + 58); ctx.quadraticCurveTo(x + 20, y + 120, x - 6, y + 170); ctx.stroke();
    });
    return 300;
  },
  toran(ctx, pal) {
    ctx.strokeStyle = pal.ink; ctx.lineWidth = 5;
    ctx.beginPath(); ctx.moveTo(0, 70); ctx.lineTo(W, 70); ctx.stroke();
    for (let i = 0; i < 18; i++) leaf(ctx, 30 + i * 60, 72, 34, i % 2 ? .12 : -.12, i % 2 ? '#3E5C45' : '#5E7A5A');
    for (let i = 0; i < 6; i++) {
      const x = 120 + i * 168;
      for (let k = 0; k < 6; k++) { ctx.fillStyle = k % 2 ? pal.accent : '#E8A317'; ctx.beginPath(); ctx.arc(x, 140 + k * 22, 11, 0, Math.PI * 2); ctx.fill(); }
    }
    diyaShape(ctx, W / 2, H - 120, 1.3, pal);
    return 300;
  },
  mandap(ctx, pal) {
    ctx.strokeStyle = pal.accent; ctx.lineWidth = 10;
    ctx.beginPath(); ctx.moveTo(M, H - M); ctx.lineTo(M, 420); ctx.quadraticCurveTo(M, 120, W / 2, 110); ctx.quadraticCurveTo(W - M, 120, W - M, 420); ctx.lineTo(W - M, H - M); ctx.stroke();
    for (const side of [-1, 1]) {
      for (let k = 0; k < 12; k++) {
        const x = W / 2 + side * (60 + k * 34), y = 160 + Math.pow(k, 1.6) * 6;
        ctx.fillStyle = k % 2 ? '#E8A317' : pal.accent; ctx.beginPath(); ctx.arc(x, y, 13, 0, Math.PI * 2); ctx.fill();
      }
    }
    ctx.fillStyle = '#E8A317'; ctx.beginPath(); ctx.arc(W / 2, 110, 22, 0, Math.PI * 2); ctx.fill();
    return 330;
  },
  diya(ctx, pal) {
    const cx = W / 2, cy = H - 40;
    for (let ring = 4; ring >= 1; ring--) {
      ctx.fillStyle = ring % 2 ? pal.soft : pal.accent;
      for (let i = 0; i < 12; i++) {
        const a = Math.PI + (i / 11) * Math.PI;
        ctx.beginPath(); ctx.ellipse(cx + Math.cos(a) * ring * 52, cy + Math.sin(a) * ring * 52, 22, 12, a, 0, Math.PI * 2); ctx.fill();
      }
    }
    for (let i = 0; i < 5; i++) diyaShape(ctx, 140 + i * 200, 150, 1, pal);
    return 260;
  },
  lanterns(ctx, pal) {
    ctx.strokeStyle = pal.ink; ctx.lineWidth = 3;
    ctx.beginPath(); ctx.moveTo(0, 50); ctx.quadraticCurveTo(W / 2, 110, W, 50); ctx.stroke();
    [150, 360, 560, 760, 940].forEach((x, i) => {
      const y = 70 + (i % 2) * 50 + 30;
      ctx.beginPath(); ctx.moveTo(x, 60 + 20 * Math.sin(x / W * Math.PI)); ctx.lineTo(x, y); ctx.stroke();
      ctx.fillStyle = i % 2 ? pal.accent : '#E8A317';
      ctx.beginPath(); ctx.ellipse(x, y + 56, 44, 56, 0, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = pal.ink; ctx.fillRect(x - 22, y, 44, 10); ctx.fillRect(x - 22, y + 104, 44, 10);
    });
    return 290;
  },
  path(ctx, pal) {
    ctx.setLineDash([3, 18]); ctx.lineCap = 'round'; ctx.strokeStyle = pal.ink; ctx.globalAlpha = .45; ctx.lineWidth = 6;
    const d = new Path2D(`M70 ${H - 150} C300 ${H - 150} 320 ${H - 260} 540 ${H - 250} S820 ${H - 140} 1010 ${H - 200}`);
    ctx.stroke(d); ctx.setLineDash([]); ctx.globalAlpha = 1; ctx.strokeStyle = '#E8A317'; ctx.lineWidth = 10;
    ctx.stroke(new Path2D(`M70 ${H - 150} C300 ${H - 150} 320 ${H - 260} 540 ${H - 250}`));
    [[70, H - 150], [540, H - 250], [1010, H - 200]].forEach(([x, y], i) => {
      ctx.fillStyle = i < 2 ? '#E8A317' : pal.bg; ctx.strokeStyle = pal.ink; ctx.lineWidth = 4;
      ctx.beginPath(); ctx.arc(x, y, i === 1 ? 22 : 14, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    });
    ctx.fillStyle = '#E8A317'; ctx.beginPath(); ctx.arc(W - 150, 150, 54, 0, Math.PI * 2); ctx.fill();
    return 120;
  },
};

/** Draw the whole card. Returns the layout, which the page exposes for checking (fit, lines). */
export function drawCard(canvas, s) {
  const ctx = canvas.getContext('2d');
  const pal = PALETTES[s.palette] || PALETTES.night;
  canvas.width = W; canvas.height = H;
  ctx.fillStyle = pal.bg; ctx.fillRect(0, 0, W, H);
  ctx.save();
  let y = (DECOR[s.template] || DECOR.path)(ctx, pal);
  ctx.restore();
  const bottom = s.template === 'diya' ? H - 270 : s.template === 'path' ? H - 300 : s.template === 'toran' ? H - 230 : H - M - 20;
  if (photo) {
    const pw = 360, ph = 400, px = (W - pw) / 2;
    ctx.save();
    ctx.beginPath(); ctx.moveTo(px, y + ph); ctx.lineTo(px, y + pw / 2); ctx.arc(px + pw / 2, y + pw / 2, pw / 2, Math.PI, 0); ctx.lineTo(px + pw, y + ph); ctx.closePath();
    ctx.clip();
    const sc = Math.max(pw / photo.width, ph / photo.height);
    ctx.drawImage(photo, px + (pw - photo.width * sc) / 2, y + (ph - photo.height * sc) / 2, photo.width * sc, photo.height * sc);
    ctx.restore();
    ctx.strokeStyle = pal.accent; ctx.lineWidth = 8;
    ctx.beginPath(); ctx.moveTo(px, y + ph); ctx.lineTo(px, y + pw / 2); ctx.arc(px + pw / 2, y + pw / 2, pw / 2, Math.PI, 0); ctx.lineTo(px + pw, y + ph); ctx.closePath(); ctx.stroke();
    y += ph + 40;
  }
  const width = W - 2 * M - 40;
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  const detH = 90, kicker = 60;
  const head = fit(ctx, s.headline, (n) => `600 ${n}px ${SERIF}`, width, 96, 44, 3, 330, 1.12);
  const headH = head.lines.length * head.size * 1.12 + 30;
  const msg = fit(ctx, s.message, (n) => `400 ${n}px ${SANS}`, width, 44, 24, 12, Math.max(60, bottom - detH - y - kicker - headH), 1.4);
  const msgH = msg.lines.length * msg.size * 1.4;
  y += Math.max(0, (bottom - detH - y - (kicker + headH + msgH)) / 2);  // centre the words in the space left
  ctx.fillStyle = pal.accent;
  ctx.font = `700 30px ${SANS}`;
  ctx.fillText("YOU'RE INVITED", W / 2, y);
  y += kicker;
  ctx.fillStyle = pal.ink;
  ctx.font = `600 ${head.size}px ${SERIF}`;
  y = drawLines(ctx, head.lines, y, head.size, 1.12) + 30;
  ctx.font = `400 ${msg.size}px ${SANS}`;
  y = drawLines(ctx, msg.lines, y, msg.size, 1.4) + 24;
  const det = fit(ctx, s.details, (n) => `700 ${n}px ${SANS}`, width, 34, 22, 2, detH, 1.3);
  ctx.fillStyle = pal.accent;
  drawLines(ctx, det.lines, Math.max(y, bottom - det.lines.length * det.size * 1.3), det.size, 1.3);
  return { fit: !(head.overflow || msg.overflow || det.overflow), lines: head.lines.length + msg.lines.length + det.lines.length,
    headline: head.size, message: msg.size };
}

let raf = 0;
function redraw() {
  cancelAnimationFrame(raf);
  raf = requestAnimationFrame(async () => {
    const canvas = $('#card');
    if (!canvas) return;
    try { await Promise.all([document.fonts.load(`600 80px Fraunces`), document.fonts.load(`400 40px 'Hanken Grotesk'`)]); } catch (e) { /* draw anyway */ }
    const s = inv();
    const layout = drawCard(canvas, Object.assign({}, s, { details: s.details || detailsLine() }));
    canvas.dataset.fit = String(layout.fit);
    canvas.dataset.lines = String(layout.lines);
    canvas.setAttribute('aria-label', `Invitation card: ${s.headline}. ${s.message} ${s.details || detailsLine()}`);
    const warn = $('#fit-warn');
    if (warn) warn.hidden = layout.fit;
  });
}

// ------------------------------------------------------------------ actions

function slug(t) { return (t || 'invite').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'invite'; }

function cardBlob() {
  return new Promise((resolve) => $('#card').toBlob(resolve, 'image/png'));
}

async function download() {
  const blob = await cardBlob();
  if (!blob) { say("The card couldn't be saved here."); return; }
  const url = URL.createObjectURL(blob);
  const a = h('a', { href: url, download: slug(inv().headline) + '-invite.png' });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
  say('Card saved as a PNG.');
}

function printCard() {
  const img = h('img', { src: $('#card').toDataURL('image/png'), alt: 'Invitation card' });
  const box = h('div', { id: 'print-card' }, img);
  document.body.append(box);
  document.body.classList.add('printing-card');
  const done = () => { document.body.classList.remove('printing-card'); box.remove(); window.removeEventListener('afterprint', done); };
  window.addEventListener('afterprint', done);
  img.decode().catch(() => {}).then(() => { window.print(); setTimeout(done, 1000); });
}

function fullText() {
  const s = inv();
  return [s.message, s.details || detailsLine()].filter(Boolean).join('\n');
}

async function copyText() {
  try { await navigator.clipboard.writeText(fullText()); say('Invitation text copied.'); } catch (e) { say('Copy is blocked here. Select the text in the box instead.'); }
}

async function share() {
  const s = inv();
  try {
    const blob = await cardBlob();
    const file = blob ? new File([blob], slug(s.headline) + '-invite.png', { type: 'image/png' }) : null;
    if (file && navigator.canShare && navigator.canShare({ files: [file] })) await navigator.share({ files: [file], text: fullText(), title: s.headline });
    else await navigator.share({ text: fullText(), title: s.headline });
  } catch (e) {
    if (e && e.name !== 'AbortError') say("Sharing didn't work here. Download the card instead.");
  }
}

function setPhoto(file) {
  if (!file || !/^image\//.test(file.type)) { say('Please choose an image file.'); return; }
  if (file.size > 15 * 1024 * 1024) { say('That photo is over 15 MB. Please choose a smaller one.'); return; }
  if (photoUrl) URL.revokeObjectURL(photoUrl);
  photoUrl = URL.createObjectURL(file);
  const img = new Image();
  img.onload = () => { photo = img; redraw(); say('Photo added. It stays in this browser.'); update({}, { focus: 'photo-x' }); };
  img.onerror = () => say("That image couldn't be read.");
  img.src = photoUrl;
}

function removePhoto() {
  photo = null;
  if (photoUrl) URL.revokeObjectURL(photoUrl);
  photoUrl = null;
  update({}, { focus: 'inv-photo' });
  redraw();
}

// ------------------------------------------------------------------ view

const set = (patch, focus = null) => { update({ inv: Object.assign({}, inv(), patch) }, { focus }); redraw(); };

export function inviteView() {
  const s = inv(), p = plan().profile;
  const opts = s.options[s.lang];
  if (!opts && !fetching) setTimeout(() => fetchTexts(s.lang), 0);
  const ai = s.source[s.lang] === 'ai';
  const recommended = Object.keys(TEMPLATES).filter((k) => TEMPLATES[k].for[0] === p.type || TEMPLATES[k].for.includes(p.type));
  setTimeout(redraw, 0);
  return h('main', { class: 'wrap invite', id: 'main' },
    h('p', { class: 'step' }, 'Invite'),
    h('h1', { class: 'disp h1', tabindex: '-1' }, 'Make the invitation'),
    guide(opts ? 'Pick one of the three versions, change anything you like, then tap Download PNG.'
      : 'I am writing three versions of the words. Pick one when they arrive, then tap Download PNG.'),
    h('p', { class: 'lead' }, 'Written from your plan, drawn on this device. Edit anything; your photo never leaves the browser.'),
    h('div', { class: 'invgrid' },
      h('div', { class: 'invprev' },
        h('canvas', { id: 'card', class: 'card', width: W, height: H, role: 'img', 'aria-label': 'Invitation card preview' }),
        h('p', { id: 'fit-warn', class: 'err', role: 'status', hidden: true }, 'The text is too long for the card, so it is cut short. Shorten the message a little.'),
        h('div', { class: 'acts' },
          primary({ id: 'dl-btn', onClick: download }, 'Download PNG'),
          h('button', { class: 'btn', type: 'button', onClick: printCard }, 'Print or save as PDF'),
          h('button', { class: 'btn', type: 'button', onClick: copyText }, 'Copy text'),
          navigator.share ? h('button', { class: 'btn', type: 'button', onClick: share }, 'Share') : null)),
      h('div', { class: 'invctl' },
        h('section', { class: 'dgroup', 'aria-labelledby': 'txt-h' },
          h('h2', { class: 'dgh', id: 'txt-h' }, 'The words ', h('span', { class: 'aitag' + (ai ? ' ai' : '') }, ai ? 'Written by AI' : 'From templates')),
          h('div', { class: 'chips', role: 'group', 'aria-label': 'Language' }, LANGS.map(([code, label]) => h('button', {
            class: 'chip', type: 'button', lang: code, 'aria-pressed': String(s.lang === code),
            onClick: () => { if (s.options[code]) { const o = s.options[code].find((x) => x.tone === s.tone) || s.options[code][0]; set({ lang: code, message: o.text }); } else { set({ lang: code }); fetchTexts(code); } } }, label))),
          opts ? h('ul', { class: 'tones', 'aria-label': 'Three versions' }, opts.map((o) => h('li', null,
            h('button', { class: 'tone', type: 'button', lang: s.lang, 'aria-pressed': String(s.message === o.text),
              onClick: () => set({ tone: o.tone, message: o.text }) },
            h('span', { class: 'step' }, TONE_LABEL[o.tone]), h('span', null, o.text))))) : h('p', { class: 'lead', role: 'status' }, 'Writing three versions…'),
          h('div', { class: 'invrow' },
            h('label', { for: 'inv-host', class: 'flab' }, 'Hosted by (optional)'),
            h('input', { id: 'inv-host', class: 'cin', type: 'text', maxlength: 60, value: s.host, autocomplete: 'off', onInput: (e) => { s.host = e.target.value; } }),
            h('label', { for: 'inv-rsvp', class: 'flab' }, 'RSVP line (optional)'),
            h('input', { id: 'inv-rsvp', class: 'cin', type: 'text', maxlength: 120, value: s.rsvp, autocomplete: 'off', placeholder: 'e.g. RSVP to Priya by 10 Nov',
              onInput: (e) => { s.rsvp = e.target.value; } }),
            h('button', { class: 'btn', type: 'button', disabled: state.busy, onClick: () => fetchTexts(s.lang) }, 'Write them again'))),
        h('section', { class: 'dgroup', 'aria-labelledby': 'edit-h' },
          h('h2', { class: 'dgh', id: 'edit-h' }, 'Edit the card'),
          h('label', { for: 'inv-head', class: 'flab' }, 'Headline'),
          h('input', { id: 'inv-head', class: 'cin', type: 'text', maxlength: 90, value: s.headline, onInput: (e) => { s.headline = e.target.value; redraw(); } }),
          h('label', { for: 'inv-msg', class: 'flab' }, 'Message'),
          h('textarea', { id: 'inv-msg', class: 'cin area', rows: 5, maxlength: 600, lang: s.lang, onInput: (e) => { s.message = e.target.value; redraw(); } }, s.message),
          h('label', { for: 'inv-det', class: 'flab' }, 'When and where'),
          h('input', { id: 'inv-det', class: 'cin', type: 'text', maxlength: 160, value: s.details || detailsLine(), onInput: (e) => { s.details = e.target.value; redraw(); } })),
        h('section', { class: 'dgroup', 'aria-labelledby': 'look-h' },
          h('h2', { class: 'dgh', id: 'look-h' }, 'Look'),
          h('div', { class: 'chips', role: 'group', 'aria-label': 'Template' }, Object.entries(TEMPLATES).map(([k, tpl]) => h('button', {
            class: 'chip', type: 'button', 'aria-pressed': String(s.template === k), onClick: () => set({ template: k }) },
          tpl.name, recommended.includes(k) && TEMPLATES[k].for.includes(p.type) ? h('span', { class: 'sr' }, ' (suits this occasion)') : null))),
          h('div', { class: 'swatches', role: 'group', 'aria-label': 'Colours' }, Object.entries(PALETTES).map(([k, pal]) => h('button', {
            class: 'swatch', type: 'button', 'aria-pressed': String(s.palette === k), 'aria-label': pal.name, title: pal.name,
            style: { background: `linear-gradient(135deg, ${pal.bg} 0 55%, ${pal.accent} 55% 100%)` }, onClick: () => set({ palette: k }) }))),
          h('div', { class: 'photo' },
            h('input', { id: 'inv-photo', class: 'sr', type: 'file', accept: 'image/*', onChange: (e) => setPhoto(e.target.files && e.target.files[0]) }),
            h('label', { class: 'btn', for: 'inv-photo' }, photo ? 'Change the photo' : 'Add your photo'),
            photo ? h('button', { class: 'linkb', id: 'photo-x', type: 'button', onClick: removePhoto }, 'Remove the photo') : null,
            h('p', { class: 'tt2' }, 'Your photo is drawn on this device and never uploaded.')),
          h('p', { class: 'tt2' }, 'AI-generated backgrounds need a paid image model, so Waypoint uses hand-drawn templates instead.')))));
}
