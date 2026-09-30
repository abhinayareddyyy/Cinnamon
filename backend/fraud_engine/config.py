"""Application settings, read from environment variables.

| Variable                | Default                  | Meaning                                         |
|-------------------------|--------------------------|-------------------------------------------------|
| FRAUD_DB_PATH           | ./data/fraud.db          | SQLite database file                            |
| NOTIFIER_BACKEND        | log                      | ``log``, ``ses`` or ``sns``                     |
| AWS_REGION              | (boto3 default)          | Region for SES/SNS                              |
| SES_SENDER              |                          | Verified SES "From" address                     |
| SES_RECIPIENTS          |                          | Comma-separated alert recipients                |
| SNS_TOPIC_ARN           |                          | Topic to publish alerts to                      |
| MEDIUM_RISK_THRESHOLD   | 40                       | Score at which a transaction becomes MEDIUM     |
| HIGH_RISK_THRESHOLD     | 70                       | Score at which it becomes HIGH                  |
| ALERT_THRESHOLD         | = HIGH_RISK_THRESHOLD    | Score at which SES/SNS alerts are sent          |
| RULE_PLUGINS            |                          | Comma-separated modules with extra rules        |
| RULE_CONFIG             | {}                       | JSON (or path to a JSON file) of rule options   |
| CORS_ORIGINS            | http://localhost:5173    | Comma-separated origins allowed to call the API |
| CONSOLE_URL             |                          | Link included in alert messages                 |
| FRONTEND_DIST           |                          | Built React app to serve from ``/``             |
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(v.strip() for v in (value or "").split(",") if v.strip())


def _rule_config(value: str | None) -> dict[str, dict[str, Any]]:
    """Parse ``RULE_CONFIG`` as inline JSON or as a path to a JSON file."""
    if not value or not value.strip():
        return {}
    text = value.strip()
    if not text.startswith("{"):
        text = Path(text).read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        raise ValueError("RULE_CONFIG must be a JSON object of {rule_name: {option: value}}")
    return data


@dataclass(frozen=True)
class Settings:
    db_path: str = "data/fraud.db"
    notifier_backend: str = "log"
    aws_region: str | None = None
    ses_sender: str | None = None
    ses_recipients: tuple[str, ...] = ()
    sns_topic_arn: str | None = None
    medium_risk_threshold: float = 40.0
    high_risk_threshold: float = 70.0
    alert_threshold: float | None = None
    rule_plugins: tuple[str, ...] = ()
    rule_config: dict[str, dict[str, Any]] = field(default_factory=dict)
    cors_origins: tuple[str, ...] = ("http://localhost:5173",)
    console_url: str | None = None
    frontend_dist: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        high = float(env.get("HIGH_RISK_THRESHOLD", 70))
        return cls(
            db_path=env.get("FRAUD_DB_PATH", "data/fraud.db"),
            notifier_backend=env.get("NOTIFIER_BACKEND", "log"),
            aws_region=env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or None,
            ses_sender=env.get("SES_SENDER") or None,
            ses_recipients=_csv(env.get("SES_RECIPIENTS")),
            sns_topic_arn=env.get("SNS_TOPIC_ARN") or None,
            medium_risk_threshold=float(env.get("MEDIUM_RISK_THRESHOLD", 40)),
            high_risk_threshold=high,
            alert_threshold=float(env["ALERT_THRESHOLD"]) if env.get("ALERT_THRESHOLD") else None,
            rule_plugins=_csv(env.get("RULE_PLUGINS")),
            rule_config=_rule_config(env.get("RULE_CONFIG")),
            cors_origins=_csv(env.get("CORS_ORIGINS", "http://localhost:5173")),
            console_url=env.get("CONSOLE_URL") or None,
            frontend_dist=env.get("FRONTEND_DIST") or None,
        )
