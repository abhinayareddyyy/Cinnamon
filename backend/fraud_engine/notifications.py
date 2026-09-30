"""High-risk alert delivery via AWS SES (email) or AWS SNS (topic).

The service calls :meth:`Notifier.notify` whenever a transaction's risk score
reaches the alert threshold. Backends:

* ``log`` - writes the alert to the application log (default; no AWS needed)
* ``ses`` - sends a plain-text email through Amazon SES ``SendEmail``
* ``sns`` - publishes to an Amazon SNS topic (fan out to email, SMS, Lambda...)

AWS credentials and region are resolved by boto3's standard chain
(environment variables, ``~/.aws``, instance/task role). ``boto3`` is only
imported when an AWS backend is actually used.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Sequence

from .engine import Evaluation
from .models import Transaction

logger = logging.getLogger(__name__)

SNS_SUBJECT_MAX = 100  # hard limit imposed by SNS Publish


class NotificationError(RuntimeError):
    """Delivery of an alert failed."""


@dataclass(frozen=True)
class FraudAlert:
    """Everything a notifier needs to describe a high-risk transaction."""

    transaction: Transaction
    evaluation: Evaluation
    console_url: str | None = None

    @property
    def subject(self) -> str:
        e = self.evaluation
        return (
            f"[Fraud alert: {e.risk_level.value}] Transaction {self.transaction.transaction_id} "
            f"scored {e.risk_score:.0f}/100"
        )

    def body(self) -> str:
        t, e = self.transaction, self.evaluation
        lines = [
            "A transaction crossed the high-risk threshold and needs review.",
            "",
            f"Transaction : {t.transaction_id}",
            f"Account     : {t.account_id}",
            f"Amount      : {t.amount:,.2f} {t.currency}",
            f"Merchant    : {t.merchant or '-'}",
            f"Time (UTC)  : {t.timestamp.isoformat()}",
            f"Location    : {t.location.label() if t.location else '-'}",
            f"Risk score  : {e.risk_score:.1f} ({e.risk_level.value})",
            "",
            "Triggered rules:",
        ]
        lines += [f"  - {r.rule_name} ({r.score:.0f}): {r.reason}" for r in e.triggered]
        if self.console_url:
            lines += ["", f"Review it in the console: {self.console_url}"]
        return "\n".join(lines)


class Notifier(ABC):
    """Delivers :class:`FraudAlert` objects. Implementations raise
    :class:`NotificationError` on failure; the caller decides how to react."""

    name: str = "notifier"

    @abstractmethod
    def notify(self, alert: FraudAlert) -> None: ...


class LoggingNotifier(Notifier):
    """Writes alerts to the log. Useful for local development and tests."""

    name = "log"

    def notify(self, alert: FraudAlert) -> None:
        logger.warning("%s\n%s", alert.subject, alert.body())


def _boto3_client(service: str, region: str | None) -> Any:
    import boto3  # imported lazily so the 'log' backend has no AWS dependency

    return boto3.client(service, region_name=region) if region else boto3.client(service)


class SesNotifier(Notifier):
    """Sends alerts as email through Amazon SES.

    ``sender`` must be an SES-verified identity; while the account is in the
    SES sandbox, recipients must be verified too.
    """

    name = "ses"

    def __init__(
        self,
        sender: str,
        recipients: Sequence[str],
        *,
        client: Any = None,
        region: str | None = None,
    ) -> None:
        if not sender:
            raise ValueError("SES sender address is required")
        if not recipients:
            raise ValueError("at least one SES recipient is required")
        self.sender = sender
        self.recipients = list(recipients)
        self._client = client or _boto3_client("ses", region)

    def notify(self, alert: FraudAlert) -> None:
        try:
            response = self._client.send_email(
                Source=self.sender,
                Destination={"ToAddresses": self.recipients},
                Message={
                    "Subject": {"Data": alert.subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": alert.body(), "Charset": "UTF-8"}},
                },
            )
        except Exception as exc:  # botocore ClientError / EndpointConnectionError / ...
            raise NotificationError(f"SES send_email failed: {exc}") from exc
        logger.info("SES alert sent for %s (MessageId=%s)",
                    alert.transaction.transaction_id, response.get("MessageId"))


class SnsNotifier(Notifier):
    """Publishes alerts to an Amazon SNS topic.

    The risk level and account are added as message attributes so subscribers
    can use SNS subscription filter policies (e.g. page on-call only for HIGH).
    """

    name = "sns"

    def __init__(self, topic_arn: str, *, client: Any = None, region: str | None = None) -> None:
        if not topic_arn:
            raise ValueError("SNS topic ARN is required")
        self.topic_arn = topic_arn
        self._client = client or _boto3_client("sns", region)

    def notify(self, alert: FraudAlert) -> None:
        try:
            response = self._client.publish(
                TopicArn=self.topic_arn,
                Subject=alert.subject[:SNS_SUBJECT_MAX],
                Message=alert.body(),
                MessageAttributes={
                    "risk_level": {
                        "DataType": "String",
                        "StringValue": alert.evaluation.risk_level.value,
                    },
                    "account_id": {
                        "DataType": "String",
                        "StringValue": alert.transaction.account_id,
                    },
                },
            )
        except Exception as exc:
            raise NotificationError(f"SNS publish failed: {exc}") from exc
        logger.info("SNS alert published for %s (MessageId=%s)",
                    alert.transaction.transaction_id, response.get("MessageId"))


def build_notifier(
    backend: str,
    *,
    region: str | None = None,
    ses_sender: str | None = None,
    ses_recipients: Sequence[str] = (),
    sns_topic_arn: str | None = None,
) -> Notifier:
    """Create the notifier selected by ``backend`` (``log``, ``ses`` or ``sns``)."""
    backend = (backend or "log").lower()
    if backend == "log":
        return LoggingNotifier()
    if backend == "ses":
        return SesNotifier(ses_sender or "", list(ses_recipients), region=region)
    if backend == "sns":
        return SnsNotifier(sns_topic_arn or "", region=region)
    raise ValueError(f"unknown notifier backend {backend!r}; expected log, ses or sns")
