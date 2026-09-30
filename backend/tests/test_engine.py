"""Tests for score aggregation, fault isolation and rule extensibility."""

from __future__ import annotations

import sys
from datetime import timedelta

import pytest

from conftest import LONDON, NEW_YORK, make_txn
from fraud_engine.engine import RiskThresholds, RuleEngine, combine_scores
from fraud_engine.models import RiskLevel
from fraud_engine.rules import Rule, RuleRegistry, RuleResult, default_registry, load_rule_plugins


class FixedRule(Rule):
    """Configurable stub rule used to drive the engine deterministically."""

    description = "test stub"

    def __init__(self, name: str, score: float | None) -> None:
        self.name = name  # instance attribute is fine for tests
        self._score = score

    def evaluate(self, transaction, context):
        if self._score is None:
            return self.no_hit()
        return self.hit(self._score, f"{self.name} fired")


class ExplodingRule(Rule):
    name = "exploding"
    description = "always raises"

    def evaluate(self, transaction, context):
        raise RuntimeError("boom")


# -- aggregation ---------------------------------------------------------------------


class TestCombineScores:
    def test_no_scores_is_zero(self):
        assert combine_scores([]) == 0

    def test_single_score_is_unchanged(self):
        assert combine_scores([(60, 1.0)]) == 60

    def test_noisy_or_reinforces_but_never_exceeds_100(self):
        assert combine_scores([(50, 1.0), (50, 1.0)]) == 75
        assert combine_scores([(100, 1.0), (80, 1.0)]) == 100
        assert combine_scores([(90, 1.0)] * 10) <= 100

    def test_weight_scales_contribution(self):
        assert combine_scores([(80, 0.5)]) == 40
        assert combine_scores([(80, 0.0)]) == 0
        assert combine_scores([(80, 2.0)]) == 100  # clamped


class TestRiskThresholds:
    @pytest.mark.parametrize(
        "score, level",
        [(0, RiskLevel.LOW), (39.99, RiskLevel.LOW), (40, RiskLevel.MEDIUM),
         (69.9, RiskLevel.MEDIUM), (70, RiskLevel.HIGH), (100, RiskLevel.HIGH)],
    )
    def test_levels(self, score, level):
        assert RiskThresholds(40, 70).level_for(score) is level

    def test_rejects_inverted_thresholds(self):
        with pytest.raises(ValueError):
            RiskThresholds(medium=80, high=50)


# -- engine --------------------------------------------------------------------------


class TestRuleEngine:
    def test_clean_transaction_is_low_and_not_flagged(self):
        engine = RuleEngine([FixedRule("a", None), FixedRule("b", None)])
        evaluation = engine.evaluate(make_txn())
        assert evaluation.risk_score == 0
        assert evaluation.risk_level is RiskLevel.LOW
        assert not evaluation.flagged
        assert len(evaluation.results) == 2

    def test_combined_rules_escalate_to_high(self):
        engine = RuleEngine([FixedRule("a", 50), FixedRule("b", 50), FixedRule("c", None)])
        evaluation = engine.evaluate(make_txn())
        assert evaluation.risk_score == 75
        assert evaluation.risk_level is RiskLevel.HIGH
        assert [r.rule_name for r in evaluation.triggered] == ["a", "b"]

    def test_rule_weight_is_applied(self):
        rule = FixedRule("a", 80)
        rule.weight = 0.5
        assert RuleEngine([rule]).evaluate(make_txn()).risk_score == 40

    def test_failing_rule_is_isolated(self):
        engine = RuleEngine([ExplodingRule(), FixedRule("ok", 45)])
        evaluation = engine.evaluate(make_txn())
        broken = evaluation.results[0]
        assert broken.error and not broken.triggered
        assert "boom" in broken.reason
        assert evaluation.risk_score == 45  # remaining rules still counted

    def test_rule_returning_wrong_type_is_treated_as_error(self):
        class BadReturn(Rule):
            name = "bad_return"

            def evaluate(self, transaction, context):
                return 42

        evaluation = RuleEngine([BadReturn()]).evaluate(make_txn())
        assert evaluation.results[0].error

    def test_duplicate_rule_names_are_rejected(self):
        with pytest.raises(ValueError, match="duplicate"):
            RuleEngine([FixedRule("x", 1), FixedRule("x", 2)])

    def test_history_is_filtered_and_sorted_before_reaching_rules(self):
        seen = {}

        class Spy(Rule):
            name = "spy"

            def evaluate(self, transaction, context):
                seen["history"] = context.history
                return self.no_hit()

        current = make_txn(txn_id="current", at=timedelta(0))
        older = make_txn(at=timedelta(minutes=-10))
        newer = make_txn(at=timedelta(minutes=-1))
        future = make_txn(at=timedelta(minutes=5))
        other_account = make_txn(account="someone-else", at=timedelta(minutes=-2))
        RuleEngine([Spy()]).evaluate(current, [older, future, current, other_account, newer])
        assert seen["history"] == (newer, older)

    def test_scores_are_clamped_to_0_100(self):
        assert RuleResult("x", True, 250).score == 100
        assert RuleResult("x", True, -5).score == 0

    def test_end_to_end_with_builtin_rules(self, engine):
        history = [make_txn(at=timedelta(minutes=-30), location=NEW_YORK)]
        evaluation = engine.evaluate(make_txn(location=LONDON), history)
        assert [r.rule_name for r in evaluation.triggered] == ["impossible_travel"]
        assert evaluation.risk_level is RiskLevel.HIGH


# -- extensibility -------------------------------------------------------------------


class TestExtensibility:
    """New rules plug in via the registry; the engine code is never touched."""

    def test_builtin_rules_are_registered(self):
        for name in ("velocity", "unusual_amount", "impossible_travel"):
            assert name in default_registry

    def test_new_rule_is_picked_up_without_engine_changes(self):
        registry = RuleRegistry()
        for name in ("velocity", "unusual_amount", "impossible_travel"):
            registry.register(default_registry.get(name))

        @registry.register
        class RoundAmountRule(Rule):
            name = "round_amount"
            description = "Flags suspiciously round amounts"

            def evaluate(self, transaction, context):
                if transaction.amount >= 1000 and transaction.amount % 1000 == 0:
                    return self.hit(35, "Round amount")
                return self.no_hit()

        engine = RuleEngine(registry.create_rules())
        assert [r.name for r in engine.rules][-1] == "round_amount"
        evaluation = engine.evaluate(make_txn(amount=5000))
        assert [r.rule_name for r in evaluation.triggered] == ["round_amount"]

    def test_create_rules_applies_config_enabled_and_weight(self):
        registry = RuleRegistry()
        registry.register(default_registry.get("velocity"))
        registry.register(default_registry.get("unusual_amount"))
        rules = registry.create_rules(
            {
                "velocity": {"max_transactions": 2, "weight": 0.5},
                "unusual_amount": {"enabled": False},
            }
        )
        assert [r.name for r in rules] == ["velocity"]
        assert rules[0].max_transactions == 2
        assert rules[0].weight == 0.5

    def test_config_for_unknown_rule_fails_fast(self):
        registry = RuleRegistry()
        registry.register(default_registry.get("velocity"))
        with pytest.raises(ValueError, match="unknown rule"):
            registry.create_rules({"velocitty": {}})

    def test_registry_rejects_invalid_and_conflicting_rules(self):
        registry = RuleRegistry()
        with pytest.raises(TypeError):
            registry.register(object)  # type: ignore[arg-type]

        class Nameless(Rule):
            def evaluate(self, transaction, context):
                return self.no_hit()

        with pytest.raises(ValueError, match="non-empty"):
            registry.register(Nameless)

        class A(Rule):
            name = "same"

            def evaluate(self, transaction, context):
                return self.no_hit()

        class B(A):
            pass

        registry.register(A)
        registry.register(A)  # re-registering the same class is a no-op
        with pytest.raises(ValueError, match="already registered"):
            registry.register(B)

    def test_plugin_module_loaded_by_name(self):
        """``plugins.blocked_merchant`` lives outside the engine package."""
        sys.modules.pop("plugins.blocked_merchant", None)
        default_registry.unregister("blocked_merchant")
        try:
            load_rule_plugins(["plugins.blocked_merchant", " "])
            assert "blocked_merchant" in default_registry
            rules = default_registry.create_rules(
                {"blocked_merchant": {"blocked_merchants": ["Shady Store"], "score": 90}}
            )
            engine = RuleEngine(rules)
            evaluation = engine.evaluate(make_txn(merchant="shady store"))
            assert [r.rule_name for r in evaluation.triggered] == ["blocked_merchant"]
            assert evaluation.risk_score == 90
        finally:
            default_registry.unregister("blocked_merchant")
            sys.modules.pop("plugins.blocked_merchant", None)
