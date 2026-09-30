import { api, ApiError } from '../api.js';
import { mockFetch } from './fixtures.js';

describe('api client', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('builds list query parameters', async () => {
    const fetchMock = mockFetch({ 'GET /api/transactions': { items: [], total: 0 } });
    await api.listTransactions({ status: 'OPEN', riskLevel: 'HIGH' });
    const url = new URL(fetchMock.mock.calls[0][0], 'http://localhost');
    expect(url.searchParams.get('status')).toBe('OPEN');
    expect(url.searchParams.get('risk_level')).toBe('HIGH');
    expect(url.searchParams.get('limit')).toBe('200');
  });

  it('omits empty filters', async () => {
    const fetchMock = mockFetch({ 'GET /api/transactions': { items: [], total: 0 } });
    await api.listTransactions();
    const url = new URL(fetchMock.mock.calls[0][0], 'http://localhost');
    expect(url.searchParams.has('status')).toBe(false);
    expect(url.searchParams.has('risk_level')).toBe(false);
  });

  it('posts review decisions as JSON with a null note when empty', async () => {
    const fetchMock = mockFetch({ 'POST /api/transactions/a%2Fb/review': { ok: true } });
    await api.review('a/b', { status: 'CLEARED', reviewer: 'ana', note: '' });
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({ status: 'CLEARED', reviewer: 'ana', note: null });
    expect(init.headers['Content-Type']).toBe('application/json');
  });

  it('surfaces string error details', async () => {
    mockFetch({ 'GET /api/transactions/x': { status: 404, body: { detail: 'transaction not found' } } });
    const err = await api.getTransaction('x').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
    expect(err.message).toBe('transaction not found');
  });

  it('joins FastAPI validation errors', async () => {
    mockFetch({
      'POST /api/transactions': {
        status: 422,
        body: { detail: [{ msg: 'amount must be > 0' }, { msg: 'bad currency' }] },
      },
    });
    await expect(api.submitTransaction({})).rejects.toThrow('amount must be > 0; bad currency');
  });

  it('falls back to the status code for non-JSON errors', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>502</html>', { status: 502 })));
    await expect(api.stats()).rejects.toThrow('Request failed (502)');
  });
});
