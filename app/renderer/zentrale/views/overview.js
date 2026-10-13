// Ansicht "Übersicht": Statuskarten, Trade-Vorschläge, Positionen, Analysen, Schnell-Chat.

import { h, icon, button, iconButton, card, viewHeader, table, emptyState, replaceChildren, pill, tag } from './dom.js';
import { analysesTable, statusCard } from './components.js';
import {
  modeInfo, accountInfo, normalizeAccount, voiceStateInfo, AUTONOMY, sideInfo, fmtQty, fmtPrice, fmtSigned, fmtInt,
  fmtCountdown, timeAgo, signature, positionPnl, proposalStatusInfo, toNum, fmtMoney,
} from './format.js';

export function createOverview(ctx) {
  const banners = h('div', { class: 'banners' });
  const stats = h('div', { class: 'stats' });
  const propsBody = h('div', { class: 'proposals' });
  const posBody = h('div');
  const anaBody = h('div');
  const propsCount = h('span', { class: 'count-badge', hidden: true });
  const chat = createChat(ctx);

  const scanBtn = button('Jetzt Markt analysieren', { variant: 'primary', icon: 'scan', onClick: () => scan(scanBtn) });

  const el = h('div', { class: 'view view-overview' },
    viewHeader('Übersicht', 'Alles Wichtige auf einen Blick – Status, Freigaben, Positionen und Analysen.', scanBtn),
    banners,
    stats,
    h('div', { class: 'ov-grid' },
      card({ class: 'area-props', title: 'Trade-Vorschläge', icon: 'bolt', subtitle: 'Freigeben, ablehnen – oder Vincent im Auto-Modus selbst handeln lassen.', actions: propsCount }, propsBody),
      h('div', { class: 'area-chat' }, chat.el),
      card({ class: 'area-pos', title: 'Offene Positionen', icon: 'layers' }, posBody),
      card({
        class: 'area-ana',
        title: 'Letzte Analysen',
        icon: 'candles',
        subtitle: 'Klick auf eine Zeile öffnet den Chart.',
        actions: button('Zum Markt', { variant: 'ghost', size: 'sm', icon: 'external', onClick: () => ctx.navigate('markt') }),
      }, anaBody)));

  const sigs = {};
  const changed = (key, value) => {
    const s = signature(value);
    if (sigs[key] === s) return false;
    sigs[key] = s;
    return true;
  };

  async function scan(btn) {
    const res = await ctx.ui.run(btn, () => ctx.api.post('/api/scan', {}));
    if (!res) return;
    const list = Array.isArray(res.analyses) ? res.analyses : [];
    ctx.patchState({ analyses: list });
    ctx.ui.toast(list.length ? `${list.length} Märkte analysiert.` : 'Analyse fertig.', 'good');
  }

  // ---------------------------------------------------------------- Banner
  function renderBanners(s) {
    const items = [];
    if (s.halted) items.push(banner('bad', 'alert', 'Handel gestoppt', String(s.halted)));
    if (s.mode === 'live') {
      items.push(banner('bad', 'bolt', 'Echtgeld-Modus aktiv', s.live_autopilot && s.autonomy === 'auto'
        ? 'Autopilot: Vincent handelt selbstständig über dein echtes Broker-Konto – klein beginnend, innerhalb deiner Risiko-Grenzen.'
        : 'Jede Order läuft über dein echtes Broker-Konto und braucht deine ausdrückliche Freigabe.'));
    }
    if (s.entries_paused) items.push(banner('warn', 'clock', 'Neue Einstiege pausiert', 'Bestehende Positionen werden weiter verwaltet.'));
    replaceChildren(banners, items);
  }

  function banner(tone, ic, title, text) {
    return h('div', { class: `banner tone-${tone}`, role: 'status' },
      icon(ic, 18), h('div', null, h('strong', { text: title }), h('span', { text: ` ${text}` })));
  }

  // ---------------------------------------------------------------- Status
  function renderStats(s) {
    const mode = modeInfo(s.mode);
    const autonomy = AUTONOMY[s.autonomy] || AUTONOMY.ask;
    const acc = accountInfo(s.tradingview);
    const tvAcc = normalizeAccount(s.tradingview?.account);
    const ai = s.ai || {};
    const voice = s.voice || {};
    const vs = voiceStateInfo(voice.state);
    const mem = s.memory || {};

    const modeExtra = h('div', { class: 'stat-chips' },
      tag(autonomy.label, s.autonomy === 'auto' ? 'accent' : 'neutral'),
      s.data_source === 'demo' ? tag('Demo-Daten', 'warn') : null);

    const tvExtra = s.tradingview?.connected
      ? h('div', { class: 'stat-chips' },
        tvAcc.broker ? tag(tvAcc.broker, 'neutral') : null,
        tvAcc.label ? tag(tvAcc.label, 'neutral') : null,
        tvAcc.balance !== null ? tag(fmtMoney(tvAcc.balance, tvAcc.currency), 'neutral') : null)
      : h('button', { type: 'button', class: 'stat-link', onClick: () => ctx.navigate('tradingview') }, 'Verbindung prüfen', icon('external', 12));

    const aiText = ai.last_error
      ? `Letzter Fehler: ${ai.last_error}`
      : ai.last_used ? `Zuletzt genutzt: ${ai.last_used}` : 'Bereit für Chat und Analysen.';

    const voiceBits = [];
    if (voice.wake_enabled !== undefined) voiceBits.push(voice.wake_enabled ? '„Hey Vincent“ an' : 'Aktivierungswort aus');
    if (voice.tts) voiceBits.push(`Ausgabe: ${voice.tts}`);
    const voiceText = voice.error ? voice.error : voiceBits.join(' · ') || 'Sprachsteuerung';

    replaceChildren(stats,
      statusCard({ label: 'Modus', value: mode.name, tone: mode.tone, icon: 'power', text: mode.info, extra: modeExtra }),
      statusCard({ label: 'TradingView-Konto', value: acc.title, tone: acc.tone, icon: 'monitor', text: acc.text, extra: tvExtra }),
      statusCard({
        label: 'KI', value: ai.chain || 'Nicht eingerichtet', tone: ai.last_error ? 'warn' : ai.chain ? 'accent' : 'neutral',
        icon: 'cpu', text: aiText,
      }),
      statusCard({ label: 'Stimme', value: vs.label, tone: voice.error ? 'bad' : vs.tone, icon: 'wave', text: voiceText }),
      statusCard({
        label: 'Gedächtnis', value: `${fmtInt(mem.total ?? 0)} Erinnerungen`, tone: 'violet', icon: 'brain',
        text: 'Lernt aus jedem Gespräch, verdichtet nachts, vergisst Unwichtiges.',
        extra: h('button', { type: 'button', class: 'stat-link', onClick: () => ctx.navigate('gedaechtnis') }, 'Öffnen', icon('external', 12)),
      }));
    for (const v of stats.querySelectorAll('.stat-value')) v.classList.toggle('is-long', v.textContent.length > 16);
  }

  // ------------------------------------------------------------ Vorschläge
  function renderProposals(list) {
    const active = list.filter((p) => ['pending', 'approved', 'executing'].includes(p.status || 'pending'));
    const pending = active.filter((p) => (p.status || 'pending') === 'pending').length;
    propsCount.hidden = pending === 0;
    propsCount.textContent = `${pending} offen`;
    if (!active.length) {
      replaceChildren(propsBody, emptyState('check', 'Keine offenen Vorschläge',
        'Sobald Vincent eine gute Gelegenheit findet, erscheint sie hier – mit Begründung und Freigabe-Knopf.'));
      return;
    }
    replaceChildren(propsBody, active.map(proposalCard));
    tick();
  }

  function proposalCard(p) {
    const side = sideInfo(p.side);
    const live = p.account_kind === 'live';
    const status = p.status || 'pending';
    const st = proposalStatusInfo(status);
    const isLimit = p.order_type === 'limit';
    const metric = (label, value, cls = '') => h('div', { class: 'pm' },
      h('span', { class: 'pm-label', text: label }), h('span', { class: `pm-value num mono ${cls}`, text: value }));

    const approveBtn = button(live ? 'Mit Echtgeld freigeben' : 'Freigeben', {
      variant: live ? 'danger' : 'primary', icon: 'check', onClick: () => approve(p, approveBtn),
    });
    const rejectBtn = button('Ablehnen', { variant: 'ghost', icon: 'x', onClick: () => reject(p, rejectBtn) });

    return h('article', { class: `proposal${live ? ' is-live' : ''}`, dataset: { id: p.id } },
      h('div', { class: 'proposal-head' },
        h('span', { class: `side-chip tone-${side.tone}` }, icon(side.tone === 'good' ? 'arrowUp' : 'arrowDown', 14), side.label),
        h('span', { class: 'proposal-sym', text: p.symbol || p.tv_symbol || '–' }),
        live ? pill('ECHTGELD', 'bad', 'alert') : pill('Paper-Konto', 'good'),
        h('span', { class: 'spacer' }),
        status === 'pending' && p.expires
          ? h('span', { class: 'countdown', title: 'Vorschlag verfällt automatisch' }, icon('clock', 14),
            h('span', { class: 'num', dataset: { expires: String(p.expires) }, text: fmtCountdown(p.expires) }))
          : pill(st.label, st.tone)),
      h('div', { class: 'proposal-metrics' },
        metric('Menge', fmtQty(p.qty)),
        metric('Order', isLimit ? `Limit ${fmtPrice(p.limit_price)}` : 'Market'),
        metric('Stop-Loss', fmtPrice(p.stop_loss), 'txt-bad'),
        metric('Take-Profit', fmtPrice(p.take_profit), 'txt-good'),
        metric('Score', fmtSigned(p.score))),
      p.reason ? h('p', { class: 'proposal-reason', text: p.reason }) : null,
      status === 'pending' ? h('div', { class: 'proposal-actions' }, rejectBtn, approveBtn) : null);
  }

  async function approve(p, btn) {
    if (p.account_kind === 'live') {
      const side = sideInfo(p.side).label.toUpperCase();
      const ok = await ctx.ui.prompt({
        title: 'Echtgeld-Order freigeben',
        tone: 'bad',
        icon: 'alert',
        text: `${side} ${fmtQty(p.qty)} ${p.symbol} – diese Order läuft über dein echtes Broker-Konto in TradingView. `
          + 'Vincent prüft vorher noch einmal das aktive Konto und das Order-Ticket.',
        input: { label: 'Tippe ECHTGELD zur Bestätigung', placeholder: 'ECHTGELD', expected: 'ECHTGELD' },
        confirmLabel: 'Echtgeld-Order freigeben',
      });
      if (ok === null) return;
    }
    const res = await ctx.ui.run(btn, () => ctx.api.post(`/api/proposals/${encodeURIComponent(p.id)}/approve`, {}));
    if (res) {
      ctx.ui.toast(res.text || 'Freigegeben.', 'good');
      ctx.refreshState();
    }
  }

  async function reject(p, btn) {
    const res = await ctx.ui.run(btn, () => ctx.api.post(`/api/proposals/${encodeURIComponent(p.id)}/reject`, {}));
    if (res) {
      ctx.ui.toast(res.text || 'Abgelehnt.', 'info');
      ctx.refreshState();
    }
  }

  function tick() {
    for (const n of propsBody.querySelectorAll('[data-expires]')) {
      const txt = fmtCountdown(n.dataset.expires);
      n.textContent = txt;
      n.closest('.countdown')?.classList.toggle('is-urgent', txt === 'abgelaufen' || /^0:/.test(txt));
    }
  }

  // ------------------------------------------------------------ Positionen
  function renderPositions(list) {
    if (!list.length) {
      replaceChildren(posBody, emptyState('layers', 'Keine offenen Positionen', 'Offene Trades aus TradingView erscheinen hier mit Stop, Ziel und Gewinn/Verlust.'));
      return;
    }
    replaceChildren(posBody, table([
      { label: 'Symbol', render: (p) => h('span', { class: 'sym-name', text: p.symbol || '–' }) },
      { label: 'Richtung', render: (p) => { const s = sideInfo(p.side); return h('span', { class: `chip tone-${s.tone}`, text: s.short }); } },
      { label: 'Menge', class: 'num-col', render: (p) => h('span', { class: 'num mono', text: fmtQty(p.qty) }) },
      { label: 'Einstieg', class: 'num-col', render: (p) => h('span', { class: 'num mono', text: fmtPrice(p.entry) }) },
      { label: 'Kurs', class: 'num-col', render: (p) => h('span', { class: 'num mono', text: fmtPrice(p.price) }) },
      { label: 'Stop', class: 'num-col', render: (p) => h('span', { class: 'num mono txt-dim', text: fmtPrice(p.stop) }) },
      { label: 'Ziel', class: 'num-col', render: (p) => h('span', { class: 'num mono txt-dim', text: fmtPrice(p.take_profit) }) },
      { label: 'G/V', class: 'num-col', render: (p) => {
        const v = positionPnl(p);
        return h('span', { class: `num mono strong ${v === null ? '' : v >= 0 ? 'txt-good' : 'txt-bad'}`, text: fmtSigned(v) });
      } },
      { label: 'Konto', render: (p) => (p.mode === 'live' ? tag('Echtgeld', 'bad') : tag('Paper', 'good')) },
      { label: 'Seit', render: (p) => h('span', { class: 'txt-dim', text: timeAgo(p.opened) }) },
    ], list, { caption: 'Offene Positionen' }));
  }

  // -------------------------------------------------------------- Analysen
  function renderAnalyses(list) {
    const sorted = [...list].sort((a, b) => Math.abs(toNum(b.score) || 0) - Math.abs(toNum(a.score) || 0));
    replaceChildren(anaBody, analysesTable(sorted, { onSelect: (a) => ctx.navigate('markt', { symbol: a.symbol }) }));
  }

  // ------------------------------------------------------------- Lebenszyklus
  let timer = null;

  function update(s) {
    if (!s) return;
    if (changed('banners', [s.halted, s.mode, s.entries_paused])) renderBanners(s);
    if (changed('stats', [s.mode, s.autonomy, s.tradingview, s.ai, s.voice, s.memory, s.data_source])) renderStats(s);
    if (changed('props', s.proposals || [])) renderProposals(s.proposals || []);
    if (changed('pos', s.positions || [])) renderPositions(s.positions || []);
    if (changed('ana', s.analyses || [])) renderAnalyses(s.analyses || []);
  }

  return {
    el,
    show() {
      update(ctx.getState());
      clearInterval(timer);
      timer = setInterval(tick, 1000);
    },
    hide() {
      clearInterval(timer);
      timer = null;
    },
    update,
    event(type, data) {
      if (type === 'chat') chat.add(data);
    },
  };
}

// --------------------------------------------------------------------------
// Schnell-Chat
// --------------------------------------------------------------------------

const SOURCE_LABELS = { voice: 'per Stimme', telegram: 'Telegram', text: '' };

function createChat(ctx) {
  const messages = [];
  const log = h('div', { class: 'chat-log', role: 'log', 'aria-live': 'polite', 'aria-label': 'Gesprächsverlauf' });
  const typing = h('div', { class: 'chat-typing', hidden: true },
    h('span', { class: 'dots' }, h('i'), h('i'), h('i')), h('span', { text: 'Vincent denkt nach …' }));
  const input = h('textarea', {
    class: 'chat-input', rows: '1', placeholder: 'Frag Vincent etwas … (Enter senden)', 'aria-label': 'Nachricht an Vincent',
  });
  const sendBtn = iconButton('send', 'Senden', { variant: 'primary', onClick: () => send() });
  const micBtn = iconButton('mic', 'Sprechen (Push-to-Talk)', {
    onClick: async () => {
      const r = await ctx.ui.run(micBtn, () => ctx.api.post('/api/voice/listen', {}));
      if (r) ctx.ui.toast('Vincent hört zu – sprich jetzt.', 'accent', { timeout: 3000 });
    },
  });

  const welcome = h('div', { class: 'chat-welcome' },
    h('div', { class: 'chat-orb', 'aria-hidden': 'true' }),
    h('div', { class: 'chat-welcome-title', text: 'Hallo, ich bin Vincent.' }),
    h('div', { class: 'chat-welcome-text', text: 'Frag mich nach dem Markt, einer Analyse oder sag mir, was ich mir merken soll.' }),
    h('div', { class: 'chat-suggest' },
      ['Wie steht BTC gerade?', 'Was hast du heute gelernt?', 'Erklär mir die Marktphase von ETH'].map((q) =>
        h('button', { type: 'button', class: 'suggest', text: q, onClick: () => { input.value = q; send(); } }))));
  log.append(welcome);

  const autoGrow = () => {
    input.style.height = 'auto';
    input.style.height = `${Math.min(120, input.scrollHeight)}px`;
  };
  input.addEventListener('input', autoGrow);
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
      ev.preventDefault();
      send();
    }
  });

  const el = card({ class: 'chat-card', title: 'Mit Vincent schreiben', icon: 'chat' },
    log, typing,
    h('div', { class: 'chat-compose' }, micBtn, input, sendBtn));

  function add(m) {
    const text = String(m?.text ?? '').trim();
    if (!text) return;
    const role = m.role === 'user' ? 'user' : 'assistant';
    const now = Date.now();
    const dup = messages.slice(-6).some((x) => x.role === role && x.text === text && now - x.at < 60000);
    if (dup) return;
    const msg = { role, text, source: m.source || 'text', at: now };
    messages.push(msg);
    welcome.remove();
    const bubble = h('div', { class: `msg msg-${role}` },
      h('div', { class: 'msg-bubble', text }),
      h('div', { class: 'msg-meta' },
        h('span', { text: role === 'user' ? 'Du' : 'Vincent' }),
        SOURCE_LABELS[msg.source] ? h('span', { text: ` · ${SOURCE_LABELS[msg.source]}` }) : null,
        h('span', { text: ` · ${new Date(now).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })}` })));
    log.append(bubble);
    while (messages.length > 120) {
      messages.shift();
      log.querySelector('.msg')?.remove();
    }
    log.scrollTop = log.scrollHeight;
  }

  async function send() {
    const text = input.value.trim();
    if (!text || sendBtn.disabled) return;
    input.value = '';
    autoGrow();
    add({ role: 'user', text, source: 'text' });
    typing.hidden = false;
    log.scrollTop = log.scrollHeight;
    const res = await ctx.ui.run(sendBtn, () => ctx.api.post('/api/chat', { text }), { errorTitle: 'Nachricht nicht gesendet' });
    typing.hidden = true;
    if (res?.reply) add({ role: 'assistant', text: res.reply, source: 'text' });
    input.focus();
  }

  return { el, add };
}
