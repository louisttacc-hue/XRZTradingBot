import { describe, expect, it } from 'vitest';
import { detectChanges, type DetectionInput, type LedgerEntry } from '../src/domain/detector.js';
import type { Period } from '../src/domain/types.js';
import { period } from './fixtures.js';

const TODAY = 20261118;
const TOMORROW = 20261119;

function input(over: Partial<DetectionInput> & { current: Period[] }): DetectionInput {
  return {
    previous: new Map(),
    window: { from: TODAY, to: 20261201 },
    ledger: new Map(),
    now: { date: TODAY, time: 700 },
    ...over,
  };
}

/** Wendet das Ergebnis auf den Ledger an – so wie es der Monitor tut. */
function applyLedger(ledger: Map<string, LedgerEntry>, result: ReturnType<typeof detectChanges>) {
  for (const key of result.ledgerDelete) ledger.delete(key);
  for (const entry of result.ledgerSet) ledger.set(entry.key, entry);
  return ledger;
}

function snapshot(...periods: Period[]): Map<number, Period[]> {
  const map = new Map<number, Period[]>();
  for (let d = TODAY; d <= 20261130; d++) map.set(d, []); // alle Tage im Fenster wurden abgefragt
  for (const p of periods) map.get(p.date)?.push(p);
  return map;
}

describe('detectChanges – Untis-Kennzeichnungen', () => {
  it('meldet einen Entfall genau einmal', () => {
    const cancelled = period({ date: TOMORROW, code: 'cancelled' });
    const first = detectChanges(input({ current: [cancelled] }));
    expect(first.events).toHaveLength(1);
    expect(first.events[0]?.kinds).toEqual(['cancelled']);

    const ledger = applyLedger(new Map(), first);
    const second = detectChanges(input({ current: [cancelled], ledger }));
    expect(second.events).toEqual([]);
  });

  it('meldet keinen neuen Entfall, wenn sich nur der Hinweistext ändert', () => {
    const before = period({ date: TOMORROW, code: 'cancelled' });
    const ledger = applyLedger(new Map(), detectChanges(input({ current: [before] })));
    const after = { ...before, substText: 'Aufgaben in Moodle' };
    expect(detectChanges(input({ current: [after], ledger })).events).toEqual([]);
  });

  it('erkennt Vertretung und Raumwechsel – auch kombiniert', () => {
    const sub = period({ date: TOMORROW, code: 'irregular', te: [{ id: 11, name: 'NEU', longname: '', orgid: 10, orgname: 'MÜL' }] });
    const room = period({ date: TOMORROW, startTime: 940, ro: [{ id: 31, name: 'A001', longname: '', orgid: 30, orgname: 'R112' }] });
    const both = period({
      date: TOMORROW,
      startTime: 1130,
      te: [{ id: 11, name: 'NEU', longname: '', orgid: 10, orgname: 'MÜL' }],
      ro: [{ id: 31, name: 'A001', longname: '', orgid: 30, orgname: 'R112' }],
    });
    const { events } = detectChanges(input({ current: [sub, room, both] }));
    expect(events.map((e) => e.kinds)).toEqual([['substitution'], ['roomChange'], ['substitution', 'roomChange']]);
  });

  it('meldet sonstige irreguläre Stunden (z. B. Verlegung) als Änderung', () => {
    const { events } = detectChanges(input({ current: [period({ date: TOMORROW, code: 'irregular' })] }));
    expect(events[0]?.kinds).toEqual(['irregular']);
  });

  it('meldet erneut, wenn sich die Vertretung selbst ändert (anderer Vertretungslehrer)', () => {
    const v1 = period({ date: TOMORROW, te: [{ id: 11, name: 'NEU', longname: '', orgid: 10, orgname: 'MÜL' }] });
    const ledger = applyLedger(new Map(), detectChanges(input({ current: [v1] })));
    const v2 = { ...v1, teachers: [{ id: 12, name: 'AND', longName: '', original: { id: 10, name: 'MÜL' } }] };
    const { events } = detectChanges(input({ current: [v2], ledger }));
    expect(events[0]?.kinds).toEqual(['substitution']);
  });

  it('meldet, wenn ein Entfall zurückgenommen wird – und danach nichts mehr', () => {
    const cancelled = period({ date: TOMORROW, code: 'cancelled' });
    const ledger = applyLedger(new Map(), detectChanges(input({ current: [cancelled] })));
    const regular: Period = { ...cancelled, status: 'regular' };
    const result = detectChanges(input({ current: [regular], ledger, previous: snapshot(cancelled) }));
    expect(result.events.map((e) => e.kinds)).toEqual([['reinstated']]);
    applyLedger(ledger, result);
    expect(detectChanges(input({ current: [regular], ledger, previous: snapshot(regular) })).events).toEqual([]);
  });

  it('überlebt einen Untis-Neuimport mit neuen IDs ohne doppelte Meldungen', () => {
    const cancelled = period({ id: 1, date: TOMORROW, code: 'cancelled' });
    const ledger = applyLedger(new Map(), detectChanges(input({ current: [cancelled] })));
    const reimported = { ...cancelled, untisId: 99999 };
    const { events } = detectChanges(input({ current: [reimported], ledger, previous: snapshot(cancelled) }));
    expect(events).toEqual([]);
  });
});

describe('detectChanges – Stundenplanvergleich ohne Kennzeichnung', () => {
  it('meldet entfernte Stunden als "removed"', () => {
    const p = period({ date: TOMORROW });
    const { events } = detectChanges(input({ current: [], previous: snapshot(p) }));
    expect(events.map((e) => e.kinds)).toEqual([['removed']]);
    expect(events[0]?.period.key).toBe(p.key);
  });

  it('meldet einen bereits gemeldeten Entfall nicht erneut, wenn die Stunde danach ganz verschwindet', () => {
    const cancelled = period({ date: TOMORROW, code: 'cancelled' });
    const ledger = applyLedger(new Map(), detectChanges(input({ current: [cancelled] })));
    const result = detectChanges(input({ current: [], ledger, previous: snapshot(cancelled) }));
    expect(result.events).toEqual([]);
    expect(result.ledgerDelete).toContain(cancelled.key);
  });

  it('meldet neue Stunden als "added" – aber nur für Tage, die schon vorher bekannt waren', () => {
    const p = period({ date: TOMORROW });
    expect(detectChanges(input({ current: [p], previous: snapshot() })).events.map((e) => e.kinds)).toEqual([['added']]);
    // Neuer Tag am Ende des Fensters: kein Vergleich möglich → keine Meldung
    expect(detectChanges(input({ current: [p], previous: new Map() })).events).toEqual([]);
  });

  it('meldet geänderten Lehrer/Raum ohne Untis-Kennzeichnung als "modified"', () => {
    const before = period({ date: TOMORROW });
    const after: Period = { ...before, rooms: [{ id: 99, name: 'B201', longName: '' }] };
    const { events } = detectChanges(input({ current: [after], previous: snapshot(before) }));
    expect(events.map((e) => e.kinds)).toEqual([['modified']]);
    expect(events[0]?.previous?.rooms[0]?.name).toBe('R112');
  });

  it('erkennt Verschiebungen über die gleiche Untis-ID', () => {
    const before = period({ id: 5, date: TOMORROW, startTime: 750, endTime: 835 });
    const after = period({ id: 5, date: TOMORROW, startTime: 1130, endTime: 1215 });
    const { events } = detectChanges(input({ current: [after], previous: snapshot(before) }));
    expect(events.map((e) => e.kinds)).toEqual([['moved']]);
    expect(events[0]?.previous?.start).toBe(750);
    expect(events[0]?.period.start).toBe(1130);
  });

  it('meldet eine neue irreguläre Stunde nur einmal (nicht zusätzlich als "added")', () => {
    const extra = period({ date: TOMORROW, code: 'irregular' });
    const { events } = detectChanges(input({ current: [extra], previous: snapshot() }));
    expect(events.map((e) => e.kinds)).toEqual([['irregular']]);
  });

  it('ignoriert unveränderte Stunden', () => {
    const p = period({ date: TOMORROW });
    expect(detectChanges(input({ current: [p], previous: snapshot(p) })).events).toEqual([]);
  });
});

describe('detectChanges – Zeitfenster', () => {
  it('meldet keine Änderungen an Stunden, die heute schon vorbei sind, merkt sie sich aber', () => {
    const past = period({ date: TODAY, startTime: 750, endTime: 835, code: 'cancelled' });
    const result = detectChanges(input({ current: [past], now: { date: TODAY, time: 900 } }));
    expect(result.events).toEqual([]);
    expect(result.ledgerSet.map((e) => e.key)).toContain(past.key);
  });

  it('ignoriert Tage außerhalb des Fensters (z. B. gestern im alten Snapshot)', () => {
    const yesterday = period({ date: 20261117 });
    const previous = snapshot();
    previous.set(20261117, [yesterday]);
    expect(detectChanges(input({ current: [], previous })).events).toEqual([]);
  });

  it('sortiert Meldungen nach Datum und Uhrzeit', () => {
    const a = period({ date: 20261120, code: 'cancelled' });
    const b = period({ date: TOMORROW, startTime: 1130, code: 'cancelled' });
    const c = period({ date: TOMORROW, startTime: 750, code: 'cancelled' });
    const { events } = detectChanges(input({ current: [a, b, c] }));
    expect(events.map((e) => [e.period.date, e.period.start])).toEqual([
      [TOMORROW, 750],
      [TOMORROW, 1130],
      [20261120, 750],
    ]);
  });
});
