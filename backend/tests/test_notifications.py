"""Tests for SES / SNS alert delivery.

botocore's ``Stubber`` intercepts the real boto3 client, so these tests verify
the exact AWS API request (operation, parameters, validation against the
service model) without network access or credentials.
"""

from __future__ import annotations

import boto3
import pytest
from botocore.stub import Stubber

from conftest import LONDON, make_txn
from fraud_engine.engine import Evaluation
from fraud_engine.models import RiskLevel
from fraud_engine.notifications import (
    FraudAlert,
    LoggingNotifier,
    NotificationError,
    SesNotifier,
    SnsNotifier,
    build_notifier,
)
from fraud_engine.rules import RuleResult


@pytest.fixture
def alert() -> FraudAlert:
    txn = make_txn(txn_id="txn-42", account="acc-9", amount=2500, location=LONDON, merchant="Harrods")
    evaluation = Evaluation(
        "txn-42", 91.5, RiskLevel.HIGH,
        (RuleResult("impossible_travel", True, 85, "5,570 km in 30 min"),
         RuleResult("velocity", False)),
    )
    return FraudAlert(txn, evaluation, console_url="https://console.example/?txn=txn-42")


def aws_client(service: str):
    return boto3.client(service, region_name="us-east-1",
                        aws_access_key_id="test", aws_secret_access_key="test")


class TestAlertContent:
    def test_subject_and_body(self, alert):
        assert alert.subject == "[Fraud alert: HIGH] Transaction txn-42 scored 92/100"
        body = alert.body()
        for expected in ("acc-9", "2,500.00 USD", "Harrods", "London, GB", "91.5 (HIGH)",
                         "impossible_travel (85): 5,570 km in 30 min",
                         "https://console.example/?txn=txn-42"):
            assert expected in body
        assert "velocity" not in body  # only triggered rules are listed


class TestSesNotifier:
    def test_sends_email_with_expected_request(self, alert):
        client = aws_client("ses")
        with Stubber(client) as stub:
            stub.add_response(
                "send_email",
                {"MessageId": "ses-123"},
                {
                    "Source": "alerts@bank.example",
                    "Destination": {"ToAddresses": ["fraud-team@bank.example", "oncall@bank.example"]},
                    "Message": {
                        "Subject": {"Data": alert.subject, "Charset": "UTF-8"},
                        "Body": {"Text": {"Data": alert.body(), "Charset": "UTF-8"}},
                    },
                },
            )
            SesNotifier("alerts@bank.example",
                        ["fraud-team@bank.example", "oncall@bank.example"],
                        client=client).notify(alert)
            stub.assert_no_pending_responses()

    def test_aws_error_is_wrapped(self, alert):
        client = aws_client("ses")
        with Stubber(client) as stub:
            stub.add_client_error("send_email", "MessageRejected", "Email address is not verified.")
            with pytest.raises(NotificationError, match="not verified"):
                SesNotifier("a@b.example", ["c@d.example"], client=client).notify(alert)

    @pytest.mark.parametrize("sender, recipients", [("", ["x@y.example"]), ("a@b.example", [])])
    def test_requires_sender_and_recipients(self, sender, recipients):
        with pytest.raises(ValueError):
            SesNotifier(sender, recipients, client=object())


class TestSnsNotifier:
    TOPIC = "arn:aws:sns:us-east-1:123456789012:fraud-alerts"

    def test_publishes_with_attributes_for_filtering(self, alert):
        client = aws_client("sns")
        with Stubber(client) as stub:
            stub.add_response(
                "publish",
                {"MessageId": "sns-1"},
                {
                    "TopicArn": self.TOPIC,
                    "Subject": alert.subject,
                    "Message": alert.body(),
                    "MessageAttributes": {
                        "risk_level": {"DataType": "String", "StringValue": "HIGH"},
                        "account_id": {"DataType": "String", "StringValue": "acc-9"},
                    },
                },
            )
            SnsNotifier(self.TOPIC, client=client).notify(alert)
            stub.assert_no_pending_responses()

    def test_subject_is_truncated_to_sns_limit(self, alert):
        long_alert = FraudAlert(make_txn(txn_id="x" * 64), alert.evaluation)
        captured = {}

        class FakeClient:
            def publish(self, **kwargs):
                captured.update(kwargs)
                return {"MessageId": "1"}

        SnsNotifier(self.TOPIC, client=FakeClient()).notify(long_alert)
        assert len(long_alert.subject) > 100
        assert len(captured["Subject"]) == 100

    def test_aws_error_is_wrapped(self, alert):
        client = aws_client("sns")
        with Stubber(client) as stub:
            stub.add_client_error("publish", "NotFound", "Topic does not exist")
            with pytest.raises(NotificationError):
                SnsNotifier(self.TOPIC, client=client).notify(alert)


class TestBuildNotifier:
    def test_log_is_default(self):
        assert isinstance(build_notifier(""), LoggingNotifier)

    def test_ses(self):
        n = build_notifier("SES", region="us-east-1", ses_sender="a@b.example",
                           ses_recipients=["c@d.example"])
        assert isinstance(n, SesNotifier) and n.recipients == ["c@d.example"]

    def test_sns(self):
        n = build_notifier("sns", region="eu-west-1", sns_topic_arn=TestSnsNotifier.TOPIC)
        assert isinstance(n, SnsNotifier)

    def test_sns_requires_topic(self):
        with pytest.raises(ValueError):
            build_notifier("sns", region="eu-west-1")

    def test_unknown_backend(self):
        with pytest.raises(ValueError, match="unknown notifier"):
            build_notifier("pigeon")

    def test_logging_notifier_logs(self, alert, caplog):
        LoggingNotifier().notify(alert)
        assert "txn-42" in caplog.text
