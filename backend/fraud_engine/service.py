"""Application service: the ingest -> evaluate -> persist -> alert pipeline.

This is the only place that knows about all collaborators. The engine stays
pure, the repository only stores data and the notifier only delivers alerts.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from .config import Settings
from .engine import Evaluation, RiskThresholds, RuleEngine
from .models import ReviewStatus, Transaction
from .notifications import FraudAlert, Notifier, build_notifier
from .repository import SQLiteRepository, TransactionRecord
from .rules import default_registry, load_rule_plugins
from .rules.base import RuleRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessingResult:
    """What happened to a submitted transaction."""

    record: TransactionRecord
    evaluation: Evaluation
    alert_sent: bool


class FraudService:
    """Coordinates rule evaluation, persistence and high-risk alerting.

    Args:
        repository: Where transactions, flags and reviews are stored.
        engine: Configured rule engine.
        notifier: Alert channel (SES, SNS or log).
        alert_threshold: Minimum risk score that triggers an alert; defaults
            to the engine's HIGH threshold.
        history_limit: Max earlier transactions loaded per evaluation.
        console_url: Optional link to the reviewer console, used in alerts.
    """

    def __init__(
        self,
        repository: SQLiteRepository,
        engine: RuleEngine,
        notifier: Notifier,
        *,
        alert_threshold: float | None = None,
        history_limit: int = 200,
        console_url: str | None = None,
    ) -> None:
        self.repository = repository
        self.engine = engine
        self.notifier = notifier
        self.alert_threshold = (
            engine.thresholds.high if alert_threshold is None else float(alert_threshold)
        )
        self.history_limit = history_limit
        self.console_url = console_url
        # Serialises read-history -> evaluate -> insert so two concurrent
        # requests for one account cannot both miss each other (velocity rule).
        self._lock = threading.Lock()

    def process_transaction(self, transaction: Transaction) -> ProcessingResult:
        """Evaluate and store ``transaction``; alert if it is high risk.

        Raises:
            DuplicateTransactionError: the transaction id was already processed.
        """
        with self._lock:
            history = self.repository.get_account_history(
                transaction.account_id, until=transaction.timestamp, limit=self.history_limit
            )
            evaluation = self.engine.evaluate(transaction, history)
            self.repository.save_evaluation(transaction, evaluation)

        alert_sent = False
        if evaluation.flagged and evaluation.risk_score >= self.alert_threshold:
            alert_sent = self._send_alert(transaction, evaluation)

        return ProcessingResult(
            record=self.repository.get_transaction(transaction.transaction_id),
            evaluation=evaluation,
            alert_sent=alert_sent,
        )

    def review_transaction(
        self, transaction_id: str, status: ReviewStatus, reviewer: str, note: str | None = None
    ) -> TransactionRecord:
        """Record a reviewer decision (REVIEWED / CLEARED / re-OPEN)."""
        return self.repository.update_review(transaction_id, status, reviewer, note)

    def _send_alert(self, transaction: Transaction, evaluation: Evaluation) -> bool:
        """Deliver an alert. Failures are logged, never raised: the transaction
        is already stored and flagged, so reviewers still see it in the console."""
        url = f"{self.console_url.rstrip('/')}/?txn={transaction.transaction_id}" if self.console_url else None
        try:
            self.notifier.notify(FraudAlert(transaction, evaluation, url))
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send %s alert for %s",
                             self.notifier.name, transaction.transaction_id)
            return False
        self.repository.mark_notified(transaction.transaction_id)
        return True


def build_service(settings: Settings, registry: RuleRegistry = default_registry) -> FraudService:
    """Wire up a :class:`FraudService` from :class:`Settings`."""
    load_rule_plugins(settings.rule_plugins)
    engine = RuleEngine(
        registry.create_rules(settings.rule_config),
        RiskThresholds(settings.medium_risk_threshold, settings.high_risk_threshold),
    )
    notifier = build_notifier(
        settings.notifier_backend,
        region=settings.aws_region,
        ses_sender=settings.ses_sender,
        ses_recipients=settings.ses_recipients,
        sns_topic_arn=settings.sns_topic_arn,
    )
    logger.info("Rules enabled: %s; notifier: %s",
                ", ".join(r.name for r in engine.rules), notifier.name)
    return FraudService(
        SQLiteRepository(settings.db_path),
        engine,
        notifier,
        alert_threshold=settings.alert_threshold,
        console_url=settings.console_url,
    )
