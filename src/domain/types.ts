/**
 * Domänen-Typen des Bots. Alles, was aus Untis kommt, wird in diese Form
 * normalisiert, bevor es verglichen, gespeichert oder formatiert wird.
 */

/** Datum im Untis-Format YYYYMMDD, z. B. 20261118. Immer Schulzeit (Europe/Berlin). */
export type UntisDate = number;

/** Uhrzeit im Untis-Format HHMM, z. B. 750 = 07:50. */
export type UntisTime = number;

export interface ElementRef {
  id: number;
  /** Kurzname, z. B. Lehrerkürzel "MÜL" oder Raum "R112". */
  name: string;
  /** Langname, z. B. "Müller" oder "Deutsch". */
  longName: string;
  /** Ursprünglich geplantes Element, falls Untis eine Vertretung/einen Raumwechsel markiert. */
  original?: { id: number; name: string };
}

export type PeriodStatus = 'regular' | 'cancelled' | 'irregular';

/** Eine einzelne Unterrichtsstunde (Untis "Period") der überwachten Klasse(n). */
export interface Period {
  /** Stabile fachliche Identität (Datum, Beginn, Unterrichtsnummer, ursprüngliches Fach). */
  key: string;
  untisId: number;
  date: UntisDate;
  start: UntisTime;
  end: UntisTime;
  /** Untis-Unterrichtsnummer (lsnumber) – identifiziert den Kurs. */
  lessonNumber: number;
  subjects: ElementRef[];
  teachers: ElementRef[];
  rooms: ElementRef[];
  classes: string[];
  /** Schülergruppe, in der Oberstufe meist der Kursname (z. B. "D-GK1"). */
  studentGroup?: string;
  lessonText?: string;
  status: PeriodStatus;
  /** Vertretungstext aus Untis. */
  substText?: string;
  /** Zusatzinfo aus Untis. */
  info?: string;
  activityType?: string;
}

export type ChangeKind =
  /** Untis meldet Entfall. */
  | 'cancelled'
  /** Untis meldet anderen Lehrer. */
  | 'substitution'
  /** Untis meldet anderen Raum. */
  | 'roomChange'
  /** Untis meldet "irregulär" (Verlegung, Zusatzstunde, Sonderveranstaltung …). */
  | 'irregular'
  /** Eine zuvor gemeldete Änderung wurde zurückgenommen. */
  | 'reinstated'
  /** Stunde neu im Plan, ohne Untis-Kennzeichnung (Stundenplanänderung). */
  | 'added'
  /** Stunde aus dem Plan verschwunden, ohne Untis-Kennzeichnung. */
  | 'removed'
  /** Stunde zeitlich verschoben (gleiche Untis-ID, andere Zeit). */
  | 'moved'
  /** Lehrer/Raum/Fach ohne Untis-Kennzeichnung geändert. */
  | 'modified';

export interface ChangeEvent {
  kinds: ChangeKind[];
  /** Aktueller Stand der Stunde (bei 'removed' der letzte bekannte Stand). */
  period: Period;
  /** Vorheriger Stand (bei 'moved' / 'modified'). */
  previous?: Period;
}

/** Zeitraster der Schule: Startzeit → Stundenbezeichnung ("1", "2", …). */
export interface TimeUnit {
  name: string;
  start: UntisTime;
  end: UntisTime;
}

export interface Holiday {
  name: string;
  longName: string;
  startDate: UntisDate;
  endDate: UntisDate;
}
