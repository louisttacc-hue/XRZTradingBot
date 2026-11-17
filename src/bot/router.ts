import { isJidBroadcast, isJidGroup, isJidNewsletter, normalizeMessageContent, type WAMessage } from 'baileys';
import { parseCommand, type CommandCooldown, type CommandName } from './commands.js';

/**
 * Entscheidet, ob eine eingehende WhatsApp-Nachricht ein Befehl ist, auf den der Bot antworten darf.
 */

export interface IncomingMessage {
  chat: string;
  /** Telefonnummer-JID des Chats, falls WhatsApp den Chat per LID adressiert. */
  chatAlt?: string | undefined;
  text: string;
  fromMe: boolean;
  timestampMs: number;
}

export interface RouteContext {
  /** Ziel-Gruppe. Unbekannt → Befehle in jeder Gruppe erlaubt (Einrichtungsphase). */
  groupJid: string | undefined;
  allowPrivate: boolean;
  adminNumbers: string[];
  nowMs: number;
  cooldown: CommandCooldown;
}

/** Nachrichten, die älter sind, werden ignoriert (z. B. Rückstau nach einem Verbindungsabbruch). */
const MAX_AGE_MS = 2 * 60_000;

export function extractIncoming(msg: WAMessage): IncomingMessage | null {
  const chat = msg.key?.remoteJid;
  if (!chat) return null;
  const content = normalizeMessageContent(msg.message);
  const text =
    content?.conversation ??
    content?.extendedTextMessage?.text ??
    content?.imageMessage?.caption ??
    content?.videoMessage?.caption ??
    null;
  if (!text) return null;
  const ts = Number(msg.messageTimestamp ?? 0);
  return {
    chat,
    chatAlt: (msg.key as { remoteJidAlt?: string }).remoteJidAlt,
    text,
    fromMe: Boolean(msg.key.fromMe),
    timestampMs: ts * 1000,
  };
}

function phoneOf(jid: string | undefined): string | undefined {
  return jid?.endsWith('@s.whatsapp.net') ? jid.split('@')[0]?.split(':')[0] : undefined;
}

export function routeCommand(msg: IncomingMessage, ctx: RouteContext): CommandName | null {
  if (msg.fromMe) return null;
  if (isJidBroadcast(msg.chat) || isJidNewsletter(msg.chat) || msg.chat === 'status@broadcast') return null;
  if (msg.timestampMs > 0 && ctx.nowMs - msg.timestampMs > MAX_AGE_MS) return null;

  const command = parseCommand(msg.text);
  if (!command) return null;

  if (isJidGroup(msg.chat)) {
    if (ctx.groupJid && msg.chat !== ctx.groupJid) return null;
  } else {
    const phone = phoneOf(msg.chat) ?? phoneOf(msg.chatAlt);
    const isAdmin = phone !== undefined && ctx.adminNumbers.includes(phone);
    if (!ctx.allowPrivate && !isAdmin) return null;
  }

  return ctx.cooldown.allow(msg.chat, command, ctx.nowMs) ? command : null;
}
