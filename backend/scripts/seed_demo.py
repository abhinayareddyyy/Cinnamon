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
    "maharashtra": Location(19.0760, 72.8777, "Maharashtra", "IN"),
    "karnataka": Location(12.9716, 77.5946, "Karnataka", "IN"),
    "west_bengal": Location(22.5726, 88.3639, "West Bengal", "IN"),
    "tamil_nadu": Location(13.0827, 80.2707, "Tamil Nadu", "IN"),
    "telangana": Location(17.3850, 78.4867, "Telangana", "IN"),
    "rajasthan": Location(26.9124, 75.7873, "Rajasthan", "IN"),
}
MERCHANTS = ["Blue Bottle Coffee", "Whole Foods", "Shell", "Amazon", "Uber", "Netflix", "Target"]


class Seeder:
    def __init__(self, service: FraudService, now: datetime) -> None:
        self.service = service
        self.now = now
        self.counter = 0
        self.rng = random.Random(42)  # deterministic demo data

    def txn(self, account: str, amount: float, ago: timedelta, city: str | None,
            merchant: str | None = None, currency: str = "INR") -> None:
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
                       count: int = 12, currency: str = "INR") -> None:
        """A couple of weeks of unremarkable spending at home."""
        for i in range(count):
            ago = timedelta(days=14 - i, hours=self.rng.uniform(0, 8))
            self.txn(account, self.rng.uniform(low, high), ago, city, currency=currency)

    def run(self) -> None:
        print("Normal history...")
        self.normal_history("acc-1001", "maharashtra", 1500, 12000)
        self.normal_history("acc-1002", "karnataka", 2000, 15000)
        self.normal_history("acc-1003", "tamil_nadu", 1000, 8000)
        self.normal_history("acc-1004", "west_bengal", 2000, 9000)
        self.normal_history("acc-1007", "telangana", 3000, 20000)

        print("Fraud scenarios...")
        # 1. Impossible travel: Maharashtra, then West Bengal 40 minutes later.
        self.txn("acc-1001", 4210.00, timedelta(hours=3), "maharashtra")
        self.txn("acc-1001", 5500.00, timedelta(hours=2, minutes=20), "west_bengal", "Kolkata Market")

        # 2. Unusual amount: a spender averaging ~INR 8,500 suddenly spends INR 33,000 and INR 4,80,000.
        self.txn("acc-1002", 33000.00, timedelta(hours=5), "karnataka", "Bengaluru Electronics")
        self.txn("acc-1002", 480000.00, timedelta(hours=2), "karnataka", "Tech Bazaar")

        # 3. Velocity / card testing: 8 tiny charges within 6 minutes.
        for i in range(8):
            self.txn("acc-1003", self.rng.uniform(1, 5), timedelta(minutes=95 - i * 0.75),
                     "tamil_nadu", "Online Games Ltd")

        # 4. Combined: large INR amount from West Bengal 30 min after Telangana -> HIGH, alert.
        self.txn("acc-1004", 3500.00, timedelta(minutes=80), "west_bengal")
        self.txn("acc-1004", 290000.00, timedelta(minutes=50), "telangana", "Hyderabad Electronics")

        # 5. Brand-new account, first transaction above the hard limit.
        self.txn("acc-1005", 1_500_000.00, timedelta(minutes=30), "maharashtra", "Wire Transfer Co")

        # 6. Plausible travel (Telangana -> Karnataka in 4h) and a normal amount: not flagged.
        self.txn("acc-1007", 9000.00, timedelta(hours=6), "telangana")
        self.txn("acc-1007", 7500.00, timedelta(hours=2), "karnataka")

        # 7. Only flagged when the example plug-in is loaded (RULE_PLUGINS=plugins.blocked_merchant).
        self.txn("acc-1007", 6000.00, timedelta(minutes=10), "karnataka", "CryptoMixer")


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
