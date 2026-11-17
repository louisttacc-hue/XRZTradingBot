import { notableState } from '../domain/detector.js';
import { addDays, formatDateShort, formatTime, isoWeek, isWeekend, mondayOf, schoolNow } from '../domain/time.js';
import type { Holiday, Period, TimeUnit, UntisDate, UntisTime } from '../domain/types.js';
import { buildNotifications, courseName, formatDayPlan, periodLabel } from '../format/messages.js';

/**
 * Chat-Befehle. Antworten werden aus dem zuletzt gespeicherten Untis-Abruf erzeugt –
 * Befehle lösen also keine zusätzlichen Untis-Anfragen aus (schnell und schonend).
 */

export type CommandName = 'heute' | 'morgen' | 'woche' | 'entfall' | 'kurse' | 'status' | 'hilfe';

const ALIASES: Record<string, CommandName> = {
  heute: 'heute',
  today: 'heute',
  morgen: 'morgen',
  tomorrow: 'morgen',
  woche: 'woche',
  week: 'woche',
  entfall: 'entfall',
  'entfälle': 'entfall',
  entfaelle: 'entfall',
  ausfall: 'entfall',
  kurse: 'kurse',
  kurs: 'kurse',
  status: 'status',
  hilfe: 'hilfe',
  help: 'hilfe',
  befehle: 'hilfe',
  start: 'hilfe',
};

export function parseCommand(text: string | null | undefined): CommandName | null {
  const trimmed = (text ?? '').trim();
  if (!/^[/!]/.test(trimmed)) return null;
  const word = trimmed.slice(1).split(/[\s@]/)[0]?.toLowerCase() ?? '';
  return ALIASES[word] ?? null;
}

export type WhatsAppState = 'open' | 'connecting' | 'close' | 'qr' | 'loggedOut';

export interface BotStatus {
  version: string;
  startedAt: number;
  whatsapp: WhatsAppState;
  untis: {
    lastSuccessAt?: number | undefined;
    lastAttemptAt?: number | undefined;
    lastError?: string | undefined;
    consecutiveFailures: number;
  };
  nextPollAt?: number | undefined;
  pollIntervalMinutes: number;
  classNames: string[];
  groupName?: string | undefined;
  stats: { outboxPending: number; sentLast24h: number; eventsLast24h: number };
}

export interface BotData {
  periods(from: UntisDate, to: UntisDate): Period[];
  isFetched(date: UntisDate): boolean;
  timegrid(): TimeUnit[];
  holidays(): Holiday[];
  lastSyncAt(): number | undefined;
  windowEnd(): UntisDate | undefined;
}

export interface CommandContext {
  now: { date: UntisDate; time: UntisTime };
  nowMs: number;
  timeZone: string;
  className: string;
  staleAfterMs: number;
  data: BotData;
  status: () => BotStatus;
}

export function runCommand(name: CommandName, ctx: CommandContext): string {
  switch (name) {
    case 'heute':
      return withFooter(dayCommand(ctx, ctx.now.date, 'Heute'), ctx);
    case 'morgen':
      return withFooter(dayCommand(ctx, addDays(ctx.now.date, 1), 'Morgen'), ctx);
    case 'woche':
      return withFooter(weekCommand(ctx), ctx);
    case 'entfall':
      return withFooter(cancelledCommand(ctx), ctx);
    case 'kurse':
      return withFooter(coursesCommand(ctx), ctx);
    case 'status':
      return statusCommand(ctx);
    case 'hilfe':
      return helpText(ctx.className);
  }
}

// ── Tagespläne ────────────────────────────────────────────────────────

function holidayOn(ctx: CommandContext, date: UntisDate): Holiday | undefined {
  return ctx.data.holidays().find((h) => h.startDate <= date && h.endDate >= date);
}

function noLessonsReason(ctx: CommandContext, date: UntisDate): string {
  const holiday = holidayOn(ctx, date);
  if (holiday) return `🏖️ ${holiday.longName || holiday.name}`;
  if (isWeekend(date)) return 'Wochenende 😎';
  return 'kein Unterricht im Plan';
}

function dayCommand(ctx: CommandContext, date: UntisDate, title: string): string {
  if (!ctx.data.isFetched(date)) return noDataText(ctx, date);
  const periods = ctx.data.periods(date, date);
  if (periods.length > 0) return formatDayPlan(periods, date, { timegrid: ctx.data.timegrid(), title });

  const lines = [`📅 *${title} · ${formatDateShort(date)}*: kein Unterricht – ${noLessonsReason(ctx, date)}`];
  const next = nextSchoolDay(ctx, addDays(date, 1));
  if (next) {
    lines.push('', formatDayPlan(ctx.data.periods(next, next), next, { timegrid: ctx.data.timegrid(), title: 'Nächster Schultag' }));
  }
  return lines.join('\n');
}

function nextSchoolDay(ctx: CommandContext, from: UntisDate): UntisDate | undefined {
  const end = ctx.data.windowEnd();
  if (!end) return undefined;
  for (let d = from; d <= end; d = addDays(d, 1)) {
    if (ctx.data.periods(d, d).length > 0) return d;
  }
  return undefined;
}

function noDataText(ctx: CommandContext, date: UntisDate): string {
  const end = ctx.data.windowEnd();
  if (end && date > end) return `📅 Für ${formatDateShort(date)} liegen noch keine Daten vor (ich schaue bis ${formatDateShort(end)} voraus).`;
  return '⏳ Noch keine Stundenplandaten – der erste Untis-Abruf läuft noch oder Untis ist gerade nicht erreichbar. Bitte gleich nochmal versuchen.';
}

// ── Woche ─────────────────────────────────────────────────────────────

/** Zeilen für alle Stunden mit Untis-Kennzeichnung (Entfall, Vertretung, …), Doppelstunden zusammengefasst. */
function changeLines(periods: readonly Period[], timegrid: readonly TimeUnit[], filter?: (p: Period) => boolean): string[] {
  const events = periods
    .filter((p) => (filter ? filter(p) : true))
    .flatMap((p) => {
      const state = notableState(p);
      return state ? [{ kinds: state.kinds, period: { ...p, substText: undefined, info: undefined } as Period }] : [];
    });
  if (events.length === 0) return [];
  const [message] = buildNotifications(events, { timegrid, maxEvents: Number.MAX_SAFE_INTEGER, className: '' });
  // Kopfzeile und Tages-Überschriften entfernen – nur die Änderungszeilen
  return (message ?? '')
    .split('\n')
    .slice(1)
    .filter((l) => l && !l.startsWith('📅'));
}

function weekCommand(ctx: CommandContext): string {
  const today = ctx.now.date;
  const monday = isWeekend(today) ? addDays(mondayOf(today), 7) : mondayOf(today);
  const start = isWeekend(today) ? monday : today;
  const friday = addDays(monday, 4);
  const timegrid = ctx.data.timegrid();

  const lines = [`🗓️ *${ctx.className} · KW ${isoWeek(monday)}* (${formatDateShort(monday)} – ${formatDateShort(friday)})`];
  if (start > monday) lines.push('_Vergangene Tage sind ausgeblendet._');

  for (let d = start; d <= friday; d = addDays(d, 1)) {
    if (!ctx.data.isFetched(d)) {
      lines.push('', `*${formatDateShort(d)}* – noch keine Daten`);
      continue;
    }
    const periods = ctx.data.periods(d, d);
    if (periods.length === 0) {
      lines.push('', `*${formatDateShort(d)}* – kein Unterricht (${noLessonsReason(ctx, d)})`);
      continue;
    }
    const first = Math.min(...periods.map((p) => p.start));
    const last = Math.max(...periods.map((p) => p.end));
    const changes = changeLines(periods, timegrid);
    lines.push('', `*${formatDateShort(d)}* – ${periodLabel(first, last, timegrid)}${changes.length ? '' : ' · keine Änderungen ✅'}`);
    lines.push(...changes);
  }
  lines.push('', '_Kompletter Tagesplan: /heute bzw. /morgen_');
  return lines.join('\n');
}

// ── Entfall ───────────────────────────────────────────────────────────

function cancelledCommand(ctx: CommandContext): string {
  const end = ctx.data.windowEnd();
  if (!end) return noDataText(ctx, ctx.now.date);
  const upcoming = ctx.data
    .periods(ctx.now.date, end)
    .filter((p) => p.status === 'cancelled' && !(p.date === ctx.now.date && p.end <= ctx.now.time));
  if (upcoming.length === 0) return `🙂 Keine Entfälle bekannt (bis ${formatDateShort(end)}).`;

  const lines = [`📋 *Entfälle ${ctx.className}* (bis ${formatDateShort(end)})`];
  const timegrid = ctx.data.timegrid();
  const dates = [...new Set(upcoming.map((p) => p.date))].sort((a, b) => a - b);
  for (const date of dates) {
    lines.push('', `📅 *${formatDateShort(date)}*`, ...changeLines(upcoming.filter((p) => p.date === date), timegrid));
  }
  return lines.join('\n');
}

// ── Kurse ─────────────────────────────────────────────────────────────

function coursesCommand(ctx: CommandContext): string {
  const end = ctx.data.windowEnd();
  if (!end) return noDataText(ctx, ctx.now.date);
  const periods = ctx.data.periods(ctx.now.date, end);
  if (periods.length === 0) return `📚 Im Plan bis ${formatDateShort(end)} stehen keine Stunden – Kursliste gerade nicht verfügbar.`;

  interface Course {
    name: string;
    group?: string;
    teachers: Set<string>;
    perWeek: Map<number, Set<string>>;
  }
  const courses = new Map<string, Course>();
  for (const p of periods) {
    const subject = p.subjects.map((s) => (s.original ? s.original.name : s.longName || s.name)).join('/') || p.lessonText || 'Unterricht';
    const id = `${p.lessonNumber}|${subject}|${p.studentGroup ?? ''}`;
    let c = courses.get(id);
    if (!c) {
      c = { name: subject, teachers: new Set(), perWeek: new Map() };
      if (p.studentGroup && p.studentGroup !== subject) c.group = p.studentGroup;
      courses.set(id, c);
    }
    for (const t of p.teachers) {
      const name = t.original?.name || t.name;
      if (name) c.teachers.add(name);
    }
    const week = isoWeek(p.date);
    const slots = c.perWeek.get(week) ?? new Set<string>();
    slots.add(`${p.date}|${p.start}`);
    c.perWeek.set(week, slots);
  }

  const sorted = [...courses.values()].sort((a, b) => a.name.localeCompare(b.name, 'de') || (a.group ?? '').localeCompare(b.group ?? '', 'de'));
  const lines = [`📚 *Kurse der ${ctx.className}* – ${sorted.length} Kurse`, ''];
  for (const c of sorted) {
    const hours = Math.max(...[...c.perWeek.values()].map((s) => s.size));
    const teachers = [...c.teachers].sort().join(', ') || '—';
    lines.push(`• *${c.name}*${c.group ? ` (${c.group})` : ''} – ${teachers} · ${hours} Std/Woche`);
  }
  lines.push('', `_Grundlage: Untis-Plan ${formatDateShort(ctx.now.date)} – ${formatDateShort(end)}_`);
  return lines.join('\n');
}

// ── Status & Hilfe ────────────────────────────────────────────────────

function clock(ms: number, ctx: CommandContext): string {
  const t = schoolNow(new Date(ms), ctx.timeZone);
  const time = `${formatTime(t.time)} Uhr`;
  return t.date === ctx.now.date ? time : `${formatDateShort(t.date)} ${time}`;
}

function duration(ms: number): string {
  const minutes = Math.max(0, Math.floor(ms / 60_000));
  if (minutes < 60) return `${minutes} Min.`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} Std. ${minutes % 60} Min.`;
  return `${Math.floor(hours / 24)} T. ${hours % 24} Std.`;
}

const WA_LABEL: Record<WhatsAppState, string> = {
  open: '✅ verbunden',
  connecting: '⏳ verbindet …',
  close: '⚠️ getrennt – verbinde neu',
  qr: '📱 wartet auf QR-Scan',
  loggedOut: '⛔ abgemeldet – QR-Code neu scannen',
};

function statusCommand(ctx: CommandContext): string {
  const s = ctx.status();
  const u = s.untis;
  let untisLine: string;
  if (u.consecutiveFailures > 0) {
    untisLine = `⚠️ ${u.consecutiveFailures} Fehlversuch(e) – ${u.lastError ?? 'unbekannter Fehler'}`;
    if (u.lastSuccessAt) untisLine += ` (letzter Erfolg: ${clock(u.lastSuccessAt, ctx)})`;
  } else if (u.lastSuccessAt) {
    untisLine = `✅ letzter Abruf ${clock(u.lastSuccessAt, ctx)} (vor ${duration(ctx.nowMs - u.lastSuccessAt)})`;
  } else {
    untisLine = '⏳ noch kein Abruf';
  }

  const end = ctx.data.windowEnd();
  const periods = end ? ctx.data.periods(ctx.now.date, end) : [];
  const courses = new Set(periods.map((p) => `${p.lessonNumber}|${courseName(p)}`)).size;

  return [
    '🤖 *Bot-Status*',
    `WhatsApp: ${WA_LABEL[s.whatsapp]}${s.groupName ? ` · Gruppe „${s.groupName}“` : ''}`,
    `Untis: ${untisLine}`,
    s.nextPollAt ? `Nächster Abruf: ${clock(s.nextPollAt, ctx)} (alle ${s.pollIntervalMinutes} Min.)` : `Abruf alle ${s.pollIntervalMinutes} Min.`,
    `Überwacht: ${s.classNames.join(', ')} · ${courses} Kurse · ${periods.length} Stunden${end ? ` bis ${formatDateShort(end)}` : ''}`,
    `Letzte 24 h: ${s.stats.eventsLast24h} Änderungen erkannt · ${s.stats.sentLast24h} Nachrichten gesendet`,
    s.stats.outboxPending > 0 ? `Warteschlange: ${s.stats.outboxPending} Nachricht(en)` : 'Warteschlange: leer',
    `Laufzeit: ${duration(ctx.nowMs - s.startedAt)} · Version ${s.version}`,
  ].join('\n');
}

export function helpText(className: string): string {
  return [
    `🤖 *Vertretungsplan-Bot ${className}*`,
    '',
    `/heute – Plan von heute (alle ${className}-Kurse)`,
    '/morgen – Plan für morgen bzw. den nächsten Schultag',
    '/woche – Änderungen der Schulwoche',
    '/entfall – alle bekannten Entfälle',
    `/kurse – alle Kurse der ${className}`,
    '/status – Zustand des Bots',
    '/hilfe – diese Übersicht',
    '',
    'Entfall, Vertretungen, Raumwechsel und Stundenplanänderungen melde ich automatisch – jede Änderung nur einmal.',
    '_Angaben ohne Gewähr – maßgeblich ist WebUntis._',
  ].join('\n');
}

function withFooter(text: string, ctx: CommandContext): string {
  const last = ctx.data.lastSyncAt();
  if (!last) return text;
  const stale = ctx.nowMs - last > ctx.staleAfterMs;
  return `${text}\n\n${stale ? '⚠️ Daten evtl. veraltet – ' : ''}_Stand: ${clock(last, ctx)}_`;
}

// ── Spam-Schutz ───────────────────────────────────────────────────────

export class CommandCooldown {
  private readonly lastByKey = new Map<string, number>();
  private readonly recent: number[] = [];

  constructor(private readonly opts: { perCommandMs: number; maxPerMinute: number }) {}

  allow(chat: string, command: string, now = Date.now()): boolean {
    while (this.recent.length && now - this.recent[0]! >= 60_000) this.recent.shift();
    if (this.recent.length >= this.opts.maxPerMinute) return false;

    const key = `${chat}|${command}`;
    const last = this.lastByKey.get(key);
    if (last !== undefined && now - last < this.opts.perCommandMs) return false;

    this.lastByKey.set(key, now);
    this.recent.push(now);
    if (this.lastByKey.size > 1000) this.lastByKey.clear();
    return true;
  }
}

