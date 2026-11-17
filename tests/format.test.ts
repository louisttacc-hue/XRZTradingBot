import { describe, expect, it } from 'vitest';
import { buildNotifications, formatDayPlan, periodLabel, splitMessage } from '../src/format/messages.js';
import type { ChangeEvent, Period, TimeUnit } from '../src/domain/types.js';
import { period } from './fixtures.js';

const timegrid: TimeUnit[] = [
  { name: '1', start: 750, end: 835 },
  { name: '2', start: 835, end: 920 },
  { name: '3', start: 940, end: 1025 },
  { name: '4', start: 1025, end: 1110 },
  { name: '5', start: 1130, end: 1215 },
];

const ctx = { timegrid, maxEvents: 25, className: 'EF' };
const ev = (p: Period, ...kinds: ChangeEvent['kinds']): ChangeEvent => ({ kinds, period: p });

describe('periodLabel', () => {
  it('nutzt das Zeitraster der Schule', () => {
    expect(periodLabel(940, 1025, timegrid)).toBe('3. Std');
    expect(periodLabel(750, 920, timegrid)).toBe('1.–2. Std');
  });
  it('fällt auf Uhrzeiten zurück, wenn das Raster nicht passt', () => {
    expect(periodLabel(800, 900, timegrid)).toBe('08:00–09:00');
    expect(periodLabel(750, 835, [])).toBe('07:50–08:35');
  });
});

describe('buildNotifications', () => {
  it('meldet Entfall, Vertretung und Raumwechsel verständlich – gruppiert nach Tag', () => {
    const events = [
      ev(period({ date: 20261119, startTime: 750, endTime: 835, code: 'cancelled', substText: 'Aufgaben in Moodle' }), 'cancelled'),
      ev(
        period({
          date: 20261119,
          startTime: 940,
          endTime: 1025,
          su: [{ id: 22, name: 'M', longname: 'Mathematik' }],
          sg: 'M-GK2',
          te: [{ id: 11, name: 'NEU', longname: '', orgid: 12, orgname: 'SCH' }],
        }),
        'substitution',
      ),
      ev(period({ date: 20261120, ro: [{ id: 31, name: 'A001', longname: '', orgid: 30, orgname: 'R112' }] }), 'roomChange'),
    ];
    const [msg, ...rest] = buildNotifications(events, ctx);
    expect(rest).toEqual([]);
    expect(msg).toContain('Do, 19.11.');
    expect(msg).toContain('Fr, 20.11.');
    expect(msg).toMatch(/❌ \*1\. Std\* · Deutsch \(D-GK1\) · MÜL — \*Entfall\*/);
    expect(msg).toContain('Aufgaben in Moodle');
    expect(msg).toMatch(/🔄 \*3\. Std\* · Mathematik \(M-GK2\) · \*Vertretung:\* NEU statt ~SCH~/);
    expect(msg).toMatch(/🚪 \*1\. Std\* · Deutsch \(D-GK1\) · MÜL · \*Raum:\* A001 statt ~R112~/);
    expect(msg!.indexOf('19.11.')).toBeLessThan(msg!.indexOf('20.11.'));
  });

  it('fasst Doppelstunden mit gleicher Änderung zusammen', () => {
    const a = period({ date: 20261119, startTime: 750, endTime: 835, code: 'cancelled' });
    const b = period({ date: 20261119, startTime: 835, endTime: 920, code: 'cancelled' });
    const [msg] = buildNotifications([ev(a, 'cancelled'), ev(b, 'cancelled')], ctx);
    expect(msg).toContain('*1.–2. Std*');
    expect(msg?.match(/Entfall/g)).toHaveLength(1);
    expect(msg).toContain('– 1 Änderung'); // gezählt wird, was man liest
  });

  it('beschreibt Rücknahmen, neue, entfernte, verschobene und still geänderte Stunden', () => {
    const p = period({ date: 20261119, startTime: 1130, endTime: 1215 });
    const before = period({ date: 20261119, startTime: 750, endTime: 835 });
    const changedRoom: Period = { ...p, rooms: [{ id: 99, name: 'B201', longName: '' }] };
    const [msg] = buildNotifications(
      [
        ev(p, 'reinstated'),
        ev(p, 'added'),
        ev(p, 'removed'),
        { kinds: ['moved'], period: p, previous: before },
        { kinds: ['modified'], period: changedRoom, previous: p },
      ],
      ctx,
    );
    expect(msg).toContain('findet doch statt');
    expect(msg).toContain('neu im Plan');
    expect(msg).toContain('nicht mehr im Plan');
    expect(msg).toContain('verschoben von 1. Std');
    expect(msg).toContain('*Raum:* B201 (vorher R112)');
  });

  it('schickt bei sehr vielen Änderungen nur eine Zusammenfassung statt Spam', () => {
    const events = Array.from({ length: 30 }, (_, i) =>
      ev(period({ date: 20261124 + (i % 3), startTime: 750 + i, code: 'cancelled' }), 'cancelled'),
    );
    const msgs = buildNotifications(events, ctx);
    expect(msgs).toHaveLength(1);
    expect(msgs[0]).toContain('30 Änderungen');
    expect(msgs[0]).toContain('30 Entfälle');
    expect(msgs[0]).toContain('/woche');
  });

  it('liefert nichts, wenn es nichts zu melden gibt', () => {
    expect(buildNotifications([], ctx)).toEqual([]);
  });
});

describe('formatDayPlan', () => {
  it('listet alle Kurse nach Stunden und markiert Änderungen', () => {
    const periods = [
      period({ startTime: 750, endTime: 835 }),
      period({ startTime: 835, endTime: 920 }),
      period({ startTime: 750, endTime: 835, lsnumber: 5, su: [{ id: 23, name: 'E', longname: 'Englisch' }], sg: 'E-GK1', code: 'cancelled' }),
    ];
    const text = formatDayPlan(periods, 20261118, { timegrid });
    expect(text).toContain('Mi, 18.11.');
    expect(text).toContain('*1.–2. Std* · Deutsch (D-GK1) · MÜL · R112');
    expect(text).toMatch(/~\*1\. Std\* · Englisch \(E-GK1\).*~ ❌ Entfall/);
  });
});

describe('splitMessage', () => {
  it('teilt lange Nachrichten an Zeilengrenzen', () => {
    const text = Array.from({ length: 100 }, (_, i) => `Zeile ${i} ${'x'.repeat(50)}`).join('\n');
    const parts = splitMessage(text, 1000);
    expect(parts.length).toBeGreaterThan(1);
    expect(parts.every((p) => p.length <= 1000)).toBe(true);
    expect(parts.join('\n')).toBe(text);
  });
});
