"""Tests for SQLite persistence of transactions, flags and reviews."""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import MAHARASHTRA, T0, make_txn
from fraud_engine.engine import Evaluation
from fraud_engine.models import ReviewStatus, RiskLevel
from fraud_engine.repository import (
    DuplicateTransactionError,
    InvalidReviewError,
    SQLiteRepository,
    TransactionNotFoundError,
)
from fraud_engine.rules import RuleResult


def evaluation_for(txn, *hits, score=None, level=None):
    """Build an Evaluation with the given ``(rule, score)`` hits."""
    results = tuple(RuleResult(name, True, s, f"{name} fired", {"k": 1}) for name, s in hits)
    results += (RuleResult("quiet_rule", False),)
    score = score if score is not None else max((s for _, s in hits), default=0)
    level = level or (RiskLevel.HIGH if score >= 70 else RiskLevel.MEDIUM if score >= 40 else RiskLevel.LOW)
    return Evaluation(txn.transaction_id, score, level, results)


def store(repo, txn, *hits, **kw):
    repo.save_evaluation(txn, evaluation_for(txn, *hits, **kw))
    return txn


class TestSaveAndLoad:
    def test_round_trip_preserves_transaction_and_verdict(self, repo):
        txn = store(repo, make_txn(amount=123.45, location=MAHARASHTRA, merchant="Shop"),
                    ("velocity", 55), ("unusual_amount", 40), score=73)
        rec = repo.get_transaction(txn.transaction_id)
        assert rec.transaction == txn  # timestamps, location, currency all survive
        assert rec.risk_score == 73
        assert rec.risk_level is RiskLevel.HIGH
        assert rec.flagged and rec.review_status is ReviewStatus.OPEN
        # Only triggered rules become fraud flags, highest score first.
        assert [(f.rule_name, f.score) for f in rec.flags] == [("velocity", 55), ("unusual_amount", 40)]
        assert rec.flags[0].details == {"k": 1}
        assert rec.notified_at is None and rec.reviews == ()

    def test_unflagged_transaction_has_no_review_status(self, repo):
        txn = store(repo, make_txn())
        rec = repo.get_transaction(txn.transaction_id)
        assert not rec.flagged
        assert rec.review_status is None
        assert rec.flags == ()

    def test_duplicate_id_is_rejected_atomically(self, repo):
        txn = store(repo, make_txn(txn_id="dup"), ("velocity", 50))
        with pytest.raises(DuplicateTransactionError):
            store(repo, make_txn(txn_id="dup"), ("velocity", 90))
        assert len(repo.get_transaction("dup").flags) == 1  # no orphan flags written

    def test_mismatched_evaluation_is_rejected(self, repo):
        a, b = make_txn(), make_txn()
        with pytest.raises(ValueError):
            repo.save_evaluation(a, evaluation_for(b))

    def test_unknown_transaction_raises(self, repo):
        with pytest.raises(TransactionNotFoundError):
            repo.get_transaction("nope")

    def test_memory_database_is_refused(self):
        with pytest.raises(ValueError):
            SQLiteRepository(":memory:")

    def test_data_survives_reopening(self, tmp_path):
        path = tmp_path / "persist.db"
        txn = store(SQLiteRepository(path), make_txn(), ("velocity", 50))
        assert SQLiteRepository(path).get_transaction(txn.transaction_id).flagged


class TestHistory:
    def test_returns_same_account_up_to_time_newest_first(self, repo):
        old = store(repo, make_txn(at=timedelta(hours=-2)))
        new = store(repo, make_txn(at=timedelta(hours=-1)))
        store(repo, make_txn(at=timedelta(hours=1)))  # in the future
        store(repo, make_txn(account="other", at=timedelta(hours=-1)))
        history = repo.get_account_history("acc-1", until=T0)
        assert [t.transaction_id for t in history] == [new.transaction_id, old.transaction_id]

    def test_limit(self, repo):
        for i in range(5):
            store(repo, make_txn(at=timedelta(minutes=-i)))
        assert len(repo.get_account_history("acc-1", until=T0, limit=3)) == 3


class TestListing:
    @pytest.fixture
    def populated(self, repo):
        store(repo, make_txn(at=timedelta(minutes=1)))  # clean
        store(repo, make_txn(at=timedelta(minutes=2)), ("velocity", 50))  # MEDIUM
        store(repo, make_txn(at=timedelta(minutes=3)), ("impossible_travel", 90))  # HIGH
        high_b = store(repo, make_txn(account="acc-2", at=timedelta(minutes=4)), ("velocity", 80))
        repo.update_review(high_b.transaction_id, ReviewStatus.CLEARED, "ana")
        return repo

    def test_flagged_only_by_default_newest_first(self, populated):
        items, total = populated.list_transactions()
        assert total == 3
        assert [r.transaction.timestamp for r in items] == sorted(
            (r.transaction.timestamp for r in items), reverse=True)
        assert all(r.flags for r in items)

    def test_include_unflagged(self, populated):
        assert populated.list_transactions(flagged_only=False)[1] == 4

    def test_filters(self, populated):
        assert populated.list_transactions(review_status=ReviewStatus.OPEN)[1] == 2
        assert populated.list_transactions(review_status=ReviewStatus.CLEARED)[1] == 1
        assert populated.list_transactions(risk_level=RiskLevel.HIGH)[1] == 2
        assert populated.list_transactions(account_id="acc-2")[1] == 1

    def test_pagination(self, populated):
        page1, total = populated.list_transactions(limit=2)
        page2, _ = populated.list_transactions(limit=2, offset=2)
        assert total == 3 and len(page1) == 2 and len(page2) == 1
        assert {r.transaction.transaction_id for r in page1}.isdisjoint(
            {r.transaction.transaction_id for r in page2})

    def test_stats(self, populated):
        populated.mark_notified(populated.list_transactions()[0][0].transaction.transaction_id)
        stats = populated.stats()
        assert stats == {
            "total": 4, "flagged": 3, "open": 2, "reviewed": 0, "cleared": 1,
            "high_risk": 2, "high_risk_open": 1, "alerts_sent": 1,
            "flags_by_rule": {"velocity": 2, "impossible_travel": 1},
        }

    def test_stats_on_empty_database(self, repo):
        assert repo.stats()["total"] == 0


class TestReview:
    def test_review_updates_state_and_appends_audit_trail(self, repo):
        txn = store(repo, make_txn(), ("velocity", 60))
        repo.update_review(txn.transaction_id, ReviewStatus.REVIEWED, " ana ", "called customer")
        rec = repo.update_review(txn.transaction_id, ReviewStatus.CLEARED, "ben", "")
        assert rec.review_status is ReviewStatus.CLEARED
        assert rec.reviewed_by == "ben" and rec.review_note is None
        assert rec.reviewed_at is not None
        assert [(e.status, e.reviewer, e.note) for e in rec.reviews] == [
            (ReviewStatus.REVIEWED, "ana", "called customer"),
            (ReviewStatus.CLEARED, "ben", None),
        ]

    def test_can_reopen(self, repo):
        txn = store(repo, make_txn(), ("velocity", 60))
        repo.update_review(txn.transaction_id, ReviewStatus.CLEARED, "ana")
        rec = repo.update_review(txn.transaction_id, ReviewStatus.OPEN, "lead", "second look")
        assert rec.review_status is ReviewStatus.OPEN

    def test_unflagged_transactions_cannot_be_reviewed(self, repo):
        txn = store(repo, make_txn())
        with pytest.raises(InvalidReviewError):
            repo.update_review(txn.transaction_id, ReviewStatus.CLEARED, "ana")

    def test_reviewer_is_required(self, repo):
        txn = store(repo, make_txn(), ("velocity", 60))
        with pytest.raises(InvalidReviewError):
            repo.update_review(txn.transaction_id, ReviewStatus.CLEARED, "   ")

    def test_unknown_transaction(self, repo):
        with pytest.raises(TransactionNotFoundError):
            repo.update_review("missing", ReviewStatus.CLEARED, "ana")
