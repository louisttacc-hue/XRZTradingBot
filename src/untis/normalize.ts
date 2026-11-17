import type { Lesson, ShortData } from 'webuntis';
import type { ElementRef, Period, PeriodStatus } from '../domain/types.js';

/**
 * Wandelt rohe Untis-Stunden (JSON-RPC getTimetable) in das interne Period-Format.
 * - entfernt Duplikate (gleiche Untis-ID aus mehreren Klassenabfragen)
 * - berechnet einen fachlichen Schlüssel, der Untis-ID-Wechsel (Stundenplan-Neuimport) übersteht
 * - sortiert nach Datum und Uhrzeit
 */
export function normalizeLessons(lessons: readonly Lesson[]): Period[] {
  const byId = new Map<number, Lesson>();
  for (const lesson of lessons) {
    if (!byId.has(lesson.id)) byId.set(lesson.id, lesson);
  }

  const periods = [...byId.values()].map(toPeriod);
  periods.sort(
    (a, b) => a.date - b.date || a.start - b.start || a.lessonNumber - b.lessonNumber || a.untisId - b.untisId,
  );

  // Schlüssel eindeutig machen (gekoppelte Stunden können Datum/Zeit/Nummer/Fach teilen).
  const seen = new Map<string, number>();
  for (const p of periods) {
    const count = seen.get(p.key) ?? 0;
    seen.set(p.key, count + 1);
    if (count > 0) p.key = `${p.key}#${count + 1}`;
  }
  return periods;
}

function toPeriod(l: Lesson): Period {
  const subjects = refs(l.su);
  const originalSubjectIds = subjects.map((s) => s.original?.id ?? s.id).sort((a, b) => a - b);
  const period: Period = {
    key: `${l.date}|${l.startTime}|${l.lsnumber ?? 0}|${originalSubjectIds.join('+')}`,
    untisId: l.id,
    date: l.date,
    start: l.startTime,
    end: l.endTime,
    lessonNumber: l.lsnumber ?? 0,
    subjects,
    teachers: refs(l.te),
    rooms: refs(l.ro),
    classes: (l.kl ?? []).map((k) => k.name).filter(Boolean),
    status: toStatus(l.code),
  };
  setIfText(period, 'studentGroup', l.sg);
  setIfText(period, 'lessonText', l.lstext);
  setIfText(period, 'substText', l.substText);
  setIfText(period, 'info', l.info);
  setIfText(period, 'activityType', l.activityType);
  return period;
}

function toStatus(code: Lesson['code']): PeriodStatus {
  if (code === 'cancelled') return 'cancelled';
  if (code === 'irregular') return 'irregular';
  return 'regular';
}

type RawRef = ShortData & { longname?: string };

function refs(list: readonly ShortData[] | undefined | null): ElementRef[] {
  if (!Array.isArray(list)) return [];
  return list.map((raw) => {
    const r = raw as RawRef;
    const ref: ElementRef = {
      id: r.id ?? 0,
      name: r.name ?? '',
      longName: r.longname || r.name || '',
    };
    if (typeof r.orgid === 'number' && r.orgid !== r.id) {
      ref.original = { id: r.orgid, name: r.orgname ?? '' };
    }
    return ref;
  });
}

function setIfText<K extends 'studentGroup' | 'lessonText' | 'substText' | 'info' | 'activityType'>(
  p: Period,
  key: K,
  value: string | undefined | null,
): void {
  const trimmed = typeof value === 'string' ? value.trim() : '';
  if (trimmed) p[key] = trimmed;
}
