"""Populate the database with realistic demo traffic, including one example
of every fraud pattern, so the reviewer console has something to show.

Usage (from the ``backend`` directory)::

    python -m scripts.seed_demo            # add demo data
    python -m scripts.seed_demo --reset    # wipe the database first

Uses the same settings as the API (``FRAUD_DB_PATH``, ``NOTIFIER_BACKEND``...),
so with ``NOTIFIER_BACKEND=ses`` the high-risk cases really send email.
"""

from __future__ import annotations

import argparse
import logging
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fraud_engine.config import Settings
from fraud_engine.models import Location, Transaction
from fraud_engine.service import FraudService, build_service

CITIES = {
    "new_york": Location(40.7128, -74.0060, "New York", "US"),
    "london": Location(51.5074, -0.1278, "London", "GB"),
    "san_francisco": Location(37.7749, -122.4194, "San Francisco", "US"),
    "chicago": Location(41.8781, -87.6298, "Chicago", "US"),
    "berlin": Location(52.5200, 13.4050, "Berlin", "DE"),
    "tokyo": Location(35.6762, 139.6503, "Tokyo", "JP"),
    "boston": Location(42.3601, -71.0589, "Boston", "US"),
}
MERCHANTS = ["Blue Bottle Coffee", "Whole Foods", "Shell", "Amazon", "Uber", "Netflix", "Target"]


class Seeder:
    def __init__(self, service: FraudService, now: datetime) -> None:
        self.service = service
        self.now = now
        self.counter = 0
        self.rng = random.Random(42)  # deterministic demo data

    def txn(self, account: str, amount: float, ago: timedelta, city: str | None,
            merchant: str | None = None, currency: str = "USD") -> None:
        self.counter += 1
        result = self.service.process_transaction(
            Transaction(
                transaction_id=f"demo-{self.counter:04d}",
                account_id=account,
                amount=round(amount, 2),
                currency=currency,
                timestamp=self.now - ago,
                merchant=merchant or self.rng.choice(MERCHANTS),
                location=CITIES[city] if city else None,
            )
        )
        e = result.evaluation
        if e.flagged:
            rules = ", ".join(r.rule_name for r in e.triggered)
            print(f"  FLAGGED {result.record.transaction.transaction_id} {account:<9} "
                  f"score={e.risk_score:5.1f} {e.risk_level.value:<6} [{rules}]"
                  f"{'  -> alert sent' if result.alert_sent else ''}")

    def normal_history(self, account: str, city: str, low: float, high: float,
                       count: int = 12, currency: str = "USD") -> None:
        """A couple of weeks of unremarkable spending at home."""
        for i in range(count):
            ago = timedelta(days=14 - i, hours=self.rng.uniform(0, 8))
            self.txn(account, self.rng.uniform(low, high), ago, city, currency=currency)

    def run(self) -> None:
        print("Normal history...")
        self.normal_history("acc-1001", "new_york", 15, 120)
        self.normal_history("acc-1002", "san_francisco", 20, 150)
        self.normal_history("acc-1003", "chicago", 10, 80)
        self.normal_history("acc-1004", "berlin", 20, 90, currency="EUR")
        self.normal_history("acc-1007", "boston", 30, 200)

        print("Fraud scenarios...")
        # 1. Impossible travel: New York, then London 40 minutes later.
        self.txn("acc-1001", 42.10, timedelta(hours=3), "new_york")
        self.txn("acc-1001", 55.00, timedelta(hours=2, minutes=20), "london", "Harrods")

        # 2. Unusual amount: a spender averaging ~$85 suddenly spends $330 (MEDIUM) and $4,800 (HIGH).
        self.txn("acc-1002", 330.00, timedelta(hours=5), "san_francisco", "Best Buy")
        self.txn("acc-1002", 4_800.00, timedelta(hours=2), "san_francisco", "Apple Store")

        # 3. Velocity / card testing: 8 tiny charges within 6 minutes.
        for i in range(8):
            self.txn("acc-1003", self.rng.uniform(1, 5), timedelta(minutes=95 - i * 0.75),
                     "chicago", "Online Games Ltd")

        # 4. Combined: large EUR amount from Tokyo 30 min after Berlin -> HIGH, alert.
        self.txn("acc-1004", 35.00, timedelta(minutes=80), "berlin")
        self.txn("acc-1004", 2_900.00, timedelta(minutes=50), "tokyo", "Akihabara Electronics",
                 currency="EUR")

        # 5. Brand-new account, first transaction above the hard limit.
        self.txn("acc-1005", 15_000.00, timedelta(minutes=30), "new_york", "Wire Transfer Co")

        # 6. Plausible travel (New York -> Boston in 4h) and a normal amount: not flagged.
        self.txn("acc-1007", 90.00, timedelta(hours=6), "new_york")
        self.txn("acc-1007", 75.00, timedelta(hours=2), "boston")

        # 7. Only flagged when the example plug-in is loaded (RULE_PLUGINS=plugins.blocked_merchant).
        self.txn("acc-1007", 60.00, timedelta(minutes=10), "boston", "CryptoMixer")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="delete the database first")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    settings = Settings.from_env()
    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            Path(settings.db_path + suffix).unlink(missing_ok=True)

    service = build_service(settings)
    seeder = Seeder(service, datetime.now(timezone.utc))
    seeder.run()
    stats = service.repository.stats()
    print(f"\nDone: {stats['total']} transactions, {stats['flagged']} flagged, "
          f"{stats['alerts_sent']} alert(s) sent. Database: {settings.db_path}")


if __name__ == "__main__":
    main()
