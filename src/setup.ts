import { join } from 'node:path';
import { createInterface } from 'node:readline/promises';
import { WhatsAppBot } from './bot/whatsapp.js';
import { Store } from './db/store.js';
import { createLogger } from './logger.js';
import { acquireLock, loadConfigOrExit, loadEnv } from './runtime.js';

/**
 * Einrichtungsassistent: WhatsApp per QR-Code (oder Kopplungscode) mit der Bot-Nummer
 * verknüpfen und die Klassengruppe auswählen.
 *
 *   npm run setup                 – interaktiv
 *   npm run setup -- --list       – nur Gruppen anzeigen
 *   npm run setup -- --group <jid>
 */

const args = process.argv.slice(2);
const argValue = (name: string) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : undefined;
};

async function main(): Promise<void> {
  loadEnv();
  const config = loadConfigOrExit();
  const logger = createLogger(process.env.LOG_LEVEL ?? 'info');
  const releaseLock = acquireLock(join(config.dataDir, 'bot.lock'));
  const store = new Store(join(config.dataDir, 'bot.db'));

  console.log('\n🤖 Einrichtung des EF-Vertretungsplan-Bots');
  console.log('   Halte das Handy mit der BOT-Nummer bereit (nicht deine private Nummer!).\n');

  const timeout = setTimeout(() => {
    console.error('\n⏱️  Keine Verbindung innerhalb von 5 Minuten. Bitte erneut versuchen: npm run setup');
    finish(1);
  }, 5 * 60_000);

  let finished = false;
  let handled = false;
  const finish = (code: number) => {
    if (finished) return;
    finished = true;
    clearTimeout(timeout);
    void bot
      .stop()
      .catch(() => undefined)
      .finally(() => {
        store.close();
        releaseLock();
        process.exit(code);
      });
  };

  const bot: WhatsAppBot = new WhatsAppBot({
    config,
    store,
    logger,
    authDir: join(config.dataDir, 'auth'),
    interactive: true,
    onOpen: async (b) => {
      if (handled) return; // nach Verbindungs-Neustart nicht erneut fragen
      handled = true;
      try {
        await chooseGroup(b);
        await b.waitForCredsFlush();
        console.log('\n✅ Einrichtung abgeschlossen. Starte jetzt den Dienst: sudo systemctl restart ef-untis-bot');
        finish(0);
      } catch (e) {
        console.error('❌ Fehler bei der Gruppenauswahl:', e instanceof Error ? e.message : e);
        finish(1);
      }
    },
  });

  async function chooseGroup(b: WhatsAppBot): Promise<void> {
    console.log('\n✅ WhatsApp ist verbunden.');
    if (config.whatsapp.groupInviteCode) {
      try {
        const jid = await b.joinByInvite(config.whatsapp.groupInviteCode);
        if (jid) console.log('   Per Einladungslink beigetreten / bereits Mitglied.');
      } catch (e) {
        console.warn('   ⚠️ Einladungslink funktioniert nicht:', e instanceof Error ? e.message : e);
      }
    }

    const groups = await b.listGroups();
    if (groups.length === 0) {
      console.log('\n⚠️  Die Bot-Nummer ist noch in keiner Gruppe.');
      console.log('   → Bot-Nummer zur Klassengruppe hinzufügen ODER WHATSAPP_GROUP_INVITE in .env setzen,');
      console.log('     dann "npm run setup" erneut ausführen. (Die WhatsApp-Kopplung bleibt gespeichert.)');
      return;
    }

    console.log('\nGruppen der Bot-Nummer:');
    groups.forEach((g, i) => console.log(`  [${i + 1}] ${g.subject}  (${g.participants?.length ?? '?'} Mitglieder)  ${g.id}`));
    if (args.includes('--list')) return;

    let chosen = groups.find((g) => g.id === argValue('--group'));
    if (!chosen && process.stdin.isTTY) {
      const current = groups.findIndex((g) => g.id === b.groupJid);
      const def = current >= 0 ? current + 1 : 1;
      const rl = createInterface({ input: process.stdin, output: process.stdout });
      const answer = await rl.question(`\nWelche Gruppe ist die Klassengruppe? Nummer [${def}]: `);
      rl.close();
      const n = Number(answer.trim() || def);
      chosen = groups[n - 1];
    } else if (!chosen && groups.length === 1) {
      chosen = groups[0];
    }
    if (!chosen) {
      console.log('Keine Gruppe gewählt.');
      return;
    }
    b.selectGroup(chosen.id, chosen.subject);
    console.log(`\n👍 Klassengruppe: „${chosen.subject}“`);
  }

  await bot.start();
}

main().catch((e: unknown) => {
  console.error('❌', e instanceof Error ? e.message : e);
  process.exit(1);
});
