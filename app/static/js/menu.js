/* "More ▾": a menu button for the less common actions, so each stop shows only its two main ones.
   Arrow keys move through the items, Home/End jump, Escape (or Tab) closes and returns focus to the
   button. Which menu is open lives in state.menu, so a re-render (photos arriving) keeps it open. */
import { h } from './dom.js';
import { state } from './store.js';
import { rerender } from './ui.js';

function setOpen(id, open, focus) {
  state.menu = open ? id : null;
  rerender({ focus });
}

// A press anywhere outside the open menu closes it, in place: a re-render here would swallow the
// click the user was making on something else.
document.addEventListener('pointerdown', (e) => {
  if (!state.menu || e.target.closest('.mmenu.open')) return;
  state.menu = null;
  for (const wrap of document.querySelectorAll('.mmenu.open')) {
    wrap.classList.remove('open');
    wrap.querySelector('.menu').remove();
    const btn = wrap.querySelector('.morebtn');
    btn.setAttribute('aria-expanded', 'false');
    btn.removeAttribute('aria-controls');
  }
}, true);

/** items: [{ id?, label, onSelect, danger? }]; falsy entries are left out. */
export function moreMenu(id, items, { label = 'More', cls = 'btn', describe = '' } = {}) {
  const list = items.filter(Boolean);
  const open = state.menu === id;
  const ids = list.map((it, i) => it.id || `${id}-${i}`);
  const keys = (e) => {
    const all = [...e.currentTarget.querySelectorAll('[role="menuitem"]')];
    const i = all.indexOf(document.activeElement);
    const to = { ArrowDown: (i + 1) % all.length, ArrowUp: (i - 1 + all.length) % all.length, Home: 0, End: all.length - 1 }[e.key];
    if (to !== undefined) { e.preventDefault(); all[to].focus(); }
    else if (e.key === 'Escape' || e.key === 'Tab') { e.preventDefault(); setOpen(id, false, id); }
  };
  const menu = open ? h('ul', { class: 'menu', id: id + '-m', role: 'menu', 'aria-labelledby': id, onKeydown: keys },
    list.map((it, i) => h('li', { role: 'none' },
      h('button', { class: 'menui' + (it.danger ? ' danger' : ''), id: ids[i], type: 'button', role: 'menuitem', tabindex: '-1',
        onClick: () => { state.menu = null; it.onSelect(); } }, it.label)))) : null;
  if (menu) {  // keep the menu on screen on narrow phones
    requestAnimationFrame(() => {
      if (menu.getBoundingClientRect().right > document.documentElement.clientWidth - 8) menu.classList.add('right');
    });
  }
  return h('div', { class: 'mmenu' + (open ? ' open' : '') },
    h('button', { class: cls + ' morebtn', id, type: 'button', 'aria-haspopup': 'menu', 'aria-expanded': String(open),
      'aria-controls': open ? id + '-m' : null,
      onClick: () => { const was = state.menu === id; setOpen(id, !was, was ? id : ids[0]); },  // read now: an outside press may have closed it
      onKeydown: (e) => {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); setOpen(id, true, e.key === 'ArrowUp' ? ids[ids.length - 1] : ids[0]); }
      } },
    label, describe ? h('span', { class: 'sr' }, ' ' + describe) : null, h('span', { 'aria-hidden': 'true' }, ' ▾')),
    menu);
}
