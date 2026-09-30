"""Unit tests for the three built-in rules, exercised in isolation."""

from __future__ import annotations

import math
from datetime import timedelta

import pytest

from conftest import BOSTON, LONDON, NEW_YORK, TOKYO, make_txn
from fraud_engine.rules import (
    ImpossibleTravelRule,
    RuleContext,
    UnusualAmountRule,
    VelocityRule,
    haversine_km,
)


def ctx(*history):
    """Context with history sorted newest first, as the engine provides it."""
    return RuleContext(tuple(sorted(history, key=lambda t: t.timestamp, reverse=True)))


# -- velocity ------------------------------------------------------------------------


class TestVelocityRule:
    def test_under_limit_does_not_trigger(self):
        rule = VelocityRule(window_minutes=10, max_transactions=5)
        history = [make_txn(at=timedelta(minutes=-i)) for i in range(1, 5)]  # 4 earlier
        result = rule.evaluate(make_txn(), ctx(*history))
        assert not result.triggered
        assert result.details["count"] == 5

    def test_first_transaction_over_limit_scores_base(self):
        rule = VelocityRule(window_minutes=10, max_transactions=5)
        history = [make_txn(at=timedelta(minutes=-i)) for i in range(1, 6)]  # 5 earlier
        result = rule.evaluate(make_txn(), ctx(*history))
        assert result.triggered
        assert result.score == 50
        assert "6 transactions in 10 min" in result.reason

    def test_score_grows_with_each_extra_transaction_and_caps_at_100(self):
        rule = VelocityRule(window_minutes=10, max_transactions=5)
        history = [make_txn(at=timedelta(seconds=-10 * i)) for i in range(1, 8)]  # 7 earlier
        assert rule.evaluate(make_txn(), ctx(*history)).score == 70  # 8 total, 3 over
        history = [make_txn(at=timedelta(seconds=-5 * i)) for i in range(1, 40)]
        assert rule.evaluate(make_txn(), ctx(*history)).score == 100

    def test_transactions_outside_window_are_ignored(self):
        rule = VelocityRule(window_minutes=10, max_transactions=2)
        history = [make_txn(at=timedelta(minutes=-11)), make_txn(at=timedelta(minutes=-30))]
        assert not rule.evaluate(make_txn(), ctx(*history)).triggered

    def test_window_boundary_is_inclusive(self):
        rule = VelocityRule(window_minutes=10, max_transactions=1)
        history = [make_txn(at=timedelta(minutes=-10))]
        assert rule.evaluate(make_txn(), ctx(*history)).triggered

    @pytest.mark.parametrize("kwargs", [{"window_minutes": 0}, {"max_transactions": 0}])
    def test_rejects_invalid_configuration(self, kwargs):
        with pytest.raises(ValueError):
            VelocityRule(**kwargs)


# -- unusual amount ----------------------------------------------------------------


class TestUnusualAmountRule:
    @staticmethod
    def history(amounts, currency="USD"):
        return [
            make_txn(amount=a, at=timedelta(days=-(i + 1)), currency=currency)
            for i, a in enumerate(amounts)
        ]

    def test_normal_amount_does_not_trigger(self):
        rule = UnusualAmountRule()
        hist = self.history([40, 60, 50, 45, 55, 70, 30])
        result = rule.evaluate(make_txn(amount=65), ctx(*hist))
        assert not result.triggered
        assert result.reason == "Amount within normal range"

    def test_statistical_outlier_triggers_with_z_score_evidence(self):
        rule = UnusualAmountRule()
        hist = self.history([40, 60, 50, 45, 55, 70, 30])  # mean 50
        result = rule.evaluate(make_txn(amount=500), ctx(*hist))
        assert result.triggered
        assert result.details["mean"] == 50
        assert result.details["z_score"] > 3
        assert "std devs above" in result.reason

    def test_score_scales_with_z_score(self):
        # mean 100, std 0 -> effective std floored at 0.5 * mean = 50
        rule = UnusualAmountRule(z_score_threshold=3, min_relative_std=0.5, absolute_limit=None)
        hist = self.history([100] * 10)
        result = rule.evaluate(make_txn(amount=250), ctx(*hist))
        assert result.score == 40  # z = 3
        assert result.details["std"] == 0 and result.details["effective_std"] == 50
        assert rule.evaluate(make_txn(amount=400), ctx(*hist)).score == 70  # z = 6
        assert rule.evaluate(make_txn(amount=5000), ctx(*hist)).score == 100  # capped

    def test_uniform_history_does_not_flag_tiny_deviation(self):
        """With std == 0 a naive z-score would be infinite for any change."""
        rule = UnusualAmountRule()
        hist = self.history([10.0] * 10)
        assert not rule.evaluate(make_txn(amount=10.5), ctx(*hist)).triggered

    def test_needs_min_history_for_statistical_check(self):
        rule = UnusualAmountRule(min_history=5)
        hist = self.history([10, 12, 11])
        result = rule.evaluate(make_txn(amount=5000), ctx(*hist))
        assert not result.triggered
        assert "Not enough history" in result.reason

    def test_absolute_limit_applies_even_without_history(self):
        rule = UnusualAmountRule(absolute_limit=10_000, absolute_limit_score=70)
        result = rule.evaluate(make_txn(amount=10_000), ctx())
        assert result.triggered
        assert result.score == 70
        assert "hard limit" in result.reason

    def test_absolute_limit_can_be_disabled(self):
        rule = UnusualAmountRule(absolute_limit=None)
        assert not rule.evaluate(make_txn(amount=1_000_000), ctx()).triggered

    def test_higher_of_both_checks_wins(self):
        rule = UnusualAmountRule(absolute_limit=10_000, absolute_limit_score=70)
        hist = self.history([20, 25, 30, 22, 28])
        result = rule.evaluate(make_txn(amount=12_000), ctx(*hist))
        assert result.score == 100  # z-score check beats the 70 from the hard limit

    def test_only_same_currency_history_is_compared(self):
        rule = UnusualAmountRule(absolute_limit=None)
        hist = self.history([5000, 6000, 5500, 4800, 5200], currency="JPY")
        result = rule.evaluate(make_txn(amount=300, currency="USD"), ctx(*hist))
        assert not result.triggered
        assert result.details["history_size"] == 0

    def test_lookback_limits_samples(self):
        rule = UnusualAmountRule(lookback=5, min_history=5, absolute_limit=None)
        recent = self.history([1000] * 5)
        old = [make_txn(amount=10, at=timedelta(days=-100 - i)) for i in range(50)]
        result = rule.evaluate(make_txn(amount=1100), ctx(*recent, *old))
        assert result.details["history_size"] == 5
        assert not result.triggered

    def test_rejects_invalid_configuration(self):
        with pytest.raises(ValueError):
            UnusualAmountRule(min_history=10, lookback=5)


# -- impossible travel -------------------------------------------------------------


class TestImpossibleTravelRule:
    def test_haversine_known_distance(self):
        # New York -> London is ~5,570 km
        assert haversine_km(NEW_YORK, LONDON) == pytest.approx(5570, rel=0.01)
        assert haversine_km(NEW_YORK, NEW_YORK) == 0

    def test_new_york_to_london_in_one_hour_triggers(self):
        rule = ImpossibleTravelRule()
        prev = make_txn(at=timedelta(hours=-1), location=NEW_YORK)
        result = rule.evaluate(make_txn(location=LONDON), ctx(prev))
        assert result.triggered
        assert 60 < result.score < 100
        assert result.details["previous_transaction_id"] == prev.transaction_id
        assert result.details["speed_kmh"] == pytest.approx(5570, rel=0.01)
        assert "New York" in result.reason

    def test_plausible_trip_does_not_trigger(self):
        rule = ImpossibleTravelRule()
        prev = make_txn(at=timedelta(hours=-4), location=NEW_YORK)  # ~300 km in 4 h
        assert not rule.evaluate(make_txn(location=BOSTON), ctx(prev)).triggered

    def test_long_flight_with_enough_time_does_not_trigger(self):
        rule = ImpossibleTravelRule()
        prev = make_txn(at=timedelta(hours=-14), location=NEW_YORK)  # ~10,800 km in 14 h
        assert not rule.evaluate(make_txn(location=TOKYO), ctx(prev)).triggered

    def test_simultaneous_distant_transactions_score_100(self):
        rule = ImpossibleTravelRule()
        prev = make_txn(at=timedelta(0), location=NEW_YORK)
        result = rule.evaluate(make_txn(at=timedelta(0), location=TOKYO), ctx(prev))
        assert result.triggered
        assert result.score == 100
        assert result.details["speed_kmh"] is None  # infinity is not JSON friendly

    def test_short_hops_below_min_distance_are_ignored(self):
        rule = ImpossibleTravelRule(min_distance_km=500)
        prev = make_txn(at=timedelta(minutes=-1), location=NEW_YORK)
        assert not rule.evaluate(make_txn(location=BOSTON), ctx(prev)).triggered

    def test_uses_latest_located_transaction(self):
        rule = ImpossibleTravelRule()
        located = make_txn(at=timedelta(hours=-1), location=NEW_YORK)
        unlocated = make_txn(at=timedelta(minutes=-5), location=None)  # e.g. online purchase
        result = rule.evaluate(make_txn(location=LONDON), ctx(located, unlocated))
        assert result.triggered

    @pytest.mark.parametrize("has_current, has_previous", [(False, True), (True, False)])
    def test_missing_locations_do_not_trigger(self, has_current, has_previous):
        rule = ImpossibleTravelRule()
        history = [make_txn(at=timedelta(hours=-1), location=NEW_YORK)] if has_previous else []
        txn = make_txn(location=LONDON if has_current else None)
        assert not rule.evaluate(txn, ctx(*history)).triggered

    def test_score_approaches_100_as_speed_grows(self):
        rule = ImpossibleTravelRule()
        prev_slow = make_txn(at=timedelta(hours=-5), location=NEW_YORK)  # ~1,100 km/h
        prev_fast = make_txn(at=timedelta(minutes=-5), location=NEW_YORK)
        slow = rule.evaluate(make_txn(location=LONDON), ctx(prev_slow)).score
        fast = rule.evaluate(make_txn(location=LONDON), ctx(prev_fast)).score
        assert 60 < slow < fast < 100
        assert not math.isnan(fast)
