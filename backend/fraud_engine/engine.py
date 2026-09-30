"""The rule engine: runs every configured rule and combines their scores.

The engine is pure: it receives the transaction plus the account history and
returns an :class:`Evaluation`. It knows nothing about storage, HTTP or AWS,
and nothing about *which* rules exist - it simply iterates over the
:class:`~fraud_engine.rules.Rule` instances it was given.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Sequence

from .models import RiskLevel, Transaction
from .rules.base import Rule, RuleContext, RuleResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RiskThresholds:
    """Score boundaries (inclusive) for the MEDIUM and HIGH risk levels."""

    medium: float = 40.0
    high: float = 70.0

    def __post_init__(self) -> None:
        if not 0 <= self.medium <= self.high <= 100:
            raise ValueError("thresholds must satisfy 0 <= medium <= high <= 100")

    def level_for(self, score: float) -> RiskLevel:
        if score >= self.high:
            return RiskLevel.HIGH
        if score >= self.medium:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW


@dataclass(frozen=True)
class Evaluation:
    """Aggregated verdict for one transaction."""

    transaction_id: str
    risk_score: float
    risk_level: RiskLevel
    results: tuple[RuleResult, ...]

    @property
    def triggered(self) -> tuple[RuleResult, ...]:
        """Only the results of rules that fired."""
        return tuple(r for r in self.results if r.triggered)

    @property
    def flagged(self) -> bool:
        """A transaction is flagged for review when at least one rule fired."""
        return bool(self.triggered)


def combine_scores(weighted_scores: Iterable[tuple[float, float]]) -> float:
    """Combine ``(score, weight)`` pairs into one 0-100 risk score.

    Each weighted score is treated as an independent probability of fraud and
    combined with a "noisy OR": ``1 - prod(1 - p_i)``. Unlike a plain sum it
    never exceeds 100, and unlike ``max`` several moderate signals reinforce
    each other - e.g. two rules scoring 50 combine to 75.
    """
    remaining = 1.0
    for score, weight in weighted_scores:
        p = min(1.0, max(0.0, score * weight / 100.0))
        remaining *= 1.0 - p
    return round((1.0 - remaining) * 100.0, 2)


class RuleEngine:
    """Evaluates transactions against a set of pluggable rules.

    Args:
        rules: Rule instances to run, typically from
            :meth:`RuleRegistry.create_rules`. Order only affects the order of
            results, not the score.
        thresholds: Boundaries used to map the score to a :class:`RiskLevel`.
    """

    def __init__(self, rules: Iterable[Rule], thresholds: RiskThresholds | None = None) -> None:
        self._rules = tuple(rules)
        names = [r.name for r in self._rules]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"duplicate rule names: {sorted(duplicates)}")
        self.thresholds = thresholds or RiskThresholds()

    @property
    def rules(self) -> tuple[Rule, ...]:
        return self._rules

    def evaluate(self, transaction: Transaction, history: Sequence[Transaction] = ()) -> Evaluation:
        """Run every rule against ``transaction``.

        ``history`` should contain earlier transactions of the same account.
        It is defensively filtered (other accounts, later timestamps and the
        transaction itself are dropped) and sorted newest first before being
        handed to the rules.

        A rule that raises is logged and recorded as a non-triggered result with
        ``error=True``; one faulty plug-in must never block scoring.
        """
        relevant = sorted(
            (
                t
                for t in history
                if t.account_id == transaction.account_id
                and t.transaction_id != transaction.transaction_id
                and t.timestamp <= transaction.timestamp
            ),
            key=lambda t: t.timestamp,
            reverse=True,
        )
        context = RuleContext(history=tuple(relevant))

        results: list[RuleResult] = []
        weighted: list[tuple[float, float]] = []
        for rule in self._rules:
            try:
                result = rule.evaluate(transaction, context)
                if not isinstance(result, RuleResult):
                    raise TypeError(f"expected RuleResult, got {type(result).__name__}")
            except Exception as exc:  # noqa: BLE001 - isolate faulty rules
                logger.exception("Rule %s failed on %s", rule.name, transaction.transaction_id)
                result = RuleResult(rule.name, False, 0.0, f"Rule error: {exc}", error=True)
            results.append(result)
            if result.triggered:
                weighted.append((result.score, rule.weight))

        score = combine_scores(weighted)
        return Evaluation(
            transaction_id=transaction.transaction_id,
            risk_score=score,
            risk_level=self.thresholds.level_for(score),
            results=tuple(results),
        )
