import { describe, expect, it } from 'vitest';
import { CommandCooldown, parseCommand, runCommand, type BotStatus, type CommandContext } from '../src/bot/commands.js';
import type { Holiday, Period, TimeUnit, UntisDate } from '../src/domain/types.js';
import { addDays } from '../src/domain/time.js';
import { period } from './fixtures.js';

const timegrid: TimeUnit[] = [
  { name: '1', start: 750, end: 835 },
  { name: '2', start: 835, end: 920 },
  { name: '3', start: 940, end: 1025 },
];

const WED = 20261118;
const THU = 20261119;
const FRI = 20261120;
const SAT = 20261121;
const MON = 20261123;

function ctx(periods: Period[], over: Partial<CommandContext> = {}, holidays: Holiday[] = []): CommandContext {
  const fetched = new Set<UntisDate>();
  for (let d = WED; d <= 20261201; d = addDays(d, 1)) fetched.add(d);
  const status: BotStatus = {
    version: '1.0.0',
    startedAt: Date.parse('2026-11-17T08:00:00Z'),
    whatsapp: 'open',
    untis: { lastSuccessAt: Date.parse('2026-11-18T06:40:00Z'), consecutiveFailures: 0 },
    nextPollAt: Date.parse('2026-11-18T06:45:00Z'),
    pollIntervalMinutes: 5,
    classNames: ['EF'],
    groupName: 'EF 2026',
    stats: { outboxPending: 0, sentLast24h: 3, eventsLast24h: 5 },
  };
  return {
    now: { date: WED, time: 742 },
    nowMs: Date.parse('2026-11-18T06:42:00Z'),
    timeZone: 'Europe/Berlin',
    className: 'EF',
    staleAfterMs: 20 * 60_000,
    data: {
      periods: (from, to) => periods.filter((p) => p.date >= from && p.date <= to),
      isFetched: (d) => fetched.has(d),
      timegrid: () => timegrid,
      holidays: () => holidays,
      lastSyncAt: () => status.untis.lastSuccessAt,
      windowEnd: () => 20261201,
    },
    status: () => status,
    ...over,
  };
}

describe('parseCommand', () => {
  it('erkennt Befehle unabhängig von Groß-/Kleinschreibung, Leerzeichen und Aliasen', () => {
    expect(parseCommand('/heute')).toBe('heute');
    expect(parseCommand('  /HEUTE  ')).toBe('heute');
    expect(parseCommand('/Morgen bitte')).toBe('morgen');
    expect(parseCommand('!entfall')).toBe('entfall');
    expect(parseCommand('/entfälle')).toBe('entfall');
    expect(parseCommand('/help')).toBe('hilfe');
    expect(parseCommand('/woche')).toBe('woche');
    expect(parseCommand('/kurse')).toBe('kurse');
    expect(parseCommand('/status')).toBe('status');
  });
  it('ignoriert normale Nachrichten und unbekannte Befehle', () => {
    expect(parseCommand('heute keine Schule?')).toBeNull();
    expect(parseCommand('/foo')).toBeNull();
    expect(parseCommand('')).toBeNull();
  });
});

describe('/heute und /morgen', () => {
  it('zeigt den heutigen Plan aller Kurse mit Stand', () => {
    const text = runCommand('heute', ctx([period({ date: WED }), period({ date: THU })]));
    expect(text).toContain('Heute · Mi, 18.11.');
    expect(text).toContain('Deutsch (D-GK1)');
    expect(text).not.toContain('19.11.');
    expect(text).toContain('Stand: 07:40');
  });

  it('springt bei /morgen am Freitag zum nächsten Schultag', () => {
    const text = runCommand('morgen', ctx([period({ date: MON })], { now: { date: FRI, time: 1200 } }));
    expect(text).toContain('Sa, 21.11.');
    expect(text).toContain('kein Unterricht');
    expect(text).toContain('Mo, 23.11.');
    expect(text).toContain('Deutsch');
  });

  it('nennt Ferien', () => {
    const holidays: Holiday[] = [{ name: 'HF', longName: 'Herbstferien', startDate: THU, endDate: 20261130 }];
    const text = runCommand('morgen', ctx([], {}, holidays));
    expect(text).toContain('Herbstferien');
  });

  it('warnt bei veralteten Daten', () => {
    const c = ctx([period({ date: WED })], { nowMs: Date.parse('2026-11-18T08:00:00Z') });
    expect(runCommand('heute', c)).toContain('⚠️');
  });
});

describe('/entfall', () => {
  it('listet nur kommende Entfälle, gruppiert nach Tag', () => {
    const periods = [
      period({ date: WED, startTime: 750, endTime: 835, code: 'cancelled', su: [{ id: 1, name: 'PH', longname: 'Physik' }], sg: 'PH-GK1' }), // schon vorbei
      period({ date: WED, startTime: 940, endTime: 1025, code: 'cancelled' }),
      period({ date: THU, code: 'cancelled', su: [{ id: 2, name: 'M', longname: 'Mathematik' }], sg: 'M-GK1' }),
      period({ date: THU, startTime: 940, endTime: 1025 }),
    ];
    const text = runCommand('entfall', ctx(periods, { now: { date: WED, time: 900 } }));
    expect(text).not.toContain('Physik');
    expect(text).toContain('Mi, 18.11.');
    expect(text).toContain('Do, 19.11.');
    expect(text).toContain('Mathematik');
    expect(text.match(/❌/g)).toHaveLength(2);
  });

  it('meldet, wenn nichts entfällt', () => {
    expect(runCommand('entfall', ctx([period({ date: THU })]))).toContain('Keine Entfälle');
  });
});

describe('/woche', () => {
  it('zeigt die Schulwoche mit Änderungen je Tag', () => {
    const periods = [
      period({ date: WED }),
      period({ date: THU, code: 'cancelled' }),
      period({ date: FRI, te: [{ id: 11, name: 'NEU', longname: '', orgid: 10, orgname: 'MÜL' }] }),
      period({ date: MON }),
    ];
    const text = runCommand('woche', ctx(periods));
    expect(text).toContain('KW 47');
    expect(text).toContain('Mi, 18.11.');
    expect(text).toContain('keine Änderungen');
    expect(text).toContain('❌');
    expect(text).toContain('NEU statt ~MÜL~');
    expect(text).not.toContain('23.11.');
  });

  it('zeigt am Wochenende die nächste Woche', () => {
    const text = runCommand('woche', ctx([period({ date: MON })], { now: { date: SAT, time: 1000 } }));
    expect(text).toContain('KW 48');
    expect(text).toContain('Mo, 23.11.');
  });
});

describe('/kurse', () => {
  it('listet alle Kurse mit Lehrkraft und Wochenstunden – Vertretungen zählen zum Originallehrer', () => {
    const periods = [
      period({ date: WED, startTime: 750, endTime: 835 }),
      period({ date: WED, startTime: 835, endTime: 920 }),
      period({ date: THU, te: [{ id: 11, name: 'NEU', longname: '', orgid: 10, orgname: 'MÜL' }] }),
      period({ date: WED, lsnumber: 9, su: [{ id: 23, name: 'E', longname: 'Englisch' }], sg: 'E-GK2', te: [{ id: 12, name: 'SCH', longname: '' }] }),
    ];
    const text = runCommand('kurse', ctx(periods));
    expect(text).toContain('2 Kurse');
    expect(text).toMatch(/Deutsch\* \(D-GK1\) – MÜL · 3 Std\/Woche/);
    expect(text).toMatch(/Englisch\* \(E-GK2\) – SCH · 1 Std\/Woche/);
    expect(text).not.toContain('NEU');
  });
});

describe('/status und /hilfe', () => {
  it('zeigt Verbindungen, letzten Abruf und Statistik', () => {
    const text = runCommand('status', ctx([period({ date: WED })]));
    expect(text).toContain('WhatsApp: ✅');
    expect(text).toContain('Untis: ✅');
    expect(text).toContain('07:40');
    expect(text).toContain('Nächster Abruf: 07:45');
    expect(text).toContain('EF');
    expect(text).toContain('1.0.0');
  });

  it('zeigt Untis-Fehler', () => {
    const c = ctx([]);
    const s = c.status();
    s.untis = { ...s.untis, consecutiveFailures: 3, lastError: 'Login fehlgeschlagen' };
    expect(runCommand('status', c)).toContain('Login fehlgeschlagen');
  });

  it('erklärt alle Befehle', () => {
    const text = runCommand('hilfe', ctx([]));
    for (const cmd of ['/heute', '/morgen', '/woche', '/entfall', '/kurse', '/status']) expect(text).toContain(cmd);
  });
});

describe('CommandCooldown', () => {
  it('beantwortet denselben Befehl im selben Chat nur einmal pro Sperrzeit', () => {
    const cd = new CommandCooldown({ perCommandMs: 30_000, maxPerMinute: 10 });
    expect(cd.allow('g1', 'heute', 0)).toBe(true);
    expect(cd.allow('g1', 'heute', 10_000)).toBe(false);
    expect(cd.allow('g1', 'morgen', 10_000)).toBe(true);
    expect(cd.allow('g2', 'heute', 10_000)).toBe(true);
    expect(cd.allow('g1', 'heute', 31_000)).toBe(true);
  });

  it('begrenzt die Gesamtzahl der Antworten pro Minute', () => {
    const cd = new CommandCooldown({ perCommandMs: 0, maxPerMinute: 3 });
    expect([1, 2, 3, 4].map((i) => cd.allow(`c${i}`, 'heute', i))).toEqual([true, true, true, false]);
    expect(cd.allow('c5', 'heute', 61_000)).toBe(true);
  });
});
