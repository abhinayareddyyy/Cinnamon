"""Rule plug-in contract and registry.

Every fraud rule is a subclass of :class:`Rule` decorated with
:func:`register_rule`. The engine never imports concrete rules; it only asks a
:class:`RuleRegistry` to instantiate whatever has been registered. Adding a
rule therefore means *adding a module*, never editing the engine:

    from fraud_engine.rules import Rule, RuleContext, register_rule

    @register_rule
    class NightOwlRule(Rule):
        name = "night_owl"
        description = "Flags transactions between 01:00 and 05:00 UTC."

        def evaluate(self, transaction, context):
            if 1 <= transaction.timestamp.hour < 5:
                return self.hit(30, "Transaction at unusual hour")
            return self.no_hit()

Modules outside this package can be loaded at start-up with
:func:`load_rule_plugins` (see the ``RULE_PLUGINS`` setting).
"""

from __future__ import annotations

import importlib
import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from types import ModuleType
from typing import Any, ClassVar, Iterable, Iterator, Mapping

from ..models import Transaction

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuleResult:
    """Outcome of one rule for one transaction.

    Attributes:
        rule_name: ``Rule.name`` of the rule that produced the result.
        triggered: Whether the rule considers the transaction suspicious.
        score: Rule-specific risk contribution in ``[0, 100]``.
        reason: Short, reviewer-facing explanation.
        details: JSON-serialisable evidence shown in the console.
        error: ``True`` if the rule raised and was skipped by the engine.
    """

    rule_name: str
    triggered: bool
    score: float = 0.0
    reason: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)
    error: bool = False

    def __post_init__(self) -> None:
        if math.isnan(self.score):
            raise ValueError("score must be a number")
        # Clamp so a misbehaving plug-in can never push the total above 100.
        object.__setattr__(self, "score", round(min(100.0, max(0.0, float(self.score))), 2))


@dataclass(frozen=True)
class RuleContext:
    """Read-only information a rule may use besides the transaction itself.

    ``history`` holds earlier transactions of the *same account* that happened
    at or before the transaction being evaluated, sorted newest first. The
    engine fetches it once and shares it across all rules.
    """

    history: tuple[Transaction, ...] = ()

    def since(self, start: datetime) -> list[Transaction]:
        """History entries with ``timestamp >= start``."""
        return [t for t in self.history if t.timestamp >= start]

    def latest_with_location(self) -> Transaction | None:
        """Most recent historical transaction that carries a location."""
        return next((t for t in self.history if t.location is not None), None)


class Rule(ABC):
    """Base class for all fraud rules.

    Subclasses must set the ``name`` and ``description`` class attributes and
    implement :meth:`evaluate`. Configuration is passed as constructor keyword
    arguments, which lets operators tune rules via ``RULE_CONFIG`` without
    touching code. Rules must be side-effect free and thread-safe.
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""

    #: Multiplier applied to this rule's score when scores are combined.
    weight: float = 1.0

    @abstractmethod
    def evaluate(self, transaction: Transaction, context: RuleContext) -> RuleResult:
        """Assess ``transaction`` and return a :class:`RuleResult`."""

    # -- helpers for subclasses -------------------------------------------------

    def hit(self, score: float, reason: str, **details: Any) -> RuleResult:
        """Build a *triggered* result for this rule."""
        return RuleResult(self.name, True, score, reason, details)

    def no_hit(self, reason: str = "", **details: Any) -> RuleResult:
        """Build a *not triggered* result for this rule."""
        return RuleResult(self.name, False, 0.0, reason, details)

    def parameters(self) -> dict[str, Any]:
        """Current configuration, exposed by the ``/api/rules`` endpoint."""
        return {k: v for k, v in vars(self).items() if not k.startswith("_")}


class RuleRegistry:
    """Maps rule names to rule classes and builds configured rule instances."""

    def __init__(self) -> None:
        self._rules: dict[str, type[Rule]] = {}

    def register(self, rule_cls: type[Rule]) -> type[Rule]:
        """Register ``rule_cls``; usable as a class decorator."""
        if not (isinstance(rule_cls, type) and issubclass(rule_cls, Rule)):
            raise TypeError(f"{rule_cls!r} is not a Rule subclass")
        if not rule_cls.name:
            raise ValueError(f"{rule_cls.__name__} must define a non-empty 'name'")
        existing = self._rules.get(rule_cls.name)
        if existing is not None and existing is not rule_cls:
            raise ValueError(
                f"rule name {rule_cls.name!r} already registered by {existing.__qualname__}"
            )
        self._rules[rule_cls.name] = rule_cls
        return rule_cls

    def unregister(self, name: str) -> None:
        """Remove a rule (mainly useful in tests)."""
        self._rules.pop(name, None)

    def get(self, name: str) -> type[Rule]:
        return self._rules[name]

    def names(self) -> list[str]:
        return list(self._rules)

    def __contains__(self, name: object) -> bool:
        return name in self._rules

    def __iter__(self) -> Iterator[type[Rule]]:
        return iter(self._rules.values())

    def create_rules(self, config: Mapping[str, Mapping[str, Any]] | None = None) -> list[Rule]:
        """Instantiate every registered rule.

        ``config`` maps a rule name to its constructor keyword arguments. Two
        reserved keys are handled here rather than by the rule:

        * ``enabled`` (bool, default ``True``) - skip the rule when ``False``.
        * ``weight`` (float) - override :attr:`Rule.weight`.

        Unknown rule names raise ``ValueError`` so typos fail loudly at start-up.
        """
        config = dict(config or {})
        unknown = set(config) - set(self._rules)
        if unknown:
            raise ValueError(f"configuration for unknown rule(s): {sorted(unknown)}")

        rules: list[Rule] = []
        for name, rule_cls in self._rules.items():
            options = dict(config.get(name, {}))
            if not options.pop("enabled", True):
                logger.info("Rule %s disabled by configuration", name)
                continue
            weight = options.pop("weight", None)
            rule = rule_cls(**options)
            if weight is not None:
                if float(weight) < 0:
                    raise ValueError(f"weight for {name!r} must be >= 0")
                rule.weight = float(weight)
            rules.append(rule)
        return rules


#: Process-wide registry used by the built-in rules and plug-ins.
default_registry = RuleRegistry()

#: Decorator that registers a rule class with :data:`default_registry`.
register_rule = default_registry.register


def load_rule_plugins(module_names: Iterable[str]) -> list[ModuleType]:
    """Import plug-in modules so their ``@register_rule`` decorators run."""
    modules = []
    for module_name in (m.strip() for m in module_names):
        if module_name:
            logger.info("Loading rule plug-in module %s", module_name)
            modules.append(importlib.import_module(module_name))
    return modules
