/** Small status indicators. Colour is always paired with text for accessibility. */

const STATUS_LABELS = {
  OPEN: 'Needs review',
  REVIEWED: 'Reviewed',
  CLEARED: 'Cleared',
};

export function RiskBadge({ level, score }) {
  return (
    <span className={`badge risk-${level.toLowerCase()}`} title={`Risk score ${score} / 100`}>
      <span className="badge-dot" aria-hidden="true" />
      {level}
      {score != null && <span className="badge-score">{Math.round(score)}</span>}
    </span>
  );
}

export function StatusBadge({ status }) {
  if (!status) return null;
  return <span className={`badge status-${status.toLowerCase()}`}>{STATUS_LABELS[status] ?? status}</span>;
}
