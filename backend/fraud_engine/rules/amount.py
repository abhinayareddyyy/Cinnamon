"""Unusual amount rule: amount far outside the account's normal spending."""

from __future__ import annotations

import statistics

from ..models import Transaction
from .base import Rule, RuleContext, RuleResult, register_rule


@register_rule
class UnusualAmountRule(Rule):
    """Flags amounts that are statistical outliers for the account, or that
    exceed a hard ceiling.

    Two independent checks; the higher score wins:

    1. **Statistical outlier** - once the account has at least
       ``min_history`` earlier transactions *in the same currency*, compute the
       z-score of the new amount against the last ``lookback`` of them. A
       z-score of ``z_score_threshold`` scores 40 and every further standard
       deviation adds 10 (capped at 100). The standard deviation is floored at
       ``min_relative_std`` x mean so an account with perfectly uniform
       spending (std = 0) doesn't flag a $10.50 coffee after ten $10 coffees.
    2. **Absolute ceiling** - any amount ``>= absolute_limit`` scores
       ``absolute_limit_score`` regardless of history. This covers brand new
       accounts that have no history yet. Set ``absolute_limit`` to ``None``
       to disable it.
    """

    name = "unusual_amount"
    description = "Flags amounts far above the account's usual spending or above a hard limit."

    def __init__(
        self,
        z_score_threshold: float = 3.0,
        min_history: int = 5,
        lookback: int = 50,
        absolute_limit: float | None = 10_000.0,
        absolute_limit_score: float = 70.0,
        min_relative_std: float = 0.5,
    ) -> None:
        if z_score_threshold <= 0:
            raise ValueError("z_score_threshold must be positive")
        if min_history < 2:
            raise ValueError("min_history must be at least 2")
        if lookback < min_history:
            raise ValueError("lookback must be >= min_history")
        self.z_score_threshold = float(z_score_threshold)
        self.min_history = int(min_history)
        self.lookback = int(lookback)
        self.absolute_limit = None if absolute_limit is None else float(absolute_limit)
        self.absolute_limit_score = float(absolute_limit_score)
        self.min_relative_std = float(min_relative_std)

    def evaluate(self, transaction: Transaction, context: RuleContext) -> RuleResult:
        amount = transaction.amount
        # Comparing amounts across currencies is meaningless without FX rates.
        samples = [
            t.amount for t in context.history if t.currency == transaction.currency
        ][: self.lookback]

        details: dict[str, object] = {"amount": amount, "history_size": len(samples)}
        candidates: list[tuple[float, str]] = []

        if self.absolute_limit is not None and amount >= self.absolute_limit:
            candidates.append(
                (
                    self.absolute_limit_score,
                    f"Amount {amount:,.2f} {transaction.currency} exceeds hard limit "
                    f"{self.absolute_limit:,.2f}",
                )
            )

        if len(samples) >= self.min_history:
            mean = statistics.fmean(samples)
            std = statistics.pstdev(samples)
            effective_std = max(std, mean * self.min_relative_std, 1.0)
            z_score = (amount - mean) / effective_std
            details.update(
                mean=round(mean, 2),
                std=round(std, 2),
                effective_std=round(effective_std, 2),  # std after the floor; used for z
                z_score=round(z_score, 2),
            )
            if z_score >= self.z_score_threshold:
                score = min(100.0, 40.0 + 10.0 * (z_score - self.z_score_threshold))
                candidates.append(
                    (
                        score,
                        f"Amount {amount:,.2f} is {z_score:.1f} std devs above the "
                        f"account average of {mean:,.2f}",
                    )
                )

        if not candidates:
            if len(samples) < self.min_history:
                return self.no_hit("Not enough history for a statistical check", **details)
            return self.no_hit("Amount within normal range", **details)

        score, reason = max(candidates, key=lambda c: c[0])
        return self.hit(score, reason, **details)
