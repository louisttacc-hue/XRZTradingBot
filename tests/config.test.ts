import { describe, expect, it } from 'vitest';
import { loadConfig, quietHoursDelayMs } from '../src/config.js';

const base = {
  UNTIS_SERVER: 'beispiel.webuntis.com',
  UNTIS_SCHOOL: 'gym-juechen',
  UNTIS_USERNAME: 'schueler',
  UNTIS_PASSWORD: 'geheim',
};

describe('loadConfig', () => {
  it('setzt sinnvolle Standardwerte', () => {
    const c = loadConfig(base);
    expect(c.untis).toMatchObject({ server: 'beispiel.webuntis.com', school: 'gym-juechen', auth: 'password', classes: ['EF'] });
    expect(c.pollIntervalMinutes).toBe(5);
    expect(c.lookaheadDays).toBe(14);
    expect(c.timeZone).toBe('Europe/Berlin');
    expect(c.quietHours).toEqual({ start: 2200, end: 600 });
  });

  it('akzeptiert eine komplette WebUntis-URL als Server', () => {
    expect(loadConfig({ ...base, UNTIS_SERVER: 'https://nessa.webuntis.com/WebUntis/?school=x#/basic/login' }).untis.server).toBe('nessa.webuntis.com');
  });

  it('liest Klassenlisten, Admin-Nummern und Gruppen-Link', () => {
    const c = loadConfig({
      ...base,
      UNTIS_CLASSES: 'EF, EF-a ,',
      ADMIN_NUMBERS: '+49 151 1234567, 0049-160-111',
      WHATSAPP_GROUP_INVITE: 'https://chat.whatsapp.com/AbCdEf123?mode=r',
      QUIET_HOURS: '',
    });
    expect(c.untis.classes).toEqual(['EF', 'EF-a']);
    expect(c.adminNumbers).toEqual(['491511234567', '0049160111'.replace(/^00/, '')]);
    expect(c.whatsapp.groupInviteCode).toBe('AbCdEf123');
    expect(c.quietHours).toBeNull();
  });

  it('meldet fehlende Zugangsdaten verständlich', () => {
    expect(() => loadConfig({ ...base, UNTIS_PASSWORD: '' })).toThrow(/UNTIS_PASSWORD/);
    expect(() => loadConfig({ ...base, UNTIS_AUTH: 'secret' })).toThrow(/UNTIS_SECRET/);
    expect(() => loadConfig({ UNTIS_SCHOOL: 'x' })).toThrow(/UNTIS_SERVER/);
  });

  it('übernimmt Secret und Benutzer direkt aus dem Untis-QR-Link (Schul-Login per Microsoft/IServ)', () => {
    const c = loadConfig({
      UNTIS_SERVER: 'nessa.webuntis.com',
      UNTIS_SCHOOL: 'gym-juechen',
      UNTIS_AUTH: 'secret',
      UNTIS_SECRET: 'untis://setschool?url=nessa.webuntis.com&school=gym-juechen&user=max.mustermann&key=ABCDEF1234567890&schoolNumber=123',
    });
    expect(c.untis.secret).toBe('ABCDEF1234567890');
    expect(c.untis.username).toBe('max.mustermann');
  });

  it('erlaubt anonymen Zugang ohne Benutzerdaten', () => {
    expect(loadConfig({ UNTIS_SERVER: 'a.webuntis.com', UNTIS_SCHOOL: 'x', UNTIS_AUTH: 'anonymous' }).untis.auth).toBe('anonymous');
  });

  it('verhindert zu häufiges Abfragen', () => {
    expect(() => loadConfig({ ...base, POLL_INTERVAL_MINUTES: '1' })).toThrow(/POLL_INTERVAL_MINUTES/);
  });
});

describe('quietHoursDelayMs', () => {
  const qh = { start: 2200, end: 600 };
  it('verschiebt Meldungen in der Nachtruhe auf das Ende der Ruhezeit', () => {
    expect(quietHoursDelayMs({ date: 20261118, time: 2300 }, qh)).toBe(7 * 3_600_000);
    expect(quietHoursDelayMs({ date: 20261118, time: 530 }, qh)).toBe(30 * 60_000);
  });
  it('sendet tagsüber sofort', () => {
    expect(quietHoursDelayMs({ date: 20261118, time: 1200 }, qh)).toBe(0);
    expect(quietHoursDelayMs({ date: 20261118, time: 600 }, qh)).toBe(0);
    expect(quietHoursDelayMs({ date: 20261118, time: 1200 }, null)).toBe(0);
  });
});
