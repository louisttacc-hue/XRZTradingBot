import { join } from 'node:path';
import { createApp } from './app.js';
import { createLogger } from './logger.js';
import { acquireLock, appVersion, loadConfigOrExit, loadEnv } from './runtime.js';

/**
 * Einstiegspunkt des Dauerbetriebs (systemd startet `node dist/index.js`).
 */
async function main(): Promise<void> {
  loadEnv();
  const config = loadConfigOrExit();
  const logger = createLogger(config.logLevel);
  const releaseLock = acquireLock(join(config.dataDir, 'bot.lock'));

  logger.info(
    {
      version: appVersion(),
      school: config.untis.school,
      server: config.untis.server,
      classes: config.untis.classes,
      auth: config.untis.auth,
      pollMinutes: config.pollIntervalMinutes,
    },
    'EF-Untis-WhatsApp-Bot startet',
  );

  const app = createApp({ config, logger });

  let shuttingDown = false;
  const shutdown = async (signal: string) => {
    if (shuttingDown) return;
    shuttingDown = true;
    logger.info({ signal }, 'Beende Bot …');
    await app.stop();
    releaseLock();
    logger.info('Bot beendet');
    setTimeout(() => process.exit(0), 300).unref();
  };
  process.on('SIGINT', () => void shutdown('SIGINT'));
  process.on('SIGTERM', () => void shutdown('SIGTERM'));
  process.on('unhandledRejection', (reason) => {
    logger.error({ err: reason instanceof Error ? reason.stack : String(reason) }, 'Unbehandelte Promise-Ablehnung');
  });
  process.on('uncaughtException', (err) => {
    logger.fatal({ err: err.stack }, 'Unerwarteter Fehler – Neustart durch systemd');
    try {
      app.store.close();
      releaseLock();
    } finally {
      process.exit(1);
    }
  });

  await app.start();
}

main().catch((e: unknown) => {
  console.error('❌ Start fehlgeschlagen:', e instanceof Error ? e.message : e);
  process.exit(1);
});
