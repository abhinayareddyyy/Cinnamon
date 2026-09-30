"""Shared fixtures and factories for the test suite."""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest

from fraud_engine.engine import RiskThresholds, RuleEngine
from fraud_engine.models import Location, Transaction
from fraud_engine.notifications import FraudAlert, Notifier
from fraud_engine.repository import SQLiteRepository
from fraud_engine.rules import default_registry
from fraud_engine.service import FraudService

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

NEW_YORK = Location(40.7128, -74.0060, "New York", "US")
BOSTON = Location(42.3601, -71.0589, "Boston", "US")
LONDON = Location(51.5074, -0.1278, "London", "GB")
TOKYO = Location(35.6762, 139.6503, "Tokyo", "JP")

_ids = itertools.count(1)


def make_txn(
    *,
    account: str = "acc-1",
    amount: float = 50.0,
    at: datetime | timedelta = timedelta(0),
    location: Location | None = None,
    currency: str = "USD",
    merchant: str = "Coffee Shop",
    txn_id: str | None = None,
) -> Transaction:
    """Build a transaction. ``at`` may be an absolute time or an offset from T0."""
    timestamp = T0 + at if isinstance(at, timedelta) else at
    return Transaction(
        transaction_id=txn_id or f"t{next(_ids)}",
        account_id=account,
        amount=amount,
        currency=currency,
        timestamp=timestamp,
        merchant=merchant,
        location=location,
    )


class RecordingNotifier(Notifier):
    """Test double that remembers alerts, optionally failing on demand."""

    name = "recording"

    def __init__(self, fail: bool = False) -> None:
        self.alerts: list[FraudAlert] = []
        self.fail = fail

    def notify(self, alert: FraudAlert) -> None:
        if self.fail:
            raise RuntimeError("simulated outage")
        self.alerts.append(alert)


@pytest.fixture
def repo(tmp_path) -> SQLiteRepository:
    return SQLiteRepository(tmp_path / "test.db")


@pytest.fixture
def engine() -> RuleEngine:
    """Engine with the three built-in rules at default settings."""
    rules = [
        default_registry.get(name)()
        for name in ("velocity", "unusual_amount", "impossible_travel")
    ]
    return RuleEngine(rules, RiskThresholds(medium=40, high=70))


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


@pytest.fixture
def service(repo, engine, notifier) -> FraudService:
    return FraudService(repo, engine, notifier)
