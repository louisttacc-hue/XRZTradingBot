// Ansicht "Markt": Kerzen-Chart mit Trade-Markern, Analyse, Backtest und Scan aller Märkte.

import { h, icon, button, card, viewHeader, table, emptyState, replaceChildren, select, tag, spinner } from './dom.js';
import { analysesTable, scoreBar, directionChip } from './components.js';
import {
  fmtPrice, fmtPct, fmtNumber, fmtSigned, regimeText, toChartMarkers, normalizeBacktestReport, toNum, signature,
} from './format.js';

const TIMEFRAMES = ['5m', '15m', '30m', '1h', '4h', '1d'].map((v) => ({ value: v, label: v }));
const COLORS = { up: '#2bd99f', down: '#ff5d73', exit: '#a9aec0', support: '#2bd99f', resistance: '#ff5d73' };

export function createMarket(ctx) {
  const symInput = h('input', {
    class: 'input input-sym', type: 'text', list: 'zt-symbols', placeholder: 'z. B. BTC/USDT', autocomplete: 'off',
    spellcheck: 'false', 'aria-label': 'Symbol',
  });
  const symList = h('datalist', { id: 'zt-symbols' });
  const tfSelect = select(TIMEFRAMES, '1h', { 'aria-label': 'Zeiteinheit' });
  const analyzeBtn = button('Analysieren', { variant: 'primary', icon: 'sparkle', onClick: () => analyze() });
  const backtestBtn = button('Backtest', { variant: 'secondary', icon: 'flask', onClick: () => backtest() });
  const scanBtn = button('Alle scannen', { variant: 'ghost', icon: 'scan', onClick: () => scanAll() });

  symInput.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      analyze();
    }
  });
  tfSelect.addEventListener('change', () => loadCandles());

  const toolbar = h('form', { class: 'toolbar card', onSubmit: (ev) => { ev.preventDefault(); analyze(); } },
    h('div', { class: 'tb-group' },
      h('label', { class: 'tb-label', text: 'Symbol' }), symInput, symList),
    h('div', { class: 'tb-group' }, h('label', { class: 'tb-label', text: 'Zeiteinheit' }), tfSelect),
    h('div', { class: 'tb-spacer' }),
    h('div', { class: 'tb-actions' }, scanBtn, backtestBtn, analyzeBtn));

  // Chart
  const chartTitle = h('span', { class: 'chart-sym', text: '–' });
  const chartTf = tag('1h', 'neutral');
  const chartPrice = h('span', { class: 'chart-price num mono', text: '' });
  const chartChange = h('span', { class: 'chart-change num' });
  const chartLegend = h('div', { class: 'chart-legend', hidden: true },
    h('span', null, h('i', { class: 'lg lg-up' }), 'Kauf'),
    h('span', null, h('i', { class: 'lg lg-down' }), 'Verkauf'),
    h('span', null, h('i', { class: 'lg lg-exit' }), 'Ausstieg'));
  const chartBox = h('div', { class: 'chart-box', role: 'img', 'aria-label': 'Kerzen-Chart' });
  const chartMsg = h('div', { class: 'chart-msg', hidden: true });
  const chartCard = h('section', { class: 'card chart-card' },
    h('div', { class: 'chart-head' },
      h('div', { class: 'chart-id' }, chartTitle, chartTf),
      h('div', { class: 'chart-quote' }, chartPrice, chartChange),
      h('div', { class: 'spacer' }),
      chartLegend),
    h('div', { class: 'chart-wrap' }, chartBox, chartMsg));

  const analysisBody = h('div');
  const analysisCard = card({ title: 'Analyse', icon: 'sparkle', class: 'analysis-card', hidden: true }, analysisBody);
  const backtestBody = h('div');
  const backtestCard = card({
    title: 'Backtest', icon: 'flask', class: 'backtest-card', hidden: true,
    subtitle: 'Walk-Forward: Parameter auf alten Daten gewählt, auf neuen, ungesehenen Daten geprüft.',
  }, backtestBody);
  const scanBody = h('div');
  const scanCard = card({ title: 'Alle Märkte', icon: 'scan', subtitle: 'Letzte Analyse deiner Beobachtungsliste. Klick lädt den Chart.' }, scanBody);

  const el = h('div', { class: 'view view-market' },
    viewHeader('Markt', 'Charts, Analyse mit über 35 Strategien und ehrliche Backtests.'),
    toolbar, chartCard, analysisCard, backtestCard, scanCard);

  // ---------------------------------------------------------------- Chart
  let chart = null;
  let series = null;
  let priceLines = [];
  let candleReq = 0;

  function ensureChart() {
    if (chart) return true;
    const LWC = globalThis.LightweightCharts;
    if (!LWC?.createChart) {
      showChartMsg('Chart-Bibliothek konnte nicht geladen werden.');
      return false;
    }
    chart = LWC.createChart(chartBox, {
      autoSize: true,
      layout: {
        background: { type: 'solid', color: 'transparent' },
        textColor: '#8b90a6',
        fontSize: 11,
        fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif',
      },
      grid: { vertLines: { color: 'rgba(255,255,255,0.035)' }, horzLines: { color: 'rgba(255,255,255,0.035)' } },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)', scaleMargins: { top: 0.12, bottom: 0.1 } },
      timeScale: { borderColor: 'rgba(255,255,255,0.08)', timeVisible: true, secondsVisible: false, rightOffset: 6 },
      crosshair: {
        mode: 0,
        vertLine: { color: 'rgba(255,255,255,0.18)', labelBackgroundColor: '#1d2030' },
        horzLine: { color: 'rgba(255,255,255,0.18)', labelBackgroundColor: '#1d2030' },
      },
      localization: { locale: 'de-DE', priceFormatter: (p) => fmtPrice(p) },
    });
    series = chart.addCandlestickSeries({
      upColor: COLORS.up, downColor: COLORS.down, wickUpColor: COLORS.up, wickDownColor: COLORS.down, borderVisible: false,
    });
    return true;
  }

  function showChartMsg(text, busy = false) {
    chartMsg.hidden = !text;
    replaceChildren(chartMsg, busy ? spinner(18) : null, text ? h('span', { text }) : null);
  }

  function currentSymbol() {
    return symInput.value.trim().toUpperCase();
  }

  async function loadCandles() {
    const symbol = currentSymbol();
    if (!symbol || !ensureChart()) return;
    const tf = tfSelect.value;
    const req = ++candleReq;
    chartTitle.textContent = symbol;
    chartTf.textContent = tf;
    showChartMsg('Lade Kursdaten …', true);
    try {
      const q = new URLSearchParams({ symbol, timeframe: tf, limit: '400' });
      const res = await ctx.api.get(`/api/candles?${q}`);
      if (req !== candleReq) return;
      const seen = new Set();
      const candles = (res?.candles || [])
        .map((c) => ({ time: Number(c.time), open: +c.open, high: +c.high, low: +c.low, close: +c.close }))
        .filter((c) => Number.isFinite(c.time) && Number.isFinite(c.close) && !seen.has(c.time) && seen.add(c.time))
        .sort((a, b) => a.time - b.time);
      series.setData(candles);
      const markers = toChartMarkers(candles, res?.markers || [], COLORS);
      series.setMarkers(markers);
      chartLegend.hidden = markers.length === 0;
      if (res?.symbol) chartTitle.textContent = res.symbol;
      if (candles.length) {
        chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, candles.length - 160), to: candles.length + 4 });
        const last = candles[candles.length - 1];
        const ref = [...candles].reverse().find((c) => c.time <= last.time - 86400) || candles[0];
        chartPrice.textContent = fmtPrice(last.close);
        const ch = ref.close ? ((last.close - ref.close) / ref.close) * 100 : null;
        chartChange.textContent = ch === null ? '' : `${fmtPct(ch)} (24h)`;
        chartChange.className = `chart-change num ${ch === null ? '' : ch >= 0 ? 'txt-good' : 'txt-bad'}`;
        showChartMsg('');
      } else {
        chartPrice.textContent = '';
        chartChange.textContent = '';
        showChartMsg('Keine Kursdaten für dieses Symbol.');
      }
    } catch (err) {
      if (req !== candleReq) return;
      showChartMsg(err?.message || 'Kursdaten nicht verfügbar.');
    }
  }

  function setPriceLines(data) {
    if (!series) return;
    for (const l of priceLines) series.removePriceLine(l);
    priceLines = [];
    const add = (price, color, title) => {
      const p = toNum(price);
      if (p === null) return;
      priceLines.push(series.createPriceLine({ price: p, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title }));
    };
    const lv = data?.levels || {};
    (lv.support || []).slice(0, 2).forEach((p) => add(p, 'rgba(43,217,159,0.55)', 'Unterstützung'));
    (lv.resistance || []).slice(0, 2).forEach((p) => add(p, 'rgba(255,93,115,0.55)', 'Widerstand'));
  }

  // ------------------------------------------------------------- Analyse
  async function analyze() {
    const symbol = currentSymbol();
    if (!symbol) {
      ctx.ui.toast('Bitte ein Symbol eingeben, z. B. BTC/USDT.', 'warn');
      symInput.focus();
      return;
    }
    loadCandles();
    analysisCard.hidden = false;
    replaceChildren(analysisBody, h('div', { class: 'loading-row' }, spinner(18), h('span', { text: `Vincent analysiert ${symbol} …` })));
    const res = await ctx.ui.run(analyzeBtn, () => ctx.api.post('/api/analyze', { symbol, timeframe: tfSelect.value }));
    if (!res) {
      replaceChildren(analysisBody, emptyState('alert', 'Analyse fehlgeschlagen', 'Prüfe das Symbol und versuche es erneut.'));
      return;
    }
    renderAnalysis(res);
    setPriceLines(res.data);
  }

  function tile(label, value, sub) {
    return h('div', { class: 'mtile' },
      h('div', { class: 'mtile-label', text: label }),
      value instanceof Node ? h('div', { class: 'mtile-value' }, value) : h('div', { class: 'mtile-value', text: value }),
      sub ? h('div', { class: 'mtile-sub', text: sub }) : null);
  }

  function renderAnalysis(res) {
    const d = res.data || {};
    const reg = d.regime || {};
    const ind = d.indicators || {};
    const dir = toNum(d.direction) || 0;
    const tiles = h('div', { class: 'mtiles' },
      tile('Kurs', h('span', { class: 'num mono', text: fmtPrice(d.price) }), d.timeframe ? `Zeiteinheit ${d.timeframe}` : ''),
      tile('Empfehlung', directionChip(d.direction, d.has_edge !== false, d.direction_word), d.direction_word || ''),
      tile('Ensemble-Score', scoreBar(d.score), `${d.active_long ?? 0} long · ${d.active_short ?? 0} short`),
      tile('Marktphase', regimeText(reg), reg.adx !== undefined ? `ADX ${fmtNumber(reg.adx, 0)} · Volatilität ${reg.volatility || '–'}` : ''),
      tile('RSI', h('span', { class: 'num', text: fmtNumber(ind.rsi, 0) }), ind.atr_pct !== undefined ? `ATR ${fmtNumber(ind.atr_pct, 2)} %` : ''),
      tile('Übergeordnet', d.htf_regime ? regimeText(d.htf_regime) : '–', 'Trend der höheren Zeiteinheit'));

    const plan = dir !== 0
      ? h('div', { class: 'trade-plan' },
        icon('shield', 16),
        h('span', { text: 'Vorschlag:' }),
        h('span', { class: 'num mono txt-bad', text: `Stop ${fmtPrice(dir > 0 ? d.stop_long : d.stop_short)}` }),
        h('span', { class: 'num mono txt-good', text: `Ziel ${fmtPrice(dir > 0 ? d.tp_long : d.tp_short)}` }))
      : null;

    const signals = Array.isArray(d.signals) ? [...d.signals].sort((a, b) => (toNum(b.weight) || 0) - (toNum(a.weight) || 0)) : [];
    const sigList = signals.length
      ? h('ul', { class: 'signals' }, signals.map((s) => {
        const sig = toNum(s.signal) || 0;
        const w = Math.max(0, Math.min(1, toNum(s.weight) || 0));
        return h('li', { class: 'signal' },
          h('span', { class: `sig-dot ${sig > 0 ? 'is-up' : sig < 0 ? 'is-down' : ''}`, title: sig > 0 ? 'Long' : sig < 0 ? 'Short' : 'Neutral' }),
          h('div', { class: 'sig-main' },
            h('div', { class: 'sig-top' },
              h('span', { class: 'sig-name', text: s.label || s.name || '–' }),
              h('span', { class: 'sig-w num', text: `${fmtNumber(w * 100, 0)} %` })),
            h('div', { class: 'sig-bar' }, h('span', { style: { width: `${w * 100}%` } })),
            s.why ? h('div', { class: 'sig-why', text: s.why }) : null));
      }))
      : emptyState('layers', 'Keine Strategie-Signale', '');

    const notes = Array.isArray(d.notes) && d.notes.length
      ? h('ul', { class: 'notes' }, d.notes.map((n) => h('li', null, icon('info', 14), h('span', { text: String(n) }))))
      : null;
    const noEdge = d.has_edge === false
      ? h('div', { class: 'banner tone-warn' }, icon('alert', 16), h('span', { text: 'Kein Strategie-Vorteil im Backtest nachweisbar – Vincent handelt diesen Markt nicht.' }))
      : null;

    replaceChildren(analysisBody,
      tiles, plan, noEdge,
      h('div', { class: 'analysis-cols' },
        h('div', null, h('h3', { class: 'sub-title', text: 'Strategien (Gewicht aus Backtest)' }), sigList),
        h('div', null, h('h3', { class: 'sub-title', text: 'Vincents Einschätzung' }),
          h('pre', { class: 'prose', text: res.text || '' }), notes)));
  }

  // ------------------------------------------------------------- Backtest
  async function backtest() {
    const symbol = currentSymbol();
    if (!symbol) {
      ctx.ui.toast('Bitte zuerst ein Symbol eingeben.', 'warn');
      return;
    }
    backtestCard.hidden = false;
    replaceChildren(backtestBody, h('div', { class: 'loading-row' }, spinner(18),
      h('span', { text: `Backtest für ${symbol} läuft – das kann bis zu einer Minute dauern …` })));
    const res = await ctx.ui.run(backtestBtn, () => ctx.api.post('/api/backtest', { symbol }));
    if (!res) {
      replaceChildren(backtestBody, emptyState('alert', 'Backtest fehlgeschlagen', ''));
      return;
    }
    const reports = (Array.isArray(res.reports) ? res.reports : Object.values(res.reports || {}))
      .map(normalizeBacktestReport)
      .sort((a, b) => (b.weight ?? -1) - (a.weight ?? -1));
    const pctCell = (v) => h('span', { class: `num mono ${v === null ? '' : v >= 0 ? 'txt-good' : 'txt-bad'}`, text: fmtPct(v, 1) });
    const tbl = reports.length
      ? table([
        { label: 'Strategie', render: (r) => h('span', { class: 'sym-name', text: r.label }) },
        { label: 'Gewicht', render: (r) => h('span', { class: 'wbar' },
          h('span', { class: 'wbar-track' }, h('span', { style: { width: `${Math.max(0, Math.min(1, r.weight || 0)) * 100}%` } })),
          h('span', { class: 'num', text: r.weight === null ? '–' : `${fmtNumber(r.weight * 100, 0)} %` })) },
        { label: 'Rendite (neu)', class: 'num-col', render: (r) => pctCell(r.ret) },
        { label: 'Trades', class: 'num-col', render: (r) => h('span', { class: 'num', text: fmtNumber(r.trades, 0) }) },
        { label: 'Trefferquote', class: 'num-col', render: (r) => h('span', { class: 'num', text: fmtPct(r.winRate, 0, false) }) },
        { label: 'Profitfaktor', class: 'num-col', render: (r) => h('span', { class: 'num', text: fmtNumber(r.profitFactor, 2) }) },
        { label: 'Max. Drawdown', class: 'num-col', render: (r) => h('span', { class: 'num txt-bad', text: r.maxDrawdown === null ? '–' : fmtPct(-r.maxDrawdown, 1) }) },
      ], reports, { caption: 'Backtest-Ergebnisse je Strategie' })
      : null;
    replaceChildren(backtestBody,
      res.text ? h('pre', { class: 'prose prose-box', text: res.text }) : null,
      tbl);
  }

  // ---------------------------------------------------------------- Scan
  async function scanAll() {
    const res = await ctx.ui.run(scanBtn, () => ctx.api.post('/api/scan', {}));
    if (!res) return;
    ctx.patchState({ analyses: Array.isArray(res.analyses) ? res.analyses : [] });
    ctx.ui.toast(`${(res.analyses || []).length} Märkte analysiert.`, 'good');
  }

  let scanSig = '';
  function renderScan(list) {
    const s = signature(list);
    if (s === scanSig) return;
    scanSig = s;
    replaceChildren(scanBody, analysesTable(list, {
      onSelect: (a) => {
        symInput.value = a.symbol;
        analyze();
        el.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
      },
      emptyText: 'Klick auf „Alle scannen“, um alle Märkte deiner Beobachtungsliste zu analysieren.',
    }));
  }

  let watchSig = '';
  function renderWatchlist(list) {
    const s = signature(list);
    if (s === watchSig) return;
    watchSig = s;
    replaceChildren(symList, (list || []).map((sym) => h('option', { value: sym })));
  }

  function update(state) {
    if (!state) return;
    renderWatchlist(state.watchlist || []);
    renderScan(state.analyses || []);
  }

  let first = true;
  return {
    el,
    show(params) {
      const st = ctx.getState() || {};
      update(st);
      if (first) {
        first = false;
        if (st.timeframe && TIMEFRAMES.some((t) => t.value === st.timeframe)) tfSelect.value = st.timeframe;
        if (!symInput.value) symInput.value = params?.symbol || (st.watchlist || [])[0] || 'BTC/USDT';
        if (params?.symbol) analyze();
        else loadCandles();
        return;
      }
      if (params?.symbol) {
        symInput.value = params.symbol;
        analyze();
      }
    },
    hide() {},
    update,
  };
}
