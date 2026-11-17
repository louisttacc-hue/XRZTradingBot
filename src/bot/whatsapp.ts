import { chmod, mkdir, rm } from 'node:fs/promises';
import { join } from 'node:path';
import makeWASocket, {
  Browsers,
  DisconnectReason,
  fetchLatestBaileysVersion,
  generateMessageIDV2,
  isJidGroup,
  makeCacheableSignalKeyStore,
  useMultiFileAuthState,
  type GroupMetadata,
  type WAMessage,
  type proto,
} from 'baileys';
import type { Logger } from 'pino';
import QRCode from 'qrcode';
import type { Config } from '../config.js';
import type { Store } from '../db/store.js';
import { splitMessage } from '../format/messages.js';
import { CommandCooldown, runCommand, type CommandContext, type WhatsAppState } from './commands.js';
import { OutboxSender } from './outbox-sender.js';
import { bareJid, extractIncoming, routeCommand } from './router.js';

/**
 * WhatsApp-Anbindung über Baileys (inoffizielle WhatsApp-Web-Schnittstelle).
 *
 * Sperrrisiko minimieren:
 *  - nur in der eigenen Klassengruppe schreiben, nie ungefragt an Fremde
 *  - gedrosseltes Senden mit zufälligen Pausen und "tippt …"-Anzeige
 *  - keine Massen- oder Mediennachrichten, kein vollständiger History-Sync, nicht dauerhaft "online"
 *  - bei "anderswo angemeldet"/"gesperrt" nicht aggressiv neu verbinden
 */

type Socket = ReturnType<typeof makeWASocket>;

export interface WhatsAppBotOptions {
  config: Config;
  store: Store;
  logger: Logger;
  /** Ordner für die WhatsApp-Sitzung (Schlüssel = Zugriff auf das Konto!). */
  authDir: string;
  /** Liefert den aktuellen Kontext für Chat-Befehle. Ohne → keine Befehle (Setup-Modus). */
  commandContext?: (() => CommandContext) | undefined;
  /** Setup-Modus: QR immer im Terminal anzeigen und nicht automatisch neu verbinden nach Logout. */
  interactive?: boolean;
  onOpen?: (bot: WhatsAppBot) => void | Promise<void>;
  /** Pausen beim Senden (Sperrschutz). Standard: 0,7–1,6 s "tippt …", 2,5–5 s zwischen Nachrichten. */
  timing?: { typingMs: () => number; gapMs: () => number } | undefined;
  /** Nur für Tests: eigener Socket statt echter WhatsApp-Verbindung. */
  socketFactory?: ((config: Parameters<typeof makeWASocket>[0]) => Socket) | undefined;
}

const GROUP_CACHE_TTL_MS = 5 * 60_000;

export class WhatsAppBot {
  state: WhatsAppState = 'close';
  private sock: Socket | undefined;
  private stopped = false;
  private reconnectAttempts = 0;
  private reconnectTimer: NodeJS.Timeout | undefined;
  private qrShownInRound = 0;
  private pairingRequested = false;
  private readonly groupCache = new Map<string, { meta: GroupMetadata; at: number }>();
  private readonly cooldown: CommandCooldown;
  private readonly outbox: OutboxSender;
  private readonly waLogger: Logger;
  private sendChain: Promise<unknown> = Promise.resolve();
  groupJid: string | undefined;
  groupName: string | undefined;

  private readonly timing: { typingMs: () => number; gapMs: () => number };

  constructor(private readonly opts: WhatsAppBotOptions) {
    this.timing = opts.timing ?? {
      typingMs: () => 700 + Math.floor(Math.random() * 900),
      gapMs: () => 2500 + Math.floor(Math.random() * 2500),
    };
    this.waLogger = opts.logger.child({ module: 'baileys' }, { level: opts.config.logLevel === 'trace' ? 'debug' : 'warn' });
    this.cooldown = new CommandCooldown({ perCommandMs: opts.config.commandCooldownSeconds * 1000, maxPerMinute: 20 });
    this.groupJid = opts.config.whatsapp.groupJid ?? opts.store.getMeta<string>('groupJid');
    this.groupName = opts.store.getMeta<string>('groupName');
    this.outbox = new OutboxSender({
      store: opts.store,
      logger: opts.logger,
      send: (chat, text, id) => this.sendText(chat, text, { messageId: id }),
      isConnected: () => this.state === 'open',
      defaultChat: () => this.groupJid,
      selfChat: () => this.selfJids()[0],
      newMessageId: () => generateMessageIDV2(this.sock?.user?.id),
      gapMs: () => this.timing.gapMs(),
    });
  }

  get socket(): Socket | undefined {
    return this.sock;
  }

  async start(): Promise<void> {
    this.stopped = false;
    await this.connect();
  }

  /** Verbindung schließen, OHNE die Sitzung abzumelden (Neustart bleibt ohne QR möglich). */
  async stop(): Promise<void> {
    this.stopped = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    try {
      this.sock?.end(undefined);
    } catch {
      /* egal */
    }
    this.sock = undefined;
    this.state = 'close';
    // Warten, bis laufende Sendevorgänge abgeschlossen sind
    await Promise.race([this.sendChain.catch(() => undefined), new Promise((r) => setTimeout(r, 5000))]);
  }

  flushOutbox(): void {
    void this.outbox.flush().catch((e) => this.opts.logger.error({ err: String(e) }, 'Outbox-Fehler'));
  }

  // ── Verbindung ────────────────────────────────────────────────────

  private async connect(): Promise<void> {
    const { logger } = this.opts;
    this.state = 'connecting';
    await mkdir(this.opts.authDir, { recursive: true, mode: 0o700 });
    await chmod(this.opts.authDir, 0o700);
    const { state: auth, saveCreds } = await useMultiFileAuthState(this.opts.authDir);

    let version: [number, number, number] | undefined;
    if (!this.opts.socketFactory) {
      try {
        const latest = await fetchLatestBaileysVersion();
        version = latest.version;
      } catch {
        logger.debug('Aktuelle WhatsApp-Web-Version nicht abrufbar – nutze Baileys-Standard');
      }
    }

    const factory = this.opts.socketFactory ?? makeWASocket;
    const sock = factory({
      ...(version ? { version } : {}),
      auth: { creds: auth.creds, keys: makeCacheableSignalKeyStore(auth.keys, this.waLogger) },
      logger: this.waLogger,
      browser: Browsers.ubuntu('Chrome'),
      markOnlineOnConnect: false,
      syncFullHistory: false,
      generateHighQualityLinkPreview: false,
      connectTimeoutMs: 60_000,
      keepAliveIntervalMs: 30_000,
      getMessage: async (key) => (key.id ? this.opts.store.getSentMessage<proto.IMessage>(key.id) : undefined),
      cachedGroupMetadata: async (jid) => this.cachedGroup(jid),
    });
    this.sock = sock;
    this.pairingRequested = false;

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {
      if (sock !== this.sock) return; // veralteter Socket
      const { connection, lastDisconnect, qr } = update;
      if (qr) void this.handleQr(sock, qr, auth.creds.registered ?? false);
      if (connection === 'open') void this.handleOpen(sock);
      if (connection === 'close') {
        const statusCode = (lastDisconnect?.error as { output?: { statusCode?: number } } | undefined)?.output?.statusCode;
        this.handleClose(statusCode, lastDisconnect?.error);
      }
    });

    sock.ev.on('messages.upsert', ({ messages, type }) => {
      if (type !== 'notify') return;
      for (const msg of messages) void this.handleMessage(msg);
    });

    const refresh = async (jid: string) => {
      try {
        const meta = await sock.groupMetadata(jid);
        this.groupCache.set(jid, { meta, at: Date.now() });
        if (jid === this.groupJid) this.rememberGroup(jid, meta.subject);
      } catch {
        this.groupCache.delete(jid);
      }
    };
    sock.ev.on('groups.update', (updates) => {
      for (const u of updates) if (u.id) void refresh(u.id);
    });
    sock.ev.on('group-participants.update', (u) => void refresh(u.id));
  }

  private async handleQr(sock: Socket, qr: string, registered: boolean): Promise<void> {
    const { logger, config } = this.opts;
    this.state = 'qr';
    this.qrShownInRound++;

    if (config.whatsapp.pairingNumber && !registered && !this.pairingRequested) {
      this.pairingRequested = true;
      try {
        const code = await sock.requestPairingCode(config.whatsapp.pairingNumber);
        const pretty = code.match(/.{1,4}/g)?.join('-') ?? code;
        console.log(`\n📱 Kopplungscode für +${config.whatsapp.pairingNumber}: ${pretty}\n   WhatsApp → Verknüpfte Geräte → Gerät hinzufügen → "Stattdessen mit Telefonnummer verknüpfen"\n`);
        logger.info('Kopplungscode erzeugt – siehe Ausgabe oben');
        return;
      } catch (e) {
        logger.warn({ err: String(e) }, 'Kopplungscode fehlgeschlagen – nutze QR-Code');
      }
    }

    const qrFile = join(this.opts.config.dataDir, 'qr.png');
    try {
      const terminal = await QRCode.toString(qr, { type: 'terminal', small: true });
      console.log(`\n📱 Mit der BOT-Nummer scannen: WhatsApp → Einstellungen → Verknüpfte Geräte → Gerät hinzufügen\n${terminal}`);
      await QRCode.toFile(qrFile, qr, { width: 400 });
      await chmod(qrFile, 0o600).catch(() => undefined);
      logger.info({ qrFile }, 'QR-Code bereit (auch als Bild gespeichert). Er wird alle ~20 s erneuert.');
    } catch (e) {
      logger.error({ err: String(e) }, 'QR-Code konnte nicht angezeigt werden');
    }
  }

  private async handleOpen(sock: Socket): Promise<void> {
    const { logger } = this.opts;
    this.state = 'open';
    this.reconnectAttempts = 0;
    this.qrShownInRound = 0;
    await rm(join(this.opts.config.dataDir, 'qr.png'), { force: true }).catch(() => undefined);
    logger.info({ user: sock.user?.id?.split(':')[0]?.replace(/\d{4}$/, '****') }, 'WhatsApp verbunden');

    try {
      await this.resolveGroup();
    } catch (e) {
      logger.warn({ err: String(e) }, 'Gruppe konnte nicht bestimmt werden');
    }
    if (this.opts.onOpen) await this.opts.onOpen(this);
    this.flushOutbox();
  }

  private handleClose(statusCode: number | undefined, error: unknown): void {
    const { logger } = this.opts;
    this.sock = undefined;
    if (this.stopped) return;
    const reason = error instanceof Error ? error.message : String(error ?? '');

    switch (statusCode) {
      case DisconnectReason.loggedOut: {
        this.state = 'loggedOut';
        logger.error('WhatsApp-Sitzung wurde abgemeldet (z. B. Gerät auf dem Handy entfernt). Neu koppeln: QR-Code erscheint gleich.');
        void rm(this.opts.authDir, { recursive: true, force: true }).then(() => this.scheduleReconnect(10_000));
        return;
      }
      case DisconnectReason.restartRequired:
        logger.info('WhatsApp verlangt Neustart der Verbindung (normal nach dem Koppeln)');
        this.scheduleReconnect(500);
        return;
      case DisconnectReason.connectionReplaced:
        this.state = 'close';
        logger.error('Diese WhatsApp-Sitzung wurde an anderer Stelle geöffnet (läuft der Bot doppelt?). Neuer Versuch in 5 Min.');
        this.scheduleReconnect(5 * 60_000);
        return;
      case DisconnectReason.forbidden:
        this.state = 'close';
        logger.fatal('WhatsApp verweigert den Zugriff (403) – Nummer evtl. gesperrt/eingeschränkt. Neuer Versuch in 1 Std.');
        this.scheduleReconnect(60 * 60_000);
        return;
      default: {
        this.state = 'close';
        // Nach mehreren ungescannten QR-Runden länger warten, damit das Log nicht vollläuft
        if (this.qrShownInRound >= 5 && !this.opts.interactive) {
          this.qrShownInRound = 0;
          logger.warn('QR-Code wurde nicht gescannt – nächster Versuch in 10 Min. (oder Dienst neu starten)');
          this.scheduleReconnect(10 * 60_000);
          return;
        }
        const delay = Math.min(2_000 * 2 ** this.reconnectAttempts, 5 * 60_000);
        this.reconnectAttempts++;
        logger.warn({ statusCode, reason, retryInSec: Math.round(delay / 1000) }, 'WhatsApp-Verbindung getrennt – verbinde neu');
        this.scheduleReconnect(delay);
      }
    }
  }

  private scheduleReconnect(delayMs: number): void {
    if (this.stopped) return;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => {
      this.connect().catch((e) => {
        this.opts.logger.error({ err: String(e) }, 'Verbindungsaufbau fehlgeschlagen');
        this.scheduleReconnect(30_000);
      });
    }, delayMs);
  }

  // ── Gruppe ────────────────────────────────────────────────────────

  private rememberGroup(jid: string, name: string | undefined): void {
    this.groupJid = jid;
    this.groupName = name;
    this.opts.store.setMeta('groupJid', jid);
    if (name) this.opts.store.setMeta('groupName', name);
  }

  private async cachedGroup(jid: string): Promise<GroupMetadata | undefined> {
    const hit = this.groupCache.get(jid);
    if (hit && Date.now() - hit.at < GROUP_CACHE_TTL_MS) return hit.meta;
    return undefined;
  }

  /** Alle Gruppen, in denen die Bot-Nummer Mitglied ist. */
  async listGroups(): Promise<GroupMetadata[]> {
    if (!this.sock) return [];
    const all = await this.sock.groupFetchAllParticipating();
    for (const meta of Object.values(all)) this.groupCache.set(meta.id, { meta, at: Date.now() });
    return Object.values(all).sort((a, b) => (a.subject ?? '').localeCompare(b.subject ?? '', 'de'));
  }

  /** Per Einladungslink beitreten (einmalig pro Link). Liefert die Gruppen-JID. */
  async joinByInvite(code: string): Promise<string | undefined> {
    if (!this.sock) throw new Error('nicht verbunden');
    const { logger, store } = this.opts;
    const info = await this.sock.groupGetInviteInfo(code);
    const groups = await this.listGroups();
    if (groups.some((g) => g.id === info.id)) {
      logger.info({ group: info.subject }, 'Bot ist bereits Mitglied der Gruppe');
      return info.id;
    }
    if (store.getMeta(`inviteTried:${code}`)) return undefined;
    store.setMeta(`inviteTried:${code}`, Date.now());
    const jid = await this.sock.groupAcceptInvite(code);
    logger.info({ group: info.subject }, 'Gruppe per Einladungslink beigetreten');
    return jid ?? info.id;
  }

  selectGroup(jid: string, name?: string): void {
    this.rememberGroup(jid, name);
  }

  private async resolveGroup(): Promise<void> {
    const { logger, config } = this.opts;
    if (config.whatsapp.groupJid) {
      this.groupJid = config.whatsapp.groupJid;
    }
    if (!this.groupJid && config.whatsapp.groupInviteCode) {
      try {
        const jid = await this.joinByInvite(config.whatsapp.groupInviteCode);
        if (jid) this.groupJid = jid;
      } catch (e) {
        logger.error({ err: String(e) }, 'Beitritt per Einladungslink fehlgeschlagen (Link abgelaufen? Admin-Freigabe nötig?)');
      }
    }
    const groups = await this.listGroups();
    // Auf der privaten Nummer NIE automatisch wählen (sonst landet der Plan evtl. in der Familiengruppe)
    if (!this.groupJid && groups.length === 1 && !config.whatsapp.ownNumber) {
      this.groupJid = groups[0]!.id;
      logger.info({ group: groups[0]!.subject }, 'Einzige Gruppe des Bots wird als Klassengruppe verwendet');
    }
    if (this.groupJid) {
      const meta = groups.find((g) => g.id === this.groupJid);
      if (!meta) {
        logger.warn({ groupJid: this.groupJid }, 'Bot ist (noch) nicht Mitglied der konfigurierten Gruppe – Nachrichten warten');
      }
      this.rememberGroup(this.groupJid, meta?.subject ?? this.groupName);
      logger.info({ group: this.groupName ?? this.groupJid }, 'Klassengruppe');
    } else {
      logger.warn(
        { groups: groups.map((g) => `${g.subject} = ${g.id}`) },
        config.whatsapp.ownNumber
          ? 'Keine Klassengruppe gewählt – bitte "sudo ./install.sh --relink" ausführen (oder WHATSAPP_GROUP_INVITE setzen).'
          : 'Keine Klassengruppe festgelegt. Bot zur Gruppe hinzufügen, WHATSAPP_GROUP_INVITE setzen oder "npm run setup" ausführen.',
      );
    }
  }

  // ── Nachrichten ───────────────────────────────────────────────────

  private async handleMessage(msg: WAMessage): Promise<void> {
    const ctxFactory = this.opts.commandContext;
    if (!ctxFactory) return;
    const incoming = extractIncoming(msg);
    if (!incoming) return;
    const command = routeCommand(incoming, {
      ownNumber: this.opts.config.whatsapp.ownNumber,
      selfJids: this.selfJids(),
      isBotMessage: (id) => this.opts.store.isBotMessageId(id),
      groupJid: this.groupJid,
      allowPrivate: this.opts.config.whatsapp.allowPrivateCommands,
      adminNumbers: this.opts.config.adminNumbers,
      nowMs: Date.now(),
      cooldown: this.cooldown,
    });
    if (!command) return;

    this.opts.logger.info({ command, group: isJidGroup(incoming.chat) }, 'Befehl empfangen');
    try {
      const reply = runCommand(command, ctxFactory());
      for (const [i, part] of splitMessage(reply).entries()) {
        await this.sendText(incoming.chat, part, i === 0 ? { quoted: msg } : {});
      }
    } catch (e) {
      this.opts.logger.error({ err: e instanceof Error ? e.stack : String(e), command }, 'Befehl fehlgeschlagen');
      await this.sendText(incoming.chat, '⚠️ Da ist etwas schiefgelaufen. Bitte später nochmal versuchen.', {}).catch(() => undefined);
    }
  }

  /**
   * Sendet Text – serialisiert über eine Kette, mit kurzer "tippt …"-Anzeige und Pause.
   * Wirft bei Fehlern (Outbox wiederholt später mit derselben ID).
   */
  sendText(chat: string, text: string, options: { messageId?: string; quoted?: WAMessage }): Promise<void> {
    const { config, store } = this.opts;
    const prefix = config.whatsapp.messagePrefix;
    const body = prefix && !text.startsWith(prefix.trim()) ? `${prefix}${text}` : text;
    const job = this.sendChain.then(async () => {
      const sock = this.sock;
      if (!sock || this.state !== 'open') throw new Error('WhatsApp nicht verbunden');
      // Auf der privaten Nummer kein "tippt …": Presence-Updates können dazu führen,
      // dass dein Handy keine Benachrichtigungen mehr anzeigt.
      const showTyping = !config.whatsapp.ownNumber;
      if (showTyping) {
        try {
          await sock.sendPresenceUpdate('composing', chat);
          await new Promise((r) => setTimeout(r, this.timing.typingMs()));
        } catch {
          /* Presence ist optional */
        }
      }
      // ID vorab festlegen und merken: So erkennt der Bot sein eigenes Echo sicher (keine Schleifen)
      const messageId = options.messageId ?? generateMessageIDV2(sock.user?.id);
      store.rememberSentMessage(messageId, chat, { extendedTextMessage: { text: body } });
      const meta = isJidGroup(chat) ? await this.cachedGroup(chat) : undefined;
      const sent = await withTimeout(
        sock.sendMessage(
          chat,
          { text: body },
          {
            messageId,
            ...(options.quoted ? { quoted: options.quoted } : {}),
            ...(meta?.ephemeralDuration ? { ephemeralExpiration: meta.ephemeralDuration } : {}),
          },
        ),
        45_000,
      );
      if (sent?.key.id && sent.message) store.rememberSentMessage(sent.key.id, chat, sent.message);
      if (showTyping) await sock.sendPresenceUpdate('paused', chat).catch(() => undefined);
    });
    this.sendChain = job.catch(() => undefined);
    return job;
  }

  /** Eigene JIDs ohne Geräte-Suffix: Telefonnummer und (falls bekannt) LID. */
  selfJids(): string[] {
    const user = this.sock?.user as { id?: string; lid?: string } | undefined;
    return [user?.id, user?.lid].filter((j): j is string => Boolean(j)).map(bareJid);
  }

  /** Für das Setup: Bot-Sitzung vollständig auf Platte schreiben lassen. */
  async waitForCredsFlush(): Promise<void> {
    await new Promise((r) => setTimeout(r, 1500));
  }
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const t = setTimeout(() => reject(new Error(`Zeitüberschreitung nach ${ms / 1000}s`)), ms);
    promise.then(
      (v) => {
        clearTimeout(t);
        resolve(v);
      },
      (e) => {
        clearTimeout(t);
        reject(e);
      },
    );
  });
}
