/**
 * Thin client for the fraud engine REST API (see backend/fraud_engine/api.py).
 *
 * All paths are relative, so in development Vite proxies them to the backend
 * and in production the console can be served by the backend itself.
 * Set VITE_API_BASE to point at a different origin.
 */

const BASE = import.meta.env.VITE_API_BASE ?? '';

/** Error carrying the HTTP status and the server's `detail` message. */
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** Turn FastAPI's error body (string or validation list) into one message. */
function describeError(body, fallback) {
  if (!body || body.detail == null) return fallback;
  if (typeof body.detail === 'string') return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join('; ');
  return fallback;
}

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  });
  if (!response.ok) {
    let body = null;
    try {
      body = await response.json();
    } catch {
      /* non-JSON error page */
    }
    throw new ApiError(describeError(body, `Request failed (${response.status})`), response.status);
  }
  return response.json();
}

export const api = {
  /** Page of flagged transactions. `status` / `riskLevel` are optional filters. */
  listTransactions({ status, riskLevel, limit = 200 } = {}) {
    const params = new URLSearchParams({ limit: String(limit) });
    if (status) params.set('status', status);
    if (riskLevel) params.set('risk_level', riskLevel);
    return request(`/api/transactions?${params}`);
  },

  /** One transaction including its fraud flags and review history. */
  getTransaction(id) {
    return request(`/api/transactions/${encodeURIComponent(id)}`);
  },

  /** Record a reviewer decision: status is REVIEWED, CLEARED or OPEN. */
  review(id, { status, reviewer, note }) {
    return request(`/api/transactions/${encodeURIComponent(id)}/review`, {
      method: 'POST',
      body: JSON.stringify({ status, reviewer, note: note || null }),
    });
  },

  stats() {
    return request('/api/stats');
  },

  /** Submit a transaction for evaluation (used by the test-transaction panel). */
  submitTransaction(payload) {
    return request('/api/transactions', { method: 'POST', body: JSON.stringify(payload) });
  },
};
