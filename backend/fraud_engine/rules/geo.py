"""Impossible travel rule: consecutive transactions too far apart to be real."""

from __future__ import annotations

import math

from ..models import Location, Transaction
from .base import Rule, RuleContext, RuleResult, register_rule

EARTH_RADIUS_KM = 6371.0088


def haversine_km(a: Location, b: Location) -> float:
    """Great-circle distance between two points in kilometres."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a.latitude, a.longitude, b.latitude, b.longitude))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, h)))


@register_rule
class ImpossibleTravelRule(Rule):
    """Flags a transaction whose location could not be reached from the
    account's previous located transaction in the elapsed time.

    The implied travel speed is ``distance / elapsed time``. Anything faster
    than ``max_speed_kmh`` (default 900 km/h, roughly a commercial jet) is
    physically implausible for a card-present purchase. Moves shorter than
    ``min_distance_km`` are ignored so GPS jitter and neighbouring cities
    never trigger the rule.

    Score: 60 just above the speed limit, rising towards 100 as the implied
    speed grows; exactly 100 for two distant locations at the same instant.
    """

    name = "impossible_travel"
    description = "Flags consecutive transactions too far apart to travel between in time."

    def __init__(self, max_speed_kmh: float = 900.0, min_distance_km: float = 100.0) -> None:
        if max_speed_kmh <= 0:
            raise ValueError("max_speed_kmh must be positive")
        if min_distance_km < 0:
            raise ValueError("min_distance_km must be >= 0")
        self.max_speed_kmh = float(max_speed_kmh)
        self.min_distance_km = float(min_distance_km)

    def evaluate(self, transaction: Transaction, context: RuleContext) -> RuleResult:
        if transaction.location is None:
            return self.no_hit("Transaction has no location")
        previous = context.latest_with_location()
        if previous is None or previous.location is None:
            return self.no_hit("No earlier located transaction")

        distance = haversine_km(previous.location, transaction.location)
        elapsed_hours = (transaction.timestamp - previous.timestamp).total_seconds() / 3600
        details = {
            "distance_km": round(distance, 1),
            "elapsed_minutes": round(elapsed_hours * 60, 1),
            "previous_transaction_id": previous.transaction_id,
            "previous_location": previous.location.label(),
            "max_speed_kmh": self.max_speed_kmh,
        }
        if distance < self.min_distance_km:
            return self.no_hit("Location consistent with previous transaction", **details)

        speed = math.inf if elapsed_hours <= 0 else distance / elapsed_hours
        # JSON has no infinity, so report None for "same instant".
        details["speed_kmh"] = None if math.isinf(speed) else round(speed, 1)
        if speed <= self.max_speed_kmh:
            return self.no_hit("Travel between locations is plausible", **details)

        score = 100.0 if math.isinf(speed) else 60.0 + 40.0 * (1 - self.max_speed_kmh / speed)
        speed_text = "simultaneously" if math.isinf(speed) else f"at ~{speed:,.0f} km/h"
        return self.hit(
            score,
            f"{distance:,.0f} km from {previous.location.label()} in "
            f"{elapsed_hours * 60:,.0f} min ({speed_text}; limit {self.max_speed_kmh:,.0f} km/h)",
            **details,
        )
