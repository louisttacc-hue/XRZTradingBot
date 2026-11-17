import type { UntisDate, UntisTime } from './types.js';

/**
 * Zeit-Helfer. Untis rechnet in Schulzeit (Europe/Berlin). Der Server kann in
 * einer beliebigen Zeitzone laufen – deshalb wird "jetzt" immer explizit in die
 * Schulzeitzone umgerechnet und Datumsarithmetik zeitzonenfrei in UTC gemacht.
 */

export const DEFAULT_TIMEZONE = 'Europe/Berlin';

const WEEKDAYS_SHORT = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'] as const;
const WEEKDAYS_LONG = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag'] as const;

const formatterCache = new Map<string, Intl.DateTimeFormat>();

function formatterFor(timeZone: string): Intl.DateTimeFormat {
  let f = formatterCache.get(timeZone);
  if (!f) {
    f = new Intl.DateTimeFormat('en-GB', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    });
    formatterCache.set(timeZone, f);
  }
  return f;
}

/** Aktuelles Datum und Uhrzeit in der Zeitzone der Schule. */
export function schoolNow(now: Date = new Date(), timeZone = DEFAULT_TIMEZONE): { date: UntisDate; time: UntisTime } {
  const parts = Object.fromEntries(formatterFor(timeZone).formatToParts(now).map((p) => [p.type, p.value]));
  const date = Number(`${parts.year}${parts.month}${parts.day}`);
  const time = Number(parts.hour) * 100 + Number(parts.minute);
  return { date, time };
}

function toUtc(date: UntisDate): Date {
  const y = Math.floor(date / 10000);
  const m = Math.floor((date % 10000) / 100);
  const d = date % 100;
  return new Date(Date.UTC(y, m - 1, d));
}

function fromUtc(d: Date): UntisDate {
  return d.getUTCFullYear() * 10000 + (d.getUTCMonth() + 1) * 100 + d.getUTCDate();
}

export function addDays(date: UntisDate, days: number): UntisDate {
  const d = toUtc(date);
  d.setUTCDate(d.getUTCDate() + days);
  return fromUtc(d);
}

/** 1 = Montag … 7 = Sonntag */
export function weekdayOf(date: UntisDate): number {
  const js = toUtc(date).getUTCDay(); // 0 = Sonntag
  return js === 0 ? 7 : js;
}

export function mondayOf(date: UntisDate): UntisDate {
  return addDays(date, 1 - weekdayOf(date));
}

export function isWeekend(date: UntisDate): boolean {
  return weekdayOf(date) >= 6;
}

/** Lokale Mitternacht – so erwartet die webuntis-Bibliothek ihre Date-Parameter. */
export function untisDateToLocalDate(date: UntisDate): Date {
  return new Date(Math.floor(date / 10000), Math.floor((date % 10000) / 100) - 1, date % 100);
}

/** Minuten seit Mitternacht – für Abstände zwischen Stunden. */
export function minutesOf(time: UntisTime): number {
  return Math.floor(time / 100) * 60 + (time % 100);
}

export function formatTime(time: UntisTime): string {
  const h = Math.floor(time / 100);
  const m = time % 100;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

function dayMonth(date: UntisDate): string {
  const m = Math.floor((date % 10000) / 100);
  const d = date % 100;
  return `${String(d).padStart(2, '0')}.${String(m).padStart(2, '0')}.`;
}

/** "Mi, 18.11." */
export function formatDateShort(date: UntisDate): string {
  return `${WEEKDAYS_SHORT[weekdayOf(date) - 1]}, ${dayMonth(date)}`;
}

/** "Mittwoch, 18.11.2026" */
export function formatDateLong(date: UntisDate): string {
  return `${WEEKDAYS_LONG[weekdayOf(date) - 1]}, ${dayMonth(date)}${Math.floor(date / 10000)}`;
}

/** Kalenderwoche nach ISO 8601. */
export function isoWeek(date: UntisDate): number {
  const d = toUtc(date);
  d.setUTCDate(d.getUTCDate() + 4 - weekdayOf(date));
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  return Math.ceil(((d.getTime() - yearStart.getTime()) / 86_400_000 + 1) / 7);
}
