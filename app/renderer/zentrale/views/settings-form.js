// Reine Logik für das Einstellungs-Formular: Felder aus dem Settings-Objekt erzeugen,
// Eingaben typgerecht einlesen und nur geänderte Werte einsammeln. Kein DOM-Zugriff.

import { toNum } from './format.js';

export const SECTION_ORDER = ['trading', 'risk', 'exchange', 'ai', 'voice', 'memory', 'appearance', 'telegram',
  'tradingview', 'mac', 'web'];

export const SECTIONS = {
  trading: { title: 'Trading', description: 'Was Vincent beobachtet, wann er einsteigt und wie selbstständig er handelt.' },
  risk: { title: 'Risiko', description: 'Positionsgrößen, Stops und Notbremsen – diese Grenzen gelten immer.' },
  exchange: { title: 'Marktdaten', description: 'Woher die Kursdaten für die Analyse kommen. Gehandelt wird nur in TradingView.' },
  ai: { title: 'KI', description: 'Welche KI Vincent nutzt und wie er denkt.' },
  voice: { title: 'Stimme', description: 'Spracherkennung, Aktivierungswort und Sprachausgabe.' },
  memory: { title: 'Gedächtnis', description: 'Wie viel Vincent sich merkt und wann er seine Erinnerungen verdichtet.' },
  appearance: { title: 'Aussehen', description: 'Skin, Farbe, Größe und Position von Vincent am Bildschirmrand.' },
  telegram: { title: 'Telegram', description: 'Optionaler Telegram-Bot für Meldungen und Freigaben unterwegs.' },
  tradingview: { title: 'TradingView', description: 'Wie Vincent die TradingView-App steuert.' },
  mac: { title: 'Mac-Steuerung', description: 'Was Vincent auf deinem Mac bedienen darf.' },
  web: { title: 'Schnittstelle', description: 'Lokale Verbindung zwischen App und Backend (nur 127.0.0.1).' },
  allgemein: { title: 'Allgemein', description: '' },
};

/** Pfade, die nicht im generierten Formular erscheinen (eigene Bedienelemente). */
export const SPECIAL_FIELDS = new Set(['live_trading_unlocked', 'live_autopilot_unlocked']);

export const FIELD_LABELS = {
  'trading.watchlist': 'Beobachtungsliste',
  'trading.timeframe': 'Zeiteinheit',
  'trading.higher_timeframe': 'Übergeordnete Zeiteinheit',
  'trading.history_candles': 'Historie (Kerzen)',
  'trading.scan_interval_minutes': 'Markt-Scan alle (Minuten)',
  'trading.manage_interval_seconds': 'Positions-Check alle (Sekunden)',
  'trading.deep_analysis_hours': 'Tiefenanalyse alle (Stunden)',
  'trading.entry_threshold': 'Einstiegs-Schwelle (Score)',
  'trading.exit_threshold': 'Ausstiegs-Schwelle (Score)',
  'trading.min_active_strategies': 'Mind. übereinstimmende Strategien',
  'trading.higher_tf_filter': 'Nicht gegen den übergeordneten Trend',
  'trading.max_extension_atr': 'Max. Abstand zur EMA20 (× ATR)',
  'trading.allow_short': 'Short-Trades erlauben',
  'trading.use_ai_review': 'KI prüft jeden Trade',
  'trading.tradingview_signal_weight': 'Gewicht von TradingView-Signalen',
  'trading.tradingview_signal_minutes': 'TradingView-Signal gültig (Minuten)',
  'trading.autonomy': 'Autonomie',
  'trading.proposal_timeout_minutes': 'Vorschlag verfällt nach (Minuten)',
  'trading.account_size_fallback': 'Kontogröße, falls nicht lesbar',
  'risk.risk_per_trade_pct': 'Risiko pro Trade (%)',
  'risk.max_position_pct': 'Max. Positionsgröße (% vom Konto)',
  'risk.max_open_positions': 'Max. offene Positionen',
  'risk.max_daily_loss_pct': 'Tagesverlust-Limit (%)',
  'risk.max_drawdown_pct': 'Notbremse ab Drawdown (%)',
  'risk.stop_atr_mult': 'Stop-Loss-Abstand (× ATR)',
  'risk.take_profit_r': 'Take-Profit (× Risiko)',
  'risk.trailing_atr_mult': 'Trailing-Stop-Abstand (× ATR)',
  'risk.trailing_activate_r': 'Trailing-Stop ab Gewinn (R)',
  'risk.breakeven_at_r': 'Stop auf Einstand ab (R)',
  'risk.cooldown_minutes_after_loss': 'Pause nach Verlust (Minuten)',
  'risk.max_live_order_usd': 'Max. Echtgeld je Order (USD)',
  'risk.fee_pct': 'Gebühren (%)',
  'risk.slippage_pct': 'Slippage (%)',
  'risk.min_order_usd': 'Mindest-Ordergröße (USD)',
  'exchange.name': 'Börse für Kursdaten',
  'exchange.data_source': 'Datenquelle',
  'ai.provider': 'KI-Anbieter',
  'ai.ollama_url': 'Ollama-Adresse',
  'ai.ollama_model': 'Ollama-Modell',
  'ai.ollama_vision_model': 'Ollama-Bildmodell',
  'ai.ollama_embed_model': 'Ollama-Embedding-Modell',
  'ai.ollama_think': 'Ollama „Nachdenken“',
  'ai.ollama_num_ctx': 'Ollama-Kontextgröße',
  'ai.anthropic_model': 'Claude-Modell',
  'ai.anthropic_effort': 'Claude-Denkaufwand',
  'ai.openai_base_url': 'OpenAI-Adresse',
  'ai.openai_model': 'OpenAI-Modell',
  'ai.temperature': 'Kreativität (Temperatur)',
  'ai.chat_history_messages': 'Chat-Verlauf (Nachrichten)',
  'ai.reflect_on_trades': 'Aus jedem Trade lernen',
  'ai.personality': 'Persönlichkeit',
  'voice.enabled': 'Stimme aktiv',
  'voice.wake_word_enabled': 'Aktivierungswort „Hey Vincent“',
  'voice.assistant_name': 'Name des Assistenten',
  'voice.follow_up_seconds': 'Weiterreden ohne Aktivierungswort (Sekunden)',
  'voice.stt_backend': 'Spracherkennung',
  'voice.stt_model': 'Erkennungsmodell (Befehle)',
  'voice.stt_fast_model': 'Erkennungsmodell (Aktivierungswort)',
  'voice.tts_provider': 'Sprachausgabe',
  'voice.elevenlabs_voice_id': 'ElevenLabs-Stimmen-ID',
  'voice.elevenlabs_model': 'ElevenLabs-Modell',
  'voice.openai_tts_model': 'OpenAI-Sprachmodell',
  'voice.openai_tts_voice': 'OpenAI-Stimme',
  'voice.piper_voice': 'Piper-Stimme',
  'voice.macos_voice': 'macOS-Stimme',
  'voice.speak_replies': 'Antworten vorlesen',
  'voice.input_device': 'Mikrofon (Gerät)',
  'voice.output_device': 'Lautsprecher (Gerät)',
  'memory.max_memories': 'Max. Anzahl Erinnerungen',
  'memory.profile_max_chars': 'Kernprofil (max. Zeichen)',
  'memory.consolidate_hours': 'Verdichten alle (Stunden)',
  'memory.extract_facts': 'Fakten aus Gesprächen lernen',
  'appearance.skin': 'Skin',
  'appearance.accent': 'Akzentfarbe',
  'appearance.size': 'Größe (Pixel)',
  'appearance.edge': 'Bildschirmrand',
  'appearance.custom_image': 'Eigenes Bild (Pfad)',
  'appearance.show_subtitles': 'Untertitel anzeigen',
  'telegram.enabled': 'Telegram aktiv',
  'telegram.allowed_chat_ids': 'Erlaubte Chat-IDs',
  'telegram.notify_trades': 'Trades melden',
  'telegram.notify_signals': 'Signale melden',
  'telegram.daily_report_time': 'Tagesbericht um (HH:MM)',
  'web.host': 'Adresse',
  'web.port': 'Port',
  'web.webhook_enabled': 'TradingView-Webhook aktiv',
  'web.webhook_port': 'Webhook-Port',
  'tradingview.app_path': 'Pfad zur TradingView-App',
  'tradingview.cdp_port': 'Lokaler Steuer-Port',
  'tradingview.allow_restart_without_asking': 'Neustart ohne Nachfrage erlauben',
  'tradingview.paper_account_name': 'Name des Paper-Kontos',
  'tradingview.chart_exchange_prefix': 'Börsen-Präfix für Symbole',
  'tradingview.use_brackets': 'Stop-Loss & Take-Profit im Ticket setzen',
  'tradingview.api_fallback': 'Notfall-Weg über interne Schnittstelle',
  'tradingview.alert_mode': 'Alert-Modus',
  'mac.automation_enabled': 'Mac-Automatisierung',
  'mac.notifications': 'Mitteilungen',
  'mac.computer_use_enabled': 'Vincent darf den Mac bedienen',
  'mac.computer_use_max_steps': 'Max. Schritte je Aufgabe',
};

export const FIELD_HINTS = {
  'trading.watchlist': 'Symbole durch Komma getrennt, z. B. BTC/USDT, ETH/USDT',
  'trading.entry_threshold': 'Ab diesem Ensemble-Score (0,1 … 0,95) wird ein Einstieg erwogen.',
  'trading.exit_threshold': 'Fällt der Score darunter, wird eine Long-Position geschlossen.',
  'trading.allow_short': 'Leerverkäufe (Short) erlauben – bei Echtgeld nur, wenn dein Broker in TradingView das kann.',
  'trading.use_ai_review': 'Die KI darf Trades nur ablehnen oder verkleinern – nie vergrößern.',
  'trading.autonomy': '„Handelt selbst“: Paper ohne Rückfrage; Echtgeld nur mit freigeschaltetem Echtgeld-Autopiloten.',
  'risk.risk_per_trade_pct': 'So viel Prozent des Kontos darf ein Trade höchstens kosten (0,05 … 5).',
  'risk.max_daily_loss_pct': 'Wird es erreicht, gibt es heute keine neuen Trades mehr.',
  'risk.max_drawdown_pct': 'Ab diesem Rückgang vom Höchststand stoppt Vincent den Handel.',
  'risk.max_live_order_usd': 'Harte Obergrenze für jede Echtgeld-Order.',
  'exchange.name': 'Jede ccxt-Börse, z. B. binance, kraken, bybit.',
  'ai.provider': '„Automatisch“: Claude, wenn ein Anthropic-Key gesetzt ist, sonst OpenAI, sonst lokal.',
  'ai.personality': 'So spricht und denkt Vincent.',
  'voice.tts_provider': 'ElevenLabs klingt am realistischsten (braucht API-Key).',
  'voice.input_device': 'Leer = Standardgerät',
  'voice.output_device': 'Leer = Standardgerät',
  'voice.macos_voice': 'Leer = beste installierte deutsche Stimme',
  'memory.consolidate_hours': 'In dieser „Schlafphase“ ordnet Vincent seine Erinnerungen.',
  'appearance.custom_image': 'Nur für den Skin „Eigenes Bild“ (PNG, GIF oder JPG).',
  'telegram.allowed_chat_ids': 'Zahlen durch Komma getrennt. Leer = Kopplung per Code.',
  'telegram.daily_report_time': 'Leer = kein Tagesbericht',
  'tradingview.cdp_port': 'Nur auf deinem Mac erreichbar (127.0.0.1).',
  'tradingview.allow_restart_without_asking': 'Sonst fragt Vincent, bevor er TradingView neu startet.',
  'tradingview.paper_account_name': 'Optional – hilft bei der sicheren Konto-Erkennung.',
  'tradingview.api_fallback': 'Nur im Notfall: Order über TradingViews interne Schnittstelle statt Klicks.',
  'mac.computer_use_enabled': 'Claude-Computersteuerung – braucht einen Anthropic-Key.',
  'web.port': 'Änderung wirkt nach Neustart der App.',
};

const TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d', '1w'].map((v) => ({ value: v, label: v }));

export const ENUMS = {
  'trading.autonomy': [
    { value: 'ask', label: 'Fragt vor jedem Trade' },
    { value: 'auto', label: 'Handelt selbst' },
  ],
  'trading.timeframe': TIMEFRAMES,
  'trading.higher_timeframe': TIMEFRAMES,
  'ai.provider': [
    { value: 'auto', label: 'Automatisch' },
    { value: 'anthropic', label: 'Claude (Anthropic)' },
    { value: 'openai', label: 'OpenAI' },
    { value: 'ollama', label: 'Lokal (Ollama)' },
  ],
  'ai.anthropic_effort': [
    { value: 'low', label: 'Niedrig – schnell' },
    { value: 'medium', label: 'Mittel' },
    { value: 'high', label: 'Hoch' },
    { value: 'xhigh', label: 'Sehr hoch' },
    { value: 'max', label: 'Maximum – langsam' },
  ],
  'voice.tts_provider': [
    { value: 'auto', label: 'Automatisch' },
    { value: 'elevenlabs', label: 'ElevenLabs (am realistischsten)' },
    { value: 'openai', label: 'OpenAI' },
    { value: 'piper', label: 'Piper (lokal)' },
    { value: 'macos', label: 'macOS-Systemstimme' },
  ],
  'voice.stt_backend': [
    { value: 'auto', label: 'Automatisch' },
    { value: 'mlx', label: 'MLX Whisper (Apple Silicon)' },
    { value: 'faster-whisper', label: 'faster-whisper' },
  ],
  'appearance.edge': [
    { value: 'right', label: 'Rechts' },
    { value: 'left', label: 'Links' },
  ],
  'appearance.skin': [
    { value: 'mensch', label: 'Mensch' },
    { value: 'nebula', label: 'Nebula' },
    { value: 'hologramm', label: 'Hologramm' },
    { value: 'neon', label: 'Neon' },
    { value: 'glas', label: 'Glas' },
    { value: 'roboter', label: 'Roboter' },
    { value: 'eigenes', label: 'Eigenes Bild' },
  ],
  'exchange.data_source': [
    { value: 'exchange', label: 'Echte Kursdaten (Börse)' },
    { value: 'demo', label: 'Demo-Daten' },
  ],
  'tradingview.alert_mode': [
    { value: 'signal', label: 'Signal – fließt in die Entscheidung ein' },
    { value: 'execute', label: 'Ausführen – sofort ein Vorschlag' },
  ],
};

/** Felder mit Kommazahl (JSON kennt keinen Unterschied zwischen 1 und 1.0). */
export const FLOAT_FIELDS = new Set([
  'trading.entry_threshold', 'trading.exit_threshold', 'trading.max_extension_atr',
  'trading.tradingview_signal_weight', 'trading.account_size_fallback',
  'risk.risk_per_trade_pct', 'risk.max_position_pct', 'risk.max_daily_loss_pct', 'risk.max_drawdown_pct',
  'risk.stop_atr_mult', 'risk.take_profit_r', 'risk.trailing_atr_mult', 'risk.trailing_activate_r',
  'risk.breakeven_at_r', 'risk.max_live_order_usd', 'risk.fee_pct', 'risk.slippage_pct', 'risk.min_order_usd',
  'ai.temperature', 'voice.follow_up_seconds',
]);

/** Listen mit Zahlen statt Text. */
export const INT_LIST_FIELDS = new Set(['telegram.allowed_chat_ids']);

export const TEXTAREA_FIELDS = new Set(['ai.personality']);

/** Grenzen wie in xrz/config.py HARD_LIMITS. */
export const LIMITS = {
  'risk.risk_per_trade_pct': [0.05, 5.0],
  'risk.max_position_pct': [1.0, 100.0],
  'risk.max_open_positions': [1, 20],
  'risk.max_daily_loss_pct': [0.5, 20.0],
  'risk.max_drawdown_pct': [2.0, 50.0],
  'risk.stop_atr_mult': [0.5, 6.0],
  'risk.take_profit_r': [0.5, 10.0],
  'risk.trailing_atr_mult': [0.5, 8.0],
  'trading.entry_threshold': [0.1, 0.95],
  'trading.exit_threshold': [-0.9, 0.5],
  'appearance.size': [72, 220],
};

/** "max_open_positions" → "Max open positions" (Fallback für unbekannte Felder). */
export function humanizeKey(key) {
  const s = String(key || '').replace(/[_.-]+/g, ' ').trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : '';
}

export function fieldLabel(path) {
  return FIELD_LABELS[path] || humanizeKey(String(path).split('.').pop());
}

export function getPath(obj, path) {
  let cur = obj;
  for (const part of String(path).split('.')) {
    if (cur == null || typeof cur !== 'object') return undefined;
    cur = cur[part];
  }
  return cur;
}

/** Setzt einen Wert per Punkt-Pfad und legt Zwischenobjekte an. */
export function setPath(obj, path, value) {
  const parts = String(path).split('.');
  let cur = obj;
  for (const part of parts.slice(0, -1)) {
    if (cur[part] == null || typeof cur[part] !== 'object' || Array.isArray(cur[part])) cur[part] = {};
    cur = cur[part];
  }
  cur[parts[parts.length - 1]] = value;
  return obj;
}

function isPlainObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

/** Bestimmt den Eingabetyp eines Feldes. */
export function fieldType(path, value) {
  if (ENUMS[path]) return 'enum';
  if (typeof value === 'boolean') return 'bool';
  if (Array.isArray(value)) return 'list';
  if (typeof value === 'number') {
    if (FLOAT_FIELDS.has(path) || !Number.isInteger(value)) return 'float';
    return 'int';
  }
  if (TEXTAREA_FIELDS.has(path) || (typeof value === 'string' && value.length > 90)) return 'textarea';
  return 'text';
}

function makeField(path, value) {
  const type = fieldType(path, value);
  const field = {
    path,
    section: path.includes('.') ? path.split('.')[0] : 'allgemein',
    key: path.split('.').pop(),
    label: fieldLabel(path),
    hint: FIELD_HINTS[path] || '',
    type,
    value: Array.isArray(value) ? [...value] : value,
  };
  if (type === 'enum') {
    const opts = ENUMS[path].map((o) => ({ ...o }));
    if (value != null && value !== '' && !opts.some((o) => o.value === value)) {
      opts.push({ value: String(value), label: String(value) });
    }
    field.options = opts;
  }
  if (type === 'list') {
    field.listOf = INT_LIST_FIELDS.has(path) || (value.length > 0 && value.every((x) => typeof x === 'number')) ? 'int' : 'str';
  }
  if (type === 'int' || type === 'float') {
    field.step = type === 'int' ? 1 : 'any';
    if (LIMITS[path]) [field.min, field.max] = LIMITS[path];
  }
  return field;
}

/**
 * Erzeugt aus dem Settings-Objekt die Formular-Abschnitte:
 * [{id, title, description, fields:[{path, key, label, hint, type, value, options?, listOf?, min?, max?, step?}]}]
 */
export function buildSettingsForm(settings) {
  const src = isPlainObject(settings) ? settings : {};
  const sections = new Map();
  const ensure = (id) => {
    if (!sections.has(id)) {
      const meta = SECTIONS[id] || { title: humanizeKey(id), description: '' };
      sections.set(id, { id, title: meta.title, description: meta.description, fields: [] });
    }
    return sections.get(id);
  };
  const ids = [...SECTION_ORDER.filter((id) => isPlainObject(src[id])),
    ...Object.keys(src).filter((k) => isPlainObject(src[k]) && !SECTION_ORDER.includes(k))];
  for (const id of ids) {
    const sec = ensure(id);
    for (const [key, value] of Object.entries(src[id])) {
      if (isPlainObject(value) || value === null || value === undefined) continue;
      sec.fields.push(makeField(`${id}.${key}`, value));
    }
  }
  for (const [key, value] of Object.entries(src)) {
    if (isPlainObject(value) || SPECIAL_FIELDS.has(key) || value === null || value === undefined) continue;
    ensure('allgemein').fields.push(makeField(key, value));
  }
  return [...sections.values()].filter((s) => s.fields.length > 0);
}

/** Startwert für das Bedienelement (Listen als Komma-Text, Zahlen als Text). */
export function fieldToInput(field) {
  const v = field.value;
  if (field.type === 'bool') return Boolean(v);
  if (field.type === 'list') return (v || []).join(', ');
  if (v === null || v === undefined) return '';
  return String(v);
}

function fmtLimit(n) {
  return String(n).replace('.', ',');
}

/** Rohwert aus dem Formular → typisierter Wert. Wirft einen Error mit deutscher Meldung. */
export function parseFieldValue(field, raw) {
  switch (field.type) {
    case 'bool':
      if (typeof raw === 'boolean') return raw;
      return ['1', 'true', 'ja', 'an', 'on', 'yes'].includes(String(raw).trim().toLowerCase());
    case 'int':
    case 'float': {
      const n = toNum(raw);
      if (n === null) throw new Error(`„${field.label}“: bitte eine Zahl eingeben.`);
      if (field.type === 'int' && !Number.isInteger(n)) throw new Error(`„${field.label}“: bitte eine ganze Zahl eingeben.`);
      if (field.min !== undefined && (n < field.min || n > field.max)) {
        throw new Error(`„${field.label}“ muss zwischen ${fmtLimit(field.min)} und ${fmtLimit(field.max)} liegen.`);
      }
      return n;
    }
    case 'list': {
      const parts = String(raw ?? '').split(',').map((s) => s.trim()).filter(Boolean);
      if (field.listOf === 'int') {
        return parts.map((p) => {
          const n = toNum(p);
          if (n === null || !Number.isInteger(n)) throw new Error(`„${field.label}“: „${p}“ ist keine ganze Zahl.`);
          return n;
        });
      }
      return parts;
    }
    case 'enum': {
      const v = String(raw ?? '');
      if (!field.options.some((o) => o.value === v)) throw new Error(`„${field.label}“: ungültige Auswahl.`);
      return v;
    }
    default:
      return String(raw ?? '');
  }
}

export function valuesEqual(a, b) {
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((x, i) => valuesEqual(x, b[i]));
  }
  return a === b;
}

/**
 * Sammelt geänderte Werte ein.
 * fields: Felder aus buildSettingsForm (flach), raws: {pfad: rohwert}
 * → {changes: verschachteltes Teil-Objekt, errors: [{path, message}], count}
 */
export function collectSettingsChanges(fields, raws) {
  const changes = {};
  const errors = [];
  let count = 0;
  for (const field of fields) {
    if (!raws || !Object.prototype.hasOwnProperty.call(raws, field.path)) continue;
    let value;
    try {
      value = parseFieldValue(field, raws[field.path]);
    } catch (err) {
      errors.push({ path: field.path, message: err.message });
      continue;
    }
    if (valuesEqual(value, field.value)) continue;
    setPath(changes, field.path, value);
    count += 1;
  }
  return { changes, errors, count };
}

/**
 * Geheimnisse: nur neu eingegebene Werte (und bewusst entfernte = "") schicken.
 * entries: {key: {value: string, remove: bool}}
 */
export function collectSecretChanges(entries) {
  const out = {};
  for (const [key, entry] of Object.entries(entries || {})) {
    if (!entry) continue;
    const value = String(entry.value ?? '').trim();
    if (value) out[key] = value;
    else if (entry.remove) out[key] = '';
  }
  return out;
}

export function flattenFields(sections) {
  return sections.flatMap((s) => s.fields);
}
