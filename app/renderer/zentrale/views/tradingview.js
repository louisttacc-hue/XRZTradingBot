// Ansicht "TradingView": Verbindungsstatus, Konto-Erkennung, Erklärung der Steuerung und Test-Order (ohne Absenden).

import { h, icon, button, card, viewHeader, replaceChildren, field, select, segmented, spinner, tag } from './dom.js';
import { accountInfo, normalizeAccount, fmtMoney, screenshotSrc, signature, toNum } from './format.js';

const STEPS = [
  ['monitor', 'Die echte TradingView-App', 'Vincent bedient die TradingView-Desktop-App auf deinem Mac – genau so, wie du es tun würdest. Es gibt keine versteckte Börsen-Verbindung.'],
  ['refresh', 'Einmaliger Neustart – mit Nachfrage', 'Damit Vincent die App steuern kann, startet er TradingView einmal mit einem lokalen Steuer-Port neu. Der Port ist nur auf deinem Mac erreichbar (127.0.0.1). Vincent fragt dich vorher.'],
  ['shield', 'Konto-Prüfung vor jeder Order', 'Vor jeder Order liest Vincent das aktive Konto aus: Im Paper-Modus muss es „Paper Trading“ sein, im Echtgeld-Modus dein echter Broker. Ist das Konto unklar, bricht er ab.'],
  ['check', 'Ticket-Kontrolle', 'Nach dem Ausfüllen liest Vincent Seite, Menge, Symbol und Preis zurück. Weicht etwas ab, verwirft er das Ticket.'],
];

export function createTradingView(ctx) {
  let status = null;

  const statusBody = h('div', { class: 'tv-status' });
  const checkBtn = button('Verbindung prüfen', { variant: 'primary', icon: 'refresh', onClick: () => check() });
  const accountBody = h('div', { class: 'tv-account' });

  // Test-Order
  const symInput = h('input', { type: 'text', class: 'input mono', value: 'BINANCE:BTCUSDT', spellcheck: 'false', autocomplete: 'off' });
  let side = 'buy';
  const sideCtl = segmented([
    { value: 'buy', label: 'Kaufen', tone: 'good' },
    { value: 'sell', label: 'Verkaufen', tone: 'bad' },
  ], { value: 'buy', label: 'Richtung', onSelect: (v) => { side = v; sideCtl.set(v); } });
  const qtyInput = h('input', { type: 'number', class: 'input mono', value: '0.001', min: '0', step: 'any', inputmode: 'decimal' });
  const testBtn = button('Test-Order (ohne Absenden)', { variant: 'secondary', icon: 'flask', type: 'submit' });
  const result = h('div', { class: 'tv-result', hidden: true });
  const form = h('form', { class: 'tv-form' },
    field('Symbol (TradingView-Format)', symInput, 'z. B. BINANCE:BTCUSDT'),
    h('div', { class: 'field' }, h('span', { class: 'field-label', text: 'Richtung' }), sideCtl.el),
    field('Menge', qtyInput),
    h('div', { class: 'tv-form-actions' }, testBtn));
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    testOrder();
  });

  const el = h('div', { class: 'view view-tv' },
    viewHeader('TradingView', 'Vincent handelt ausschließlich in deiner TradingView-App – und prüft vorher immer das Konto.', checkBtn),
    h('div', { class: 'tv-grid' },
      card({ class: 'tv-conn', title: 'Verbindung', icon: 'link' }, statusBody),
      card({ class: 'tv-acc', title: 'Erkanntes Konto', icon: 'shield' }, accountBody),
      card({ class: 'tv-how', title: 'So steuert Vincent TradingView', icon: 'info' },
        h('ol', { class: 'steps' }, STEPS.map(([ic, title, text], i) => h('li', { class: 'step' },
          h('span', { class: 'step-num' }, icon(ic, 16)),
          h('div', null, h('div', { class: 'step-title', text: `${i + 1}. ${title}` }), h('p', { class: 'step-text', text }))))))),
    card({
      class: 'tv-test', title: 'Test-Order', icon: 'flask',
      subtitle: 'Vincent füllt das Order-Ticket aus und macht ein Foto – abgeschickt wird NIE. So siehst du, dass die Steuerung funktioniert.',
    }, h('div', { class: 'tv-test-grid' }, form, result)));

  function render() {
    const s = status || {};
    const acc = accountInfo(s);
    const a = normalizeAccount(s.account);
    replaceChildren(statusBody,
      h('div', { class: `conn-big tone-${s.connected ? 'good' : 'neutral'}` },
        h('span', { class: 'conn-dot' }),
        h('span', { class: 'conn-text', text: s.connected ? 'Verbunden' : 'Nicht verbunden' })),
      h('dl', { class: 'kv' },
        h('dt', { text: 'Steuerung' }), h('dd', { text: driverLabel(s.driver) }),
        s.version ? h('dt', { text: 'TradingView-Version' }) : null, s.version ? h('dd', { text: String(s.version) }) : null),
      s.message ? h('p', { class: 'tv-msg', text: s.message }) : null);

    const evidence = a.evidence.length
      ? h('div', { class: 'evidence' },
        h('div', { class: 'sub-title', text: 'Woran Vincent das erkannt hat' }),
        h('ul', null, a.evidence.map((e) => h('li', null, icon('check', 12), h('span', { text: e })))))
      : null;
    replaceChildren(accountBody,
      h('div', { class: `acc-badge tone-${acc.tone}` },
        icon(acc.kind === 'live' ? 'alert' : acc.kind === 'paper' ? 'flask' : 'info', 22),
        h('div', null, h('div', { class: 'acc-title', text: acc.title }), h('div', { class: 'acc-text', text: acc.text }))),
      s.connected
        ? h('dl', { class: 'kv' },
          h('dt', { text: 'Art' }), h('dd', { text: { paper: 'Paper Trading (Übungsgeld)', live: 'Echtgeld', unknown: 'Unbekannt' }[a.kind] }),
          h('dt', { text: 'Broker' }), h('dd', { text: a.broker || '–' }),
          h('dt', { text: 'Konto' }), h('dd', { text: a.label || '–' }),
          a.balance !== null ? h('dt', { text: 'Kontostand' }) : null,
          a.balance !== null ? h('dd', { class: 'num mono', text: fmtMoney(a.balance, a.currency) }) : null)
        : null,
      evidence);
  }

  function driverLabel(d) {
    return {
      cdp: 'Direkt über lokalen Steuer-Port (zuverlässig)',
      ax: 'Über macOS-Bedienungshilfen',
      vision: 'Über Bildschirm-Erkennung (langsamer)',
      none: 'Keine',
    }[d] || (d ? String(d) : 'Keine');
  }

  async function check() {
    const prev = statusBody.firstChild;
    prev?.classList?.add('is-checking');
    const res = await ctx.ui.run(checkBtn, () => ctx.api.post('/api/tradingview/check', {}));
    prev?.classList?.remove('is-checking');
    if (!res) return;
    status = res;
    ctx.patchState({ tradingview: { connected: !!res.connected, driver: res.driver, account: res.account, message: res.message } });
    render();
    ctx.ui.toast(res.message || (res.connected ? 'TradingView ist verbunden.' : 'TradingView ist nicht verbunden.'), res.connected ? 'good' : 'warn');
  }

  async function testOrder() {
    const symbol = symInput.value.trim().toUpperCase();
    const qty = toNum(qtyInput.value);
    if (!symbol) {
      symInput.focus();
      return;
    }
    if (qty === null || qty <= 0) {
      ctx.ui.toast('Bitte eine Menge größer als 0 eingeben.', 'warn');
      qtyInput.focus();
      return;
    }
    result.hidden = false;
    replaceChildren(result, h('div', { class: 'loading-row' }, spinner(18), h('span', { text: 'Vincent füllt das Order-Ticket aus (ohne Absenden) …' })));
    const res = await ctx.ui.run(testBtn, () => ctx.api.post('/api/tradingview/test-order', { symbol, side, qty }));
    if (!res) {
      result.hidden = true;
      return;
    }
    const src = screenshotSrc(res.screenshot_b64);
    replaceChildren(result,
      h('div', { class: `banner tone-${res.ok ? 'good' : 'bad'}` },
        icon(res.ok ? 'check' : 'alert', 18),
        h('div', null,
          h('strong', { text: res.ok ? 'Test erfolgreich' : 'Test fehlgeschlagen' }),
          res.stage ? h('span', { class: 'stage' }, ' · Schritt: ', tag(String(res.stage), 'neutral')) : null,
          res.message ? h('p', { class: 'banner-text', text: res.message }) : null)),
      src
        ? h('figure', { class: 'shot' },
          h('img', { src, alt: 'Bildschirmfoto des ausgefüllten Order-Tickets' }),
          h('figcaption', { text: 'Bildschirmfoto des ausgefüllten Tickets – nicht abgeschickt.' }))
        : null);
  }

  let sig = '';
  return {
    el,
    show() {
      const st = ctx.getState();
      if (!status && st?.tradingview) status = st.tradingview;
      const first = (st?.watchlist || [])[0];
      if (first && symInput.value === 'BINANCE:BTCUSDT') symInput.value = `BINANCE:${first.replace('/', '')}`;
      render();
    },
    hide() {},
    update(state) {
      if (!state?.tradingview) return;
      const s = signature(state.tradingview);
      if (s === sig) return;
      sig = s;
      status = { ...(status || {}), ...state.tradingview };
      render();
    },
    event(type, data) {
      if (type === 'tradingview' && data?.status) {
        status = { ...(status || {}), ...data.status };
        render();
      }
    },
  };
}
