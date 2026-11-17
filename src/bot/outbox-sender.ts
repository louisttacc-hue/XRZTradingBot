import type { Logger } from 'pino';
import type { Store } from '../db/store.js';

/**
 * Liefert Nachrichten aus der Outbox aus – gedrosselt, in Reihenfolge und mit einer
 * vorab vergebenen, gespeicherten Nachrichten-ID. Wird eine Nachricht nach Absturz oder
 * Timeout erneut gesendet, trägt sie dieselbe ID; WhatsApp-Clients zeigen sie nur einmal.
 */

export interface OutboxSenderDeps {
  store: Store;
  logger: Logger;
  send: (chat: string, text: string, messageId: string) => Promise<void>;
  isConnected: () => boolean;
  defaultChat: () => string | undefined;
  /** Eigener Chat ("Nachricht an mich selbst") – Ziel für chat = 'self'. */
  selfChat?: () => string | undefined;
  newMessageId: () => string;
  /** Pause zwischen zwei Nachrichten (Spam-/Sperrschutz). */
  gapMs: () => number;
  now?: () => number;
}

const MAX_ATTEMPTS_BEFORE_PAUSE = 5;
/** Platzhalter-Ziel für den eigenen Chat ("Nachricht an mich selbst"). */
export const SELF_CHAT = 'self';

export class OutboxSender {
  private flushing: Promise<void> | null = null;
  private again = false;

  constructor(private readonly deps: OutboxSenderDeps) {}

  /** Sendet alle fälligen Nachrichten. Parallele Aufrufe werden zusammengefasst. */
  flush(): Promise<void> {
    if (this.flushing) {
      this.again = true;
      return this.flushing;
    }
    this.flushing = (async () => {
      try {
        do {
          this.again = false;
          await this.flushOnce();
        } while (this.again);
      } finally {
        this.flushing = null;
      }
    })();
    return this.flushing;
  }

  private async flushOnce(): Promise<void> {
    const { store, logger } = this.deps;
    const now = this.deps.now ?? Date.now;
    const due = store.dueMessages(now());
    let warnedNoGroup = false;
    for (const msg of due) {
      if (!this.deps.isConnected()) return;
      const chat = msg.chat === SELF_CHAT ? this.deps.selfChat?.() : (msg.chat ?? this.deps.defaultChat());
      if (!chat) {
        if (msg.chat === null && !warnedNoGroup) {
          logger.warn('Keine Klassengruppe gewählt – Gruppennachrichten warten (sudo ./install.sh --relink)');
          warnedNoGroup = true;
        }
        continue; // andere Nachrichten (z. B. an Admins) trotzdem zustellen
      }
      if (msg.attempts >= MAX_ATTEMPTS_BEFORE_PAUSE && now() - msg.createdAt < msg.attempts * 60_000) continue;

      let messageId = msg.messageId;
      if (!messageId) {
        messageId = this.deps.newMessageId();
        store.assignMessageId(msg.id, messageId);
      }
      try {
        await this.deps.send(chat, msg.text, messageId);
        store.markSent(msg.id, now());
        logger.info({ chat, id: messageId, chars: msg.text.length }, 'Nachricht gesendet');
      } catch (e) {
        const error = e instanceof Error ? e.message : String(e);
        store.markFailedAttempt(msg.id, error);
        logger.warn({ chat, err: error, attempt: msg.attempts + 1 }, 'Senden fehlgeschlagen – neuer Versuch später');
        return; // Reihenfolge wahren: nicht mit späteren Nachrichten weitermachen
      }
      const gap = this.deps.gapMs();
      if (gap > 0) await new Promise((r) => setTimeout(r, gap));
    }
  }
}
