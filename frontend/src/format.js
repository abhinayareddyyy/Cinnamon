/** Display helpers shared by the console components. */

const RULE_LABELS = {
  velocity: 'Velocity',
  unusual_amount: 'Unusual amount',
  impossible_travel: 'Impossible travel',
};

/** Turn `snake_case` into "Snake case". */
export function humanize(key) {
  const text = String(key).replace(/_/g, ' ').trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * Friendly rule name. Rules added later via plug-ins are humanized
 * automatically, so the console needs no change when a rule is added.
 */
export function ruleLabel(name) {
  return RULE_LABELS[name] ?? humanize(name);
}

export function formatMoney(amount, currency) {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount);
  } catch {
    return `${Number(amount).toFixed(2)} ${currency}`; // unknown currency code
  }
}

export function formatDateTime(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatLocation(txn) {
  const place = [txn.city, txn.country].filter(Boolean).join(', ');
  if (place) return place;
  if (txn.latitude != null) return `${txn.latitude.toFixed(3)}, ${txn.longitude.toFixed(3)}`;
  return 'No location';
}

/** Render a rule-evidence value compactly. */
export function formatDetail(value) {
  if (value == null) return '—';
  if (typeof value === 'number') {
    return Number.isInteger(value) ? value.toLocaleString() : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
