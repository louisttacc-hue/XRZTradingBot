import pino, { type Logger } from 'pino';

/**
 * Logger mit Schwärzung sensibler Felder. Für systemd/journalctl wird lesbar formatiert.
 */
export function createLogger(level: string, pretty = process.env.LOG_FORMAT !== 'json'): Logger {
  const options: pino.LoggerOptions = {
    level,
    redact: {
      paths: ['password', 'secret', '*.password', '*.secret', 'untis.password', 'untis.secret', 'creds', '*.creds'],
      censor: '[geheim]',
    },
  };
  if (pretty) {
    return pino({
      ...options,
      transport: {
        target: 'pino-pretty',
        options: { colorize: process.stdout.isTTY, translateTime: 'SYS:dd.mm. HH:MM:ss', ignore: 'pid,hostname' },
      },
    });
  }
  return pino(options);
}
