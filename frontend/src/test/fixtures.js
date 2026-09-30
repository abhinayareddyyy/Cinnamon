/** Sample API payloads shaped exactly like the backend's TransactionOut. */

export function makeTxn(overrides = {}) {
  return {
    transaction_id: 'txn-1',
    account_id: 'acc-1001',
    amount: 55,
    currency: 'USD',
    merchant: 'Harrods',
    timestamp: '2026-09-30T10:00:00Z',
    latitude: 51.5074,
    longitude: -0.1278,
    city: 'London',
    country: 'GB',
    risk_score: 95.7,
    risk_level: 'HIGH',
    flagged: true,
    review_status: 'OPEN',
    reviewed_by: null,
    review_note: null,
    reviewed_at: null,
    notified_at: '2026-09-30T10:00:01Z',
    created_at: '2026-09-30T10:00:00Z',
    flags: [
      {
        rule_name: 'impossible_travel',
        score: 95.7,
        reason: '5,570 km from New York, US in 40 min',
        details: { distance_km: 5570.2, previous_location: 'New York, US', speed_kmh: 8355.3 },
        created_at: '2026-09-30T10:00:00Z',
      },
    ],
    reviews: [],
    ...overrides,
  };
}

export const STATS = {
  total: 78, flagged: 9, open: 2, reviewed: 0, cleared: 0,
  high_risk: 6, high_risk_open: 1, alerts_sent: 6, flags_by_rule: {},
};

/**
 * Install a fake `fetch` that routes by method + path. `routes` maps
 * "GET /api/stats" style keys to a body or a function(url, init) returning
 * `{ status, body }` or a body. Returns the vi.fn so calls can be asserted.
 */
export function mockFetch(routes) {
  const fn = vi.fn(async (input, init = {}) => {
    const url = new URL(input, 'http://localhost');
    const key = `${init.method ?? 'GET'} ${url.pathname}`;
    const route = routes[key];
    if (route === undefined) {
      return new Response(JSON.stringify({ detail: `no mock for ${key}` }), { status: 404 });
    }
    let result = typeof route === 'function' ? await route(url, init) : route;
    if (!result || result.status === undefined) result = { status: 200, body: result };
    return new Response(JSON.stringify(result.body), {
      status: result.status,
      headers: { 'Content-Type': 'application/json' },
    });
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}
