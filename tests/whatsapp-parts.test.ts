import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import pino from 'pino';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { CommandCooldown } from '../src/bot/commands.js';
import { OutboxSender } from '../src/bot/outbox-sender.js';
import { extractIncoming, routeCommand, type RouteContext } from '../src/bot/router.js';
import { Store } from '../src/db/store.js';

const GROUP = '120363000000000000@g.us';
const NOW = 1_800_000_000_000;

function waMessage(over: { chat?: string; text?: string; fromMe?: boolean; ts?: number; alt?: string; ephemeral?: boolean; id?: string }) {
  const content = { extendedTextMessage: { text: over.text ?? '/heute' } };
  return {
    key: { remoteJid: over.chat ?? GROUP, fromMe: over.fromMe ?? false, id: over.id ?? 'X1', participant: '123@lid', remoteJidAlt: over.alt },
    messageTimestamp: over.ts ?? NOW / 1000,
    message: over.ephemeral ? { ephemeralMessage: { message: content } } : content,
  };
}

function rctx(over: Partial<RouteContext> = {}): RouteContext {
  return {
    groupJid: GROUP,
    allowPrivate: false,
    adminNumbers: ['4915100000'],
    nowMs: NOW,
    cooldown: new CommandCooldown({ perCommandMs: 30_000, maxPerMinute: 20 }),
    ownNumber: false,
    selfJids: [],
    isBotMessage: () => false,
    ...over,
  };
}

describe('extractIncoming', () => {
  it('liest Text auch aus verschwindenden Nachrichten und Bildunterschriften', () => {
    expect(extractIncoming(waMessage({ ephemeral: true, text: '/woche' }) as never)?.text).toBe('/woche');
    const img = { key: { remoteJid: GROUP, fromMe: false }, messageTimestamp: NOW / 1000, message: { imageMessage: { caption: '/kurse' } } };
    expect(extractIncoming(img as never)?.text).toBe('/kurse');
  });
  it('ignoriert Nachrichten ohne Text', () => {
    const sticker = { key: { remoteJid: GROUP, fromMe: false }, messageTimestamp: NOW / 1000, message: { stickerMessage: {} } };
    expect(extractIncoming(sticker as never)).toBeNull();
  });
});

describe('routeCommand', () => {
  const route = (msg: ReturnType<typeof waMessage>, c = rctx()) => {
    const incoming = extractIncoming(msg as never);
    return incoming ? routeCommand(incoming, c) : null;
  };

  it('beantwortet Befehle in der Klassengruppe', () => {
    expect(route(waMessage({}))).toBe('heute');
  });
  it('ignoriert eigene, alte und fremde Nachrichten', () => {
    expect(route(waMessage({ fromMe: true }))).toBeNull();
    expect(route(waMessage({ ts: NOW / 1000 - 600 }))).toBeNull(); // Rückstau nach Reconnect
    expect(route(waMessage({ chat: '999@g.us' }))).toBeNull(); // andere Gruppe
    expect(route(waMessage({ chat: 'status@broadcast' }))).toBeNull();
    expect(route(waMessage({ text: 'Hat jemand die Hausaufgaben?' }))).toBeNull();
  });
  it('erlaubt private Chats nur, wenn freigegeben oder für Admins', () => {
    expect(route(waMessage({ chat: '4917600000@s.whatsapp.net' }))).toBeNull();
    expect(route(waMessage({ chat: '4917600000@s.whatsapp.net' }), rctx({ allowPrivate: true }))).toBe('heute');
    expect(route(waMessage({ chat: '4915100000@s.whatsapp.net' }))).toBe('heute');
    expect(route(waMessage({ chat: '555@lid', alt: '4915100000@s.whatsapp.net' }))).toBe('heute');
  });
  describe('auf der eigenen Nummer', () => {
    const own = (over: Partial<RouteContext> = {}) =>
      rctx({ ownNumber: true, selfJids: ['4915111111111@s.whatsapp.net', '98765@lid'], isBotMessage: (id) => id === 'BOT1', ...over });

    it('beantwortet Befehle, die du selbst vom Handy schreibst', () => {
      expect(route(waMessage({ fromMe: true, id: 'PHONE1' }), own())).toBe('heute');
    });
    it('reagiert nie auf eigene Bot-Nachrichten (keine Endlosschleife)', () => {
      expect(route(waMessage({ fromMe: true, id: 'BOT1' }), own())).toBeNull();
    });
    it('erlaubt Befehle im Chat "Nachricht an mich selbst" (Telefonnummer oder LID)', () => {
      expect(route(waMessage({ chat: '4915111111111@s.whatsapp.net', fromMe: true, id: 'P2' }), own())).toBe('heute');
      expect(route(waMessage({ chat: '98765@lid', fromMe: true, id: 'P3' }), own())).toBe('heute');
    });
    it('antwortet ohne gewählte Klassengruppe in keiner Gruppe (nicht versehentlich in der Familiengruppe)', () => {
      expect(route(waMessage({}), own({ groupJid: undefined }))).toBeNull();
      expect(route(waMessage({ chat: '4915111111111@s.whatsapp.net', fromMe: true, id: 'P4' }), own({ groupJid: undefined }))).toBe('heute');
    });
    it('ignoriert eigene Nachrichten in fremden Privatchats', () => {
      expect(route(waMessage({ chat: '4917600000@s.whatsapp.net', fromMe: true, id: 'P5' }), own())).toBeNull();
    });
  });

  it('ignoriert im Modus "eigene Bot-Nummer" weiterhin eigene Nachrichten', () => {
    expect(route(waMessage({ fromMe: true, id: 'PHONE1' }))).toBeNull();
  });

  it('drosselt wiederholte Befehle', () => {
    const c = rctx();
    expect(route(waMessage({}), c)).toBe('heute');
    expect(route(waMessage({}), c)).toBeNull();
  });
});

describe('OutboxSender', () => {
  let dir: string;
  let store: Store;
  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'efbot-outbox-'));
    store = new Store(join(dir, 'bot.db'));
  });
  afterEach(() => {
    store.close();
    rmSync(dir, { recursive: true, force: true });
  });

  function sender(send: (chat: string, text: string, id: string) => Promise<void>, connected = () => true) {
    let n = 0;
    return new OutboxSender({
      store,
      logger: pino({ level: 'silent' }),
      send,
      isConnected: connected,
      defaultChat: () => GROUP,
      newMessageId: () => `ID${++n}`,
      gapMs: () => 0,
      now: () => Date.now(),
    });
  }

  it('sendet fällige Nachrichten in Reihenfolge an die Gruppe und markiert sie', async () => {
    store.enqueue({ text: 'eins', notBefore: 0, expiresAt: Date.now() + 60_000 });
    store.enqueue({ text: 'zwei', notBefore: 0, expiresAt: Date.now() + 60_000, chat: '4915100000@s.whatsapp.net' });
    const sent: string[] = [];
    await sender(async (chat, text) => void sent.push(`${chat}:${text}`)).flush();
    expect(sent).toEqual([`${GROUP}:eins`, '4915100000@s.whatsapp.net:zwei']);
    expect(store.dueMessages(Date.now())).toEqual([]);
  });

  it('verwendet bei einem erneuten Versuch dieselbe Nachrichten-ID (keine Dubletten bei Absturz/Timeout)', async () => {
    store.enqueue({ text: 'x', notBefore: 0, expiresAt: Date.now() + 60_000 });
    const ids: string[] = [];
    let fail = true;
    const s = sender(async (_c, _t, id) => {
      ids.push(id);
      if (fail) throw new Error('timeout');
    });
    await s.flush();
    fail = false;
    await s.flush();
    expect(ids).toEqual(['ID1', 'ID1']);
    expect(store.dueMessages(Date.now())).toEqual([]);
  });

  it('wartet, solange WhatsApp nicht verbunden ist', async () => {
    store.enqueue({ text: 'x', notBefore: 0, expiresAt: Date.now() + 60_000 });
    let calls = 0;
    await sender(async () => void calls++, () => false).flush();
    expect(calls).toBe(0);
    expect(store.dueMessages(Date.now())).toHaveLength(1);
  });

  it('wartet ohne bekannte Gruppe, statt die Nachricht zu verlieren', async () => {
    store.enqueue({ text: 'x', notBefore: 0, expiresAt: Date.now() + 60_000 });
    const s = new OutboxSender({
      store,
      logger: pino({ level: 'silent' }),
      send: async () => {
        throw new Error('darf nicht senden');
      },
      isConnected: () => true,
      defaultChat: () => undefined,
      newMessageId: () => 'A',
      gapMs: () => 0,
      now: () => Date.now(),
    });
    await s.flush();
    expect(store.dueMessages(Date.now())).toHaveLength(1);
  });

  it('stellt Admin-Nachrichten zu, auch wenn Gruppennachrichten noch auf die Gruppenwahl warten', async () => {
    store.enqueue({ text: 'an Gruppe', notBefore: 0, expiresAt: Date.now() + 60_000 });
    store.enqueue({ text: 'an Admin', notBefore: 0, expiresAt: Date.now() + 60_000, chat: '4915100000@s.whatsapp.net' });
    const sent: string[] = [];
    const s = new OutboxSender({
      store,
      logger: pino({ level: 'silent' }),
      send: async (chat, text) => void sent.push(`${chat}:${text}`),
      isConnected: () => true,
      defaultChat: () => undefined,
      newMessageId: () => `ID${sent.length}`,
      gapMs: () => 0,
    });
    await s.flush();
    expect(sent).toEqual(['4915100000@s.whatsapp.net:an Admin']);
    expect(store.dueMessages(Date.now()).map((m) => m.text)).toEqual(['an Gruppe']);
  });

  it('schickt Nachrichten an "self" in den eigenen Chat, sobald dieser bekannt ist', async () => {
    store.enqueue({ text: 'Fehler!', notBefore: 0, expiresAt: Date.now() + 60_000, chat: 'self' });
    const sent: string[] = [];
    let self: string | undefined;
    const s = new OutboxSender({
      store,
      logger: pino({ level: 'silent' }),
      send: async (chat, text) => void sent.push(`${chat}:${text}`),
      isConnected: () => true,
      defaultChat: () => GROUP,
      selfChat: () => self,
      newMessageId: () => 'S1',
      gapMs: () => 0,
    });
    await s.flush();
    expect(sent).toEqual([]);
    self = '4915111111111@s.whatsapp.net';
    await s.flush();
    expect(sent).toEqual(['4915111111111@s.whatsapp.net:Fehler!']);
  });

  it('läuft nie doppelt parallel', async () => {
    store.enqueue({ text: 'x', notBefore: 0, expiresAt: Date.now() + 60_000 });
    let calls = 0;
    const s = sender(async () => {
      calls++;
      await new Promise((r) => setTimeout(r, 20));
    });
    await Promise.all([s.flush(), s.flush(), s.flush()]);
    expect(calls).toBe(1);
  });
});
