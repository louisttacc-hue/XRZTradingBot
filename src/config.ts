import { resolve } from 'node:path';
import { z } from 'zod';
import { minutesOf } from './domain/time.js';
import type { UntisDate, UntisTime } from './domain/types.js';

/**
 * Konfiguration aus Umgebungsvariablen (.env). Wird beim Start vollständig geprüft –
 * Fehler werden mit dem Variablennamen gemeldet, Passwörter nie ausgegeben.
 */

export type UntisAuthMode = 'password' | 'secret' | 'anonymous';

export interface QuietHours {
  start: UntisTime;
  end: UntisTime;
}

export interface Config {
  untis: {
    server: string;
    school: string;
    auth: UntisAuthMode;
    username: string;
    password: string;
    secret: string;
    /** Klassen, deren Stundenplan überwacht wird. Leer = eigene Klasse des Accounts. */
    classes: string[];
  };
  whatsapp: {
    groupJid: string | undefined;
    groupInviteCode: string | undefined;
    pairingNumber: string | undefined;
    allowPrivateCommands: boolean;
    /** Bot läuft als verknüpftes Gerät auf der eigenen, privaten WhatsApp-Nummer (keine zweite SIM). */
    ownNumber: boolean;
    /** Wird jeder Bot-Nachricht vorangestellt (Standard auf eigener Nummer: "🤖 "). */
    messagePrefix: string;
  };
  adminNumbers: string[];
  pollIntervalMinutes: number;
  lookaheadDays: number;
  timeZone: string;
  quietHours: QuietHours | null;
  maxEventsPerMessage: number;
  sendWelcomeMessage: boolean;
  commandCooldownSeconds: number;
  dataDir: string;
  logLevel: string;
  displayName: string;
}

const bool = (def: boolean) =>
  z
    .string()
    .optional()
    .transform((v) => (v === undefined || v.trim() === '' ? def : /^(1|true|ja|yes|on)$/i.test(v.trim())));

const int = (def: number, min: number, max: number) =>
  z
    .string()
    .optional()
    .transform((v) => (v === undefined || v.trim() === '' ? def : Number(v)))
    .pipe(z.number().int().min(min).max(max));

const text = (def = '') =>
  z
    .string()
    .optional()
    .transform((v) => (v ?? def).trim());

const schema = z.object({
  UNTIS_SERVER: z
    .string({ error: 'fehlt (z. B. nessa.webuntis.com)' })
    .trim()
    .min(1, 'fehlt (z. B. nessa.webuntis.com)'),
  UNTIS_SCHOOL: z.string({ error: 'fehlt (Schulname aus der WebUntis-URL)' }).trim().min(1, 'fehlt'),
  UNTIS_AUTH: z.enum(['password', 'secret', 'anonymous']).optional().default('password'),
  UNTIS_USERNAME: text(),
  UNTIS_PASSWORD: z.string().optional().default(''),
  UNTIS_SECRET: text(),
  UNTIS_CLASSES: text('EF'),
  WHATSAPP_GROUP_JID: text(),
  WHATSAPP_GROUP_INVITE: text(),
  WHATSAPP_PAIRING_NUMBER: text(),
  ALLOW_PRIVATE_COMMANDS: bool(false),
  WHATSAPP_OWN_NUMBER: bool(true),
  BOT_MESSAGE_PREFIX: z.string().optional(),
  ADMIN_NUMBERS: text(),
  POLL_INTERVAL_MINUTES: int(5, 3, 120),
  LOOKAHEAD_DAYS: int(14, 1, 28),
  TIMEZONE: text('Europe/Berlin'),
  QUIET_HOURS: z.string().optional(),
  MAX_EVENTS_PER_MESSAGE: int(25, 3, 200),
  SEND_WELCOME_MESSAGE: bool(true),
  COMMAND_COOLDOWN_SECONDS: int(30, 0, 3600),
  DATA_DIR: text('./data'),
  LOG_LEVEL: z.enum(['fatal', 'error', 'warn', 'info', 'debug', 'trace']).optional().default('info'),
  BOT_CLASS_LABEL: text(),
});

export class ConfigError extends Error {
  override name = 'ConfigError';
}

function parseServer(raw: string): string {
  const value = raw.trim();
  if (/^https?:\/\//i.test(value)) return new URL(value).hostname;
  return value.replace(/\/.*$/, '');
}

function parseTime(v: string): UntisTime {
  const m = /^(\d{1,2}):?(\d{2})$/.exec(v.trim());
  if (!m) throw new ConfigError(`QUIET_HOURS: Uhrzeit "${v}" ungültig (Format HH:MM-HH:MM)`);
  return Number(m[1]) * 100 + Number(m[2]);
}

function parseQuietHours(raw: string | undefined): QuietHours | null {
  if (raw === undefined) return { start: 2200, end: 600 };
  if (raw.trim() === '' || /^(aus|off|none|false)$/i.test(raw.trim())) return null;
  const [start, end] = raw.split('-');
  if (!start || !end) throw new ConfigError('QUIET_HOURS: Format HH:MM-HH:MM, z. B. 22:00-06:00');
  return { start: parseTime(start), end: parseTime(end) };
}

function parseInvite(raw: string): string | undefined {
  if (!raw) return undefined;
  const m = /chat\.whatsapp\.com\/(?:invite\/)?([A-Za-z0-9]+)/.exec(raw);
  return m ? m[1] : raw.replace(/[^A-Za-z0-9]/g, '') || undefined;
}

export function normalizePhone(raw: string): string {
  return raw.replace(/\D/g, '').replace(/^00/, '');
}

export function loadConfig(env: Record<string, string | undefined> = process.env): Config {
  const parsed = schema.safeParse(env);
  if (!parsed.success) {
    const details = parsed.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`).join('\n  ');
    throw new ConfigError(`Ungültige Konfiguration (.env):\n  ${details}`);
  }
  const e = parsed.data;

  // Komplettlink aus dem Untis-QR-Code (untis://setschool?...&user=…&key=…) akzeptieren
  if (/key=/i.test(e.UNTIS_SECRET)) {
    const params = new URLSearchParams(e.UNTIS_SECRET.slice(e.UNTIS_SECRET.indexOf('?') + 1));
    e.UNTIS_SECRET = params.get('key') ?? '';
    if (!e.UNTIS_USERNAME) e.UNTIS_USERNAME = params.get('user') ?? '';
  }

  if (e.UNTIS_AUTH === 'password' && (!e.UNTIS_USERNAME || !e.UNTIS_PASSWORD)) {
    throw new ConfigError('UNTIS_USERNAME und UNTIS_PASSWORD werden für UNTIS_AUTH=password benötigt.');
  }
  if (e.UNTIS_AUTH === 'secret' && (!e.UNTIS_USERNAME || !e.UNTIS_SECRET)) {
    throw new ConfigError('UNTIS_USERNAME und UNTIS_SECRET werden für UNTIS_AUTH=secret benötigt (QR-Code aus Untis: Profil → Freigaben).');
  }
  try {
    new Intl.DateTimeFormat('de-DE', { timeZone: e.TIMEZONE });
  } catch {
    throw new ConfigError(`TIMEZONE: unbekannte Zeitzone "${e.TIMEZONE}"`);
  }

  const classes = e.UNTIS_CLASSES.split(',')
    .map((c) => c.trim())
    .filter(Boolean);

  return {
    untis: {
      server: parseServer(e.UNTIS_SERVER),
      school: e.UNTIS_SCHOOL,
      auth: e.UNTIS_AUTH,
      username: e.UNTIS_USERNAME,
      password: e.UNTIS_PASSWORD,
      secret: e.UNTIS_SECRET.replace(/\s/g, ''),
      classes,
    },
    whatsapp: {
      groupJid: e.WHATSAPP_GROUP_JID || undefined,
      groupInviteCode: parseInvite(e.WHATSAPP_GROUP_INVITE),
      pairingNumber: e.WHATSAPP_PAIRING_NUMBER ? normalizePhone(e.WHATSAPP_PAIRING_NUMBER) : undefined,
      allowPrivateCommands: e.ALLOW_PRIVATE_COMMANDS,
      ownNumber: e.WHATSAPP_OWN_NUMBER,
      messagePrefix: e.BOT_MESSAGE_PREFIX ?? (e.WHATSAPP_OWN_NUMBER ? '🤖 ' : ''),
    },
    adminNumbers: e.ADMIN_NUMBERS.split(',').map(normalizePhone).filter(Boolean),
    pollIntervalMinutes: e.POLL_INTERVAL_MINUTES,
    lookaheadDays: e.LOOKAHEAD_DAYS,
    timeZone: e.TIMEZONE,
    quietHours: parseQuietHours(e.QUIET_HOURS),
    maxEventsPerMessage: e.MAX_EVENTS_PER_MESSAGE,
    sendWelcomeMessage: e.SEND_WELCOME_MESSAGE,
    commandCooldownSeconds: e.COMMAND_COOLDOWN_SECONDS,
    dataDir: resolve(e.DATA_DIR),
    logLevel: e.LOG_LEVEL,
    displayName: e.BOT_CLASS_LABEL || classes.join('/') || 'Klasse',
  };
}

/** Wartezeit bis zum Ende der Nachtruhe (0 = sofort senden). */
export function quietHoursDelayMs(now: { date: UntisDate; time: UntisTime }, qh: QuietHours | null): number {
  if (!qh) return 0;
  const t = minutesOf(now.time);
  const start = minutesOf(qh.start);
  const end = minutesOf(qh.end);
  const inQuiet = start <= end ? t >= start && t < end : t >= start || t < end;
  if (!inQuiet) return 0;
  const wait = (end - t + 24 * 60) % (24 * 60);
  return wait * 60_000;
}
