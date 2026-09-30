"""Fraud rules.

Importing this package registers the three built-in rules with
:data:`default_registry`. Additional rules live in their own modules and are
registered with :func:`register_rule`; see :mod:`fraud_engine.rules.base`.
"""

from .base import (
    Rule,
    RuleContext,
    RuleRegistry,
    RuleResult,
    default_registry,
    load_rule_plugins,
    register_rule,
)

# Imported for their registration side effect.
from .amount import UnusualAmountRule
from .geo import ImpossibleTravelRule, haversine_km
from .velocity import VelocityRule

__all__ = [
    "ImpossibleTravelRule",
    "Rule",
    "RuleContext",
    "RuleRegistry",
    "RuleResult",
    "UnusualAmountRule",
    "VelocityRule",
    "default_registry",
    "haversine_km",
    "load_rule_plugins",
    "register_rule",
]
