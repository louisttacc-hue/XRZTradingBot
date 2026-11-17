import { mkdtempSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { Store } from '../src/db/store.js';
import { period } from './fixtures.js';

let dir: string;
let store: Store;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'efbot-store-'));
  store = new Store(join(dir, 'bot.db'));
});

afterEach(() => {
  store.close();
  rmSync(dir, { recursive: true, force: true });
});

const window = { from: 20261118, to: 20261120 };

describe('Store – Snapshot und Ledger', () => {
  it('speichert einen Abruf atomar und lädt ihn wieder – auch nach Neustart', () => {
    const a = period({ date: 20261118 });
    const b = period({ date: 20261119, code: 'cancelled' });
    store.commitPoll({
      window,
      periods: [a, b],
      ledgerSet: [{ key: b.key, date: b.date, fingerprint: 'cancelled' }],
      ledgerDelete: [],
      messages: [{ text: 'Hallo', notBefore: 0, expiresAt: Date.now() + 60_000 }],
      events: 1,
      fetchedAt: 1000,
    });
    store.close();

    store = new Store(join(dir, 'bot.db'));
    const snap = store.loadSnapshot();
    expect([...snap.keys()]).toEqual([20261118, 20261119, 20261120]); // leerer Tag zählt als abgefragt
    expect(snap.get(20261119)?.[0]).toEqual(b);
    expect(snap.get(20261120)).toEqual([]);
    expect(store.loadLedger().get(b.key)?.fingerprint).toBe('cancelled');
    expect(store.dueMessages(Date.now())).toHaveLength(1);
  });

  it('ersetzt Tage im Fenster komplett und räumt Vergangenes auf', () => {
    const old = period({ date: 20261117 });
    const gone = period({ date: 20261118, startTime: 940 });
    store.commitPoll({ window: { from: 20261117, to: 20261118 }, periods: [old, gone], ledgerSet: [{ key: old.key, date: old.date, fingerprint: 'x' }], ledgerDelete: [], messages: [], events: 0, fetchedAt: 1 });

    const kept = period({ date: 20261118, startTime: 750 });
    store.commitPoll({ window, periods: [kept], ledgerSet: [], ledgerDelete: [], messages: [], events: 0, fetchedAt: 2 });

    const snap = store.loadSnapshot();
    expect(snap.has(20261117)).toBe(false);
    expect(snap.get(20261118)?.map((p) => p.key)).toEqual([kept.key]);
    expect(store.loadLedger().size).toBe(0);
  });

  it('liefert Stunden für einen Zeitraum sortiert', () => {
    const late = period({ date: 20261119, startTime: 1130 });
    const early = period({ date: 20261119, startTime: 750 });
    store.commitPoll({ window, periods: [late, early], ledgerSet: [], ledgerDelete: [], messages: [], events: 0, fetchedAt: 1 });
    expect(store.loadPeriods(20261119, 20261119).map((p) => p.start)).toEqual([750, 1130]);
  });
});

describe('Store – Outbox', () => {
  it('liefert fällige Nachrichten erst ab notBefore und nur bis zum Sendeerfolg', () => {
    store.enqueue({ text: 'später', notBefore: 5000, expiresAt: 10_000 });
    expect(store.dueMessages(4000)).toEqual([]);
    const [msg] = store.dueMessages(5000);
    expect(msg?.text).toBe('später');

    store.assignMessageId(msg!.id, 'ABC123');
    expect(store.dueMessages(5000)[0]?.messageId).toBe('ABC123'); // gleiche ID bei erneutem Versuch
    store.markSent(msg!.id, 6000);
    expect(store.dueMessages(6000)).toEqual([]);
  });

  it('verwirft abgelaufene Nachrichten statt sie verspätet zu senden', () => {
    store.enqueue({ text: 'alt', notBefore: 0, expiresAt: 1000 });
    expect(store.dueMessages(2000)).toEqual([]);
    expect(store.stats().outboxExpired).toBe(1);
  });

  it('merkt sich Fehlversuche', () => {
    const id = store.enqueue({ text: 'x', notBefore: 0, expiresAt: 10_000 });
    store.markFailedAttempt(id, 'offline');
    expect(store.dueMessages(1)[0]?.attempts).toBe(1);
  });
});

describe('Store – Meta und Nachrichtenspeicher', () => {
  it('speichert beliebige JSON-Werte', () => {
    store.setMeta('groupJid', '123@g.us');
    store.setMeta('timegrid', [{ name: '1', start: 750, end: 835 }]);
    expect(store.getMeta<string>('groupJid')).toBe('123@g.us');
    expect(store.getMeta('timegrid')).toEqual([{ name: '1', start: 750, end: 835 }]);
    expect(store.getMeta('fehlt')).toBeUndefined();
  });

  it('speichert gesendete Nachrichten für WhatsApp-Wiederholungsanfragen', () => {
    store.rememberSentMessage('MSG1', '123@g.us', { conversation: 'hi' });
    expect(store.getSentMessage('MSG1')).toEqual({ conversation: 'hi' });
    expect(store.getSentMessage('NOPE')).toBeUndefined();
  });

  it('legt die Datenbankdatei nur für den Besitzer lesbar an', () => {
    expect(statSync(join(dir, 'bot.db')).mode & 0o077).toBe(0);
  });
});
