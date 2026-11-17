import { EventEmitter } from 'node:events';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import pino from 'pino';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp, type App } from '../src/app.js';
import { loadConfig, type Config } from '../src/config.js';
import { addDays, schoolNow } from '../src/domain/time.js';
import { WebUntisSource } from '../src/untis/source.js';
import { FakeUntis, fakeLesson } from './fake-untis.js';

/**
 * End-to-End: echte Verdrahtung (createApp) mit echtem Untis-Adapter (webuntis-Bibliothek)
 * gegen einen Fake-Untis-Server, echter SQLite-Datenbank und echtem WhatsAppBot –
 * nur der WhatsApp-Socket selbst ist simuliert.
 */

const GROUP = '120363011111111111@g.us';
const OTHER_GROUP = '120363022222222222@g.us';

interface Sent {
  jid: string;
  text: string;
  messageId?: string;
  quoted: boolean;
}

class FakeSocket {
  ev = new EventEmitter();
  user = { id: '4915199999999:3@s.whatsapp.net', lid: '77777:3@lid' };
  presenceCalls = 0;
  ended = false;
  constructor(private readonly log: Sent[]) {}
  async sendMessage(jid: string, content: { text: string }, options: { messageId?: string; quoted?: unknown } = {}) {
    const entry: Sent = { jid, text: content.text, quoted: Boolean(options.quoted) };
    if (options.messageId) entry.messageId = options.messageId;
    this.log.push(entry);
    return { key: { id: options.messageId ?? `AUTO${this.log.length}`, remoteJid: jid, fromMe: true }, message: { conversation: content.text } };
  }
  async sendPresenceUpdate() {
    this.presenceCalls++;
  }
  async groupFetchAllParticipating() {
    return { [GROUP]: { id: GROUP, subject: 'EF 2026/27', participants: [{ id: 'a' }, { id: 'b' }] } };
  }
  async groupMetadata(jid: string) {
    return { id: jid, subject: 'EF 2026/27', participants: [] };
  }
  end() {
    this.ended = true;
  }
  open() {
    this.ev.emit('connection.update', { connection: 'open' });
  }
  close(statusCode: number) {
    this.ev.emit('connection.update', { connection: 'close', lastDisconnect: { error: Object.assign(new Error('closed'), { output: { statusCode } }) } });
  }
  receive(text: string, chat = GROUP, opts: { fromMe?: boolean; id?: string } = {}) {
    this.ev.emit('messages.upsert', {
      type: 'notify',
      messages: [
        {
          key: { remoteJid: chat, fromMe: opts.fromMe ?? false, id: opts.id ?? `IN${Math.random()}`, participant: '1234@lid' },
          messageTimestamp: Math.floor(Date.now() / 1000),
          message: { conversation: text },
        },
      ],
    });
  }
}

let dir: string;
let fake: FakeUntis;
let baseUrl: string;
let sent: Sent[];
let sockets: FakeSocket[];
let app: App | undefined;

const today = schoolNow(new Date()).date;
const d1 = addDays(today, 1);
const d2 = addDays(today, 2);

function config(over: Record<string, string> = {}): Config {
  return loadConfig({
    WHATSAPP_OWN_NUMBER: 'false',
    UNTIS_SERVER: 'gym.webuntis.com',
    UNTIS_SCHOOL: 'gym-test',
    UNTIS_USERNAME: 'schueler',
    UNTIS_PASSWORD: 'richtig',
    UNTIS_CLASSES: 'EF',
    DATA_DIR: dir,
    QUIET_HOURS: '', // Test soll zu jeder Uhrzeit laufen
    COMMAND_COOLDOWN_SECONDS: '0',
    ...over,
  });
}

function startApp(over: Record<string, string> = {}): App {
  const cfg = config(over);
  const logger = pino({ level: 'silent' });
  app = createApp({
    config: cfg,
    logger,
    source: new WebUntisSource(cfg.untis, logger, { baseUrlOverride: baseUrl, timeoutMs: 3000 }),
    socketFactory: () => {
      const s = new FakeSocket(sent);
      sockets.push(s);
      return s as never;
    },
    timing: { typingMs: () => 0, gapMs: () => 0 },
  });
  return app;
}

const lastSocket = () => sockets[sockets.length - 1]!;

beforeEach(async () => {
  dir = mkdtempSync(join(tmpdir(), 'efbot-e2e-'));
  fake = new FakeUntis({ schoolyears: [{ id: 10, name: 'SJ', startDate: addDays(today, -100), endDate: addDays(today, 200) }] });
  baseUrl = await fake.start();
  sent = [];
  sockets = [];
  fake.lessons = [
    fakeLesson({ id: 1, date: d1, startTime: 750, endTime: 835 }),
    fakeLesson({ id: 2, date: d1, startTime: 835, endTime: 920 }),
    fakeLesson({ id: 3, date: d1, startTime: 750, endTime: 835, lsnumber: 5, su: [{ id: 22, name: 'M', longname: 'Mathematik' }], sg: 'M-GK2', te: [{ id: 12, name: 'SCH' }], ro: [{ id: 31, name: 'R204' }] }),
    fakeLesson({ id: 4, date: d2, startTime: 940, endTime: 1025, lsnumber: 6, su: [{ id: 23, name: 'E', longname: 'Englisch' }], sg: 'E-GK1', te: [{ id: 13, name: 'KRA' }] }),
    fakeLesson({ id: 5, date: d2, kl: [{ id: 4, name: 'Q1' }] }), // andere Stufe → darf nie auftauchen
  ];
});

afterEach(async () => {
  await app?.stop();
  app = undefined;
  await fake.stop();
  rmSync(dir, { recursive: true, force: true });
});

describe('End-to-End', () => {
  it('überwacht Untis, meldet Änderungen genau einmal in der Gruppe und beantwortet Befehle', async () => {
    const a = startApp();
    await a.start();
    await vi.waitFor(() => expect(a.store.getMeta('initialized')).toBe(true));
    lastSocket().open();

    // 1) Begrüßung landet in der automatisch erkannten Klassengruppe
    await vi.waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ jid: GROUP });
    expect(sent[0]?.text).toContain('Hallo EF');
    expect(a.bot.groupName).toBe('EF 2026/27');

    // 2) Untis ändert sich: Entfall (Doppelstunde) + Raumwechsel
    fake.lessons[0]!.code = 'cancelled';
    fake.lessons[1]!.code = 'cancelled';
    fake.lessons[2]!.ro = [{ id: 32, name: 'A001', orgid: 31, orgname: 'R204' }];
    await a.monitor.pollOnce();
    await vi.waitFor(() => expect(sent).toHaveLength(2));
    const update = sent[1]!;
    expect(update.jid).toBe(GROUP);
    expect(update.text).toContain('2 Änderungen');
    expect(update.text).toMatch(/❌ \*1\.–2\. Std\* · Deutsch \(D-GK1\)/);
    expect(update.text).toContain('*Raum:* A001 statt ~R204~');
    expect(update.text).not.toContain('Q1');
    expect(update.messageId).toBeTruthy();

    // 3) Gleicher Stand → keine weitere Nachricht
    await a.monitor.pollOnce();
    await new Promise((r) => setTimeout(r, 100));
    expect(sent).toHaveLength(2);

    // 4) Befehle in der Gruppe
    lastSocket().receive('/entfall');
    await vi.waitFor(() => expect(sent).toHaveLength(3));
    expect(sent[2]).toMatchObject({ jid: GROUP, quoted: true });
    expect(sent[2]?.text).toContain('Entfälle EF');
    expect(sent[2]?.text).toContain('Deutsch');

    lastSocket().receive('/kurse');
    await vi.waitFor(() => expect(sent).toHaveLength(4));
    expect(sent[3]?.text).toContain('3 Kurse');

    lastSocket().receive('/status');
    await vi.waitFor(() => expect(sent).toHaveLength(5));
    expect(sent[4]?.text).toContain('WhatsApp: ✅');
    expect(sent[4]?.text).toContain('Untis: ✅');

    // Befehle aus fremden Gruppen und normale Nachrichten werden ignoriert
    lastSocket().receive('/heute', OTHER_GROUP);
    lastSocket().receive('Wer hat Mathe-Hausaufgaben?');
    await new Promise((r) => setTimeout(r, 150));
    expect(sent).toHaveLength(5);

    // 5) Verbindungsabbruch → automatischer Reconnect, nichts wird doppelt gesendet
    lastSocket().close(515);
    await vi.waitFor(() => expect(sockets).toHaveLength(2), { timeout: 5000 });
    lastSocket().open();
    await a.monitor.pollOnce();
    await new Promise((r) => setTimeout(r, 150));
    expect(sent).toHaveLength(5);

    // Untis-Session wurde jedes Mal sauber beendet
    expect(fake.activeSessions.size).toBe(0);
  });

  it('sendet nach einem Neustart nichts doppelt und meldet nur echte neue Änderungen', async () => {
    let a = startApp();
    await a.start();
    await vi.waitFor(() => expect(a.store.getMeta('initialized')).toBe(true));
    lastSocket().open();
    await vi.waitFor(() => expect(sent).toHaveLength(1)); // Begrüßung

    fake.lessons[3]!.te = [{ id: 14, name: 'NEU', orgid: 13, orgname: 'KRA' }];
    await a.monitor.pollOnce();
    await vi.waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[1]?.text).toContain('*Vertretung:* NEU statt ~KRA~');

    // Neustart (z. B. Server-Reboot)
    await a.stop();
    a = startApp();
    await a.start();
    lastSocket().open();
    await a.monitor.pollOnce();
    await new Promise((r) => setTimeout(r, 200));
    expect(sent).toHaveLength(2); // keine Dubletten, keine zweite Begrüßung

    // Vertretung wird zurückgenommen → "findet doch statt"
    fake.lessons[3]!.te = [{ id: 13, name: 'KRA' }];
    await a.monitor.pollOnce();
    await vi.waitFor(() => expect(sent).toHaveLength(3));
    expect(sent[2]?.text).toContain('findet doch statt');
  });

  it('hält Meldungen zurück, solange WhatsApp getrennt ist, und liefert sie danach aus', async () => {
    const a = startApp();
    await a.start();
    await vi.waitFor(() => expect(a.store.getMeta('initialized')).toBe(true));
    // Noch keine Verbindung: Änderung passiert
    fake.lessons[0]!.code = 'cancelled';
    await a.monitor.pollOnce();
    expect(sent).toHaveLength(0);
    expect(a.store.stats().outboxPending).toBe(2); // Begrüßung + Änderung

    lastSocket().open();
    await vi.waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[0]?.text).toContain('Hallo EF');
    expect(sent[1]?.text).toContain('Entfall');
  });

  it('löscht bei Abmeldung vom Handy die Sitzung und fordert neue Kopplung an', async () => {
    const a = startApp();
    await a.start();
    lastSocket().open();
    expect(existsSync(join(dir, 'auth'))).toBe(true);
    lastSocket().close(401);
    await vi.waitFor(() => expect(a.bot.state).toBe('loggedOut'));
    await vi.waitFor(() => expect(existsSync(join(dir, 'auth'))).toBe(false));
  });

  it('läuft ohne zweite SIM auf der eigenen Nummer: 🤖-Kennzeichnung, eigene Befehle, keine Schleifen', async () => {
    const SELF = '4915199999999@s.whatsapp.net';
    const a = startApp({ WHATSAPP_OWN_NUMBER: 'true' });
    await a.start();
    await vi.waitFor(() => expect(a.store.getMeta('initialized')).toBe(true));
    lastSocket().open();

    // Keine automatische Gruppenwahl auf der privaten Nummer → nichts landet in einer Gruppe
    await new Promise((r) => setTimeout(r, 150));
    expect(a.bot.groupJid).toBeUndefined();
    expect(sent).toHaveLength(0);
    lastSocket().receive('/heute'); // jemand in irgendeiner Gruppe
    await new Promise((r) => setTimeout(r, 100));
    expect(sent).toHaveLength(0);

    // "Nachricht an mich selbst" funktioniert immer
    lastSocket().receive('/status', SELF, { fromMe: true, id: 'PHONE-1' });
    await vi.waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]?.jid).toBe(SELF);
    expect(sent[0]?.text.startsWith('🤖 *Bot-Status*')).toBe(true); // kein doppeltes 🤖

    // Gruppe wählen (passiert sonst im Setup) → Begrüßung geht raus, gekennzeichnet
    a.bot.selectGroup(GROUP, 'EF 2026/27');
    a.bot.flushOutbox();
    await vi.waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[1]).toMatchObject({ jid: GROUP });
    expect(sent[1]?.text).toMatch(/^🤖 👋 \*Hallo EF!\* Ab jetzt läuft über diesen Account/);

    // Änderung wird gemeldet
    fake.lessons[0]!.code = 'cancelled';
    await a.monitor.pollOnce();
    await vi.waitFor(() => expect(sent).toHaveLength(3));
    expect(sent[2]?.text).toMatch(/^🤖 🔔 \*Vertretungsplan EF\*/);

    // Du selbst schreibst /entfall in die Gruppe → Antwort
    lastSocket().receive('/entfall', GROUP, { fromMe: true, id: 'PHONE-2' });
    await vi.waitFor(() => expect(sent).toHaveLength(4));
    expect(sent[3]?.text).toContain('Entfälle EF');

    // Echo der eigenen Bot-Nachricht (gleiche ID) löst nie etwas aus
    lastSocket().receive('/heute', GROUP, { fromMe: true, id: sent[3]!.messageId! });
    await new Promise((r) => setTimeout(r, 150));
    expect(sent).toHaveLength(4);

    // Kein "tippt …" auf der privaten Nummer (Handy-Benachrichtigungen bleiben normal)
    expect(lastSocket().presenceCalls).toBe(0);
  });
});
