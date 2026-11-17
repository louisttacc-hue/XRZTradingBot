import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';

/**
 * Minimaler Nachbau des WebUntis-JSON-RPC-Endpunkts (/WebUntis/jsonrpc.do) für Tests.
 * Spricht exakt das Protokoll, das die webuntis-Bibliothek verwendet.
 */

export interface FakeLesson {
  id: number;
  date: number;
  startTime: number;
  endTime: number;
  kl: { id: number; name: string; longname?: string }[];
  te: { id: number; name: string; longname?: string; orgid?: number; orgname?: string }[];
  su: { id: number; name: string; longname?: string }[];
  ro: { id: number; name: string; longname?: string; orgid?: number; orgname?: string }[];
  lsnumber: number;
  code?: 'cancelled' | 'irregular';
  substText?: string;
  sg?: string;
}

export interface FakeUntisOptions {
  school?: string;
  user?: string;
  password?: string;
  classes?: { id: number; name: string; longName: string }[];
  schoolyears?: { id: number; name: string; startDate: number; endDate: number }[];
  /** Klassen, deren Stundenplan der Account NICHT sehen darf (-8509). */
  forbiddenClasses?: number[];
  klasseId?: number;
}

export class FakeUntis {
  lessons: FakeLesson[] = [];
  calls: string[] = [];
  activeSessions = new Set<string>();
  /** Nächste n Anfragen schlagen mit HTTP 503 fehl. */
  failNext = 0;
  failStatus = 503;
  private server: Server;
  private sessionCounter = 0;
  readonly opts: Required<FakeUntisOptions>;

  constructor(opts: FakeUntisOptions = {}) {
    this.opts = {
      school: 'gym-test',
      user: 'schueler',
      password: 'richtig',
      classes: [
        { id: 3, name: 'EF', longName: 'Einführungsphase' },
        { id: 4, name: 'Q1', longName: 'Qualifikationsphase 1' },
      ],
      schoolyears: [{ id: 10, name: '2026/2027', startDate: 20260803, endDate: 20270716 }],
      forbiddenClasses: [],
      klasseId: 3,
      ...opts,
    };
    this.server = createServer((req, res) => {
      let body = '';
      req.on('data', (c) => (body += c));
      req.on('end', () => {
        const send = (status: number, payload: unknown) => {
          res.writeHead(status, { 'content-type': 'application/json' });
          res.end(JSON.stringify(payload));
        };
        if (this.failNext > 0) {
          this.failNext--;
          return send(this.failStatus, { error: 'unavailable' });
        }
        const url = new URL(req.url ?? '/', 'http://localhost');
        if (url.pathname !== '/WebUntis/jsonrpc.do') return send(404, {});
        if (url.searchParams.get('school') !== this.opts.school) {
          return send(200, { jsonrpc: '2.0', id: '1', error: { code: -8500, message: 'invalid schoolname' } });
        }
        const rpc = JSON.parse(body) as { id: string; method: string; params: Record<string, any> };
        this.calls.push(rpc.method);
        const ok = (result: unknown) => send(200, { jsonrpc: '2.0', id: rpc.id, result });
        const fail = (code: number, message: string) => send(200, { jsonrpc: '2.0', id: rpc.id, error: { code, message } });

        if (rpc.method === 'authenticate') {
          if (rpc.params.user !== this.opts.user || rpc.params.password !== this.opts.password) return fail(-8504, 'bad credentials');
          const sessionId = `S${++this.sessionCounter}`;
          this.activeSessions.add(sessionId);
          return ok({ sessionId, personType: 5, personId: 77, klasseId: this.opts.klasseId });
        }

        const session = /JSESSIONID=([^;]+)/.exec(req.headers.cookie ?? '')?.[1];
        if (!session || !this.activeSessions.has(session)) return fail(-8520, 'not authenticated');

        switch (rpc.method) {
          case 'logout':
            this.activeSessions.delete(session);
            return ok(null);
          case 'getSchoolyears':
            return ok(this.opts.schoolyears);
          case 'getKlassen':
            return ok(this.opts.classes.map((c) => ({ ...c, active: true })));
          case 'getTimegridUnits':
            return ok([
              { day: 2, timeUnits: [
                { name: '1', startTime: 750, endTime: 835 },
                { name: '2', startTime: 835, endTime: 920 },
                { name: '3', startTime: 940, endTime: 1025 },
              ] },
            ]);
          case 'getHolidays':
            return ok([{ id: 1, name: 'WF', longName: 'Weihnachtsferien', startDate: 20261221, endDate: 20270105 }]);
          case 'getTimetable': {
            const o = rpc.params.options;
            const start = Number(o.startDate);
            const end = Number(o.endDate);
            const inYear = this.opts.schoolyears.some((y) => start >= y.startDate && end <= y.endDate);
            if (!inYear) return fail(-7004, 'no allowed date');
            if (this.opts.forbiddenClasses.includes(o.element.id)) return fail(-8509, 'no right for timetable');
            return ok(this.lessons.filter((l) => l.date >= start && l.date <= end && l.kl.some((k) => k.id === o.element.id)));
          }
          default:
            return fail(-32601, 'method not found');
        }
      });
    });
  }

  async start(): Promise<string> {
    await new Promise<void>((resolve) => this.server.listen(0, '127.0.0.1', resolve));
    return `http://127.0.0.1:${(this.server.address() as AddressInfo).port}/`;
  }

  async stop(): Promise<void> {
    this.server.closeAllConnections();
    await new Promise<void>((resolve) => this.server.close(() => resolve()));
  }
}

export function fakeLesson(over: Partial<FakeLesson> = {}): FakeLesson {
  return {
    id: Math.floor(Math.random() * 1e9),
    date: 20261118,
    startTime: 750,
    endTime: 835,
    kl: [{ id: 3, name: 'EF' }],
    te: [{ id: 10, name: 'MÜL', longname: 'Müller' }],
    su: [{ id: 20, name: 'D', longname: 'Deutsch' }],
    ro: [{ id: 30, name: 'R112', longname: 'Raum 112' }],
    lsnumber: 4711,
    sg: 'D-GK1',
    ...over,
  };
}
