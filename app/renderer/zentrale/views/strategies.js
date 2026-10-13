// Ansicht "Strategien": filterbare Karten aller Strategien mit Kategorie und passenden Marktphasen.

import { h, icon, button, viewHeader, emptyState, replaceChildren, tag, spinner } from './dom.js';
import { categoryTag } from './components.js';
import { STRATEGY_CATEGORIES, phaseLabel, fmtInt } from './format.js';

export function createStrategies(ctx) {
  const countBadge = h('span', { class: 'count-pill', text: '…' });
  const search = h('input', {
    class: 'input input-search', type: 'search', placeholder: 'Strategie suchen …', 'aria-label': 'Strategien durchsuchen',
    dataset: { search: '1' },
  });
  const chips = h('div', { class: 'chips', role: 'tablist', 'aria-label': 'Kategorie' });
  const grid = h('div', { class: 'strat-grid' });
  const reloadBtn = button('Neu laden', { variant: 'ghost', icon: 'refresh', size: 'sm', onClick: () => load(true) });

  const head = viewHeader('Strategien', 'Vincents Werkzeugkasten: Jede Strategie wird laufend per Backtest bewertet – nur was nachweislich funktioniert, bekommt Gewicht.');
  head.querySelector('.view-title').append(countBadge);

  const el = h('div', { class: 'view view-strategies' },
    head,
    h('div', { class: 'filterbar' }, chips, h('div', { class: 'search-wrap' }, icon('search', 16), search), reloadBtn),
    grid);

  let all = [];
  let category = 'Alle';
  let loaded = false;

  search.addEventListener('input', render);

  function renderChips() {
    const counts = new Map();
    for (const s of all) counts.set(s.category || 'Sonstige', (counts.get(s.category || 'Sonstige') || 0) + 1);
    const cats = ['Alle', ...STRATEGY_CATEGORIES.filter((c) => counts.has(c)), ...[...counts.keys()].filter((c) => !STRATEGY_CATEGORIES.includes(c))];
    replaceChildren(chips, cats.map((c) => {
      const n = c === 'Alle' ? all.length : counts.get(c) || 0;
      const b = h('button', {
        type: 'button', class: 'chip-btn', role: 'tab', 'aria-selected': String(c === category),
        onClick: () => { category = c; renderChips(); render(); },
      }, h('span', { text: c }), h('span', { class: 'chip-count', text: String(n) }));
      return b;
    }));
  }

  function render() {
    const q = search.value.trim().toLowerCase();
    const list = all.filter((s) => (category === 'Alle' || (s.category || 'Sonstige') === category)
      && (!q || `${s.label} ${s.name} ${s.description} ${s.category}`.toLowerCase().includes(q)));
    if (!list.length) {
      replaceChildren(grid, emptyState('search', 'Keine Strategie gefunden', 'Andere Kategorie oder Suchbegriff probieren.'));
      return;
    }
    replaceChildren(grid, list.map(strategyCard));
  }

  function strategyCard(s) {
    const params = s.params && typeof s.params === 'object' ? Object.entries(s.params) : [];
    const phases = Array.isArray(s.good_in) ? s.good_in : [];
    return h('article', { class: 'strat' },
      h('div', { class: 'strat-head' },
        h('h3', { class: 'strat-name', text: s.label || s.name }),
        categoryTag(s.category)),
      h('p', { class: 'strat-desc', text: s.description || '' }),
      phases.length
        ? h('div', { class: 'strat-phases' },
          h('span', { class: 'strat-k', text: 'Passt zu' }),
          phases.map((p) => tag(phaseLabel(p), 'phase')))
        : null,
      params.length
        ? h('div', { class: 'strat-params', title: 'Standard-Parameter' },
          params.map(([k, v]) => h('span', { class: 'param' }, h('span', { class: 'param-k', text: k }), h('span', { class: 'param-v', text: String(v) }))))
        : null);
  }

  async function load(force = false) {
    if (loaded && !force) return;
    if (!all.length) replaceChildren(grid, h('div', { class: 'loading-row' }, spinner(18), h('span', { text: 'Lade Strategien …' })));
    const res = await ctx.ui.run(force ? reloadBtn : null, () => ctx.api.get('/api/strategies'));
    if (!Array.isArray(res)) {
      if (!all.length) replaceChildren(grid, emptyState('alert', 'Strategien nicht verfügbar', 'Das Backend hat keine Liste geliefert.'));
      return;
    }
    loaded = true;
    all = res;
    countBadge.textContent = `${fmtInt(all.length)} Strategien`;
    if (!['Alle', ...all.map((s) => s.category || 'Sonstige')].includes(category)) category = 'Alle';
    renderChips();
    render();
  }

  return {
    el,
    show() {
      load();
    },
    hide() {},
    update() {},
  };
}
