import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import TransactionDetail from '../components/TransactionDetail.jsx';
import { makeTxn } from './fixtures.js';

function setup(txnOverrides = {}, onReview = vi.fn().mockResolvedValue(undefined)) {
  const user = userEvent.setup();
  const onClose = vi.fn();
  render(<TransactionDetail txn={makeTxn(txnOverrides)} onReview={onReview} onClose={onClose} />);
  return { user, onReview, onClose };
}

describe('TransactionDetail', () => {
  it('shows the verdict and the evidence behind each flag', () => {
    setup();
    expect(screen.getByRole('heading', { name: 'txn-1' })).toBeInTheDocument();
    expect(screen.getByText('HIGH')).toBeInTheDocument();
    expect(screen.getByText('Needs review')).toBeInTheDocument();
    expect(screen.getByText('Alert sent')).toBeInTheDocument();
    expect(screen.getByText('Impossible travel')).toBeInTheDocument();
    expect(screen.getByText('5,570 km from New York, US in 40 min')).toBeInTheDocument();
    expect(screen.getByText('Previous location')).toBeInTheDocument();
    expect(screen.getByText('No decisions recorded yet.')).toBeInTheDocument();
  });

  it('requires a reviewer name before recording a decision', async () => {
    const { user, onReview } = setup();
    await user.click(screen.getByRole('button', { name: 'Clear as legitimate' }));
    expect(onReview).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('Enter your name');
  });

  it('submits the decision with trimmed reviewer and note, and remembers the reviewer', async () => {
    const { user, onReview } = setup();
    await user.type(screen.getByLabelText('Reviewer'), '  Ana  ');
    await user.type(screen.getByLabelText('Note (optional)'), ' Customer confirmed travel ');
    await user.click(screen.getByRole('button', { name: 'Clear as legitimate' }));
    expect(onReview).toHaveBeenCalledWith('CLEARED', 'Ana', 'Customer confirmed travel');
    expect(window.localStorage.getItem('fraud-console.reviewer')).toBe('Ana');
    expect(screen.getByLabelText('Note (optional)')).toHaveValue(''); // cleared after success
  });

  it('pre-fills the remembered reviewer', () => {
    window.localStorage.setItem('fraud-console.reviewer', 'Ben');
    setup();
    expect(screen.getByLabelText('Reviewer')).toHaveValue('Ben');
  });

  it('shows server errors and keeps the note', async () => {
    const failing = vi.fn().mockRejectedValue(new Error('only flagged transactions can be reviewed'));
    const { user } = setup({}, failing);
    await user.type(screen.getByLabelText('Reviewer'), 'Ana');
    await user.type(screen.getByLabelText('Note (optional)'), 'hmm');
    await user.click(screen.getByRole('button', { name: 'Mark reviewed' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('only flagged transactions');
    expect(screen.getByLabelText('Note (optional)')).toHaveValue('hmm');
  });

  it('offers Reopen only for decided transactions and disables the current status', () => {
    setup();
    expect(screen.queryByRole('button', { name: 'Reopen' })).not.toBeInTheDocument();
    render(
      <TransactionDetail
        txn={makeTxn({ transaction_id: 'txn-2', review_status: 'REVIEWED' })}
        onReview={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const panel = screen.getByRole('article', { name: 'Details for txn-2' });
    expect(panel.querySelector('button.btn-primary')).toBeDisabled(); // Mark reviewed
    expect(screen.getByRole('button', { name: 'Reopen' })).toBeEnabled();
  });

  it('renders the review audit trail', () => {
    setup({
      review_status: 'CLEARED',
      reviews: [
        { status: 'REVIEWED', reviewer: 'Ana', note: 'Calling customer', created_at: '2026-09-30T10:05:00Z' },
        { status: 'CLEARED', reviewer: 'Ben', note: null, created_at: '2026-09-30T10:20:00Z' },
      ],
    });
    expect(screen.getByText('Ana')).toBeInTheDocument();
    expect(screen.getByText('Ben')).toBeInTheDocument();
    expect(screen.getByText('Calling customer')).toBeInTheDocument();
  });

  it('humanizes rules added via plug-ins', () => {
    setup({
      flags: [{ rule_name: 'blocked_merchant', score: 80, reason: 'blocklisted', details: {}, created_at: '2026-09-30T10:00:00Z' }],
    });
    expect(screen.getByText('Blocked merchant')).toBeInTheDocument();
  });

  it('closes', async () => {
    const { user, onClose } = setup();
    await user.click(screen.getByRole('button', { name: 'Close details' }));
    expect(onClose).toHaveBeenCalled();
  });
});
