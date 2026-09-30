"""Tests for the ingest -> evaluate -> persist -> alert pipeline and settings."""

from __future__ import annotations

import json
import threading
from datetime import timedelta

import pytest

from conftest import MAHARASHTRA, WEST_BENGAL, RecordingNotifier, make_txn
from fraud_engine.config import Settings
from fraud_engine.models import ReviewStatus, RiskLevel
from fraud_engine.notifications import LoggingNotifier
from fraud_engine.repository import DuplicateTransactionError
from fraud_engine.service import FraudService, build_service


class TestProcessing:
    def test_clean_transaction_is_stored_without_alert(self, service, notifier):
        result = service.process_transaction(make_txn())
        assert not result.evaluation.flagged
        assert not result.alert_sent and notifier.alerts == []
        assert result.record.risk_level is RiskLevel.LOW

    def test_history_from_the_database_drives_the_rules(self, service):
        """The service must feed previously stored transactions to the engine."""
        service.process_transaction(make_txn(at=timedelta(minutes=-20), location=MAHARASHTRA))
        result = service.process_transaction(make_txn(location=WEST_BENGAL))
        assert [r.rule_name for r in result.evaluation.triggered] == ["impossible_travel"]

    def test_high_risk_sends_one_alert_and_marks_notified(self, service, notifier):
        service.process_transaction(make_txn(at=timedelta(minutes=-20), location=MAHARASHTRA))
        result = service.process_transaction(make_txn(txn_id="hot", location=WEST_BENGAL))
        assert result.evaluation.risk_level is RiskLevel.HIGH
        assert result.alert_sent
        assert [a.transaction.transaction_id for a in notifier.alerts] == ["hot"]
        assert result.record.notified_at is not None

    def test_medium_risk_is_flagged_but_not_alerted(self, service, notifier):
        for i in range(5):
            service.process_transaction(make_txn(at=timedelta(minutes=-5 + i)))
        result = service.process_transaction(make_txn(at=timedelta(minutes=1)))  # 6th in window
        assert result.evaluation.risk_level is RiskLevel.MEDIUM
        assert result.record.review_status is ReviewStatus.OPEN
        assert not result.alert_sent and notifier.alerts == []

    def test_custom_alert_threshold(self, repo, engine, notifier):
        service = FraudService(repo, engine, notifier, alert_threshold=40)
        for i in range(6):
            service.process_transaction(make_txn(at=timedelta(minutes=i)))
        assert len(notifier.alerts) == 1  # the MEDIUM (50) velocity hit now alerts

    def test_notifier_failure_does_not_lose_the_transaction(self, repo, engine):
        service = FraudService(repo, engine, RecordingNotifier(fail=True))
        result = service.process_transaction(make_txn(amount=50_000))
        assert result.evaluation.flagged and not result.alert_sent
        stored = repo.get_transaction(result.record.transaction.transaction_id)
        assert stored.flagged and stored.notified_at is None

    def test_alert_contains_console_link(self, repo, engine, notifier):
        service = FraudService(repo, engine, notifier, console_url="https://console.example/")
        service.process_transaction(make_txn(txn_id="big", amount=50_000))
        assert notifier.alerts[0].console_url == "https://console.example/?txn=big"

    def test_duplicate_submission(self, service):
        service.process_transaction(make_txn(txn_id="same"))
        with pytest.raises(DuplicateTransactionError):
            service.process_transaction(make_txn(txn_id="same"))

    def test_review(self, service):
        result = service.process_transaction(make_txn(amount=50_000))
        rec = service.review_transaction(
            result.record.transaction.transaction_id, ReviewStatus.CLEARED, "ana", "verified"
        )
        assert rec.review_status is ReviewStatus.CLEARED

    def test_concurrent_submissions_are_all_counted(self, service):
        """The lock keeps velocity accurate when requests race each other."""
        # Identical timestamps: whatever order the threads run in, the k-th one
        # processed sees exactly k-1 stored transactions in its window.
        txns = [make_txn() for _ in range(12)]
        threads = [threading.Thread(target=service.process_transaction, args=(t,)) for t in txns]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        _, total = service.repository.list_transactions(flagged_only=False)
        _, flagged = service.repository.list_transactions()
        assert total == 12
        assert flagged == 12 - 5  # every transaction after the 5th breaches the limit


class TestSettingsAndWiring:
    def test_from_env_parses_all_fields(self, tmp_path):
        cfg = tmp_path / "rules.json"
        cfg.write_text(json.dumps({"velocity": {"max_transactions": 3}}))
        s = Settings.from_env({
            "FRAUD_DB_PATH": str(tmp_path / "x.db"),
            "NOTIFIER_BACKEND": "ses",
            "AWS_REGION": "eu-west-1",
            "SES_SENDER": "a@b.example",
            "SES_RECIPIENTS": "c@d.example, e@f.example,",
            "HIGH_RISK_THRESHOLD": "80",
            "ALERT_THRESHOLD": "90",
            "RULE_PLUGINS": "plugins.blocked_merchant",
            "RULE_CONFIG": str(cfg),
            "CORS_ORIGINS": "http://a,http://b",
        })
        assert s.ses_recipients == ("c@d.example", "e@f.example")
        assert s.high_risk_threshold == 80 and s.alert_threshold == 90
        assert s.rule_config == {"velocity": {"max_transactions": 3}}
        assert s.rule_plugins == ("plugins.blocked_merchant",)
        assert s.cors_origins == ("http://a", "http://b")

    def test_defaults(self):
        s = Settings.from_env({})
        assert s.notifier_backend == "log" and s.alert_threshold is None
        assert s.rule_config == {}

    def test_inline_rule_config_must_be_object_of_objects(self):
        with pytest.raises(ValueError):
            Settings.from_env({"RULE_CONFIG": '{"velocity": 3}'})

    def test_build_service(self, tmp_path):
        s = Settings(
            db_path=str(tmp_path / "svc.db"),
            rule_config={"impossible_travel": {"enabled": False}, "velocity": {"max_transactions": 2}},
            medium_risk_threshold=30,
            high_risk_threshold=60,
        )
        service = build_service(s)
        assert [r.name for r in service.engine.rules] == ["unusual_amount", "velocity"]
        assert service.engine.thresholds.high == 60
        assert service.alert_threshold == 60
        assert isinstance(service.notifier, LoggingNotifier)
