// Gemeinsame Darstellungen (Analysen-Tabelle, Score-Balken, Statuskarte), genutzt von mehreren Ansichten.

import { h, icon, table, tag, emptyState } from './dom.js';
import { fmtPct, fmtPrice, fmtNumber, fmtSigned, regimeText, directionInfo, scorePercent, timeAgo, toNum } from './format.js';

/** Score −1…+1 als Mini-Skala mit Mittelstrich. */
export function scoreBar(score) {
  const n = toNum(score);
  const pct = scorePercent(n);
  const tone = n === null ? 'neutral' : n >= 0.05 ? 'good' : n <= -0.05 ? 'bad' : 'neutral';
  const left = Math.min(50, pct);
  const width = Math.abs(pct - 50);
  return h('span', { class: `score tone-${tone}`, title: `Ensemble-Score ${fmtSigned(n)} (−1 … +1)` },
    h('span', { class: 'score-track' },
      h('span', { class: 'score-fill', style: { left: `${left}%`, width: `${width}%` } }),
      h('span', { class: 'score-mid' })),
    h('span', { class: 'score-num num', text: fmtSigned(n) }));
}

export function directionChip(direction, hasEdge = true, word = '') {
  const d = directionInfo(direction, hasEdge);
  const chip = h('span', { class: `chip tone-${d.tone}${hasEdge ? '' : ' is-weak'}`, title: word || d.hint },
    icon(d.icon, 12), h('span', { text: d.label }));
  return hasEdge || d.tone === 'neutral'
    ? chip
    : h('span', { class: 'chip-stack' }, chip, h('span', { class: 'chip-note', text: 'ohne Vorteil' }));
}

export function changeText(x) {
  const n = toNum(x);
  return h('span', { class: `num ${n === null ? '' : n >= 0 ? 'txt-good' : 'txt-bad'}`, text: fmtPct(n) });
}

function rsiCell(rsi) {
  const n = toNum(rsi);
  if (n === null) return '–';
  const hint = n >= 70 ? 'überkauft' : n <= 30 ? 'überverkauft' : '';
  return h('span', { class: 'rsi' },
    h('span', { class: 'num', text: fmtNumber(n, 0) }),
    hint ? h('span', { class: `rsi-hint ${n >= 70 ? 'txt-bad' : 'txt-good'}`, text: hint }) : null);
}

/** Tabelle: Symbol, Kurs, 24h, Marktphase, RSI, Score, Empfehlung. */
export function analysesTable(analyses, { onSelect, emptyText } = {}) {
  const rows = Array.isArray(analyses) ? analyses : [];
  if (!rows.length) {
    return emptyState('scan', 'Noch keine Analysen', emptyText || 'Starte eine Analyse – Vincent prüft alle Märkte deiner Beobachtungsliste.');
  }
  return table([
    { label: 'Symbol', class: 'col-sym', render: (a) => h('span', { class: 'sym' },
      h('span', { class: 'sym-name', text: a.symbol || '–' }),
      a.ts ? h('span', { class: 'sym-sub', text: timeAgo(a.ts) }) : null) },
    { label: 'Kurs', class: 'num-col', render: (a) => h('span', { class: 'num mono', text: fmtPrice(a.price) }) },
    { label: '24h', class: 'num-col', render: (a) => changeText(a.change_24h) },
    { label: 'Marktphase', render: (a) => h('span', { class: 'phase', text: regimeText(a.regime) }) },
    { label: 'RSI', class: 'num-col', render: (a) => rsiCell(a.rsi) },
    { label: 'Score', render: (a) => scoreBar(a.score) },
    { label: 'Empfehlung', render: (a) => directionChip(a.direction, a.has_edge !== false, a.direction_word) },
  ], rows, { onRowClick: onSelect, caption: 'Letzte Analysen' });
}

/** Große Statuskarte der Übersicht. */
export function statusCard({ label, value, tone = 'neutral', icon: ic, text, extra }) {
  return h('article', { class: `stat tone-${tone}` },
    h('div', { class: 'stat-top' },
      h('span', { class: 'stat-icon' }, icon(ic, 18)),
      h('span', { class: 'stat-label', text: label })),
    h('div', { class: 'stat-value', text: value }),
    text ? h('p', { class: 'stat-text', text }) : null,
    extra || null);
}

export function categoryTag(cat) {
  const tones = {
    Trend: 'accent', Momentum: 'info', 'Mean-Reversion': 'good', Ausbruch: 'warn', Volumen: 'violet', Kerzenmuster: 'pink',
  };
  return tag(cat || 'Sonstige', tones[cat] || 'neutral');
}
