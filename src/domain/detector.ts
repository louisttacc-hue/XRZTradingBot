import type { ChangeEvent, ChangeKind, Period, UntisDate, UntisTime } from './types.js';

/**
 * Änderungserkennung – reine Funktion ohne I/O.
 *
 * Zwei sich ergänzende Quellen:
 *  1. Untis-Kennzeichnungen (Entfall, Vertretung, Raumwechsel, irregulär). Jeder solche
 *     Zustand wird mit einem Fingerabdruck im "Ledger" gespeichert und nur gemeldet, wenn
 *     er neu ist oder sich geändert hat. Verschwindet er wieder, wird das gemeldet.
 *  2. Vergleich mit dem letzten Snapshot für Tage, die in beiden Abrufen enthalten sind:
 *     neue, entfernte, verschobene oder still geänderte Stunden (z. B. neuer Stundenplan).
 *
 * Pro Stunde entsteht höchstens ein Ereignis pro Durchlauf.
 */

export interface LedgerEntry {
  key: string;
  date: UntisDate;
  fingerprint: string;
}

export interface DetectionInput {
  /** Letzter Snapshot je abgefragtem Tag (auch leere Tage sind enthalten). */
  previous: ReadonlyMap<UntisDate, readonly Period[]>;
  /** Aktueller Abruf für das gesamte Fenster. */
  current: readonly Period[];
  window: { from: UntisDate; to: UntisDate };
  ledger: ReadonlyMap<string, LedgerEntry>;
  now: { date: UntisDate; time: UntisTime };
}

export interface DetectionResult {
  events: ChangeEvent[];
  ledgerSet: LedgerEntry[];
  ledgerDelete: string[];
}

interface NotableState {
  kinds: ChangeKind[];
  fingerprint: string;
}

const ids = (list: readonly { id: number }[]) => list.map((x) => x.id).sort((a, b) => a - b).join(',');

/** Liefert den meldenswerten Zustand einer Stunde laut Untis – oder null für regulären Unterricht. */
export function notableState(p: Period): NotableState | null {
  if (p.status === 'cancelled') return { kinds: ['cancelled'], fingerprint: 'cancelled' };

  const kinds: ChangeKind[] = [];
  if (p.teachers.some((t) => t.original)) kinds.push('substitution');
  if (p.rooms.some((r) => r.original)) kinds.push('roomChange');
  if (kinds.length === 0 && p.status === 'irregular') kinds.push('irregular');
  if (kinds.length === 0) return null;

  const fingerprint = [kinds.join('+'), `t${ids(p.teachers)}`, `r${ids(p.rooms)}`, `s${ids(p.subjects)}`, `e${p.end}`].join('|');
  return { kinds, fingerprint };
}

/** Inhalt, der für den stillen Vergleich (ohne Untis-Kennzeichnung) relevant ist. */
function contentSignature(p: Period): string {
  return [p.end, ids(p.teachers), ids(p.rooms), ids(p.subjects)].join('|');
}

export function detectChanges(input: DetectionInput): DetectionResult {
  const { window, ledger, now } = input;
  const inWindow = (d: UntisDate) => d >= window.from && d <= window.to;
  const isOver = (p: Period) => p.date < now.date || (p.date === now.date && p.end <= now.time);

  const current = input.current.filter((p) => inWindow(p.date));
  const currentByKey = new Map(current.map((p) => [p.key, p]));

  const events = new Map<string, ChangeEvent>();
  const ledgerSet: LedgerEntry[] = [];
  const ledgerDelete = new Set<string>();

  const emit = (key: string, event: ChangeEvent) => {
    if (!isOver(event.period) && !events.has(key)) events.set(key, event);
  };

  // 1) Untis-Kennzeichnungen gegen den Ledger prüfen
  for (const p of current) {
    const state = notableState(p);
    const known = ledger.get(p.key);
    if (state) {
      if (known?.fingerprint !== state.fingerprint) {
        emit(p.key, { kinds: state.kinds, period: p });
        ledgerSet.push({ key: p.key, date: p.date, fingerprint: state.fingerprint });
      }
    } else if (known) {
      emit(p.key, { kinds: ['reinstated'], period: p });
      ledgerDelete.add(p.key);
    }
  }

  // 2) Stiller Vergleich für Tage, die in beiden Abrufen enthalten sind
  const comparableDates = [...input.previous.keys()].filter(inWindow);
  const removed: Period[] = [];
  const added: Period[] = [];

  for (const date of comparableDates) {
    const before = input.previous.get(date) ?? [];
    const beforeKeys = new Set(before.map((p) => p.key));

    for (const old of before) {
      const now = currentByKey.get(old.key);
      if (!now) {
        removed.push(old);
        continue;
      }
      if (notableState(now)) continue; // durch Untis-Kennzeichnung abgedeckt
      if (old.status === 'regular' && contentSignature(old) !== contentSignature(now)) {
        emit(now.key, { kinds: ['modified'], period: now, previous: old });
      }
    }
    for (const p of current) {
      if (p.date === date && !beforeKeys.has(p.key) && !notableState(p)) added.push(p);
    }
  }

  // Verschiebungen: gleiche Untis-ID, anderer Zeitpunkt
  const addedById = new Map(added.map((p) => [p.untisId, p]));
  for (const old of removed) {
    const knownState = ledger.get(old.key);
    if (knownState) ledgerDelete.add(old.key);
    // Bereits als Entfall gemeldet → Verschwinden ist keine Neuigkeit.
    if (knownState?.fingerprint === 'cancelled' || old.status === 'cancelled') continue;

    const moved = addedById.get(old.untisId);
    if (moved) {
      addedById.delete(old.untisId);
      emit(moved.key, { kinds: ['moved'], period: moved, previous: old });
    } else {
      emit(old.key, { kinds: ['removed'], period: old });
    }
  }
  for (const p of addedById.values()) emit(p.key, { kinds: ['added'], period: p });

  const sorted = [...events.values()].sort(
    (a, b) => a.period.date - b.period.date || a.period.start - b.period.start || a.period.key.localeCompare(b.period.key),
  );
  return { events: sorted, ledgerSet, ledgerDelete: [...ledgerDelete] };
}
