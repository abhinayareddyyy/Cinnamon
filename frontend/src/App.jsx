import { useCallback, useEffect, useState } from 'react';
import { api } from './api.js';
import FilterBar from './components/FilterBar.jsx';
import SimulatePanel from './components/SimulatePanel.jsx';
import StatsBar from './components/StatsBar.jsx';
import TransactionDetail from './components/TransactionDetail.jsx';
import TransactionTable from './components/TransactionTable.jsx';

/** How often the queue refreshes itself so new flags appear without a reload. */
export const POLL_INTERVAL_MS = 15000;

/** Alert emails link to `/?txn=<id>`; keep the URL in sync so links are shareable. */
function readSelectedFromUrl() {
  return new URLSearchParams(window.location.search).get('txn');
}
function writeSelectedToUrl(id) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set('txn', id);
  else url.searchParams.delete('txn');
  window.history.replaceState(null, '', url);
}

export default function App() {
  const [status, setStatus] = useState('OPEN');
  const [riskLevel, setRiskLevel] = useState('');
  const [page, setPage] = useState({ items: [], total: 0 });
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedId, setSelectedId] = useState(readSelectedFromUrl);
  const [selected, setSelected] = useState(null);

  /** Reload the queue and the counters for the current filters. */
  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [list, counters] = await Promise.all([
        api.listTransactions({ status: status === 'ALL' ? undefined : status, riskLevel: riskLevel || undefined }),
        api.stats(),
      ]);
      setPage(list);
      setStats(counters);
      setError(null);
    } catch (e) {
      setError(`Could not load transactions: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, [status, riskLevel]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  // Load the full record (flags + review history) whenever the selection changes.
  useEffect(() => {
    writeSelectedToUrl(selectedId);
    if (!selectedId) {
      setSelected(null);
      return undefined;
    }
    let cancelled = false;
    api
      .getTransaction(selectedId)
      .then((txn) => !cancelled && setSelected(txn))
      .catch((e) => {
        if (cancelled) return;
        setError(`Could not open ${selectedId}: ${e.message}`);
        setSelectedId(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  /** Record a decision, then refresh the queue (the row may leave the current tab). */
  async function handleReview(newStatus, reviewer, note) {
    const updated = await api.review(selected.transaction_id, { status: newStatus, reviewer, note });
    setSelected(updated);
    await refresh();
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true">
            <path d="M16 2 4 7v8c0 7.5 5.1 13.4 12 15 6.9-1.6 12-7.5 12-15V7z" fill="currentColor" />
          </svg>
          <div>
            <h1>Fraud Review Console</h1>
            <p className="muted">Transactions flagged by the rule engine</p>
          </div>
        </div>
        <StatsBar stats={stats} />
      </header>

      <SimulatePanel onSubmitted={refresh} />

      {error && (
        <div className="banner-error" role="alert">
          {error}
          <button type="button" className="btn btn-ghost" onClick={() => setError(null)}>Dismiss</button>
        </div>
      )}

      <main className={`workspace ${selected ? 'has-detail' : ''}`}>
        <section className="queue" aria-label="Flagged transactions">
          <FilterBar
            status={status}
            riskLevel={riskLevel}
            onStatusChange={setStatus}
            onRiskLevelChange={setRiskLevel}
            onRefresh={refresh}
            refreshing={loading}
          />
          <p className="queue-count muted" aria-live="polite">
            {loading && page.items.length === 0 ? 'Loading…' : `${page.total} transaction${page.total === 1 ? '' : 's'}`}
          </p>
          <TransactionTable
            items={page.items}
            selectedId={selectedId}
            onSelect={setSelectedId}
            loading={loading}
          />
        </section>

        {selected && (
          <TransactionDetail
            key={selected.transaction_id}
            txn={selected}
            onReview={handleReview}
            onClose={() => setSelectedId(null)}
          />
        )}
      </main>
    </div>
  );
}
