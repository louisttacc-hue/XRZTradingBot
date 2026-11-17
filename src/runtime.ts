import { closeSync, existsSync, mkdirSync, openSync, readFileSync, unlinkSync, writeSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import dotenv from 'dotenv';
import type { BotData, BotStatus, CommandContext } from './bot/commands.js';
import { ConfigError, loadConfig, type Config } from './config.js';
import type { Store } from './db/store.js';
import { addDays, schoolNow } from './domain/time.js';
import type { Holiday, TimeUnit } from './domain/types.js';

/** .env laden (Pfad über ENV_FILE änderbar). Bereits gesetzte Variablen haben Vorrang. */
export function loadEnv(): void {
  dotenv.config({ path: process.env.ENV_FILE ?? resolve(process.cwd(), '.env'), quiet: true });
}

export function appVersion(): string {
  try {
    const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')) as { version?: string };
    return pkg.version ?? '0.0.0';
  } catch {
    return '0.0.0';
  }
}

/**
 * Verhindert, dass Bot und Setup (oder zwei Bots) gleichzeitig laufen – zwei
 * WhatsApp-Verbindungen mit derselben Sitzung werfen sich gegenseitig raus.
 */
export function acquireLock(path: string): () => void {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  if (existsSync(path)) {
    const pid = Number(readFileSync(path, 'utf8').trim());
    if (pid && pid !== process.pid && isAlive(pid)) {
      throw new Error(
        `Der Bot läuft bereits (PID ${pid}). Für das Setup zuerst stoppen: sudo systemctl stop ef-untis-bot`,
      );
    }
    unlinkSync(path);
  }
  const fd = openSync(path, 'wx', 0o600);
  writeSync(fd, String(process.pid));
  closeSync(fd);
  return () => {
    try {
      if (readFileSync(path, 'utf8').trim() === String(process.pid)) unlinkSync(path);
    } catch {
      /* schon weg */
    }
  };
}

function isAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return (e as NodeJS.ErrnoException).code === 'EPERM';
  }
}

/** Baut den Kontext für Chat-Befehle aus dem gespeicherten Untis-Stand. */
export function buildCommandContext(store: Store, config: Config, status: () => BotStatus): CommandContext {
  const nowDate = new Date();
  const now = schoolNow(nowDate, config.timeZone);
  const fetched = store.fetchedDates(now.date, addDays(now.date, 60));
  const windowEnd = fetched.size ? Math.max(...fetched.keys()) : undefined;

  const data: BotData = {
    periods: (from, to) => store.loadPeriods(from, to),
    isFetched: (date) => fetched.has(date),
    timegrid: () => store.getMeta<TimeUnit[]>('timegrid') ?? [],
    holidays: () => store.getMeta<Holiday[]>('holidays') ?? [],
    lastSyncAt: () => store.getMeta<number>('lastSuccessAt'),
    windowEnd: () => windowEnd,
  };
  return {
    now,
    nowMs: nowDate.getTime(),
    timeZone: config.timeZone,
    className: config.displayName,
    staleAfterMs: Math.max(20, config.pollIntervalMinutes * 4) * 60_000,
    data,
    status,
  };
}

/** Konfiguration laden; bei Fehlern verständliche Meldung und Exit-Code 78 (EX_CONFIG). */
export function loadConfigOrExit(): Config {
  try {
    return loadConfig();
  } catch (e) {
    if (e instanceof ConfigError) {
      console.error(`❌ ${e.message}\n\nBitte .env prüfen (Vorlage: .env.example) oder ./install.sh erneut ausführen.`);
      process.exit(78); // systemd startet dann nicht endlos neu (RestartPreventExitStatus=78)
    }
    throw e;
  }
}
