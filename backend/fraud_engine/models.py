"""Core domain models shared by the rule engine, persistence layer and API.

These types are deliberately framework-free (plain dataclasses) so the rule
engine can be used and unit-tested without FastAPI, SQLite or AWS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class RiskLevel(str, Enum):
    """Coarse risk bucket derived from the numeric 0-100 risk score."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ReviewStatus(str, Enum):
    """Workflow state of a *flagged* transaction in the reviewer console.

    Transactions that no rule flagged have no review status at all.
    """

    OPEN = "OPEN"  # flagged and waiting for a reviewer
    REVIEWED = "REVIEWED"  # investigated; suspicion confirmed or escalated
    CLEARED = "CLEARED"  # investigated; false positive, transaction is legitimate


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime.

    Naive datetimes are assumed to already be in UTC, which is the convention
    for every timestamp stored by this system.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Location:
    """Geographic point where a transaction took place."""

    latitude: float
    longitude: float
    city: str | None = None
    country: str | None = None

    def __post_init__(self) -> None:
        if not -90.0 <= self.latitude <= 90.0:
            raise ValueError(f"latitude out of range: {self.latitude}")
        if not -180.0 <= self.longitude <= 180.0:
            raise ValueError(f"longitude out of range: {self.longitude}")

    def label(self) -> str:
        """Human readable place name, falling back to coordinates."""
        parts = [p for p in (self.city, self.country) if p]
        if parts:
            return ", ".join(parts)
        return f"({self.latitude:.4f}, {self.longitude:.4f})"


@dataclass(frozen=True)
class Transaction:
    """A single card/account transaction submitted for risk evaluation.

    ``amount`` is a float for simplicity; a production system would carry
    ``Decimal`` or integer minor units end to end.
    """

    transaction_id: str
    account_id: str
    amount: float
    currency: str
    timestamp: datetime
    merchant: str = ""
    location: Location | None = None

    def __post_init__(self) -> None:
        if not self.transaction_id:
            raise ValueError("transaction_id is required")
        if not self.account_id:
            raise ValueError("account_id is required")
        if self.amount <= 0:
            raise ValueError(f"amount must be positive, got {self.amount}")
        # Normalise so that rules never have to worry about time zones or case.
        object.__setattr__(self, "timestamp", ensure_utc(self.timestamp))
        object.__setattr__(self, "currency", self.currency.upper())
