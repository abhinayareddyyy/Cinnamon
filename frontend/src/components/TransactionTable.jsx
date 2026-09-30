import { RiskBadge, StatusBadge } from './Badges.jsx';
import { formatDateTime, formatLocation, formatMoney, ruleLabel } from '../format.js';

/**
 * The review queue. Each row is keyboard-selectable (Enter / Space) and opens
 * the detail panel via `onSelect`.
 */
export default function TransactionTable({ items, selectedId, onSelect, loading }) {
  if (!loading && items.length === 0) {
    return (
      <div className="empty">
        <p className="empty-title">Nothing here</p>
        <p>No flagged transactions match these filters.</p>
      </div>
    );
  }

  return (
    <div className="table-wrap">
      <table className="txn-table">
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Account</th>
            <th scope="col" className="num">Amount</th>
            <th scope="col">Merchant · Location</th>
            <th scope="col">Rules triggered</th>
            <th scope="col">Risk</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {items.map((txn) => {
            const selected = txn.transaction_id === selectedId;
            return (
              <tr
                key={txn.transaction_id}
                className={selected ? 'row-selected' : undefined}
                tabIndex={0}
                aria-selected={selected}
                aria-label={`Transaction ${txn.transaction_id}`}
                onClick={() => onSelect(txn.transaction_id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onSelect(txn.transaction_id);
                  }
                }}
              >
                <td className="nowrap muted">{formatDateTime(txn.timestamp)}</td>
                <td>
                  <div className="mono">{txn.account_id}</div>
                  <div className="sub mono">{txn.transaction_id}</div>
                </td>
                <td className="num strong">{formatMoney(txn.amount, txn.currency)}</td>
                <td>
                  <div>{txn.merchant || '—'}</div>
                  <div className="sub">{formatLocation(txn)}</div>
                </td>
                <td>
                  <div className="chips">
                    {txn.flags.map((f) => (
                      <span key={f.rule_name} className="chip" title={f.reason}>
                        {ruleLabel(f.rule_name)}
                      </span>
                    ))}
                  </div>
                </td>
                <td><RiskBadge level={txn.risk_level} score={txn.risk_score} /></td>
                <td><StatusBadge status={txn.review_status} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
