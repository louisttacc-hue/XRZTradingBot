import { describe, expect, it } from 'vitest';
import {
  addDays,
  formatDateLong,
  formatDateShort,
  formatTime,
  mondayOf,
  schoolNow,
  untisDateToLocalDate,
  weekdayOf,
} from '../src/domain/time.js';

describe('schoolNow', () => {
  it('liefert Datum und Uhrzeit in Europe/Berlin, auch wenn der Server in UTC läuft', () => {
    // 2026-11-17 23:30 UTC = 2026-11-18 00:30 in Berlin (Winterzeit, UTC+1)
    const now = schoolNow(new Date('2026-11-17T23:30:00Z'), 'Europe/Berlin');
    expect(now).toEqual({ date: 20261118, time: 30 });
  });

  it('berücksichtigt die Sommerzeit', () => {
    // 2026-06-10 05:50 UTC = 07:50 in Berlin (UTC+2)
    expect(schoolNow(new Date('2026-06-10T05:50:00Z'), 'Europe/Berlin')).toEqual({ date: 20260610, time: 750 });
  });
});

describe('Datumsrechnung', () => {
  it('addiert Tage über Monats- und Jahresgrenzen', () => {
    expect(addDays(20261130, 1)).toBe(20261201);
    expect(addDays(20261231, 1)).toBe(20270101);
    expect(addDays(20260301, -1)).toBe(20260228);
  });

  it('bestimmt Wochentag (1 = Montag … 7 = Sonntag) und Montag der Woche', () => {
    expect(weekdayOf(20261118)).toBe(3); // Mittwoch
    expect(weekdayOf(20261122)).toBe(7); // Sonntag
    expect(mondayOf(20261118)).toBe(20261116);
    expect(mondayOf(20261122)).toBe(20261116);
  });

  it('wandelt in lokale Mitternacht für die webuntis-Bibliothek', () => {
    const d = untisDateToLocalDate(20261118);
    expect([d.getFullYear(), d.getMonth() + 1, d.getDate(), d.getHours()]).toEqual([2026, 11, 18, 0]);
  });
});

describe('Formatierung', () => {
  it('formatiert Uhrzeiten zweistellig', () => {
    expect(formatTime(750)).toBe('07:50');
    expect(formatTime(1305)).toBe('13:05');
  });

  it('formatiert Daten deutsch', () => {
    expect(formatDateShort(20261118)).toBe('Mi, 18.11.');
    expect(formatDateLong(20261118)).toBe('Mittwoch, 18.11.2026');
  });
});
