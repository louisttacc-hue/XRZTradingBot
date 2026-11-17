import { notableState } from '../domain/detector.js';
import { formatDateShort, formatTime, minutesOf } from '../domain/time.js';
import type { ChangeEvent, ChangeKind, ElementRef, Period, TimeUnit, UntisDate, UntisTime } from '../domain/types.js';

/**
 * Formatierung aller WhatsApp-Texte (WhatsApp-Markdown: *fett*, _kursiv_, ~durchgestrichen~).
 */

export const MAX_MESSAGE_LENGTH = 3500;
/** Maximaler Abstand (Minuten) zwischen zwei Stunden, die als Block zusammengefasst werden. */
const MERGE_GAP_MINUTES = 20;

// ── Bausteine ──────────────────────────────────────────────────────────

export function periodLabel(start: UntisTime, end: UntisTime, timegrid: readonly TimeUnit[]): string {
  const first = timegrid.find((u) => u.start === start);
  const last = timegrid.find((u) => u.end === end);
  if (first && last) {
    return first.name === last.name ? `${first.name}. Std` : `${first.name}.–${last.name}. Std`;
  }
  return `${formatTime(start)}–${formatTime(end)}`;
}

export function courseName(p: Period): string {
  const subject = p.subjects.map((s) => s.longName || s.name).filter(Boolean).join('/') || p.lessonText || 'Unterricht';
  const group = p.studentGroup && p.studentGroup !== subject ? ` (${p.studentGroup})` : '';
  return `${subject}${group}`;
}

function names(list: readonly ElementRef[]): string {
  return list.map((x) => x.name || '—').join(', ');
}

/** "NEU statt ~MÜL~" bzw. nur "MÜL" */
function withOriginal(list: readonly ElementRef[], emptyLabel: string): string {
  if (list.length === 0) return '';
  return list
    .map((x) => {
      const current = x.id === 0 || !x.name ? emptyLabel : x.name;
      return x.original ? `${current} statt ~${x.original.name || '?'}~` : current;
    })
    .join(', ');
}

function originalNames(list: readonly ElementRef[]): string {
  return list.map((x) => x.original?.name || x.name).filter(Boolean).join(', ');
}

function textNote(p: Period): string {
  const notes = [p.substText, p.info].filter((t): t is string => Boolean(t));
  return notes.length ? `\n     _„${notes.join(' – ')}“_` : '';
}

// ── Zusammenfassen von Doppelstunden ──────────────────────────────────

interface Line {
  date: UntisDate;
  start: UntisTime;
  end: UntisTime;
  /** Inhalt ohne Zeitangabe – gleiche Inhalte direkt hintereinander werden zusammengefasst. */
  render: (label: string) => string;
  mergeKey: string;
}

function mergeLines(lines: Line[], timegrid: readonly TimeUnit[]): { date: UntisDate; text: string }[] {
  const merged: Line[] = [];
  for (const line of lines) {
    const prev = [...merged].reverse().find((m) => m.date === line.date && m.mergeKey === line.mergeKey);
    if (prev && minutesOf(line.start) - minutesOf(prev.end) <= MERGE_GAP_MINUTES && line.start >= prev.end) {
      prev.end = line.end;
    } else {
      merged.push({ ...line });
    }
  }
  return merged.map((l) => ({ date: l.date, text: l.render(periodLabel(l.start, l.end, timegrid)) }));
}

// ── Änderungsmeldungen ────────────────────────────────────────────────

export interface NotificationContext {
  timegrid: readonly TimeUnit[];
  /** Ab so vielen Änderungen gibt es nur eine Zusammenfassung. */
  maxEvents: number;
  className: string;
}

const KIND_LABELS: Record<ChangeKind, string> = {
  cancelled: 'Entfälle',
  substitution: 'Vertretungen',
  roomChange: 'Raumwechsel',
  irregular: 'sonstige Änderungen',
  reinstated: 'Rücknahmen',
  added: 'neue Stunden',
  removed: 'entfernte Stunden',
  moved: 'Verschiebungen',
  modified: 'Planänderungen',
};

function eventBody(e: ChangeEvent, timegrid: readonly TimeUnit[]): { icon: string; body: string } {
  const p = e.period;
  const course = courseName(p);
  const teacher = names(p.teachers);
  const room = names(p.rooms);
  const base = [course, teacher, room].filter(Boolean).join(' · ');
  const kinds = new Set(e.kinds);

  if (kinds.has('cancelled')) return { icon: '❌', body: `${[course, originalNames(p.teachers)].filter(Boolean).join(' · ')} — *Entfall*` };
  if (kinds.has('reinstated')) return { icon: '✅', body: `${base} — *findet doch statt*` };
  if (kinds.has('substitution') || kinds.has('roomChange')) {
    const parts = [course];
    parts.push(kinds.has('substitution') ? `*Vertretung:* ${withOriginal(p.teachers, 'ohne Lehrer')}` : teacher);
    parts.push(kinds.has('roomChange') ? `*Raum:* ${withOriginal(p.rooms, 'kein Raum')}` : room);
    return { icon: kinds.has('substitution') ? '🔄' : '🚪', body: parts.filter(Boolean).join(' · ') };
  }
  if (kinds.has('irregular')) return { icon: '⚠️', body: `${base} — *geändert/Sonderstunde*` };
  if (kinds.has('added')) return { icon: '➕', body: `${base} — *neu im Plan*` };
  if (kinds.has('removed')) return { icon: '➖', body: `${base} — *nicht mehr im Plan*` };
  if (kinds.has('moved') && e.previous) {
    const prev = e.previous;
    const from = periodLabel(prev.start, prev.end, timegrid) + (prev.date !== p.date ? ` (${formatDateShort(prev.date)})` : '');
    return { icon: '↪️', body: `${base} — *verschoben von ${from}*` };
  }
  if (kinds.has('modified') && e.previous) {
    const prev = e.previous;
    const diffs: string[] = [];
    if (names(prev.teachers) !== teacher) diffs.push(`*Lehrer:* ${teacher || '—'} (vorher ${names(prev.teachers) || '—'})`);
    if (names(prev.rooms) !== room) diffs.push(`*Raum:* ${room || '—'} (vorher ${names(prev.rooms) || '—'})`);
    if (courseName(prev) !== course) diffs.push(`*Fach:* ${course} (vorher ${courseName(prev)})`);
    if (prev.end !== p.end) diffs.push(`*Ende:* ${formatTime(p.end)} (vorher ${formatTime(prev.end)})`);
    return { icon: '✏️', body: [course, ...(diffs.length ? diffs : ['*Planänderung*'])].join(' · ') };
  }
  return { icon: 'ℹ️', body: base };
}

/**
 * Baut die WhatsApp-Nachricht(en) für alle Änderungen eines Durchlaufs.
 * Eine Nachricht pro Durchlauf (bei Überlänge aufgeteilt), bei Massenänderungen nur eine Zusammenfassung.
 */
export function buildNotifications(events: readonly ChangeEvent[], ctx: NotificationContext): string[] {
  if (events.length === 0) return [];

  if (events.length > ctx.maxEvents) return [massSummary(events, ctx)];

  const lines: Line[] = events.map((e) => {
    const { icon, body } = eventBody(e, ctx.timegrid);
    const note = textNote(e.period);
    return {
      date: e.period.date,
      start: e.period.start,
      end: e.period.end,
      mergeKey: `${icon}|${body}|${note}|${e.period.lessonNumber}`,
      render: (label) => `${icon} *${label}* · ${body}${note}`,
    };
  });

  const merged = mergeLines(lines, ctx.timegrid);
  const header = `🔔 *Vertretungsplan ${ctx.className}* – ${merged.length === 1 ? '1 Änderung' : `${merged.length} Änderungen`}`;
  return splitMessage([header, ...groupByDay(merged)].join('\n'));
}

function groupByDay(lines: { date: UntisDate; text: string }[]): string[] {
  const out: string[] = [];
  let currentDate: UntisDate | undefined;
  for (const line of lines) {
    if (line.date !== currentDate) {
      out.push('', `📅 *${formatDateShort(line.date)}*`);
      currentDate = line.date;
    }
    out.push(line.text);
  }
  return out;
}

function massSummary(events: readonly ChangeEvent[], ctx: NotificationContext): string {
  const counts = new Map<ChangeKind, number>();
  for (const e of events) {
    const primary = e.kinds[0] ?? 'modified';
    counts.set(primary, (counts.get(primary) ?? 0) + 1);
  }
  const summary = [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([kind, n]) => `${n} ${KIND_LABELS[kind]}`)
    .join(', ');
  const dates = [...new Set(events.map((e) => e.period.date))].sort((a, b) => a - b);
  const range =
    dates.length === 1 ? formatDateShort(dates[0]!) : `${formatDateShort(dates[0]!)} bis ${formatDateShort(dates[dates.length - 1]!)}`;
  return [
    `📢 *Großes Stundenplan-Update ${ctx.className}*`,
    '',
    `Untis meldet gerade *${events.length} Änderungen* (${summary}) – vermutlich ein neuer Stundenplan oder eine Sonderwoche.`,
    `Betroffen: ${range}`,
    '',
    '👉 Details: /heute · /morgen · /woche · /entfall',
  ].join('\n');
}

// ── Tagesplan ─────────────────────────────────────────────────────────

export function periodMarker(p: Period): string {
  const state = notableState(p);
  if (!state) return '';
  if (state.kinds.includes('cancelled')) return ' ❌ Entfall';
  if (state.kinds.includes('substitution')) return ' 🔄';
  if (state.kinds.includes('roomChange')) return ' 🚪';
  return ' ⚠️';
}

/** Ein Tag mit allen Kursen, Doppelstunden zusammengefasst. */
export function formatDayPlan(periods: readonly Period[], date: UntisDate, opts: { timegrid: readonly TimeUnit[]; title?: string }): string {
  const day = periods.filter((p) => p.date === date).sort((a, b) => a.start - b.start || a.key.localeCompare(b.key));
  const title = `📅 *${opts.title ? `${opts.title} · ` : ''}${formatDateShort(date)}*`;
  const lines: Line[] = day.map((p) => {
    const cancelled = p.status === 'cancelled';
    const parts = [courseName(p), withOriginal(p.teachers, 'ohne Lehrer'), withOriginal(p.rooms, 'kein Raum')].filter(Boolean);
    const marker = periodMarker(p);
    const note = textNote(p);
    return {
      date,
      start: p.start,
      end: p.end,
      mergeKey: `${parts.join('|')}|${marker}|${note}|${p.lessonNumber}|${cancelled}`,
      render: (label) => {
        const main = `*${label}* · ${cancelled ? [courseName(p), originalNames(p.teachers), originalNames(p.rooms)].filter(Boolean).join(' · ') : parts.join(' · ')}`;
        return `${cancelled ? `~${main}~` : main}${marker}${note}`;
      },
    };
  });
  return [title, ...mergeLines(lines, opts.timegrid).map((l) => l.text)].join('\n');
}

// ── Hilfsfunktionen ───────────────────────────────────────────────────

/** Teilt lange Texte an Zeilengrenzen, damit WhatsApp sie zuverlässig zustellt. */
export function splitMessage(text: string, max = MAX_MESSAGE_LENGTH): string[] {
  if (text.length <= max) return [text];
  const parts: string[] = [];
  let current = '';
  for (const line of text.split('\n')) {
    const candidate = current ? `${current}\n${line}` : line;
    if (candidate.length > max && current) {
      parts.push(current);
      current = line;
    } else {
      current = candidate;
    }
    while (current.length > max) {
      parts.push(current.slice(0, max));
      current = current.slice(max);
    }
  }
  if (current) parts.push(current);
  return parts;
}
