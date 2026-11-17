import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import pino from 'pino';
import type { Lesson } from 'webuntis';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { loadConfig, type Config } from '../src/config.js';
import { Store } from '../src/db/store.js';
import { Monitor, nextPollDelayMs } from '../src/service/monitor.js';
import { UntisError, type UntisSource } from '../src/untis/source.js';
import { rawLesson } from './fixtures.js';

const logger = pino({ level: 'silent' });
let dir: string;
let store: Store;
let lessons: Lesson[];
let failWith: Error | null;
let clock: Date;

const source: UntisSource = {
  async fetch(window) {
    if (failWith) throw failWith;
    return {
      lessons: lessons.filter((l) => l.date >= window.from && l.date <= window.to),
      classes: [{ id: 3, name: 'EF' }],
      timegrid: [{ name: '1', start: 750, end: 835 }],
      holidays: [],
    };
  },
};

function config(over: Record<string, string> = {}): Config {
  return loadConfig({
    UNTIS_SERVER: 'x.webuntis.com',
    UNTIS_SCHOOL: 'x',
    UNTIS_USERNAME: 'u',
    UNTIS_PASSWORD: 'p',
    DATA_DIR: dir,
    ADMIN_NUMBERS: '4915100000',
    ...over,
  });
}

function monitor(over: Record<string, string> = {}) {
  return new Monitor({ source, store, config: config(over), logger, now: () => clock });
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'efbot-monitor-'));
  store = new Store(join(dir, 'bot.db'));
  lessons = [rawLesson({ id: 1, date: 20261119 }), rawLesson({ id: 2, date: 20261120, startTime: 940, endTime: 1025 })];
  failWith = null;
  clock = new Date('2026-11-18T09:00:00Z'); // 10:00 Uhr in Jüchen
});

afterEach(() => {
  store.close();
  rmSync(dir, { recursive: true, force: true });
});

const pending = () => store.dueMessages(clock.getTime() + 8 * 3_600_000);

describe('Monitor', () => {
  it('erster Lauf: merkt sich den Ist-Zustand still und schickt nur eine Begrüßung', async () => {
    lessons.push(rawLesson({ id: 3, date: 20261123, code: 'cancelled' }));
    await monitor().pollOnce();
    const msgs = pending();
    expect(msgs).toHaveLength(1);
    expect(msgs[0]?.text).toContain('Hallo EF');
    expect(msgs[0]?.text).toContain('1 Änderung');
    expect(store.loadLedger().size).toBe(1);
  });

  it('meldet eine neue Änderung genau einmal – auch über einen Neustart hinweg', async () => {
    await monitor({ SEND_WELCOME_MESSAGE: 'false' }).pollOnce();
    expect(pending()).toHaveLength(0);

    lessons[0] = { ...lessons[0]!, code: 'cancelled' };
    const outcome = await monitor({ SEND_WELCOME_MESSAGE: 'false' }).pollOnce();
    expect(outcome.events).toBe(1);
    expect(pending()).toHaveLength(1);
    expect(pending()[0]?.text).toContain('Entfall');

    await monitor({ SEND_WELCOME_MESSAGE: 'false' }).pollOnce();
    expect(pending()).toHaveLength(1);

    // Neustart: Datenbank schließen und neu öffnen
    store.close();
    store = new Store(join(dir, 'bot.db'));
    clock = new Date(clock.getTime() + 5 * 60_000);
    await monitor({ SEND_WELCOME_MESSAGE: 'false' }).pollOnce();
    expect(pending()).toHaveLength(1);
  });

  it('hält Meldungen in der Nachtruhe bis 06:00 zurück', async () => {
    await monitor({ SEND_WELCOME_MESSAGE: 'false' }).pollOnce();
    clock = new Date('2026-11-18T22:30:00Z'); // 23:30 Uhr
    lessons[0] = { ...lessons[0]!, code: 'cancelled' };
    await monitor({ SEND_WELCOME_MESSAGE: 'false' }).pollOnce();
    expect(store.dueMessages(clock.getTime())).toHaveLength(0);
    expect(store.dueMessages(Date.parse('2026-11-19T05:00:00Z'))).toHaveLength(1); // 06:00 Uhr
  });

  it('übersteht Untis-Ausfälle ohne Datenverlust und merkt sich den Fehler', async () => {
    const m = monitor({ SEND_WELCOME_MESSAGE: 'false' });
    await m.pollOnce();
    failWith = new UntisError('network', 'Untis nicht erreichbar (ETIMEDOUT).');
    await expect(m.pollOnce()).rejects.toThrow('ETIMEDOUT');
    expect(m.untisStatus().consecutiveFailures).toBe(1);
    expect(m.untisStatus().lastError).toContain('ETIMEDOUT');
    expect(store.loadSnapshot().get(20261119)).toHaveLength(1);

    failWith = null;
    await m.pollOnce();
    expect(m.untisStatus().consecutiveFailures).toBe(0);
  });

  it('benachrichtigt Admins einmalig bei anhaltenden Fehlern und bei Erholung', async () => {
    const m = monitor({ SEND_WELCOME_MESSAGE: 'false' });
    await m.pollOnce();
    failWith = new UntisError('auth', 'Untis-Login fehlgeschlagen: Benutzername oder Passwort falsch.');
    for (let i = 0; i < 5; i++) await m.pollOnce().catch(() => undefined);
    const alerts = pending().filter((x) => x.chat === '4915100000@s.whatsapp.net');
    expect(alerts).toHaveLength(1);
    expect(alerts[0]?.text).toContain('Passwort');

    failWith = null;
    await m.pollOnce();
    expect(pending().filter((x) => x.chat === '4915100000@s.whatsapp.net').map((x) => x.text).at(-1)).toContain('wieder');
  });

  it('ignoriert einen plötzlich leeren Abruf zunächst (Schutz vor Untis-Störungen)', async () => {
    const m = monitor({ SEND_WELCOME_MESSAGE: 'false' });
    await m.pollOnce();
    const saved = lessons;
    lessons = [];
    expect((await m.pollOnce()).skipped).toBe('empty');
    expect((await m.pollOnce()).skipped).toBe('empty');
    expect(pending()).toHaveLength(0);
    const third = await m.pollOnce();
    expect(third.skipped).toBeUndefined();
    expect(pending()[0]?.text).toContain('nicht mehr im Plan');
    lessons = saved;
  });

  it('speichert Zeitraster, Ferien und Klassen für die Befehle', async () => {
    await monitor().pollOnce();
    expect(store.getMeta('timegrid')).toEqual([{ name: '1', start: 750, end: 835 }]);
    expect(store.getMeta('classes')).toEqual([{ id: 3, name: 'EF' }]);
  });
});

describe('nextPollDelayMs', () => {
  const day = { date: 20261118, time: 1000 };
  it('fragt tagsüber im eingestellten Intervall ab', () => {
    expect(nextPollDelayMs({ intervalMinutes: 5, failures: 0, failureKind: undefined, now: day, random: () => 0.5 })).toBe(5 * 60_000);
  });
  it('wartet nachts und nach Login-Fehlern länger (Account-Sperre vermeiden)', () => {
    expect(nextPollDelayMs({ intervalMinutes: 5, failures: 0, failureKind: undefined, now: { date: 20261118, time: 230 }, random: () => 0.5 })).toBe(30 * 60_000);
    expect(nextPollDelayMs({ intervalMinutes: 5, failures: 1, failureKind: 'auth', now: day, random: () => 0.5 })).toBe(30 * 60_000);
  });
  it('erhöht die Wartezeit bei Netzwerkfehlern schrittweise', () => {
    const d = (failures: number) => nextPollDelayMs({ intervalMinutes: 5, failures, failureKind: 'network', now: day, random: () => 0.5 });
    expect([d(1), d(2), d(3), d(4), d(8)].map((x) => x / 60_000)).toEqual([5, 10, 20, 30, 30]);
  });
});
