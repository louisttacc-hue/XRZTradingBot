import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import pino from 'pino';
import { UntisError, WebUntisSource } from '../src/untis/source.js';
import type { Config } from '../src/config.js';
import { FakeUntis, fakeLesson } from './fake-untis.js';

let fake: FakeUntis;
let baseUrl: string;
const logger = pino({ level: 'silent' });

function untisConfig(over: Partial<Config['untis']> = {}): Config['untis'] {
  return { server: 'unused.webuntis.com', school: 'gym-test', auth: 'password', username: 'schueler', password: 'richtig', secret: '', classes: ['EF'], ...over };
}

function source(over: Partial<Config['untis']> = {}) {
  return new WebUntisSource(untisConfig(over), logger, { baseUrlOverride: baseUrl, timeoutMs: 3000 });
}

beforeEach(async () => {
  fake = new FakeUntis();
  baseUrl = await fake.start();
});

afterEach(async () => {
  await fake.stop();
});

describe('WebUntisSource (echte webuntis-Bibliothek gegen Fake-Server)', () => {
  it('lädt den kompletten EF-Stundenplan (alle Kurse) und meldet sich wieder ab', async () => {
    fake.lessons = [
      fakeLesson({ id: 1, date: 20261118 }),
      fakeLesson({ id: 2, date: 20261119, su: [{ id: 21, name: 'M', longname: 'Mathematik' }], sg: 'M-GK1', code: 'cancelled' }),
      fakeLesson({ id: 3, date: 20261118, kl: [{ id: 4, name: 'Q1' }] }), // andere Stufe
      fakeLesson({ id: 4, date: 20261210 }), // außerhalb des Fensters
    ];
    const result = await source().fetch({ from: 20261118, to: 20261201 }, { masterData: true });

    expect(result.lessons.map((l) => l.id).sort()).toEqual([1, 2]);
    expect(result.classes).toEqual([{ id: 3, name: 'EF' }]);
    expect(result.timegrid).toEqual([
      { name: '1', start: 750, end: 835 },
      { name: '2', start: 835, end: 920 },
      { name: '3', start: 940, end: 1025 },
    ]);
    expect(result.holidays?.[0]).toMatchObject({ longName: 'Weihnachtsferien', startDate: 20261221 });
    expect(fake.calls[0]).toBe('authenticate');
    expect(fake.calls.at(-1)).toBe('logout');
    expect(fake.activeSessions.size).toBe(0);
  });

  it('begrenzt den Abruf auf das Schuljahr (kein Fehler am Schuljahresende)', async () => {
    fake.lessons = [fakeLesson({ id: 1, date: 20270714 })];
    const result = await source().fetch({ from: 20270713, to: 20270727 }, { masterData: false });
    expect(result.lessons.map((l) => l.id)).toEqual([1]);
  });

  it('liefert in den Sommerferien (kein Schuljahr) einfach keine Stunden', async () => {
    const result = await source().fetch({ from: 20270720, to: 20270802 }, { masterData: false });
    expect(result.lessons).toEqual([]);
    expect(fake.calls).not.toContain('getTimetable');
  });

  it('meldet falsche Zugangsdaten verständlich und ohne Passwort im Text', async () => {
    const err = await source({ password: 'xyz-wrong-123' }).fetch({ from: 20261118, to: 20261118 }, { masterData: false }).catch((e) => e);
    expect(err).toBeInstanceOf(UntisError);
    expect(err.kind).toBe('auth');
    expect(err.message).toMatch(/Login/);
    expect(err.message).not.toContain('xyz-wrong-123');
  });

  it('meldet eine unbekannte Klasse mit Liste der vorhandenen Klassen', async () => {
    const err = await source({ classes: ['EX'] }).fetch({ from: 20261118, to: 20261118 }, { masterData: false }).catch((e) => e);
    expect(err.kind).toBe('config');
    expect(err.message).toContain('EF, Q1');
    expect(fake.activeSessions.size).toBe(0); // trotz Fehler abgemeldet
  });

  it('meldet fehlende Berechtigung für den Klassenplan', async () => {
    await fake.stop();
    fake = new FakeUntis({ forbiddenClasses: [3] });
    baseUrl = await fake.start();
    const err = await source().fetch({ from: 20261118, to: 20261118 }, { masterData: false }).catch((e) => e);
    expect(err.kind).toBe('permission');
  });

  it('nutzt ohne konfigurierte Klasse die eigene Klasse des Accounts', async () => {
    fake.lessons = [fakeLesson({ id: 7 })];
    const result = await source({ classes: [] }).fetch({ from: 20261118, to: 20261118 }, { masterData: false });
    expect(result.lessons.map((l) => l.id)).toEqual([7]);
    expect(result.classes).toEqual([{ id: 3, name: 'EF' }]);
  });

  it('meldet HTTP-Sperren (z. B. Firewall) nicht fälschlich als falsches Passwort', async () => {
    fake.failNext = 1;
    fake.failStatus = 403;
    const err = await source().fetch({ from: 20261118, to: 20261118 }, { masterData: false }).catch((e) => e);
    expect(err.kind).toBe('network');
    expect(err.message).toContain('403');
    expect(err.message).not.toMatch(/Passwort/);
  });

  it('klassifiziert Serverausfälle als vorübergehend', async () => {
    fake.failNext = 1;
    const err = await source().fetch({ from: 20261118, to: 20261118 }, { masterData: false }).catch((e) => e);
    expect(err.kind).toBe('network');
  });
});
