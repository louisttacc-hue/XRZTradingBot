// DOM-Bausteine der Zentrale. Texte werden ausschließlich als textContent gesetzt –
// Backend- oder KI-Text landet nie als HTML im Dokument.

const SVG_NS = 'http://www.w3.org/2000/svg';

/** Element bauen: h('div', {class, text, onClick, dataset, style, ...attrs}, ...kinder) */
export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = String(v);
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k === 'style' && typeof v === 'object') {
        for (const [sk, sv] of Object.entries(v)) {
          if (sk.startsWith('--')) el.style.setProperty(sk, String(sv));
          else el.style[sk] = sv;
        }
      } else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === 'value') el.value = v;
      else if (k === 'checked') el.checked = Boolean(v);
      else if (k === 'disabled') el.disabled = Boolean(v);
      else if (k === 'selected') el.selected = Boolean(v);
      else el.setAttribute(k, v === true ? '' : String(v));
    }
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function replaceChildren(el, ...children) {
  clear(el);
  return append(el, children);
}

// Strich-Icons (24×24, eigene Pfade).
const ICONS = {
  grid: ['M4 4h6v6H4z', 'M14 4h6v6h-6z', 'M14 14h6v6h-6z', 'M4 14h6v6H4z'],
  candles: ['M7 3v3', 'M7 17v4', 'M5 6h4v11H5z', 'M17 3v6', 'M17 18v3', 'M15 9h4v9h-4z'],
  layers: ['M12 3 3 8l9 5 9-5-9-5z', 'M3 13l9 5 9-5', 'M3 17.5 12 22.5l9-5'],
  brain: ['M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 3 3 3 3 0 0 0 3-2V6a3 3 0 0 0-3-2z',
    'M15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-3 3 3 3 0 0 1-3-2'],
  mic: ['M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z', 'M19 11a7 7 0 0 1-14 0', 'M12 18v3', 'M8 21h8'],
  monitor: ['M3 4h18v12H3z', 'M8 21h8', 'M12 16v5', 'M6.5 12.5l3-3.5 2.5 2 5-5'],
  sliders: ['M4 21v-7', 'M4 10V3', 'M12 21v-9', 'M12 8V3', 'M20 21v-5', 'M20 12V3', 'M1 14h6', 'M9 8h6', 'M17 16h6'],
  list: ['M9 6h12', 'M9 12h12', 'M9 18h12', 'M4 6h.01', 'M4 12h.01', 'M4 18h.01'],
  check: ['M5 12.5l4.5 4.5L19 7.5'],
  x: ['M6 6l12 12', 'M18 6 6 18'],
  pin: ['M9 4h6l-1 6 3 3v2H7v-2l3-3-1-6z', 'M12 15v6'],
  trash: ['M4 7h16', 'M10 11v6', 'M14 11v6', 'M6 7l1 13h10l1-13', 'M9 7V4h6v3'],
  edit: ['M4 20h4L19 9l-4-4L4 16v4z', 'M13.5 6.5l4 4'],
  search: ['M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z', 'M20 20l-4-4'],
  refresh: ['M20 11a8 8 0 0 0-14.9-3.5', 'M4 4v4h4', 'M4 13a8 8 0 0 0 14.9 3.5', 'M20 20v-4h-4'],
  play: ['M8 5v14l11-7z'],
  send: ['M4 12 20 4l-6 16-3-7-7-1z', 'M11 13l9-9'],
  alert: ['M12 3 2 20h20L12 3z', 'M12 10v4', 'M12 17h.01'],
  info: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z', 'M12 11v6', 'M12 7.5h.01'],
  bolt: ['M13 2 4 14h7l-1 8 9-12h-7l1-8z'],
  cpu: ['M7 7h10v10H7z', 'M10 3v4', 'M14 3v4', 'M10 17v4', 'M14 17v4', 'M3 10h4', 'M3 14h4', 'M17 10h4', 'M17 14h4'],
  wave: ['M3 12h2', 'M7 8v8', 'M11 5v14', 'M15 9v6', 'M19 11v2'],
  shield: ['M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6l-8-3z'],
  plus: ['M12 5v14', 'M5 12h14'],
  power: ['M12 3v9', 'M6.3 6.3a8 8 0 1 0 11.4 0'],
  link: ['M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1', 'M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1'],
  sparkle: ['M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3z', 'M19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7.7-2z'],
  palette: ['M12 3a9 9 0 1 0 0 18c1.1 0 1.5-.8 1.5-1.5 0-1.2-1-1.5-1-2.5s.8-1.5 2-1.5H17a4 4 0 0 0 4-4c0-4.7-4-8.5-9-8.5z',
    'M7.5 11h.01', 'M10 7h.01', 'M14.5 7h.01', 'M17 10.5h.01'],
  arrowUp: ['M12 19V5', 'M6 11l6-6 6 6'],
  arrowDown: ['M12 5v14', 'M6 13l6 6 6-6'],
  minus: ['M5 12h14'],
  key: ['M15 4a5 5 0 1 0 0 10 5 5 0 0 0 0-10z', 'M11.5 12.5 4 20', 'M6 18l2 2', 'M8 16l2 2'],
  eye: ['M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z', 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z'],
  chat: ['M4 5h16v11H9l-5 4V5z'],
  clock: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z', 'M12 7v5l3 2'],
  flask: ['M9 3h6', 'M10 3v6L4.5 18.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.5-2.5L14 9V3', 'M7 15h10'],
  scan: ['M4 8V4h4', 'M16 4h4v4', 'M20 16v4h-4', 'M8 20H4v-4', 'M7 12h10'],
  external: ['M14 4h6v6', 'M20 4l-9 9', 'M18 14v6H4V6h6'],
  lock: ['M6 11h12v10H6z', 'M8 11V7a4 4 0 0 1 8 0v4'],
  user: ['M12 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8z', 'M4 21a8 8 0 0 1 16 0'],
};

export function icon(name, size = 18, cls = '') {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.8');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  svg.setAttribute('class', `icon ${cls}`.trim());
  for (const d of ICONS[name] || ICONS.info) {
    const p = document.createElementNS(SVG_NS, 'path');
    p.setAttribute('d', d);
    svg.append(p);
  }
  return svg;
}

// --------------------------------------------------------------------------
// Wiederkehrende Bausteine
// --------------------------------------------------------------------------

export function viewHeader(title, subtitle, ...actions) {
  return h('header', { class: 'view-head' },
    h('div', { class: 'view-head-text' },
      h('h1', { class: 'view-title', text: title }),
      subtitle ? h('p', { class: 'view-sub', text: subtitle }) : null),
    actions.length ? h('div', { class: 'view-actions' }, actions) : null);
}

export function card(props, ...children) {
  const { title, subtitle, actions, icon: ic, class: cls, ...rest } = props || {};
  const head = title
    ? h('div', { class: 'card-head' },
      h('div', { class: 'card-head-text' },
        h('h2', { class: 'card-title' }, ic ? icon(ic, 16) : null, h('span', { text: title })),
        subtitle ? h('p', { class: 'card-sub', text: subtitle }) : null),
      actions ? h('div', { class: 'card-actions' }, actions) : null)
    : null;
  return h('section', { class: `card ${cls || ''}`.trim(), ...rest }, head, children);
}

export function button(label, opts = {}) {
  const { variant = 'secondary', icon: ic, size, onClick, title, type = 'button', ...rest } = opts;
  return h('button', {
    type,
    class: `btn btn-${variant}${size ? ` btn-${size}` : ''}`,
    title,
    onClick,
    ...rest,
  }, ic ? icon(ic, size === 'sm' ? 14 : 16) : null, label ? h('span', { class: 'btn-label', text: label }) : null);
}

export function iconButton(name, label, opts = {}) {
  const { onClick, variant = 'ghost', ...rest } = opts;
  return h('button', {
    type: 'button',
    class: `icon-btn icon-btn-${variant}`,
    'aria-label': label,
    title: label,
    onClick,
    ...rest,
  }, icon(name, 16));
}

export function pill(text, tone = 'neutral', ic = null) {
  return h('span', { class: `pill tone-${tone}` }, ic ? icon(ic, 12) : h('span', { class: 'pill-dot' }), h('span', { text }));
}

export function tag(text, tone = 'neutral') {
  return h('span', { class: `tag tone-${tone}`, text });
}

export function emptyState(ic, title, text) {
  return h('div', { class: 'empty' },
    h('div', { class: 'empty-icon' }, icon(ic, 22)),
    h('div', { class: 'empty-title', text: title }),
    text ? h('div', { class: 'empty-text', text }) : null);
}

/** Schalter (role=switch) mit Beschriftung. */
export function toggle({ label, hint, checked = false, onChange, disabled = false, id }) {
  const input = h('input', { type: 'checkbox', class: 'switch-input', role: 'switch', checked, disabled, id });
  input.addEventListener('change', () => onChange?.(input.checked, input));
  const el = h('label', { class: `switch-row${disabled ? ' is-disabled' : ''}` },
    h('span', { class: 'switch-text' },
      h('span', { class: 'switch-label', text: label }),
      hint ? h('span', { class: 'switch-hint', text: hint }) : null),
    h('span', { class: 'switch' }, input, h('span', { class: 'switch-track' }, h('span', { class: 'switch-thumb' }))));
  return { el, input };
}

/** Segment-Steuerung (Radio-Gruppe) mit Pfeiltasten-Fokus. */
export function segmented(options, { value, onSelect, label, size } = {}) {
  const group = h('div', { class: `seg${size ? ` seg-${size}` : ''}`, role: 'radiogroup', 'aria-label': label });
  const buttons = options.map((o) => {
    const b = h('button', {
      type: 'button',
      class: 'seg-btn',
      role: 'radio',
      'aria-checked': String(o.value === value),
      dataset: { value: o.value, tone: o.tone || 'accent' },
      title: o.title,
      onClick: () => onSelect?.(o.value, b),
    }, o.icon ? icon(o.icon, 14) : null, h('span', { text: o.label }));
    return b;
  });
  group.append(...buttons);
  group.addEventListener('keydown', (ev) => {
    const idx = buttons.indexOf(document.activeElement);
    if (idx < 0) return;
    let next = -1;
    if (ev.key === 'ArrowRight' || ev.key === 'ArrowDown') next = (idx + 1) % buttons.length;
    if (ev.key === 'ArrowLeft' || ev.key === 'ArrowUp') next = (idx - 1 + buttons.length) % buttons.length;
    if (next >= 0) {
      ev.preventDefault();
      buttons[next].focus();
    }
  });
  const set = (v) => {
    for (const b of buttons) {
      const on = b.dataset.value === v;
      b.setAttribute('aria-checked', String(on));
      b.tabIndex = on || (!buttons.some((x) => x.dataset.value === v) && b === buttons[0]) ? 0 : -1;
    }
  };
  set(value);
  return { el: group, set, buttons };
}

/** Tabelle aus Spalten-Definitionen: [{label, class, render(row) → Node|string}] */
export function table(columns, rows, { onRowClick, rowClass, caption } = {}) {
  const thead = h('thead', null, h('tr', null, columns.map((c) => h('th', { class: c.class || '', scope: 'col', text: c.label }))));
  const tbody = h('tbody', null, rows.map((row) => {
    const tr = h('tr', { class: rowClass ? rowClass(row) : '' },
      columns.map((c) => {
        const v = c.render(row);
        return h('td', { class: c.class || '' }, v instanceof Node ? v : String(v ?? '–'));
      }));
    if (onRowClick) {
      tr.classList.add('is-clickable');
      tr.tabIndex = 0;
      tr.addEventListener('click', () => onRowClick(row));
      tr.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          onRowClick(row);
        }
      });
    }
    return tr;
  }));
  return h('div', { class: 'table-wrap' },
    h('table', { class: 'table' }, caption ? h('caption', { class: 'sr-only', text: caption }) : null, thead, tbody));
}

/** Formularfeld mit Beschriftung. */
export function field(label, control, hint) {
  const id = control.id || `f-${Math.random().toString(36).slice(2, 9)}`;
  control.id = id;
  return h('div', { class: 'field' },
    h('label', { class: 'field-label', for: id, text: label }),
    control,
    hint ? h('div', { class: 'field-hint', text: hint }) : null);
}

export function select(options, value, props = {}) {
  return h('select', { class: 'input', ...props },
    options.map((o) => h('option', { value: o.value, selected: o.value === value, text: o.label })));
}

export function spinner(size = 16) {
  return h('span', { class: 'spinner', style: { width: `${size}px`, height: `${size}px` }, 'aria-hidden': 'true' });
}
