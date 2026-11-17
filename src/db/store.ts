import { chmodSync, closeSync, existsSync, mkdirSync, openSync } from 'node:fs';
import { dirname } from 'node:path';
import Database from 'better-sqlite3';
import type { LedgerEntry } from '../domain/detector.js';
import { addDays } from '../domain/time.js';
import type { Period, UntisDate } from '../domain/types.js';

/**
 * Persistenz (SQLite). Alles, was ein Poll-Durchlauf ändert – Snapshot, Ledger und
 * ausgehende Nachrichten – wird in EINER Transaktion geschrieben. Stürzt der Bot ab,
 * ist der Stand entweder ganz alt oder ganz neu: keine verlorenen und keine doppelten
 * Meldungen.
 */

export interface OutboxDraft {
  text: string;
  /** Frühester Sendezeitpunkt (ms) – z. B. Ende der Nachtruhe. */
  notBefore: number;
  /** Danach wird die Nachricht verworfen (ms). */
  expiresAt: number;
  /** Ziel-Chat; leer = konfigurierte Klassengruppe. */
  chat?: string;
}

export interface OutboxMessage {
  id: number;
  text: string;
  chat: string | null;
  messageId: string | null;
  attempts: number;
  createdAt: number;
}

export interface PollCommit {
  window: { from: UntisDate; to: UntisDate };
  periods: readonly Period[];
  ledgerSet: readonly LedgerEntry[];
  ledgerDelete: readonly string[];
  messages: readonly OutboxDraft[];
  events: number;
  fetchedAt: number;
}

export interface StoreStats {
  outboxPending: number;
  outboxSent: number;
  outboxExpired: number;
  sentLast24h: number;
  eventsLast24h: number;
  ledgerSize: number;
  snapshotPeriods: number;
}

const MIGRATIONS: string[] = [
  `
  CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
  );
  CREATE TABLE snapshot_days (
    date INTEGER PRIMARY KEY,
    fetched_at INTEGER NOT NULL
  );
  CREATE TABLE snapshot_periods (
    date INTEGER NOT NULL,
    key TEXT NOT NULL,
    start INTEGER NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (date, key)
  );
  CREATE TABLE ledger (
    key TEXT PRIMARY KEY,
    date INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    updated_at INTEGER NOT NULL
  );
  CREATE INDEX ledger_date ON ledger(date);
  CREATE TABLE outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat TEXT,
    text TEXT NOT NULL,
    message_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'expired')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at INTEGER NOT NULL,
    not_before INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    sent_at INTEGER
  );
  CREATE INDEX outbox_due ON outbox(status, not_before);
  CREATE TABLE sent_messages (
    id TEXT PRIMARY KEY,
    chat TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL
  );
  CREATE TABLE poll_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at INTEGER NOT NULL,
    periods INTEGER NOT NULL,
    events INTEGER NOT NULL
  );
  `,
];

export class Store {
  private readonly db: Database.Database;

  constructor(path: string) {
    if (path !== ':memory:') {
      mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
      if (!existsSync(path)) closeSync(openSync(path, 'a', 0o600));
      chmodSync(path, 0o600);
    }
    this.db = new Database(path);
    this.db.pragma('journal_mode = WAL');
    this.db.pragma('synchronous = NORMAL');
    this.db.pragma('busy_timeout = 5000');
    this.migrate();
  }

  private migrate(): void {
    const version = this.db.pragma('user_version', { simple: true }) as number;
    for (let v = version; v < MIGRATIONS.length; v++) {
      this.db.transaction(() => {
        this.db.exec(MIGRATIONS[v]!);
        this.db.pragma(`user_version = ${v + 1}`);
      })();
    }
  }

  close(): void {
    if (this.db.open) this.db.close();
  }

  // ── Snapshot & Ledger ────────────────────────────────────────────────

  loadSnapshot(): Map<UntisDate, Period[]> {
    const result = new Map<UntisDate, Period[]>();
    for (const row of this.db.prepare('SELECT date FROM snapshot_days ORDER BY date').all() as { date: number }[]) {
      result.set(row.date, []);
    }
    const rows = this.db.prepare('SELECT date, data FROM snapshot_periods ORDER BY date, start, key').all() as {
      date: number;
      data: string;
    }[];
    for (const row of rows) result.get(row.date)?.push(JSON.parse(row.data) as Period);
    return result;
  }

  loadPeriods(from: UntisDate, to: UntisDate): Period[] {
    const rows = this.db
      .prepare('SELECT data FROM snapshot_periods WHERE date BETWEEN ? AND ? ORDER BY date, start, key')
      .all(from, to) as { data: string }[];
    return rows.map((r) => JSON.parse(r.data) as Period);
  }

  /** Datum → Zeitpunkt des letzten erfolgreichen Abrufs. */
  fetchedDates(from: UntisDate, to: UntisDate): Map<UntisDate, number> {
    const rows = this.db.prepare('SELECT date, fetched_at FROM snapshot_days WHERE date BETWEEN ? AND ?').all(from, to) as {
      date: number;
      fetched_at: number;
    }[];
    return new Map(rows.map((r) => [r.date, r.fetched_at]));
  }

  loadLedger(): Map<string, LedgerEntry> {
    const rows = this.db.prepare('SELECT key, date, fingerprint FROM ledger').all() as LedgerEntry[];
    return new Map(rows.map((r) => [r.key, { key: r.key, date: r.date, fingerprint: r.fingerprint }]));
  }

  commitPoll(commit: PollCommit): void {
    const { window, periods, fetchedAt } = commit;
    const tx = this.db.transaction(() => {
      // Vergangenes aufräumen
      this.db.prepare('DELETE FROM snapshot_days WHERE date < ?').run(window.from);
      this.db.prepare('DELETE FROM snapshot_periods WHERE date < ?').run(window.from);
      this.db.prepare('DELETE FROM ledger WHERE date < ?').run(window.from);

      // Tage im Fenster vollständig ersetzen
      this.db.prepare('DELETE FROM snapshot_periods WHERE date BETWEEN ? AND ?').run(window.from, window.to);
      const day = this.db.prepare('INSERT OR REPLACE INTO snapshot_days (date, fetched_at) VALUES (?, ?)');
      for (let d = window.from; d <= window.to; d = addDays(d, 1)) day.run(d, fetchedAt);

      const insert = this.db.prepare('INSERT OR REPLACE INTO snapshot_periods (date, key, start, data) VALUES (?, ?, ?, ?)');
      for (const p of periods) {
        if (p.date >= window.from && p.date <= window.to) insert.run(p.date, p.key, p.start, JSON.stringify(p));
      }

      const del = this.db.prepare('DELETE FROM ledger WHERE key = ?');
      for (const key of commit.ledgerDelete) del.run(key);
      const upsert = this.db.prepare(
        'INSERT INTO ledger (key, date, fingerprint, updated_at) VALUES (?, ?, ?, ?) ' +
          'ON CONFLICT(key) DO UPDATE SET fingerprint = excluded.fingerprint, date = excluded.date, updated_at = excluded.updated_at',
      );
      for (const e of commit.ledgerSet) upsert.run(e.key, e.date, e.fingerprint, fetchedAt);

      for (const m of commit.messages) this.insertOutbox(m, fetchedAt);

      this.db.prepare('INSERT INTO poll_log (fetched_at, periods, events) VALUES (?, ?, ?)').run(fetchedAt, periods.length, commit.events);
      this.db.prepare('DELETE FROM poll_log WHERE fetched_at < ?').run(fetchedAt - 7 * 86_400_000);
    });
    tx();
  }

  // ── Outbox ───────────────────────────────────────────────────────────

  private insertOutbox(m: OutboxDraft, now: number): number {
    const info = this.db
      .prepare('INSERT INTO outbox (chat, text, created_at, not_before, expires_at) VALUES (?, ?, ?, ?, ?)')
      .run(m.chat ?? null, m.text, now, m.notBefore, m.expiresAt);
    return Number(info.lastInsertRowid);
  }

  enqueue(m: OutboxDraft): number {
    return this.insertOutbox(m, Date.now());
  }

  /** Fällige Nachrichten in Erzeugungsreihenfolge; Abgelaufenes wird dabei verworfen. */
  dueMessages(now: number, limit = 20): OutboxMessage[] {
    this.db.prepare("UPDATE outbox SET status = 'expired' WHERE status = 'pending' AND expires_at <= ?").run(now);
    const rows = this.db
      .prepare(
        "SELECT id, text, chat, message_id, attempts, created_at FROM outbox WHERE status = 'pending' AND not_before <= ? ORDER BY id LIMIT ?",
      )
      .all(now, limit) as { id: number; text: string; chat: string | null; message_id: string | null; attempts: number; created_at: number }[];
    return rows.map((r) => ({ id: r.id, text: r.text, chat: r.chat, messageId: r.message_id, attempts: r.attempts, createdAt: r.created_at }));
  }

  /** Vergibt die WhatsApp-Nachrichten-ID VOR dem Senden – Wiederholungen nutzen dieselbe ID. */
  assignMessageId(id: number, messageId: string): void {
    this.db.prepare('UPDATE outbox SET message_id = ? WHERE id = ? AND message_id IS NULL').run(messageId, id);
  }

  markSent(id: number, at = Date.now()): void {
    this.db.prepare("UPDATE outbox SET status = 'sent', sent_at = ? WHERE id = ?").run(at, id);
  }

  markFailedAttempt(id: number, error: string): void {
    this.db.prepare('UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE id = ?').run(error.slice(0, 500), id);
  }

  // ── Meta ─────────────────────────────────────────────────────────────

  getMeta<T = unknown>(key: string): T | undefined {
    const row = this.db.prepare('SELECT value FROM meta WHERE key = ?').get(key) as { value: string } | undefined;
    return row ? (JSON.parse(row.value) as T) : undefined;
  }

  setMeta(key: string, value: unknown): void {
    this.db
      .prepare('INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value')
      .run(key, JSON.stringify(value));
  }

  deleteMeta(key: string): void {
    this.db.prepare('DELETE FROM meta WHERE key = ?').run(key);
  }

  // ── Gesendete Nachrichten (für Baileys getMessage / Retry) ─────────

  rememberSentMessage(id: string, chat: string, content: unknown): void {
    this.db
      .prepare('INSERT OR REPLACE INTO sent_messages (id, chat, content, created_at) VALUES (?, ?, ?, ?)')
      .run(id, chat, JSON.stringify(content), Date.now());
    this.db.prepare('DELETE FROM sent_messages WHERE created_at < ?').run(Date.now() - 7 * 86_400_000);
  }

  /** Stammt diese WhatsApp-Nachrichten-ID vom Bot selbst? (Outbox oder gesendete Antworten) */
  isBotMessageId(id: string): boolean {
    return Boolean(
      this.db.prepare('SELECT 1 FROM sent_messages WHERE id = ? UNION SELECT 1 FROM outbox WHERE message_id = ? LIMIT 1').get(id, id),
    );
  }

  getSentMessage<T = unknown>(id: string): T | undefined {
    const row = this.db.prepare('SELECT content FROM sent_messages WHERE id = ?').get(id) as { content: string } | undefined;
    return row ? (JSON.parse(row.content) as T) : undefined;
  }

  // ── Statistik ────────────────────────────────────────────────────────

  stats(now = Date.now()): StoreStats {
    const count = (sql: string, ...params: unknown[]) => (this.db.prepare(sql).get(...params) as { n: number }).n;
    const dayAgo = now - 86_400_000;
    return {
      outboxPending: count("SELECT COUNT(*) n FROM outbox WHERE status = 'pending'"),
      outboxSent: count("SELECT COUNT(*) n FROM outbox WHERE status = 'sent'"),
      outboxExpired: count("SELECT COUNT(*) n FROM outbox WHERE status = 'expired'"),
      sentLast24h: count("SELECT COUNT(*) n FROM outbox WHERE status = 'sent' AND sent_at >= ?", dayAgo),
      eventsLast24h: count('SELECT COALESCE(SUM(events), 0) n FROM poll_log WHERE fetched_at >= ?', dayAgo),
      ledgerSize: count('SELECT COUNT(*) n FROM ledger'),
      snapshotPeriods: count('SELECT COUNT(*) n FROM snapshot_periods'),
    };
  }
}
