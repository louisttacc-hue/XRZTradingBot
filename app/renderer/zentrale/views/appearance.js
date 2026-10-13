// Ansicht "Stimme & Aussehen": Skin-Galerie, Akzentfarbe, Größe, Rand, Untertitel,
// Sprachausgabe mit Stimmenliste und Probehören sowie macOS-Berechtigungen.

import { h, icon, button, card, viewHeader, emptyState, replaceChildren, toggle, segmented, field, tag, spinner } from './dom.js';
import { TTS_PROVIDERS, voiceSettingKey, normalizeAccent, permissionInfo } from './format.js';

/** Fallback, falls overlay/skins.js (noch) nicht ladbar ist und das Backend keine Liste schickt. */
export const DEFAULT_SKINS = [
  { id: 'mensch', name: 'Mensch', description: 'Kleiner animierter Mensch mit Headset, der winkt, gestikuliert und lauscht.' },
  { id: 'nebula', name: 'Nebula', description: 'Leuchtende Energiewolke in deiner Akzentfarbe.' },
  { id: 'hologramm', name: 'Hologramm', description: 'Flimmerndes Hologramm mit Scanlinien.' },
  { id: 'neon', name: 'Neon', description: 'Kräftiger Neonring mit Glow.' },
  { id: 'glas', name: 'Glas', description: 'Schlichte Glaskugel mit sanftem Schimmer.' },
  { id: 'roboter', name: 'Roboter', description: 'Freundliches Robotergesicht.' },
  { id: 'eigenes', name: 'Eigenes Bild', description: 'Dein eigenes Bild oder GIF als Avatar.' },
];

const ACCENTS = ['#7c5cff', '#4c8dff', '#22c3ee', '#2bd99f', '#ffb547', '#ff5d73', '#ff6bd6', '#e6e8f2'];

const PERMISSIONS = [
  { kind: 'microphone', keys: ['microphone', 'mic'], label: 'Mikrofon', text: 'Für „Hey Vincent“ und die Sprechtaste (⌥ Leertaste).' },
  { kind: 'screen', keys: ['screen', 'screen_recording', 'screenRecording'], label: 'Bildschirmaufnahme', text: 'Damit Vincent TradingView lesen und das aktive Konto prüfen kann.' },
  { kind: 'accessibility', keys: ['accessibility'], label: 'Bedienungshilfen', text: 'Damit Vincent in TradingView klicken und tippen darf.' },
];

/** Skin-Liste aus overlay/skins.js, Backend oder Fallback zusammenführen (nur id/name/description). */
export function mergeSkins(...lists) {
  const out = new Map();
  for (const list of lists) {
    if (!Array.isArray(list)) continue;
    for (const s of list) {
      const id = typeof s === 'string' ? s : s?.id;
      if (!id || out.has(id)) continue;
      const base = DEFAULT_SKINS.find((d) => d.id === id) || {};
      out.set(String(id), {
        id: String(id),
        name: String((typeof s === 'object' && s.name) || base.name || id),
        description: String((typeof s === 'object' && s.description) || base.description || ''),
      });
    }
  }
  return [...out.values()];
}

export function createAppearance(ctx) {
  let settings = null;
  let secrets = {};
  let skins = DEFAULT_SKINS;
  let overlaySkins = null;

  // ----------------------------------------------------------- Aussehen
  const gallery = h('div', { class: 'skin-grid', role: 'radiogroup', 'aria-label': 'Skin auswählen' });
  const accentInput = h('input', { type: 'color', class: 'color-input', 'aria-label': 'Akzentfarbe wählen' });
  const accentSwatches = h('div', { class: 'swatches' });
  const sizeInput = h('input', { type: 'range', min: '72', max: '220', step: '4', class: 'range', 'aria-label': 'Größe von Vincent' });
  const sizeVal = h('span', { class: 'num range-val' });
  const edgeCtl = segmented([{ value: 'left', label: 'Links' }, { value: 'right', label: 'Rechts' }], {
    label: 'Bildschirmrand', size: 'sm', onSelect: (v) => save({ appearance: { edge: v } }, `Vincent wohnt jetzt am ${v === 'left' ? 'linken' : 'rechten'} Rand.`),
  });
  const imageInput = h('input', { type: 'text', class: 'input', placeholder: '/Users/du/Bilder/vincent.png', spellcheck: 'false' });
  const imageApply = button('Übernehmen', { variant: 'secondary', size: 'sm', onClick: () => saveImage() });
  const imageRow = h('div', { class: 'image-row', hidden: true },
    field('Eigenes Bild (Pfad)', imageInput, 'PNG, GIF oder JPG. Am besten quadratisch mit transparentem Hintergrund.'), imageApply);
  const subtitles = toggle({
    label: 'Untertitel anzeigen', hint: 'Zeigt neben Vincent an, was er gerade sagt.',
    onChange: (v) => save({ appearance: { show_subtitles: v } }, v ? 'Untertitel an.' : 'Untertitel aus.'),
  });

  const previewOrb = h('div', { class: 'swatch preview-orb' }, h('span', { class: 'swatch-core' }));
  const preview = h('div', { class: 'preview-stage', 'aria-hidden': 'true' },
    h('div', { class: 'preview-window' }, h('i'), h('i'), h('i')), previewOrb);

  sizeInput.addEventListener('input', () => {
    sizeVal.textContent = `${sizeInput.value} px`;
    applyPreview();
  });
  sizeInput.addEventListener('change', () => save({ appearance: { size: Number(sizeInput.value) } }, `Größe: ${sizeInput.value} px`));
  accentInput.addEventListener('input', () => {
    ctx.setAccent(accentInput.value);
    markAccent(accentInput.value);
  });
  accentInput.addEventListener('change', () => save({ appearance: { accent: accentInput.value } }, 'Akzentfarbe gespeichert.'));
  imageInput.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') saveImage();
  });

  const looksCard = card({ class: 'looks-card', title: 'Aussehen', icon: 'palette', subtitle: 'So erscheint Vincent am Bildschirmrand.' },
    gallery,
    imageRow,
    h('div', { class: 'looks-controls' },
      h('div', { class: 'ctl' },
        h('div', { class: 'ctl-label', text: 'Akzentfarbe' }),
        h('div', { class: 'ctl-row' }, accentSwatches, accentInput)),
      h('div', { class: 'ctl' },
        h('div', { class: 'ctl-label' }, 'Größe ', sizeVal),
        sizeInput),
      h('div', { class: 'ctl ctl-inline' },
        h('div', { class: 'ctl-label', text: 'Bildschirmrand' }), edgeCtl.el),
      subtitles.el),
    preview);

  // -------------------------------------------------------------- Stimme
  const providerList = h('div', { class: 'provider-list', role: 'radiogroup', 'aria-label': 'Sprachausgabe' });
  const voicesBox = h('div', { class: 'voices' });
  const voicesInfo = h('span', { class: 'muted' });
  const sayText = h('input', { type: 'text', class: 'input', value: 'Hallo, ich bin Vincent. So klinge ich – passt das für dich?', 'aria-label': 'Testsatz' });
  const sayBtn = button('Probehören', { variant: 'primary', icon: 'play', size: 'sm', onClick: () => sayTest() });
  const elevenId = h('input', { type: 'text', class: 'input mono', placeholder: 'z. B. 21m00Tcm4TlvDq8ikWAM', spellcheck: 'false' });
  elevenId.addEventListener('change', () => save({ voice: { elevenlabs_voice_id: elevenId.value.trim() } }, 'ElevenLabs-Stimme gespeichert.'));
  const wake = toggle({
    label: 'Aktivierungswort „Hey Vincent“', hint: 'Vincent hört auf seinen Namen – ohne Taste.',
    onChange: (v) => save({ voice: { wake_word_enabled: v } }, v ? '„Hey Vincent“ ist an.' : '„Hey Vincent“ ist aus.'),
  });
  const speak = toggle({
    label: 'Antworten vorlesen', hint: 'Vincent spricht seine Antworten – auch bei getippten Fragen.',
    onChange: (v) => save({ voice: { speak_replies: v } }, v ? 'Antworten werden vorgelesen.' : 'Antworten werden nicht vorgelesen.'),
  });
  const reloadVoices = button('', { variant: 'ghost', size: 'sm', icon: 'refresh', title: 'Stimmen neu laden', 'aria-label': 'Stimmen neu laden', onClick: () => loadVoices(reloadVoices) });

  const voiceCard = card({ class: 'voice-card', title: 'Stimme', icon: 'mic', subtitle: 'Wie Vincent klingt und wann er zuhört.' },
    h('h3', { class: 'sub-title', text: 'Sprachausgabe' }),
    providerList,
    h('div', { class: 'row-between' }, h('h3', { class: 'sub-title', text: 'Stimmen' }), h('div', { class: 'row-gap' }, voicesInfo, reloadVoices)),
    voicesBox,
    h('div', { class: 'say-row' }, sayText, sayBtn),
    field('ElevenLabs-Stimmen-ID', elevenId, 'Optional: ID einer eigenen oder geklonten Stimme aus deinem ElevenLabs-Konto.'),
    h('div', { class: 'switch-list' }, wake.el, speak.el));

  // ------------------------------------------------------ Berechtigungen
  const permBody = h('div', { class: 'perm-list' });
  const permRefresh = button('Erneut prüfen', { variant: 'ghost', size: 'sm', icon: 'refresh', onClick: () => loadPermissions(permRefresh) });
  const permCard = card({ class: 'perm-card', title: 'macOS-Berechtigungen', icon: 'lock', subtitle: 'Ohne diese Freigaben kann Vincent nicht hören oder TradingView bedienen.', actions: permRefresh }, permBody);

  const el = h('div', { class: 'view view-appearance' },
    viewHeader('Stimme & Aussehen', 'Gib Vincent dein Lieblingsgesicht und die realistischste Stimme.'),
    h('div', { class: 'two-col' }, looksCard, voiceCard),
    permCard);

  // ------------------------------------------------------------ Logik
  async function save(partial, okText) {
    const res = await ctx.ui.run(null, () => ctx.api.post('/api/settings', { settings: partial }));
    if (!res) {
      await loadSettings();
      return null;
    }
    mergeInto(settings, partial);
    if (okText) ctx.ui.toast(okText, 'good', { timeout: 2200 });
    for (const n of res.notes || []) ctx.ui.toast(String(n), 'info');
    render();
    return res;
  }

  function mergeInto(target, partial) {
    if (!target) return;
    for (const [k, v] of Object.entries(partial)) {
      if (v && typeof v === 'object' && !Array.isArray(v)) {
        target[k] = target[k] || {};
        mergeInto(target[k], v);
      } else {
        target[k] = v;
      }
    }
  }

  function saveImage() {
    const path = imageInput.value.trim();
    save({ appearance: { custom_image: path } }, path ? 'Eigenes Bild übernommen.' : 'Eigenes Bild entfernt.');
  }

  function app() {
    return settings?.appearance || {};
  }

  function markAccent(color) {
    for (const b of accentSwatches.children) b.setAttribute('aria-pressed', String(b.dataset.color === color));
  }

  function applyPreview() {
    const a = app();
    previewOrb.className = `swatch preview-orb swatch-${a.skin || 'mensch'}`;
    const size = Number(sizeInput.value || a.size || 132);
    previewOrb.style.setProperty('--orb', `${Math.round(size * 0.62)}px`);
    preview.classList.toggle('edge-left', a.edge === 'left');
  }

  function render() {
    if (!settings) return;
    const a = app();
    const v = settings.voice || {};
    const accent = normalizeAccent(a.accent);

    replaceChildren(gallery, skins.map((s) => {
      const selected = s.id === (a.skin || 'mensch');
      const b = h('button', {
        type: 'button', class: 'skin', role: 'radio', 'aria-checked': String(selected),
        onClick: () => {
          if (s.id !== a.skin) save({ appearance: { skin: s.id } }, `Skin „${s.name}“ aktiv.`);
        },
      },
      h('span', { class: `swatch swatch-${s.id}` }, h('span', { class: 'swatch-core' })),
      h('span', { class: 'skin-name' }, h('span', { text: s.name }), selected ? icon('check', 14) : null),
      h('span', { class: 'skin-desc', text: s.description }));
      return b;
    }));
    imageRow.hidden = a.skin !== 'eigenes';
    if (document.activeElement !== imageInput) imageInput.value = a.custom_image || '';

    accentInput.value = accent;
    replaceChildren(accentSwatches, ACCENTS.map((c) => h('button', {
      type: 'button', class: 'swatch-dot', style: { '--c': c }, dataset: { color: c }, 'aria-label': `Akzentfarbe ${c}`,
      'aria-pressed': String(c === accent),
      onClick: () => {
        ctx.setAccent(c);
        save({ appearance: { accent: c } }, 'Akzentfarbe gespeichert.');
      },
    })));
    sizeInput.value = String(a.size ?? 132);
    sizeVal.textContent = `${sizeInput.value} px`;
    edgeCtl.set(a.edge || 'right');
    subtitles.input.checked = a.show_subtitles !== false;
    applyPreview();

    const provider = v.tts_provider || 'auto';
    replaceChildren(providerList, TTS_PROVIDERS.map((p) => {
      const needsKey = (p.id === 'elevenlabs' && secrets.elevenlabs_api_key && !secrets.elevenlabs_api_key.set)
        || (p.id === 'openai' && secrets.openai_api_key && !secrets.openai_api_key.set);
      return h('button', {
        type: 'button', class: 'provider', role: 'radio', 'aria-checked': String(p.id === provider),
        onClick: () => {
          if (p.id !== provider) save({ voice: { tts_provider: p.id } }, `Sprachausgabe: ${p.label}`).then(() => loadVoices());
        },
      },
      h('span', { class: 'radio-dot' }),
      h('span', { class: 'provider-main' },
        h('span', { class: 'provider-top' },
          h('span', { class: 'provider-name', text: p.label }),
          p.badge ? tag(p.badge, 'accent') : null,
          needsKey ? tag('API-Key fehlt', 'warn') : null),
        h('span', { class: 'provider-info', text: p.info })));
    }));

    if (document.activeElement !== elevenId) elevenId.value = v.elevenlabs_voice_id || '';
    wake.input.checked = v.wake_word_enabled !== false;
    speak.input.checked = v.speak_replies !== false;
    renderVoices();
  }

  let voices = null;
  function renderVoices() {
    if (!voices) {
      replaceChildren(voicesBox, h('div', { class: 'loading-row' }, spinner(16), h('span', { text: 'Lade Stimmen …' })));
      return;
    }
    const key = voiceSettingKey(voices.provider);
    const current = key ? settings?.voice?.[key] || '' : '';
    voicesInfo.textContent = voices.provider ? `Anbieter: ${providerLabel(voices.provider)}` : '';
    const list = Array.isArray(voices.voices) ? voices.voices : [];
    if (!list.length) {
      replaceChildren(voicesBox, emptyState('mic', 'Keine Stimmen gefunden', 'Für diesen Anbieter sind keine Stimmen verfügbar – prüfe API-Key bzw. Installation.'));
      return;
    }
    replaceChildren(voicesBox, h('div', { class: 'voice-list', role: 'radiogroup', 'aria-label': 'Stimme wählen' }, list.map((vo) => {
      const selected = current ? vo.id === current : false;
      return h('button', {
        type: 'button', class: 'voice', role: 'radio', 'aria-checked': String(selected), title: vo.id,
        onClick: async () => {
          if (!key) return;
          if (!selected) await save({ voice: { [key]: vo.id } }, `Stimme „${vo.name || vo.id}“ gewählt.`);
        },
      },
      h('span', { class: 'radio-dot' }),
      h('span', { class: 'voice-name', text: vo.name || vo.id }),
      vo.lang ? h('span', { class: 'voice-lang', text: vo.lang }) : null);
    })));
  }

  function providerLabel(id) {
    return TTS_PROVIDERS.find((p) => p.id === id)?.label || id;
  }

  async function sayTest() {
    const text = sayText.value.trim() || 'Hallo, ich bin Vincent.';
    const res = await ctx.ui.run(sayBtn, () => ctx.api.post('/api/voice/say', { text }));
    if (res) ctx.ui.toast('Vincent spricht …', 'accent', { timeout: 2000 });
  }

  async function loadVoices(btn = null) {
    voices = null;
    renderVoices();
    const res = await ctx.ui.run(btn, () => ctx.api.get('/api/voice/voices'));
    voices = res || { provider: '', voices: [] };
    renderVoices();
  }

  async function loadSettings() {
    const res = await ctx.ui.run(null, () => ctx.api.get('/api/settings'));
    if (!res) return;
    settings = res.settings || {};
    secrets = res.secrets || {};
    skins = mergeSkins(overlaySkins, res.skins, DEFAULT_SKINS);
    render();
  }

  async function loadOverlaySkins() {
    if (overlaySkins) return;
    try {
      const mod = await import('../../overlay/skins.js');
      if (Array.isArray(mod.SKINS)) overlaySkins = mod.SKINS.map((s) => ({ id: s.id, name: s.name, description: s.description }));
    } catch {
      overlaySkins = [];
    }
  }

  // Berechtigungen
  async function loadPermissions(btn = null) {
    const bridge = globalThis.window?.vincent;
    if (!bridge?.getPermissions) {
      replaceChildren(permBody, PERMISSIONS.map((p) => permRow(p, null, false)),
        h('p', { class: 'muted small', text: 'Berechtigungen lassen sich nur in der Vincent-App prüfen.' }));
      return;
    }
    const res = await ctx.ui.run(btn, () => Promise.resolve(bridge.getPermissions()));
    const perms = res && typeof res === 'object' ? res : {};
    replaceChildren(permBody, PERMISSIONS.map((p) => {
      const key = p.keys.find((k) => k in perms);
      return permRow(p, key ? perms[key] : undefined, true);
    }));
  }

  function permRow(p, value, available) {
    const info = permissionInfo(value);
    const bridge = globalThis.window?.vincent;
    const actions = [];
    if (available && p.kind === 'microphone' && info.granted !== true && bridge?.requestMicrophone) {
      const ask = button('Zugriff erlauben', { variant: 'primary', size: 'sm' });
      ask.addEventListener('click', async () => {
        await ctx.ui.run(ask, () => Promise.resolve(bridge.requestMicrophone()));
        loadPermissions();
      });
      actions.push(ask);
    }
    const open = button('Systemeinstellungen öffnen', { variant: 'secondary', size: 'sm', icon: 'external', disabled: !available || !bridge?.openPermissionSettings });
    open.addEventListener('click', () => ctx.ui.run(open, () => Promise.resolve(bridge.openPermissionSettings(p.kind))));
    actions.push(open);
    return h('div', { class: 'perm' },
      h('span', { class: `perm-icon tone-${info.tone}` }, icon(info.granted === true ? 'check' : info.granted === false ? 'alert' : 'info', 16)),
      h('div', { class: 'perm-main' },
        h('div', { class: 'perm-top' }, h('span', { class: 'perm-name', text: p.label }), h('span', { class: `chip tone-${info.tone}`, text: available ? info.label : 'Nur in der App' })),
        h('div', { class: 'perm-text', text: p.text })),
      h('div', { class: 'perm-actions' }, actions));
  }

  return {
    el,
    async show() {
      await loadOverlaySkins();
      loadSettings();
      loadVoices();
      loadPermissions();
    },
    hide() {},
    update(state) {
      // Skin/Farbe können auch per Stimme oder Telegram geändert werden.
      if (!settings || !state?.skin) return;
      const a = app();
      if ((state.skin.id && state.skin.id !== a.skin) || (state.skin.accent && normalizeAccent(state.skin.accent) !== normalizeAccent(a.accent))) {
        a.skin = state.skin.id || a.skin;
        a.accent = state.skin.accent || a.accent;
        render();
      }
    },
  };
}
