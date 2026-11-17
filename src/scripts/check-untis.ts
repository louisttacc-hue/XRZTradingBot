import pino from 'pino';
import { loadConfig } from '../config.js';
import { notableState } from '../domain/detector.js';
import { addDays, formatDateShort, schoolNow } from '../domain/time.js';
import { courseName } from '../format/messages.js';
import { loadEnv } from '../runtime.js';
import { normalizeLessons } from '../untis/normalize.js';
import { UntisError, WebUntisSource } from '../untis/source.js';

/**
 * Prüft die Untis-Zugangsdaten ohne WhatsApp: `npm run check:untis`
 */
async function main(): Promise<void> {
  loadEnv();
  const config = loadConfig();
  const now = schoolNow(new Date(), config.timeZone);
  const window = { from: now.date, to: addDays(now.date, 13) };
  console.log(`🔎 Prüfe Untis: Schule "${config.untis.school}" auf ${config.untis.server} (Login: ${config.untis.auth}) …`);

  const source = new WebUntisSource(config.untis, pino({ level: 'warn' }));
  const result = await source.fetch(window, { masterData: true });
  const periods = normalizeLessons(result.lessons);
  const courses = new Set(periods.map((p) => `${p.lessonNumber}|${courseName(p)}`));
  const changes = periods.filter((p) => notableState(p));

  console.log(`✅ Login erfolgreich.`);
  console.log(`   Klassen: ${result.classes.map((c) => `${c.name} (ID ${c.id})`).join(', ')}`);
  console.log(`   Zeitraum: ${formatDateShort(window.from)} – ${formatDateShort(window.to)}`);
  console.log(`   Stunden: ${periods.length} · Kurse: ${courses.size} · Änderungen laut Untis: ${changes.length}`);
  console.log(`   Zeitraster: ${result.timegrid?.length ?? 0} Stunden · Ferien bekannt: ${result.holidays?.length ?? 0}`);
  console.log('\n   Kurse:');
  for (const c of [...new Set(periods.map((p) => courseName(p)))].sort((a, b) => a.localeCompare(b, 'de'))) console.log(`   • ${c}`);
}

main().catch((e: unknown) => {
  if (e instanceof UntisError) console.error(`❌ ${e.message}`);
  else console.error('❌', e instanceof Error ? e.message : e);
  process.exit(1);
});
