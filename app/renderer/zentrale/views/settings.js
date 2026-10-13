// Ansicht "Einstellungen": Zugangsdaten (maskiert), generiertes Formular aller Bereiche,
// separate Sicherheits-Box für Echtgeld, Speichern nur geänderter Werte.

import { h, icon, button, iconButton, card, viewHeader, emptyState, replaceChildren, toggle, select, spinner } from './dom.js';
import {
  buildSettingsForm, flattenFields, fieldToInput, parseFieldValue, valuesEqual, collectSettingsChanges,
  collectSecretChanges, getPath,
} from './settings-form.js';
import { fmtNumber } from './format.js';

export function createSettings(ctx) {
  let settings = null;
  let secrets = {};
  let sections = [];
  let fields = [];
  let raws = {};
  let secretEntries = {};
  let tab = 'secrets';
  const controls = new Map();

  const tabs = h('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Bereiche' });
  const panel = h('div', { class: 'tab-panel', role: 'tabpanel' });
  const safety = h('div');
  const notesBox = h('div', { class: 'notes-box', hidden: true });
  const dirtyText = h('span', { class: 'savebar-text', text: 'Keine Änderungen' });
  const discardBtn = button('Verwerfen', { variant: 'ghost', onClick: () => discard() });
  const saveBtn = button('Speichern', { variant: 'primary', icon: 'check', onClick: () => save() });
  const savebar = h('div', { class: 'savebar' }, icon('info', 16), dirtyText, h('span', { class: 'spacer' }), discardBtn, saveBtn);

  const el = h('div', { class: 'view view-settings' },
    viewHeader('Einstellungen', 'Alle Einstellungen von Vincent. Änderungen werden erst mit „Speichern“ übernommen.'),
    notesBox,
    tabs,
    panel,
    safety,
    savebar);

  // --------------------------------------------------------------- Tabs
  function renderTabs() {
    const items = [{ id: 'secrets', title: 'Zugangsdaten' }, ...sections.map((s) => ({ id: s.id, title: s.title }))];
    replaceChildren(tabs, items.map((t) => {
      const b = h('button', {
        type: 'button', class: 'tab', role: 'tab', 'aria-selected': String(t.id === tab), dataset: { tab: t.id },
        tabIndex: t.id === tab ? 0 : -1,
        onClick: () => selectTab(t.id),
      }, h('span', { text: t.title }), h('span', { class: 'tab-dot', 'aria-hidden': 'true' }));
      return b;
    }));
    updateDirty();
  }

  tabs.addEventListener('keydown', (ev) => {
    const list = [...tabs.querySelectorAll('.tab')];
    const idx = list.indexOf(document.activeElement);
    if (idx < 0) return;
    let next = -1;
    if (ev.key === 'ArrowRight') next = (idx + 1) % list.length;
    if (ev.key === 'ArrowLeft') next = (idx - 1 + list.length) % list.length;
    if (next >= 0) {
      ev.preventDefault();
      selectTab(list[next].dataset.tab);
      list[next].focus();
    }
  });

  function selectTab(id) {
    tab = id;
    for (const b of tabs.querySelectorAll('.tab')) {
      const on = b.dataset.tab === id;
      b.setAttribute('aria-selected', String(on));
      b.tabIndex = on ? 0 : -1;
    }
    renderPanel();
  }

  // ------------------------------------------------------------- Panel
  function renderPanel() {
    if (tab === 'secrets') {
      replaceChildren(panel, secretsCard());
      return;
    }
    const sec = sections.find((s) => s.id === tab) || sections[0];
    if (!sec) {
      replaceChildren(panel, emptyState('sliders', 'Keine Einstellungen', ''));
      return;
    }
    replaceChildren(panel, card({ title: sec.title, subtitle: sec.description, class: 'settings-card' },
      h('div', { class: 'form-grid' }, sec.fields.map(fieldControl))));
  }

  function fieldControl(f) {
    const raw = raws[f.path];
    let control;
    let wrapClass = 'sfield';
    if (f.type === 'bool') {
      const t = toggle({ label: f.label, hint: f.hint, checked: Boolean(raw), onChange: (v) => setRaw(f, v) });
      controls.set(f.path, t.input);
      return h('div', { class: 'sfield sfield-bool', dataset: { path: f.path } }, t.el, h('div', { class: 'sfield-err', role: 'alert' }));
    }
    if (f.type === 'enum') {
      control = select(f.options, String(raw), { class: 'input' });
      control.addEventListener('change', () => setRaw(f, control.value));
    } else if (f.type === 'textarea') {
      control = h('textarea', { class: 'input', rows: '4', value: raw });
      control.addEventListener('input', () => setRaw(f, control.value));
      wrapClass += ' sfield-wide';
    } else {
      const numeric = f.type === 'int' || f.type === 'float';
      control = h('input', {
        class: `input${numeric ? ' num' : ''}`, type: 'text', value: raw, spellcheck: 'false', autocomplete: 'off',
        inputmode: numeric ? (f.type === 'int' ? 'numeric' : 'decimal') : null,
        placeholder: f.type === 'list' ? 'durch Komma getrennt' : '',
      });
      control.addEventListener('input', () => setRaw(f, control.value));
      if (f.type === 'list') wrapClass += ' sfield-wide';
    }
    control.id = `set-${f.path.replace(/\./g, '-')}`;
    controls.set(f.path, control);
    const range = f.min !== undefined ? `Erlaubt: ${fmtNumber(f.min, f.type === 'int' ? 0 : 2)} – ${fmtNumber(f.max, f.type === 'int' ? 0 : 2)}` : '';
    const hint = [f.hint, range].filter(Boolean).join(' · ');
    const wrap = h('div', { class: wrapClass, dataset: { path: f.path } },
      h('label', { class: 'field-label', for: control.id, text: f.label }),
      control,
      hint ? h('div', { class: 'field-hint', text: hint }) : null,
      h('div', { class: 'sfield-err', role: 'alert' }));
    markField(f, wrap);
    return wrap;
  }

  function setRaw(f, value) {
    raws[f.path] = value;
    markField(f);
    updateDirty();
  }

  function markField(f, wrap = panel.querySelector(`[data-path="${CSS.escape(f.path)}"]`)) {
    if (!wrap) return;
    let dirty = false;
    let error = '';
    try {
      dirty = !valuesEqual(parseFieldValue(f, raws[f.path]), f.value);
    } catch (err) {
      error = err.message;
    }
    wrap.classList.toggle('is-dirty', dirty);
    wrap.classList.toggle('is-invalid', Boolean(error));
    const errEl = wrap.querySelector('.sfield-err');
    if (errEl) errEl.textContent = error;
  }

  // ------------------------------------------------------- Zugangsdaten
  function secretsCard() {
    const keys = Object.keys(secrets);
    if (!keys.length) return card({ title: 'Zugangsdaten' }, emptyState('key', 'Keine Zugangsdaten', 'Das Backend meldet keine Schlüssel.'));
    return card({
      title: 'Zugangsdaten & API-Keys', icon: 'key', class: 'settings-card',
      subtitle: 'Werden sicher im macOS-Schlüsselbund gespeichert. Gespeicherte Werte werden nie angezeigt – nur, ob sie gesetzt sind.',
    }, h('div', { class: 'secret-list' }, keys.map(secretRow)));
  }

  function secretRow(key) {
    const meta = secrets[key] || {};
    const entry = secretEntries[key] || (secretEntries[key] = { value: '', remove: false });
    const input = h('input', {
      class: 'input mono', type: 'password', value: entry.value, autocomplete: 'off', spellcheck: 'false',
      placeholder: meta.set ? 'Neuen Wert eingeben, um zu ersetzen' : 'Wert eingeben',
      'aria-label': meta.label || key,
    });
    const status = h('span', { class: 'secret-status' });
    const removeBtn = button('', { variant: 'ghost', size: 'sm' });
    const eye = iconButton('eye', 'Eingabe anzeigen', {
      onClick: () => { input.type = input.type === 'password' ? 'text' : 'password'; },
    });
    const row = h('div', { class: 'secret' },
      h('div', { class: 'secret-main' },
        h('div', { class: 'secret-top' }, h('span', { class: 'secret-name', text: meta.label || key }), status),
        h('div', { class: 'secret-input' }, input, eye, meta.set ? removeBtn : null)));
    const paint = () => {
      row.classList.toggle('is-dirty', Boolean(entry.value.trim()) || entry.remove);
      if (entry.remove) {
        replaceChildren(status, h('span', { class: 'chip tone-bad', text: 'wird beim Speichern entfernt' }));
      } else if (entry.value.trim()) {
        replaceChildren(status, h('span', { class: 'chip tone-accent', text: 'neuer Wert – noch nicht gespeichert' }));
      } else if (meta.set) {
        replaceChildren(status, h('span', { class: 'chip tone-good', text: 'gesetzt ✓' }),
          meta.preview ? h('span', { class: 'secret-preview mono', text: meta.preview }) : null);
      } else {
        replaceChildren(status, h('span', { class: 'chip tone-neutral', text: 'nicht gesetzt' }));
      }
      replaceChildren(removeBtn, h('span', { class: 'btn-label', text: entry.remove ? 'Rückgängig' : 'Entfernen' }));
    };
    input.addEventListener('input', () => {
      entry.value = input.value;
      if (entry.value) entry.remove = false;
      paint();
      updateDirty();
    });
    removeBtn.addEventListener('click', () => {
      entry.remove = !entry.remove;
      if (entry.remove) {
        entry.value = '';
        input.value = '';
      }
      paint();
      updateDirty();
    });
    paint();
    return row;
  }

  // ----------------------------------------------------------- Sicherheit
  function renderSafety() {
    const unlocked = Boolean(settings?.live_trading_unlocked);
    const maxUsd = getPath(settings, 'risk.max_live_order_usd');
    const t = toggle({
      label: 'Echtgeld-Handel freischalten',
      hint: unlocked ? 'Freigeschaltet – der LIVE-Modus kann aktiviert werden.' : 'Gesperrt – der LIVE-Modus lässt sich nicht aktivieren.',
      checked: unlocked,
      onChange: (v, input) => setLive(v, input),
    });
    const pilot = unlocked && Boolean(settings?.live_autopilot_unlocked);
    const ap = toggle({
      label: 'Echtgeld-Autopilot erlauben',
      hint: pilot
        ? 'Aktiv – mit Autonomie „Handelt selbst“ tradet Vincent im LIVE-Modus ohne Einzel-Freigabe (klein anfangen, Obergrenzen, Notbremse).'
        : 'Aus – jede Echtgeld-Order braucht dein „Ja, mit Echtgeld“.',
      checked: pilot,
      disabled: !unlocked,
      onChange: (v, input) => setAutopilot(v, input),
    });
    replaceChildren(safety, h('section', { class: `safety${unlocked ? ' is-unlocked' : ''}` },
      h('div', { class: 'safety-head' }, icon('shield', 20), h('h2', { text: 'Sicherheit' })),
      h('p', { class: 'safety-text', text: 'Erst wenn diese Sperre aufgehoben ist, lässt sich der LIVE-Modus überhaupt einschalten. '
        + 'Ohne Autopilot braucht jede Echtgeld-Order deine ausdrückliche Freigabe. Vincent prüft vorher immer das aktive Konto, '
        + `und es gilt die Obergrenze von ${maxUsd !== undefined ? fmtNumber(maxUsd, 0) : '–'} USD je Order.` }),
      t.el, ap.el));
  }

  async function setAutopilot(on, input) {
    if (on) {
      const typed = await ctx.ui.prompt({
        title: 'Echtgeld-Autopilot erlauben?',
        tone: 'bad',
        icon: 'alert',
        text: 'Im LIVE-Modus mit Autonomie „Handelt selbst“ setzt Vincent dann Echtgeld-Trades ohne Rückfrage – '
          + 'klein beginnend, innerhalb deiner Risiko-Grenzen und der Obergrenze je Order. Verluste sind möglich, '
          + 'es gibt keine Gewinngarantie. „Stopp“ oder Notstopp beendet alles. Tippe AUTOPILOT, wenn du das willst.',
        input: { label: 'Bestätigung', placeholder: 'AUTOPILOT', expected: 'AUTOPILOT' },
        confirmLabel: 'Autopilot erlauben',
      });
      if (typed === null) {
        input.checked = false;
        return;
      }
    }
    const res = await ctx.ui.run(null, () => ctx.api.post('/api/settings', { settings: { live_autopilot_unlocked: on } }));
    if (!res) {
      input.checked = !on;
      return;
    }
    settings.live_autopilot_unlocked = on;
    ctx.ui.toast(on ? 'Echtgeld-Autopilot ist erlaubt.' : 'Echtgeld-Autopilot ist aus – Vincent fragt wieder vor jeder Echtgeld-Order.', on ? 'warn' : 'good');
    showNotes(res.notes);
    renderSafety();
    ctx.refreshState();
  }

  async function setLive(on, input) {
    if (on) {
      const typed = await ctx.ui.prompt({
        title: 'Echtgeld-Handel freischalten?',
        tone: 'bad',
        icon: 'alert',
        text: 'Damit kann Vincent – nach deiner Freigabe pro Trade – mit echtem Geld über deinen Broker in TradingView handeln. '
          + 'Verluste sind möglich. Tippe FREISCHALTEN, wenn du das wirklich willst.',
        input: { label: 'Bestätigung', placeholder: 'FREISCHALTEN', expected: 'FREISCHALTEN' },
        confirmLabel: 'Echtgeld freischalten',
      });
      if (typed === null) {
        input.checked = false;
        return;
      }
    }
    const res = await ctx.ui.run(null, () => ctx.api.post('/api/settings', { settings: on ? { live_trading_unlocked: true } : { live_trading_unlocked: false, live_autopilot_unlocked: false } }));
    if (!res) {
      input.checked = !on;
      return;
    }
    settings.live_trading_unlocked = on;
    if (!on) settings.live_autopilot_unlocked = false;
    ctx.ui.toast(on ? 'Echtgeld-Handel ist freigeschaltet.' : 'Echtgeld-Handel ist wieder gesperrt.', on ? 'warn' : 'good');
    showNotes(res.notes);
    renderSafety();
  }

  // ------------------------------------------------------- Speichern
  function updateDirty() {
    const { count, errors } = collectSettingsChanges(fields, raws);
    const sec = Object.keys(collectSecretChanges(secretEntries)).length;
    const total = count + sec;
    savebar.classList.toggle('is-dirty', total > 0);
    savebar.classList.toggle('is-invalid', errors.length > 0);
    dirtyText.textContent = errors.length
      ? `${errors.length} ungültige Eingabe${errors.length === 1 ? '' : 'n'} – bitte korrigieren`
      : total ? `${total} ungespeicherte Änderung${total === 1 ? '' : 'en'}` : 'Keine Änderungen';
    discardBtn.disabled = total === 0 && errors.length === 0;
    for (const b of tabs.querySelectorAll('.tab')) {
      const id = b.dataset.tab;
      let dirty = false;
      if (id === 'secrets') dirty = sec > 0;
      else {
        const sf = sections.find((s) => s.id === id)?.fields || [];
        const r = collectSettingsChanges(sf, raws);
        dirty = r.count > 0 || r.errors.length > 0;
      }
      b.classList.toggle('is-dirty', dirty);
    }
  }

  function showNotes(notes) {
    const list = Array.isArray(notes) ? notes.filter(Boolean) : [];
    notesBox.hidden = !list.length;
    replaceChildren(notesBox,
      h('div', { class: 'notes-head' }, icon('info', 16), h('strong', { text: 'Hinweise vom Speichern' }),
        h('span', { class: 'spacer' }), iconButton('x', 'Hinweise schließen', { onClick: () => { notesBox.hidden = true; } })),
      h('ul', null, list.map((n) => h('li', { text: String(n) }))));
  }

  async function save() {
    const { changes, errors, count } = collectSettingsChanges(fields, raws);
    if (errors.length) {
      const first = errors[0];
      const sec = sections.find((s) => s.fields.some((f) => f.path === first.path));
      if (sec && sec.id !== tab) selectTab(sec.id);
      controls.get(first.path)?.focus();
      ctx.ui.toast(first.message, 'bad', { title: 'Bitte korrigieren' });
      return;
    }
    const sec = collectSecretChanges(secretEntries);
    if (!count && !Object.keys(sec).length) {
      ctx.ui.toast('Es gibt nichts zu speichern.', 'info', { timeout: 2500 });
      return;
    }
    const body = {};
    if (count) body.settings = changes;
    if (Object.keys(sec).length) body.secrets = sec;
    const res = await ctx.ui.run(saveBtn, () => ctx.api.post('/api/settings', body));
    if (!res) return;
    ctx.ui.toast('Einstellungen gespeichert.', 'good');
    showNotes(res.notes);
    secretEntries = {};
    await load();
  }

  function discard() {
    secretEntries = {};
    applySettings(settings, secrets);
    ctx.ui.toast('Änderungen verworfen.', 'info', { timeout: 2000 });
  }

  function applySettings(s, sec) {
    settings = s || {};
    secrets = sec || {};
    sections = buildSettingsForm(settings);
    fields = flattenFields(sections);
    raws = Object.fromEntries(fields.map((f) => [f.path, fieldToInput(f)]));
    controls.clear();
    if (tab !== 'secrets' && !sections.some((x) => x.id === tab)) tab = 'secrets';
    renderTabs();
    renderPanel();
    renderSafety();
    updateDirty();
  }

  async function load() {
    if (!settings) replaceChildren(panel, h('div', { class: 'loading-row' }, spinner(18), h('span', { text: 'Lade Einstellungen …' })));
    const res = await ctx.ui.run(null, () => ctx.api.get('/api/settings'));
    if (!res) return;
    applySettings(res.settings, res.secrets);
  }

  return {
    el,
    show() {
      // Nicht überschreiben, solange ungespeicherte Änderungen existieren.
      if (settings && savebar.classList.contains('is-dirty')) return;
      load();
    },
    hide() {},
    update() {},
  };
}
