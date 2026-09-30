import { useState } from 'react';
import { RiskBadge, StatusBadge } from './Badges.jsx';
import {
  formatDateTime,
  formatDetail,
  formatLocation,
  formatMoney,
  humanize,
  ruleLabel,
} from '../format.js';

const REVIEWER_KEY = 'fraud-console.reviewer';

/** localStorage can throw (private mode, blocked storage); never let it break the UI. */
function loadReviewer() {
  try {
    return window.localStorage.getItem(REVIEWER_KEY) ?? '';
  } catch {
    return '';
  }
}
function saveReviewer(name) {
  try {
    window.localStorage.setItem(REVIEWER_KEY, name);
  } catch {
    /* ignore */
  }
}

/**
 * Full view of one flagged transaction: facts, the evidence from each rule
 * that fired, the review form and the audit trail.
 *
 * `onReview(status, reviewer, note)` must return a promise; errors it throws
 * are shown inline.
 */
export default function TransactionDetail({ txn, onReview, onClose }) {
  const [reviewer, setReviewer] = useState(loadReviewer);
  const [note, setNote] = useState('');
  const [pending, setPending] = useState(null); // status currently being submitted
  const [error, setError] = useState(null);

  async function submit(status) {
    const name = reviewer.trim();
    if (!name) {
      setError('Enter your name before recording a decision.');
      return;
    }
    setPending(status);
    setError(null);
    try {
      saveReviewer(name);
      await onReview(status, name, note.trim());
      setNote('');
    } catch (e) {
      setError(e.message);
    } finally {
      setPending(null);
    }
  }

  const busy = pending !== null;

  return (
    <article className="detail" aria-label={`Details for ${txn.transaction_id}`}>
      <header className="detail-head">
        <div>
          <p className="eyebrow">Transaction</p>
          <h2 className="mono">{txn.transaction_id}</h2>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close details">
          ×
        </button>
      </header>

      <div className="detail-verdict">
        <RiskBadge level={txn.risk_level} score={txn.risk_score} />
        <StatusBadge status={txn.review_status} />
        {txn.notified_at && (
          <span className="badge badge-neutral" title={formatDateTime(txn.notified_at)}>
            Alert sent
          </span>
        )}
      </div>

      <dl className="facts">
        <div><dt>Amount</dt><dd className="strong">{formatMoney(txn.amount, txn.currency)}</dd></div>
        <div><dt>Account</dt><dd className="mono">{txn.account_id}</dd></div>
        <div><dt>Merchant</dt><dd>{txn.merchant || '—'}</dd></div>
        <div><dt>Time</dt><dd>{formatDateTime(txn.timestamp)}</dd></div>
        <div><dt>Location</dt><dd>{formatLocation(txn)}</dd></div>
        <div><dt>Risk score</dt><dd>{txn.risk_score.toFixed(1)} / 100</dd></div>
      </dl>

      <section>
        <h3>Why it was flagged</h3>
        <ul className="flags">
          {txn.flags.map((flag) => (
            <li key={flag.rule_name} className="flag">
              <div className="flag-head">
                <span className="flag-name">{ruleLabel(flag.rule_name)}</span>
                <span className="flag-score">{Math.round(flag.score)}</span>
              </div>
              <div className="meter" aria-hidden="true">
                <span style={{ width: `${flag.score}%` }} />
              </div>
              <p className="flag-reason">{flag.reason}</p>
              {Object.keys(flag.details).length > 0 && (
                <dl className="evidence">
                  {Object.entries(flag.details).map(([k, v]) => (
                    <div key={k}>
                      <dt>{humanize(k)}</dt>
                      <dd>{formatDetail(v)}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </li>
          ))}
        </ul>
      </section>

      <section className="review-box">
        <h3>Decision</h3>
        <label className="field">
          <span>Reviewer</span>
          <input
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
            placeholder="Your name"
            maxLength={100}
            autoComplete="name"
          />
        </label>
        <label className="field">
          <span>Note (optional)</span>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            maxLength={2000}
            placeholder="e.g. Confirmed with cardholder by phone"
          />
        </label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="review-actions">
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy || txn.review_status === 'REVIEWED'}
            onClick={() => submit('REVIEWED')}
            title="Investigated and confirmed suspicious or escalated"
          >
            {pending === 'REVIEWED' ? 'Saving…' : 'Mark reviewed'}
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={busy || txn.review_status === 'CLEARED'}
            onClick={() => submit('CLEARED')}
            title="Investigated and found legitimate (false positive)"
          >
            {pending === 'CLEARED' ? 'Saving…' : 'Clear as legitimate'}
          </button>
          {txn.review_status !== 'OPEN' && (
            <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => submit('OPEN')}>
              {pending === 'OPEN' ? 'Saving…' : 'Reopen'}
            </button>
          )}
        </div>
      </section>

      <section>
        <h3>History</h3>
        {txn.reviews.length === 0 ? (
          <p className="muted">No decisions recorded yet.</p>
        ) : (
          <ol className="timeline">
            {txn.reviews.map((event, i) => (
              <li key={i}>
                <StatusBadge status={event.status} />
                <span>
                  by <strong>{event.reviewer}</strong> · {formatDateTime(event.created_at)}
                </span>
                {event.note && <p className="timeline-note">{event.note}</p>}
              </li>
            ))}
          </ol>
        )}
      </section>
    </article>
  );
}
