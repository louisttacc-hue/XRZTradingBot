import { describe, expect, it } from 'vitest';
import { normalizeLessons } from '../src/untis/normalize.js';
import { rawLesson } from './fixtures.js';

describe('normalizeLessons', () => {
  it('übernimmt Zeiten, Kurs, Lehrer, Raum und Status', () => {
    const [p] = normalizeLessons([rawLesson({ id: 1, code: 'cancelled', substText: 'Aufgaben in Moodle' })]);
    expect(p).toMatchObject({
      untisId: 1,
      date: 20261118,
      start: 750,
      end: 835,
      lessonNumber: 4711,
      status: 'cancelled',
      substText: 'Aufgaben in Moodle',
      studentGroup: 'D-GK1',
      classes: ['EF'],
      subjects: [{ id: 20, name: 'D', longName: 'Deutsch' }],
      teachers: [{ id: 10, name: 'MÜL', longName: 'Müller' }],
      rooms: [{ id: 30, name: 'R112', longName: 'Raum 112' }],
    });
  });

  it('erkennt Vertretungslehrer und Raumwechsel über orgid/orgname', () => {
    const [p] = normalizeLessons([
      rawLesson({
        code: 'irregular',
        te: [{ id: 11, name: 'NEU', longname: 'Neumann', orgid: 10, orgname: 'MÜL' }],
        ro: [{ id: 31, name: 'A001', longname: 'Aula', orgid: 30, orgname: 'R112' }],
      }),
    ]);
    expect(p?.status).toBe('irregular');
    expect(p?.teachers[0]?.original).toEqual({ id: 10, name: 'MÜL' });
    expect(p?.rooms[0]?.original).toEqual({ id: 30, name: 'R112' });
  });

  it('entfernt doppelte Stunden (gleiche Untis-ID aus mehreren Klassen-Abfragen) und sortiert nach Zeit', () => {
    const late = rawLesson({ id: 2, startTime: 940, endTime: 1025 });
    const early = rawLesson({ id: 3, startTime: 750, endTime: 835, lsnumber: 1 });
    const result = normalizeLessons([late, early, { ...late }]);
    expect(result.map((p) => p.untisId)).toEqual([3, 2]);
  });

  it('bildet einen stabilen Schlüssel aus Datum, Beginn, Kurs und ursprünglichem Fach – unabhängig von der Untis-ID', () => {
    const [a] = normalizeLessons([rawLesson({ id: 1 })]);
    const [b] = normalizeLessons([rawLesson({ id: 999 })]);
    expect(a?.key).toBe(b?.key);
    // Fachwechsel durch Vertretung ändert den Schlüssel nicht
    const [c] = normalizeLessons([
      rawLesson({ id: 5, su: [{ id: 21, name: 'M', longname: 'Mathematik', orgid: 20, orgname: 'D' }] }),
    ]);
    expect(c?.key).toBe(a?.key);
  });

  it('macht Schlüssel eindeutig, wenn Untis gekoppelte Stunden mit gleicher Nummer liefert', () => {
    const result = normalizeLessons([rawLesson({ id: 1 }), rawLesson({ id: 2 })]);
    expect(new Set(result.map((p) => p.key)).size).toBe(2);
  });

  it('verträgt fehlende Felder und leere Lehrer (id 0)', () => {
    const [p] = normalizeLessons([
      rawLesson({ te: [{ id: 0, name: '', longname: '', orgid: 10, orgname: 'MÜL' }], su: [], ro: undefined as never, sg: undefined }),
    ]);
    expect(p?.subjects).toEqual([]);
    expect(p?.rooms).toEqual([]);
    expect(p?.teachers[0]).toMatchObject({ id: 0, name: '', original: { id: 10, name: 'MÜL' } });
  });
});
