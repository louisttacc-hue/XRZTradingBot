/**
 * Sucht eine Schule in der offiziellen Untis-Schulsuche (wie die Untis-App):
 *   npm run find-school -- "Gymnasium Jüchen"
 * Gibt Server (UNTIS_SERVER) und Schulname (UNTIS_SCHOOL) aus.
 */

export interface SchoolSearchResult {
  displayName: string;
  address: string;
  server: string;
  loginName: string;
}

export async function searchSchools(query: string, fetchImpl: typeof fetch = fetch): Promise<SchoolSearchResult[]> {
  const res = await fetchImpl('https://mobile.webuntis.com/ms/schoolquery2', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ id: 'ef-untis-bot', method: 'searchSchool', params: [{ search: query }], jsonrpc: '2.0' }),
  });
  if (!res.ok) throw new Error(`Schulsuche: HTTP ${res.status}`);
  const data = (await res.json()) as {
    result?: { schools?: { displayName?: string; address?: string; server?: string; serverUrl?: string; loginName?: string }[] };
    error?: { message?: string; code?: number };
  };
  if (data.error) {
    if (data.error.code === -6003) throw new Error('Zu viele Treffer – Suchbegriff genauer fassen.');
    throw new Error(`Schulsuche: ${data.error.message ?? 'Fehler'}`);
  }
  return (data.result?.schools ?? []).map((s) => ({
    displayName: s.displayName ?? '',
    address: s.address ?? '',
    server: s.server ?? (s.serverUrl ? new URL(s.serverUrl).hostname : ''),
    loginName: s.loginName ?? '',
  }));
}

async function main(): Promise<void> {
  const query = process.argv.slice(2).join(' ').trim() || 'Gymnasium Jüchen';
  console.log(`🔎 Suche "${query}" …`);
  const schools = await searchSchools(query);
  if (schools.length === 0) {
    console.log('Keine Schule gefunden. Tipp: Ort oder Teil des Namens versuchen, z. B. "Jüchen".');
    return;
  }
  for (const s of schools) {
    console.log(`\n🏫 ${s.displayName} – ${s.address}`);
    console.log(`   UNTIS_SERVER=${s.server}`);
    console.log(`   UNTIS_SCHOOL=${s.loginName}`);
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((e: unknown) => {
    console.error('❌', e instanceof Error ? e.message : e);
    process.exit(1);
  });
}
