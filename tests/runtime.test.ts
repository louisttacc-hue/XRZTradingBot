import { existsSync, mkdtempSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { acquireLock } from '../src/runtime.js';

let dir: string;
beforeEach(() => (dir = mkdtempSync(join(tmpdir(), 'efbot-lock-'))));
afterEach(() => rmSync(dir, { recursive: true, force: true }));

describe('acquireLock', () => {
  it('legt ein fehlendes Datenverzeichnis privat an (Erstinstallation)', () => {
    const release = acquireLock(join(dir, 'data', 'bot.lock'));
    expect(existsSync(join(dir, 'data', 'bot.lock'))).toBe(true);
    expect(statSync(join(dir, 'data')).mode & 0o077).toBe(0);
    release();
    expect(existsSync(join(dir, 'data', 'bot.lock'))).toBe(false);
  });

  it('verhindert einen zweiten gleichzeitigen Start', () => {
    writeFileSync(join(dir, 'bot.lock'), String(process.ppid)); // lebender fremder Prozess
    expect(() => acquireLock(join(dir, 'bot.lock'))).toThrow(/läuft bereits/);
  });

  it('übernimmt eine verwaiste Sperre nach einem Absturz', () => {
    writeFileSync(join(dir, 'bot.lock'), '999999999');
    expect(() => acquireLock(join(dir, 'bot.lock'))()).not.toThrow();
  });
});
