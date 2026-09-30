import { formatDateTime, formatDetail, formatLocation, formatMoney, humanize, ruleLabel } from '../format.js';

describe('format helpers', () => {
  it('labels built-in rules and humanizes plug-in rules', () => {
    expect(ruleLabel('impossible_travel')).toBe('Impossible travel');
    expect(ruleLabel('blocked_merchant')).toBe('Blocked merchant');
    expect(humanize('z_score')).toBe('Z score');
  });

  it('formats money, tolerating unknown currency codes', () => {
    expect(formatMoney(1234.5, 'INR')).toMatch(/1,234\.50/);
    expect(formatMoney(10, 'ZZZZ')).toBe('10.00 ZZZZ');
  });

  it('formats locations with fallbacks', () => {
    expect(formatLocation({ city: 'West Bengal', country: 'IN' })).toBe('West Bengal, IN');
    expect(formatLocation({ latitude: 1.23456, longitude: 2.5 })).toBe('1.235, 2.500');
    expect(formatLocation({ latitude: null })).toBe('No location');
  });

  it('formats rule evidence values', () => {
    expect(formatDetail(null)).toBe('—');
    expect(formatDetail(5570)).toBe((5570).toLocaleString());
    expect(formatDetail(3.14159)).toBe((3.14).toLocaleString());
    expect(formatDetail('New York')).toBe('New York');
    expect(formatDetail({ a: 1 })).toBe('{"a":1}');
  });

  it('handles missing or invalid dates', () => {
    expect(formatDateTime(null)).toBe('—');
    expect(formatDateTime('not a date')).toBe('—');
    expect(formatDateTime('2026-09-30T10:00:00Z')).toMatch(/2026/);
  });
});
