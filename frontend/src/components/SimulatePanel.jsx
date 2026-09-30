import { useState } from 'react';
import { api } from '../api.js';
import { ruleLabel } from '../format.js';

/** Preset places so testers can provoke the impossible-travel rule quickly. */
export const PLACES = {
  none: null,
  maharashtra: { latitude: 19.076, longitude: 72.8777, city: 'Maharashtra', country: 'IN' },
  karnataka: { latitude: 12.9716, longitude: 77.5946, city: 'Karnataka', country: 'IN' },
  west_bengal: { latitude: 22.5726, longitude: 88.3639, city: 'West Bengal', country: 'IN' },
  tamil_nadu: { latitude: 13.0827, longitude: 80.2707, city: 'Tamil Nadu', country: 'IN' },
  telangana: { latitude: 17.385, longitude: 78.4867, city: 'Telangana', country: 'IN' },
};

/**
 * Collapsible form for submitting a test transaction to the engine, so a
 * reviewer or tester can watch rules fire without an external feed.
 */
export default function SimulatePanel({ onSubmitted }) {
  const [form, setForm] = useState({
    account_id: 'acc-2001',
    amount: '49.99',
    currency: 'INR',
    merchant: 'Test Merchant',
    place: 'maharashtra',
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
          <input value="INR" readOnly aria-label="Currency" />
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
