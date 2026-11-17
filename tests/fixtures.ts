import type { Lesson } from 'webuntis';
import { normalizeLessons } from '../src/untis/normalize.js';
import type { Period } from '../src/domain/types.js';

let nextId = 1000;

/** Baut eine Untis-Stunde, wie sie die JSON-RPC-Methode getTimetable liefert. */
export function rawLesson(overrides: Partial<Lesson> = {}): Lesson {
  return {
    id: nextId++,
    date: 20261118,
    startTime: 750,
    endTime: 835,
    kl: [{ id: 1, name: 'EF', longname: 'Einführungsphase' }],
    te: [{ id: 10, name: 'MÜL', longname: 'Müller' }],
    su: [{ id: 20, name: 'D', longname: 'Deutsch' }],
    ro: [{ id: 30, name: 'R112', longname: 'Raum 112' }],
    lsnumber: 4711,
    activityType: 'Unterricht',
    sg: 'D-GK1',
    ...overrides,
  } as Lesson;
}

export function period(overrides: Partial<Lesson> = {}): Period {
  const [p] = normalizeLessons([rawLesson(overrides)]);
  if (!p) throw new Error('fixture failed');
  return p;
}
