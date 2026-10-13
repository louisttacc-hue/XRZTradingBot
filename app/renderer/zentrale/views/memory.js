// Ansicht "Gedächtnis": Kernprofil, Statistik, durchsuchbare Erinnerungen mit Bearbeiten/Anheften/Löschen.

import { h, icon, button, iconButton, card, viewHeader, emptyState, replaceChildren, tag, select, spinner } from './dom.js';
import { MEMORY_CATEGORIES, memoryCategoryLabel, timeAgo, fmtInt, fmtNumber, toNum, fmtDateTime } from './format.js';

const PAGE = 30;
const CAT_TONES = {
  profil: 'accent', vorliebe: 'pink', regel: 'bad', fakt: 'info', lektion: 'warn', strategie: 'violet',
  wissen: 'good', notiz: 'neutral', ereignis: 'neutral',
};

/** by_category als {kat: anzahl} oder Liste → [[kat, anzahl]] absteigend. */
export function categoryCounts(by) {
  let pairs = [];
  if (Array.isArray(by)) {
    pairs = by.map((x) => (Array.isArray(x) ? [x[0], x[1]] : [x?.category, x?.count]));
  } else if (by && typeof by === 'object') {
    pairs = Object.entries(by);
  }
  return pairs
    .map(([k, v]) => [String(k ?? ''), toNum(v) || 0])
    .filter(([k]) => k)
    .sort((a, b) => b[1] - a[1]);
}

export function createMemory(ctx) {
  const catOptions = [{ value: '', label: 'Alle Kategorien' },
    ...Object.entries(MEMORY_CATEGORIES).map(([value, label]) => ({ value, label }))];

  // Liste
  const search = h('input', {
    class: 'input input-search', type: 'search', placeholder: 'Erinnerungen durchsuchen …', 'aria-label': 'Erinnerungen durchsuchen',
    dataset: { search: '1' },
  });
  const catFilter = select(catOptions, '', { 'aria-label': 'Kategorie filtern' });
  const addToggle = button('Neue Erinnerung', { variant: 'primary', icon: 'plus', size: 'sm', onClick: () => toggleAdd() });
  const listEl = h('ul', { class: 'mem-list' });
  const listInfo = h('span', { class: 'muted', text: '' });
  const moreBtn = button('Mehr laden', { variant: 'ghost', size: 'sm', onClick: () => loadList(true) });
  moreBtn.hidden = true;

  // Neu anlegen
  const addText = h('textarea', { class: 'input', rows: '3', placeholder: 'Was soll sich Vincent merken? z. B. „Ich handle nie am Freitagabend.“', 'aria-label': 'Neue Erinnerung' });
  const addCat = select(Object.entries(MEMORY_CATEGORIES).map(([value, label]) => ({ value, label })), 'notiz', { 'aria-label': 'Kategorie' });
  const addSave = button('Merken', { variant: 'primary', icon: 'check', size: 'sm', onClick: () => addMemory() });
  const addForm = h('div', { class: 'mem-add', hidden: true },
    addText,
    h('div', { class: 'mem-add-row' }, h('label', { class: 'tb-label', text: 'Kategorie' }), addCat, h('span', { class: 'spacer' }),
      button('Abbrechen', { variant: 'ghost', size: 'sm', onClick: () => toggleAdd(false) }), addSave));

  // Statistik
  const statTotal = h('div', { class: 'big-num num', text: '–' });
  const statBars = h('div', { class: 'cat-bars' });
  const statMeta = h('div', { class: 'stat-meta' });
  const consolidateBtn = button('Jetzt verdichten', { variant: 'secondary', icon: 'sparkle', size: 'sm', onClick: () => consolidate() });

  // Profil
  const profileText = h('textarea', { class: 'input profile-input', rows: '9', 'aria-label': 'Kernprofil', placeholder: 'Wer bist du, wie handelst du, was ist dir wichtig? Vincent hat das immer im Kopf.' });
  const profileCount = h('span', { class: 'muted num' });
  const profileSave = button('Profil speichern', { variant: 'primary', size: 'sm', icon: 'check', onClick: () => saveProfile() });
  let profileOrig = '';
  profileText.addEventListener('input', () => {
    profileCount.textContent = `${fmtInt(profileText.value.length)} Zeichen`;
    profileSave.classList.toggle('is-dirty', profileText.value !== profileOrig);
  });

  const explain = h('div', { class: 'callout' },
    h('span', { class: 'callout-icon' }, icon('brain', 18)),
    h('p', { text: 'So funktioniert Vincents Gedächtnis: Er lernt aus jedem Gespräch, verdichtet seine Erinnerungen jede Nacht, vergisst Unwichtiges – angeheftete Einträge bleiben für immer.' }));

  const el = h('div', { class: 'view view-memory' },
    viewHeader('Gedächtnis', 'Was Vincent über dich, deine Regeln und den Markt weiß.'),
    explain,
    h('div', { class: 'mem-grid' },
      card({ class: 'mem-main', title: 'Erinnerungen', icon: 'list', actions: addToggle },
        addForm,
        h('div', { class: 'filterbar compact' }, h('div', { class: 'search-wrap' }, icon('search', 16), search), catFilter),
        listEl,
        h('div', { class: 'list-foot' }, listInfo, moreBtn)),
      h('div', { class: 'mem-side' },
        card({ title: 'Statistik', icon: 'grid', actions: consolidateBtn }, h('div', { class: 'stat-total' }, statTotal, h('span', { class: 'muted', text: 'Erinnerungen' })), statBars, statMeta),
        card({ title: 'Kernprofil', icon: 'user', subtitle: 'Das hat Vincent in jedem Gespräch im Kopf.' },
          profileText, h('div', { class: 'row-between' }, profileCount, profileSave)))));

  let items = [];
  let total = 0;
  let searchTimer = null;
  let listReq = 0;
  search.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadList(false), 250);
  });
  catFilter.addEventListener('change', () => loadList(false));

  function toggleAdd(force) {
    const open = force ?? addForm.hidden;
    addForm.hidden = !open;
    if (open) addText.focus();
  }

  async function addMemory() {
    const text = addText.value.trim();
    if (!text) {
      addText.focus();
      return;
    }
    const res = await ctx.ui.run(addSave, () => ctx.api.post('/api/memory', { text, category: addCat.value }));
    if (!res) return;
    addText.value = '';
    toggleAdd(false);
    ctx.ui.toast('Vincent hat sich das gemerkt.', 'good');
    loadList(false);
    loadStats();
  }

  async function loadList(more) {
    const req = ++listReq;
    const offset = more ? items.length : 0;
    const q = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
    if (search.value.trim()) q.set('q', search.value.trim());
    if (catFilter.value) q.set('category', catFilter.value);
    if (!more && !items.length) replaceChildren(listEl, h('li', { class: 'loading-row' }, spinner(18), h('span', { text: 'Lade Erinnerungen …' })));
    const res = await ctx.ui.run(more ? moreBtn : null, () => ctx.api.get(`/api/memory?${q}`));
    if (req !== listReq || !res) return;
    const got = Array.isArray(res.items) ? res.items : [];
    items = more ? [...items, ...got] : got;
    total = toNum(res.count) ?? items.length;
    renderList();
  }

  function renderList() {
    if (!items.length) {
      replaceChildren(listEl, h('li', null, emptyState('brain', 'Keine Erinnerungen gefunden',
        search.value || catFilter.value ? 'Andere Suche oder Kategorie probieren.' : 'Erzähl Vincent etwas über dich – er merkt es sich automatisch.')));
    } else {
      replaceChildren(listEl, items.map(memoryItem));
    }
    listInfo.textContent = `${fmtInt(items.length)} von ${fmtInt(total)} angezeigt`;
    moreBtn.hidden = items.length >= total;
  }

  function memoryItem(m) {
    const imp = Math.max(0, Math.min(1, toNum(m.importance) ?? 0.5));
    const textEl = h('p', { class: 'mem-text', text: m.text || '' });
    const pinBtn = iconButton('pin', m.pinned ? 'Nicht mehr anheften' : 'Anheften (wird nie vergessen)', {
      class: `icon-btn icon-btn-ghost${m.pinned ? ' is-on' : ''}`, 'aria-pressed': String(Boolean(m.pinned)),
      onClick: () => patch(m, { pinned: !m.pinned }, pinBtn),
    });
    const editBtn = iconButton('edit', 'Bearbeiten', { onClick: () => edit(m, li) });
    const delBtn = iconButton('trash', 'Löschen', { class: 'icon-btn icon-btn-ghost is-danger', onClick: () => remove(m, delBtn) });
    const li = h('li', { class: `mem${m.pinned ? ' is-pinned' : ''}`, dataset: { id: String(m.id) } },
      h('div', { class: 'mem-body' },
        h('div', { class: 'mem-top' },
          tag(memoryCategoryLabel(m.category), CAT_TONES[m.category] || 'neutral'),
          m.pinned ? h('span', { class: 'mem-pinned' }, icon('pin', 12), 'angeheftet') : null),
        textEl,
        h('div', { class: 'mem-meta' },
          h('span', { class: 'imp', title: `Wichtigkeit ${fmtNumber(imp * 100, 0)} %` },
            h('span', { class: 'imp-track' }, h('span', { style: { width: `${imp * 100}%` } })),
            h('span', { text: `Wichtigkeit ${fmtNumber(imp * 100, 0)} %` })),
          m.source ? h('span', { text: `Quelle: ${m.source}` }) : null,
          h('span', { title: fmtDateTime(m.updated || m.created), text: timeAgo(m.updated || m.created) }))),
      h('div', { class: 'mem-actions' }, pinBtn, editBtn, delBtn));
    return li;
  }

  function edit(m, li) {
    const ta = h('textarea', { class: 'input', rows: '3', value: m.text || '', 'aria-label': 'Erinnerung bearbeiten' });
    const imp = h('input', { type: 'range', min: '0', max: '1', step: '0.05', value: String(toNum(m.importance) ?? 0.5), class: 'range', 'aria-label': 'Wichtigkeit' });
    const impVal = h('span', { class: 'num muted', text: `${fmtNumber((toNum(m.importance) ?? 0.5) * 100, 0)} %` });
    imp.addEventListener('input', () => { impVal.textContent = `${fmtNumber(Number(imp.value) * 100, 0)} %`; });
    const save = button('Speichern', { variant: 'primary', size: 'sm', icon: 'check' });
    const cancel = button('Abbrechen', { variant: 'ghost', size: 'sm', onClick: () => form.replaceWith(memoryItem(m)) });
    save.addEventListener('click', async () => {
      const text = ta.value.trim();
      if (!text) return;
      const body = {};
      if (text !== m.text) body.text = text;
      if (Number(imp.value) !== (toNum(m.importance) ?? 0.5)) body.importance = Number(imp.value);
      if (!Object.keys(body).length) {
        form.replaceWith(memoryItem(m));
        return;
      }
      const ok = await patch(m, body, save);
      if (ok) form.replaceWith(memoryItem(m));
    });
    ta.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') cancel.click();
      if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) save.click();
    });
    const form = h('li', { class: 'mem is-editing' },
      h('div', { class: 'mem-body' }, ta,
        h('div', { class: 'mem-add-row' }, h('label', { class: 'tb-label', text: 'Wichtigkeit' }), imp, impVal,
          h('span', { class: 'spacer' }), cancel, save)));
    li.replaceWith(form);
    ta.focus();
  }

  async function patch(m, body, btn) {
    const res = await ctx.ui.run(btn, () => ctx.api.patch(`/api/memory/${encodeURIComponent(m.id)}`, body));
    if (!res) return false;
    Object.assign(m, body);
    if ('pinned' in body) {
      const node = listEl.querySelector(`[data-id="${CSS.escape(String(m.id))}"]`);
      node?.replaceWith(memoryItem(m));
      ctx.ui.toast(m.pinned ? 'Angeheftet – wird nie vergessen.' : 'Nicht mehr angeheftet.', 'info', { timeout: 2500 });
    } else {
      ctx.ui.toast('Erinnerung aktualisiert.', 'good', { timeout: 2500 });
    }
    return true;
  }

  async function remove(m, btn) {
    const ok = await ctx.ui.confirm({
      title: 'Erinnerung löschen?', tone: 'bad', icon: 'trash',
      text: `„${String(m.text || '').slice(0, 160)}“ wird endgültig vergessen.`, confirmLabel: 'Löschen',
    });
    if (!ok) return;
    const res = await ctx.ui.run(btn, () => ctx.api.del(`/api/memory/${encodeURIComponent(m.id)}`));
    if (!res) return;
    items = items.filter((x) => x.id !== m.id);
    total = Math.max(0, total - 1);
    renderList();
    loadStats();
  }

  async function loadStats() {
    const s = await ctx.ui.run(null, () => ctx.api.get('/api/memory/stats'));
    if (!s) return;
    statTotal.textContent = fmtInt(s.total ?? 0);
    const pairs = categoryCounts(s.by_category);
    const max = Math.max(1, ...pairs.map((p) => p[1]));
    replaceChildren(statBars, pairs.length
      ? pairs.map(([cat, n]) => h('div', { class: 'cat-bar', title: `${memoryCategoryLabel(cat)}: ${fmtInt(n)}` },
        h('span', { class: 'cat-name', text: memoryCategoryLabel(cat) }),
        h('span', { class: 'cat-track' }, h('span', { style: { width: `${(n / max) * 100}%` } })),
        h('span', { class: 'cat-n num', text: fmtInt(n) })))
      : h('div', { class: 'muted', text: 'Noch keine Kategorien.' }));
    replaceChildren(statMeta,
      h('div', null, icon('clock', 14), h('span', { text: s.last_consolidation ? `Zuletzt verdichtet ${timeAgo(s.last_consolidation)}` : 'Noch nie verdichtet' })),
      s.profile_chars !== undefined ? h('div', null, icon('user', 14), h('span', { text: `Kernprofil: ${fmtInt(s.profile_chars)} Zeichen` })) : null);
  }

  async function loadProfile() {
    const res = await ctx.ui.run(null, () => ctx.api.get('/api/memory/profile'));
    if (!res) return;
    profileOrig = String(res.text || '');
    profileText.value = profileOrig;
    profileText.dispatchEvent(new Event('input'));
  }

  async function saveProfile() {
    const text = profileText.value;
    const res = await ctx.ui.run(profileSave, () => ctx.api.put('/api/memory/profile', { text }));
    if (!res) return;
    profileOrig = text;
    profileText.dispatchEvent(new Event('input'));
    ctx.ui.toast('Kernprofil gespeichert.', 'good');
    loadStats();
  }

  async function consolidate() {
    const res = await ctx.ui.run(consolidateBtn, () => ctx.api.post('/api/memory/consolidate', {}));
    if (!res) return;
    ctx.ui.info('Gedächtnis verdichtet', res.text || 'Fertig.', 'good');
    loadStats();
    loadList(false);
  }

  return {
    el,
    show() {
      loadStats();
      loadProfile();
      loadList(false);
    },
    hide() {},
    update() {},
  };
}
