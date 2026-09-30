"""Transaction velocity rule: too many transactions in a short time window."""

from __future__ import annotations

from datetime import timedelta

from ..models import Transaction
from .base import Rule, RuleContext, RuleResult, register_rule


@register_rule
class VelocityRule(Rule):
    """Flags an account that makes more than ``max_transactions`` within
    ``window_minutes`` (the current transaction included).

    Card-testing and account-takeover attacks typically fire off many small
    transactions in quick succession, so a burst is a strong signal even when
    each individual amount looks harmless.

    Score: ``base_score`` for the first transaction over the limit, plus
    ``score_per_extra`` for each further one, capped at 100.
    """

    name = "velocity"
    description = "Flags bursts of transactions from one account in a short time window."

    def __init__(
        self,
        window_minutes: float = 10.0,
        max_transactions: int = 5,
        base_score: float = 50.0,
        score_per_extra: float = 10.0,
    ) -> None:
        if window_minutes <= 0:
            raise ValueError("window_minutes must be positive")
        if max_transactions < 1:
            raise ValueError("max_transactions must be at least 1")
        self.window_minutes = float(window_minutes)
        self.max_transactions = int(max_transactions)
        self.base_score = float(base_score)
        self.score_per_extra = float(score_per_extra)

    def evaluate(self, transaction: Transaction, context: RuleContext) -> RuleResult:
        window_start = transaction.timestamp - timedelta(minutes=self.window_minutes)
        count = len(context.since(window_start)) + 1  # +1 for the current transaction
        details = {
            "count": count,
            "window_minutes": self.window_minutes,
            "max_transactions": self.max_transactions,
        }
        if count <= self.max_transactions:
            return self.no_hit(
                f"{count} transaction(s) in {self.window_minutes:g} min", **details
            )

        excess = count - self.max_transactions
        score = min(100.0, self.base_score + self.score_per_extra * (excess - 1))
        return self.hit(
            score,
            f"{count} transactions in {self.window_minutes:g} min "
            f"(limit {self.max_transactions})",
            **details,
        )
