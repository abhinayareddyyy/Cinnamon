import { useState } from 'react';
import { api } from '../api.js';
import { ruleLabel } from '../format.js';

/** Preset places so testers can provoke the impossible-travel rule quickly. */
export const PLACES = {
  none: null,
  new_york: { latitude: 40.7128, longitude: -74.006, city: 'New York', country: 'US' },
  boston: { latitude: 42.3601, longitude: -71.0589, city: 'Boston', country: 'US' },
  london: { latitude: 51.5074, longitude: -0.1278, city: 'London', country: 'GB' },
  berlin: { latitude: 52.52, longitude: 13.405, city: 'Berlin', country: 'DE' },
  tokyo: { latitude: 35.6762, longitude: 139.6503, city: 'Tokyo', country: 'JP' },
};

/**
 * Collapsible form for submitting a test transaction to the engine, so a
 * reviewer or tester can watch rules fire without an external feed.
 */
export default function SimulatePanel({ onSubmitted }) {
  const [form, setForm] = useState({
    account_id: 'acc-2001',
    amount: '49.99',
    currency: 'USD',
    merchant: 'Test Merchant',
    place: 'new_york',
  });
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const update = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  async function handleSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { place, amount, ...rest } = form;
      const res = await api.submitTransaction({ ...rest, amount: Number(amount), ...(PLACES[place] ?? {}) });
      setResult(res);
      onSubmitted?.(res);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const fired = result?.rule_results.filter((r) => r.triggered) ?? [];

  return (
    <details className="simulate">
      <summary>Submit a test transaction</summary>
      <form onSubmit={handleSubmit} className="simulate-form">
        <label className="field">
          <span>Account</span>
          <input value={form.account_id} onChange={update('account_id')} required maxLength={64} />
        </label>
        <label className="field">
          <span>Amount</span>
          <input type="number" min="0.01" step="0.01" value={form.amount} onChange={update('amount')} required />
        </label>
        <label className="field field-sm">
          <span>Currency</span>
          <input value={form.currency} onChange={update('currency')} required pattern="[A-Za-z]{3}" maxLength={3} />
        </label>
        <label className="field">
          <span>Merchant</span>
          <input value={form.merchant} onChange={update('merchant')} maxLength={200} />
        </label>
        <label className="field">
          <span>Location</span>
          <select value={form.place} onChange={update('place')}>
            {Object.entries(PLACES).map(([key, p]) => (
              <option key={key} value={key}>{p ? p.city : 'None (online)'}</option>
            ))}
          </select>
        </label>
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? 'Scoring…' : 'Score it'}
        </button>
      </form>
      {error && <p className="form-error" role="alert">{error}</p>}
      {result && (
        <p className="simulate-result" role="status">
          <strong>{result.transaction.transaction_id}</strong> scored{' '}
          <strong>{result.transaction.risk_score.toFixed(1)}</strong> ({result.transaction.risk_level})
          {fired.length > 0 ? ` — ${fired.map((r) => ruleLabel(r.rule_name)).join(', ')}` : ' — no rules fired'}
          {result.alert_sent && ' · alert sent'}
        </p>
      )}
    </details>
  );
}
