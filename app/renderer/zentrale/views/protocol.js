// Ansicht "Protokoll": alle Ereignisse mit Stufen-Farben, Filter, Suche und Auto-Aktualisierung.

import { h, icon, button, card, viewHeader, table, emptyState, replaceChildren, toggle } from './dom.js';
import { levelInfo, fmtTime, fmtDateTime, signature } from './format.js';

const FILTERS = [
  { id: 'all', label: 'Alle' },
  { id: 'trade', label: 'Trades' },
  { id: 'warning', label: 'Warnungen' },
  { id: 'error', label: 'Fehler' },
  { id: 'info', label: 'Info' },
];
const REFRESH_MS = 5000;

export function createProtocol(ctx) {
  let events = [];
  let filter = 'all';
  let timer = null;
  let visible = false;
  let lastSig = '';

  const chips = h('div', { class: 'chips', role: 'tablist', 'aria-label': 'Stufe filtern' });
  const search = h('input', {
    class: 'input input-search', type: 'search', placeholder: 'Meldungen durchsuchen …', 'aria-label': 'Protokoll durchsuchen',
    dataset: { search: '1' },
  });
  const auto = toggle({ label: 'Automatisch aktualisieren', checked: true, onChange: () => schedule() });
  const refreshBtn = button('Aktualisieren', { variant: 'ghost', size: 'sm', icon: 'refresh', onClick: () => load(refreshBtn) });
  const body = h('div', { class: 'log-body' });
  const stamp = h('span', { class: 'muted small' });

  search.addEventListener('input', render);

  const el = h('div', { class: 'view view-protocol' },
    viewHeader('Protokoll', 'Jede Aktion von Vincent wird hier festgehalten – nachvollziehbar und ehrlich.'),
    h('div', { class: 'filterbar' }, chips, h('div', { class: 'search-wrap' }, icon('search', 16), search), auto.el, refreshBtn),
    card({ class: 'log-card', actions: stamp, title: 'Ereignisse', icon: 'list' }, body));

  function renderChips() {
    const counts = { all: events.length, trade: 0, warning: 0, error: 0, info: 0 };
    for (const e of events) counts[levelInfo(e.level).group] += 1;
    replaceChildren(chips, FILTERS.map((f) => h('button', {
      type: 'button', class: 'chip-btn', role: 'tab', 'aria-selected': String(f.id === filter),
      onClick: () => { filter = f.id; renderChips(); render(); },
    }, h('span', { text: f.label }), h('span', { class: 'chip-count', text: String(counts[f.id] || 0) }))));
  }

  function render() {
    const q = search.value.trim().toLowerCase();
    const rows = events.filter((e) => (filter === 'all' || levelInfo(e.level).group === filter)
      && (!q || `${e.message} ${e.kind} ${e.level}`.toLowerCase().includes(q)));
    if (!rows.length) {
      replaceChildren(body, emptyState('list', events.length ? 'Nichts gefunden' : 'Noch keine Ereignisse',
        events.length ? 'Anderen Filter oder Suchbegriff probieren.' : 'Sobald Vincent arbeitet, erscheinen hier seine Schritte.'));
      return;
    }
    replaceChildren(body, table([
      { label: 'Zeit', class: 'col-time', render: (e) => h('span', { class: 'num mono txt-dim', title: fmtDateTime(e.ts), text: fmtTime(e.ts) }) },
      { label: 'Stufe', class: 'col-level', render: (e) => { const l = levelInfo(e.level); return h('span', { class: `chip tone-${l.tone}`, text: l.label }); } },
      { label: 'Art', class: 'col-kind', render: (e) => h('span', { class: 'kind', text: e.kind || '–' }) },
      { label: 'Meldung', class: 'col-msg', render: (e) => h('span', { class: 'log-msg', text: e.message || '' }) },
    ], rows, { rowClass: (e) => `lvl-${levelInfo(e.level).group}`, caption: 'Protokoll' }));
  }

  async function load(btn = null) {
    const res = await ctx.ui.run(btn, () => ctx.api.get('/api/events?limit=300'));
    if (!Array.isArray(res)) return;
    stamp.textContent = `Stand ${fmtTime(Date.now())}`;
    const s = signature(res);
    if (s === lastSig) return;
    lastSig = s;
    events = res;
    renderChips();
    render();
  }

  function schedule() {
    clearInterval(timer);
    timer = null;
    if (visible && auto.input.checked) timer = setInterval(() => load(), REFRESH_MS);
  }

  renderChips();
  return {
    el,
    show() {
      visible = true;
      load();
      schedule();
    },
    hide() {
      visible = false;
      schedule();
    },
    update() {},
    event(type) {
      // Neue Meldungen sofort zeigen, wenn die Ansicht offen ist.
      if (visible && auto.input.checked && (type === 'notify' || type === 'proposal')) load();
    },
  };
}
