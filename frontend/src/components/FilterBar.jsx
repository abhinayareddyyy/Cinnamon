/** Review-queue tabs plus a risk-level filter. */

export const STATUS_TABS = [
  { value: 'OPEN', label: 'Needs review' },
  { value: 'REVIEWED', label: 'Reviewed' },
  { value: 'CLEARED', label: 'Cleared' },
  { value: 'ALL', label: 'All flagged' },
];

export default function FilterBar({ status, riskLevel, onStatusChange, onRiskLevelChange, onRefresh, refreshing }) {
  return (
    <div className="filter-bar">
      <div className="tabs" role="tablist" aria-label="Review status">
        {STATUS_TABS.map((tab) => (
          <button
            key={tab.value}
            type="button"
            role="tab"
            aria-selected={status === tab.value}
            className={`tab ${status === tab.value ? 'tab-active' : ''}`}
            onClick={() => onStatusChange(tab.value)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="filter-actions">
        <label className="select-label">
          <span>Risk</span>
          <select value={riskLevel} onChange={(e) => onRiskLevelChange(e.target.value)}>
            <option value="">Any</option>
            <option value="HIGH">High</option>
            <option value="MEDIUM">Medium</option>
            <option value="LOW">Low</option>
          </select>
        </label>
        <button type="button" className="btn btn-ghost" onClick={onRefresh} disabled={refreshing}>
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>
    </div>
  );
}
