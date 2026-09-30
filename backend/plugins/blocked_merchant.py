"""Example plug-in rule, living *outside* the core ``fraud_engine`` package.

It demonstrates that new rules need no change to the engine. Enable it with::

    RULE_PLUGINS=plugins.blocked_merchant

and optionally tune it::

    RULE_CONFIG='{"blocked_merchant": {"blocked_merchants": ["Shady Store"], "score": 90}}'
"""

from __future__ import annotations

from typing import Iterable

from fraud_engine.models import Transaction
from fraud_engine.rules import Rule, RuleContext, RuleResult, register_rule


@register_rule
class BlockedMerchantRule(Rule):
    """Flags payments to merchants on a blocklist (case-insensitive)."""

    name = "blocked_merchant"
    description = "Flags payments to merchants on a configurable blocklist."

    def __init__(
        self,
        blocked_merchants: Iterable[str] = ("CryptoMixer", "Anon Gift Cards"),
        score: float = 80.0,
    ) -> None:
        self.blocked_merchants = sorted({m.strip() for m in blocked_merchants if m.strip()})
        self.score = float(score)
        self._lookup = {m.casefold() for m in self.blocked_merchants}

    def evaluate(self, transaction: Transaction, context: RuleContext) -> RuleResult:
        if transaction.merchant.strip().casefold() in self._lookup:
            return self.hit(self.score, f"Merchant '{transaction.merchant}' is blocklisted")
        return self.no_hit()
