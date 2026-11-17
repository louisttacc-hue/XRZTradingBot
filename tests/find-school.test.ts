import { describe, expect, it } from 'vitest';
import { searchSchools } from '../src/scripts/find-school.js';

describe('searchSchools', () => {
  it('fragt die Untis-Schulsuche ab und liefert Server und Login-Namen', async () => {
    let body = '';
    const fakeFetch = (async (_url: string, init: RequestInit) => {
      body = String(init.body);
      return new Response(
        JSON.stringify({
          result: { schools: [{ displayName: 'Gymnasium Jüchen', address: '41363 Jüchen', serverUrl: 'https://nessa.webuntis.com/WebUntis/?school=gym-juechen', loginName: 'gym-juechen' }] },
        }),
      );
    }) as unknown as typeof fetch;
    const result = await searchSchools('Jüchen', fakeFetch);
    expect(JSON.parse(body)).toMatchObject({ method: 'searchSchool', params: [{ search: 'Jüchen' }] });
    expect(result).toEqual([{ displayName: 'Gymnasium Jüchen', address: '41363 Jüchen', server: 'nessa.webuntis.com', loginName: 'gym-juechen' }]);
  });

  it('meldet zu ungenaue Suchen verständlich', async () => {
    const fakeFetch = (async () => new Response(JSON.stringify({ error: { code: -6003, message: 'too many results' } }))) as unknown as typeof fetch;
    await expect(searchSchools('Gym', fakeFetch)).rejects.toThrow(/genauer/);
  });
});
