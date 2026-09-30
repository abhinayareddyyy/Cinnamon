import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../App.jsx';
import { STATS, makeTxn, mockFetch } from './fixtures.js';

const OPEN_TXN = makeTxn();
const MEDIUM_TXN = makeTxn({
  transaction_id: 'txn-2',
  account_id: 'acc-1003',
  amount: 3.5,
  merchant: 'Online Games Ltd',
  city: 'Chicago',
  country: 'US',
  risk_score: 50,
  risk_level: 'MEDIUM',
  notified_at: null,
  flags: [{ rule_name: 'velocity', score: 50, reason: '6 transactions in 10 min (limit 5)', details: { count: 6 }, created_at: '2026-09-30T10:00:00Z' }],
});

/** A small in-memory backend so review actions change what the list returns. */
function fakeBackend() {
  const db = { 'txn-1': OPEN_TXN, 'txn-2': MEDIUM_TXN };
  return mockFetch({
    'GET /api/stats': STATS,
    'GET /api/transactions': (url) => {
      const status = url.searchParams.get('status');
      const risk = url.searchParams.get('risk_level');
      const items = Object.values(db).filter(
        (t) => (!status || t.review_status === status) && (!risk || t.risk_level === risk),
      );
      return { items, total: items.length, limit: 200, offset: 0 };
    },
    'GET /api/transactions/txn-1': () => db['txn-1'],
    'GET /api/transactions/txn-2': () => db['txn-2'],
    'POST /api/transactions/txn-1/review': (url, init) => {
      const { status, reviewer, note } = JSON.parse(init.body);
      db['txn-1'] = {
        ...db['txn-1'],
        review_status: status,
        reviewed_by: reviewer,
        reviews: [...db['txn-1'].reviews, { status, reviewer, note, created_at: '2026-09-30T11:00:00Z' }],
      };
      return db['txn-1'];
    },
  });
}

function rows() {
  return screen.queryAllByRole('row').slice(1); // drop the header row
}

describe('App', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('lists open flagged transactions and headline stats', async () => {
    const fetchMock = fakeBackend();
    render(<App />);
    expect(await screen.findByText('Harrods')).toBeInTheDocument();
    expect(screen.getByText('Online Games Ltd')).toBeInTheDocument();
    expect(rows()).toHaveLength(2);
    expect(screen.getByText('2 transactions')).toBeInTheDocument();
    const summary = screen.getByLabelText('Summary');
    expect(within(summary).getByText('78')).toBeInTheDocument();

    const listCall = fetchMock.mock.calls.find(([u]) => String(u).startsWith('/api/transactions?'));
    expect(listCall[0]).toContain('status=OPEN');
  });

  it('filters by risk level', async () => {
    const user = userEvent.setup();
    fakeBackend();
    render(<App />);
    await screen.findByText('Harrods');
    await user.selectOptions(screen.getByRole('combobox', { name: 'Risk' }), 'MEDIUM');
    await waitFor(() => expect(screen.queryByText('Harrods')).not.toBeInTheDocument());
    expect(screen.getByText('Online Games Ltd')).toBeInTheDocument();
  });

  it('opens a transaction, clears it, and it leaves the "Needs review" queue', async () => {
    const user = userEvent.setup();
    const fetchMock = fakeBackend();
    render(<App />);
    await user.click(await screen.findByRole('row', { name: 'Transaction txn-1' }));

    const panel = await screen.findByRole('article', { name: 'Details for txn-1' });
    expect(within(panel).getByText('5,570 km from New York, US in 40 min')).toBeInTheDocument();
    expect(window.location.search).toBe('?txn=txn-1'); // shareable deep link

    await user.type(within(panel).getByLabelText('Reviewer'), 'Ana');
    await user.type(within(panel).getByLabelText('Note (optional)'), 'Cardholder is travelling');
    await user.click(within(panel).getByRole('button', { name: 'Clear as legitimate' }));

    const reviewCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(reviewCall[1].body)).toEqual({
      status: 'CLEARED', reviewer: 'Ana', note: 'Cardholder is travelling',
    });

    // Detail reflects the decision and its audit entry...
    expect(await within(panel).findByText('Cardholder is travelling')).toBeInTheDocument();
    expect(within(panel).getAllByText('Cleared').length).toBeGreaterThan(0);
    // ...and the queue refreshed: only the MEDIUM transaction is still open.
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.getByRole('row', { name: 'Transaction txn-2' })).toBeInTheDocument();

    // The cleared transaction appears under the Cleared tab.
    await user.click(screen.getByRole('tab', { name: 'Cleared' }));
    expect(await screen.findByRole('row', { name: 'Transaction txn-1' })).toBeInTheDocument();
  });

  it('selects rows with the keyboard', async () => {
    const user = userEvent.setup();
    fakeBackend();
    render(<App />);
    const row = await screen.findByRole('row', { name: 'Transaction txn-2' });
    row.focus();
    await user.keyboard('{Enter}');
    expect(await screen.findByRole('article', { name: 'Details for txn-2' })).toBeInTheDocument();
  });

  it('opens the transaction referenced in an alert link (?txn=...)', async () => {
    window.history.replaceState(null, '', '/?txn=txn-2');
    fakeBackend();
    render(<App />);
    expect(await screen.findByRole('article', { name: 'Details for txn-2' })).toBeInTheDocument();
  });

  it('shows an error banner when the API is down', async () => {
    mockFetch({ 'GET /api/stats': { status: 500, body: { detail: 'database locked' } } });
    render(<App />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load transactions');
  });

  it('shows an empty state', async () => {
    mockFetch({
      'GET /api/stats': STATS,
      'GET /api/transactions': { items: [], total: 0, limit: 200, offset: 0 },
    });
    render(<App />);
    expect(await screen.findByText('No flagged transactions match these filters.')).toBeInTheDocument();
  });

  it('submits a test transaction and reports the verdict', async () => {
    const user = userEvent.setup();
    const fetchMock = fakeBackend();
    // Wrap the fake backend to add the "create transaction" route.
    const routes = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input, init = {}) => {
      if (init.method === 'POST' && String(input) === '/api/transactions') {
        return new Response(JSON.stringify({
          transaction: makeTxn({ transaction_id: 'txn_new', risk_score: 70, risk_level: 'HIGH' }),
          rule_results: [
            { rule_name: 'unusual_amount', triggered: true, score: 70, reason: 'x', details: {}, error: false },
            { rule_name: 'velocity', triggered: false, score: 0, reason: '', details: {}, error: false },
          ],
          alert_sent: true,
        }), { status: 201 });
      }
      return routes(input, init);
    });
    render(<App />);
    await screen.findByText('Harrods');
    await user.click(screen.getByText('Submit a test transaction'));
    const amount = screen.getByLabelText('Amount');
    await user.clear(amount);
    await user.type(amount, '15000');
    await user.selectOptions(screen.getByLabelText('Location'), 'tokyo');
    await user.click(screen.getByRole('button', { name: 'Score it' }));

    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('txn_new scored 70.0 (HIGH) — Unusual amount · alert sent');
    const post = fetchMock.mock.calls.find(([u, i]) => i?.method === 'POST');
    expect(JSON.parse(post[1].body)).toMatchObject({ amount: 15000, city: 'Tokyo', latitude: 35.6762 });
  });
});
