import type { Logger } from 'pino';
import { helpText } from '../bot/commands.js';
import { quietHoursDelayMs, type Config } from '../config.js';
import type { Store } from '../db/store.js';
import { detectChanges, notableState } from '../domain/detector.js';
import { addDays, schoolNow } from '../domain/time.js';
import type { TimeUnit, UntisDate, UntisTime } from '../domain/types.js';
import { buildNotifications } from '../format/messages.js';
import { normalizeLessons } from '../untis/normalize.js';
import { UntisError, type UntisErrorKind, type UntisSource } from '../untis/source.js';

/**
 * Überwachungsschleife: alle n Minuten Untis abrufen, Änderungen erkennen und
 * die Meldungen zusammen mit dem neuen Stand atomar in der Datenbank ablegen.
 * Das Versenden übernimmt der WhatsApp-Teil über die Outbox.
 */

export interface UntisStatus {
  lastSuccessAt?: number | undefined;
  lastAttemptAt?: number | undefined;
  lastError?: string | undefined;
  lastErrorKind?: UntisErrorKind | undefined;
  consecutiveFailures: number;
}

export interface PollOutcome {
  periods: number;
  events: number;
  skipped?: 'empty';
}

export interface MonitorDeps {
  source: UntisSource;
  store: Store;
  config: Config;
  logger: Logger;
  now?: () => Date;
  /** Wird aufgerufen, sobald neue Nachrichten in der Outbox liegen. */
  onOutbox?: () => void;
}

const MASTER_DATA_MAX_AGE_MS = 6 * 3_600_000;
const MESSAGE_TTL_MS = 12 * 3_600_000;
const EMPTY_CONFIRMATIONS = 3;
const MAX_DELAY_MS = 30 * 60_000;

export class Monitor {
  private status: UntisStatus = { consecutiveFailures: 0 };
  private emptyStreak = 0;
  private alerted = false;
  private alertQueued = false;
  private timer: NodeJS.Timeout | undefined;
  private running = false;
  private stopped = true;
  nextPollAt: number | undefined;

  constructor(private readonly deps: MonitorDeps) {}

  private now(): Date {
    return this.deps.now ? this.deps.now() : new Date();
  }

  untisStatus(): UntisStatus {
    return { ...this.status, lastSuccessAt: this.status.lastSuccessAt ?? this.deps.store.getMeta<number>('lastSuccessAt') };
  }

  /** Ein kompletter Durchlauf. Wirft bei Untis-Fehlern (Status wird vorher aktualisiert). */
  async pollOnce(): Promise<PollOutcome> {
    const { store, config, logger } = this.deps;
    const nowDate = this.now();
    const nowMs = nowDate.getTime();
    const now = schoolNow(nowDate, config.timeZone);
    const window = { from: now.date, to: addDays(now.date, config.lookaheadDays - 1) };
    this.status.lastAttemptAt = nowMs;

    const masterDataAt = store.getMeta<number>('masterDataAt');
    const masterDue = !masterDataAt || nowMs - masterDataAt > MASTER_DATA_MAX_AGE_MS || !store.getMeta('timegrid');

    let fetched;
    try {
      fetched = await this.deps.source.fetch(window, { masterData: masterDue });
    } catch (e) {
      this.recordFailure(e);
      throw e;
    }

    if (masterDue) {
      if (fetched.timegrid?.length) store.setMeta('timegrid', fetched.timegrid);
      if (fetched.holidays) store.setMeta('holidays', fetched.holidays);
      store.setMeta('masterDataAt', nowMs);
    }
    store.setMeta('classes', fetched.classes);

    const periods = normalizeLessons(fetched.lessons);
    const previous = store.loadSnapshot();

    // Schutz vor Untis-Störungen: Ein plötzlich komplett leerer Plan wird erst nach mehrfacher Bestätigung übernommen.
    const previousInWindow = [...previous.entries()]
      .filter(([d]) => d >= window.from && d <= window.to)
      .reduce((n, [, list]) => n + list.length, 0);
    if (periods.length === 0 && previousInWindow > 0 && ++this.emptyStreak < EMPTY_CONFIRMATIONS) {
      logger.warn({ previousInWindow, streak: this.emptyStreak }, 'Untis liefert plötzlich keinen Unterricht – warte auf Bestätigung');
      this.recordSuccess(nowMs);
      return { periods: 0, events: 0, skipped: 'empty' };
    }
    if (periods.length > 0) this.emptyStreak = 0;

    const ledger = store.loadLedger();
    const baseline = store.getMeta<boolean>('initialized') !== true;
    const detection = detectChanges({ previous, current: periods, window, ledger, now });
    const timegrid = store.getMeta<TimeUnit[]>('timegrid') ?? [];

    const delay = quietHoursDelayMs(now, config.quietHours);
    const texts = baseline
      ? []
      : buildNotifications(detection.events, { timegrid, maxEvents: config.maxEventsPerMessage, className: config.displayName });
    const messages = texts.map((text) => ({ text, notBefore: nowMs + delay, expiresAt: nowMs + delay + MESSAGE_TTL_MS }));

    if (baseline && config.sendWelcomeMessage && !store.getMeta('welcomed')) {
      const known = periods.filter((p) => notableState(p)).length;
      messages.push({ text: welcomeText(config, known), notBefore: nowMs + delay, expiresAt: nowMs + delay + MESSAGE_TTL_MS });
      store.setMeta('welcomed', true);
    }

    store.commitPoll({
      window,
      periods,
      ledgerSet: detection.ledgerSet,
      ledgerDelete: detection.ledgerDelete,
      messages,
      events: baseline ? 0 : detection.events.length,
      fetchedAt: nowMs,
    });
    if (baseline) {
      store.setMeta('initialized', true);
      logger.info({ periods: periods.length, known: detection.ledgerSet.length }, 'Erster Abruf: Ist-Zustand gespeichert (keine Meldungen)');
    } else if (detection.events.length > 0) {
      logger.info({ events: detection.events.length, messages: messages.length }, 'Änderungen erkannt');
    } else {
      logger.debug({ periods: periods.length }, 'Keine Änderungen');
    }

    this.recordSuccess(nowMs);
    if (messages.length > 0 || this.alertQueued) this.deps.onOutbox?.();
    this.alertQueued = false;
    return { periods: periods.length, events: baseline ? 0 : detection.events.length };
  }

  private recordSuccess(nowMs: number): void {
    const { store } = this.deps;
    if (this.alerted) {
      this.alertAdmins(`✅ Untis-Abruf funktioniert wieder (nach ${this.status.consecutiveFailures} Fehlversuchen).`, nowMs);
      this.alerted = false;
    }
    this.status = { ...this.status, consecutiveFailures: 0, lastError: undefined, lastErrorKind: undefined, lastSuccessAt: nowMs };
    store.setMeta('lastSuccessAt', nowMs);
  }

  private recordFailure(e: unknown): void {
    const err = e instanceof UntisError ? e : new UntisError('unknown', e instanceof Error ? e.message : String(e));
    this.status = {
      ...this.status,
      consecutiveFailures: this.status.consecutiveFailures + 1,
      lastError: err.message,
      lastErrorKind: err.kind,
    };
    this.deps.logger.error({ kind: err.kind, failures: this.status.consecutiveFailures }, err.message);

    const threshold = err.kind === 'network' || err.kind === 'unknown' ? 6 : 2;
    if (!this.alerted && this.status.consecutiveFailures >= threshold) {
      this.alerted = true;
      this.alertAdmins(
        `⚠️ *EF-Bot:* Untis-Abruf schlägt seit ${this.status.consecutiveFailures} Versuchen fehl.\n${err.message}\n\nDer Bot versucht es automatisch weiter. Details: journalctl -u ef-untis-bot`,
        this.now().getTime(),
      );
      this.deps.onOutbox?.();
    }
  }

  private alertAdmins(text: string, nowMs: number): void {
    for (const number of this.deps.config.adminNumbers) {
      this.deps.store.enqueue({ text, chat: `${number}@s.whatsapp.net`, notBefore: 0, expiresAt: nowMs + MESSAGE_TTL_MS });
      this.alertQueued = true;
    }
  }

  // ── Zeitsteuerung ──────────────────────────────────────────────────

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.schedule(0);
  }

  stop(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = undefined;
    this.nextPollAt = undefined;
  }

  /** Sofortiger Abruf außer der Reihe (z. B. nach Reconnect). */
  triggerSoon(): void {
    if (!this.stopped && !this.running) this.schedule(2_000);
  }

  private schedule(delayMs: number): void {
    if (this.stopped) return;
    if (this.timer) clearTimeout(this.timer);
    this.nextPollAt = Date.now() + delayMs;
    this.timer = setTimeout(() => void this.tick(), delayMs);
    this.timer.unref?.();
  }

  private async tick(): Promise<void> {
    if (this.running || this.stopped) return;
    this.running = true;
    try {
      await this.pollOnce();
    } catch {
      // bereits geloggt & im Status vermerkt
    } finally {
      this.running = false;
      const now = schoolNow(this.now(), this.deps.config.timeZone);
      this.schedule(
        nextPollDelayMs({
          intervalMinutes: this.deps.config.pollIntervalMinutes,
          failures: this.status.consecutiveFailures,
          failureKind: this.status.lastErrorKind,
          now,
          random: Math.random,
        }),
      );
    }
  }
}

/** Abstand bis zum nächsten Abruf – mit Backoff bei Fehlern, Nachtmodus und etwas Zufall. */
export function nextPollDelayMs(opts: {
  intervalMinutes: number;
  failures: number;
  failureKind: UntisErrorKind | undefined;
  now: { date: UntisDate; time: UntisTime };
  random: () => number;
}): number {
  const base = opts.intervalMinutes * 60_000;
  let delay = base;
  if (opts.failures > 0) {
    delay =
      opts.failureKind === 'auth' || opts.failureKind === 'config' || opts.failureKind === 'permission'
        ? MAX_DELAY_MS
        : Math.min(base * 2 ** (Math.min(opts.failures, 6) - 1), MAX_DELAY_MS);
  }
  // Nachts (00:00–05:00) ändert sich praktisch nichts – Untis schonen.
  if (opts.now.time < 500) delay = Math.max(delay, MAX_DELAY_MS);
  const jitter = 1 + (opts.random() - 0.5) * 0.2; // ±10 %
  return Math.round(delay * jitter);
}

function welcomeText(config: Config, known: number): string {
  return [
    `👋 *Hallo ${config.displayName}!* Ich bin euer Vertretungsplan-Bot.`,
    `Ich schaue alle ${config.pollIntervalMinutes} Minuten in WebUntis nach und melde hier automatisch Entfall, Vertretungen, Raumwechsel und Stundenplanänderungen für *alle* ${config.displayName}-Kurse.`,
    '',
    `Aktuell bekannt: ${known === 1 ? '1 Änderung' : `${known} Änderungen`} in den nächsten ${config.lookaheadDays} Tagen → /entfall, /woche`,
    '',
    helpText(config.displayName),
  ].join('\n');
}
