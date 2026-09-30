/** Headline counters from GET /api/stats. */

const TILES = [
  { key: 'open', label: 'Needs review', tone: 'accent' },
  { key: 'high_risk_open', label: 'High risk, open', tone: 'danger' },
  { key: 'reviewed', label: 'Reviewed' },
  { key: 'cleared', label: 'Cleared' },
  { key: 'alerts_sent', label: 'Alerts sent' },
  { key: 'total', label: 'Transactions scored' },
];

export default function StatsBar({ stats }) {
  return (
    <dl className="stats" aria-label="Summary">
      {TILES.map(({ key, label, tone }) => (
        <div key={key} className={`stat ${tone ? `stat-${tone}` : ''}`}>
          <dt>{label}</dt>
          <dd>{stats ? stats[key].toLocaleString() : '–'}</dd>
        </div>
      ))}
    </dl>
  );
}
