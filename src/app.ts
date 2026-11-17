import { join } from 'node:path';
import type { Logger } from 'pino';
import type { BotStatus } from './bot/commands.js';
import { WhatsAppBot, type WhatsAppBotOptions } from './bot/whatsapp.js';
import type { Config } from './config.js';
import { Store } from './db/store.js';
import { appVersion, buildCommandContext } from './runtime.js';
import { Monitor } from './service/monitor.js';
import { WebUntisSource, type UntisSource } from './untis/source.js';

/**
 * Verdrahtet alle Teile des Bots. Wird vom Dauerbetrieb (index.ts) und vom
 * End-to-End-Test identisch genutzt.
 */

export interface AppOptions {
  config: Config;
  logger: Logger;
  /** Optional andere Untis-Quelle (Tests). */
  source?: UntisSource;
  socketFactory?: WhatsAppBotOptions['socketFactory'];
  timing?: WhatsAppBotOptions['timing'];
}

export interface App {
  store: Store;
  bot: WhatsAppBot;
  monitor: Monitor;
  status(): BotStatus;
  start(): Promise<void>;
  stop(): Promise<void>;
}

export function createApp(opts: AppOptions): App {
  const { config, logger } = opts;
  const store = new Store(join(config.dataDir, 'bot.db'));
  const version = appVersion();
  const startedAt = Date.now();

  const bot = new WhatsAppBot({
    config,
    store,
    logger,
    authDir: join(config.dataDir, 'auth'),
    commandContext: () => buildCommandContext(store, config, status),
    socketFactory: opts.socketFactory,
    timing: opts.timing,
  });
  const monitor = new Monitor({
    source: opts.source ?? new WebUntisSource(config.untis, logger),
    store,
    config,
    logger,
    onOutbox: () => bot.flushOutbox(),
  });

  function status(): BotStatus {
    const stats = store.stats();
    const classes = (store.getMeta<{ name: string }[]>('classes') ?? []).map((c) => c.name).filter(Boolean);
    return {
      version,
      startedAt,
      whatsapp: bot.state,
      untis: monitor.untisStatus(),
      nextPollAt: monitor.nextPollAt,
      pollIntervalMinutes: config.pollIntervalMinutes,
      classNames: classes.length ? classes : config.untis.classes,
      groupName: bot.groupName,
      stats: { outboxPending: stats.outboxPending, sentLast24h: stats.sentLast24h, eventsLast24h: stats.eventsLast24h },
    };
  }

  // Regelmäßig Outbox leeren (Ende der Nachtruhe, Wiederholungen nach Fehlern)
  let outboxTimer: NodeJS.Timeout | undefined;

  return {
    store,
    bot,
    monitor,
    status,
    async start() {
      outboxTimer = setInterval(() => bot.flushOutbox(), 30_000);
      outboxTimer.unref();
      monitor.start();
      await bot.start();
    },
    async stop() {
      if (outboxTimer) clearInterval(outboxTimer);
      monitor.stop();
      await bot.stop().catch(() => undefined);
      store.close();
    },
  };
}
