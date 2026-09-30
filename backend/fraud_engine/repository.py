"""SQLite persistence for transactions, fraud flags and review actions.

Schema overview::

    transactions   one row per evaluated transaction, including its risk score
                   and current review workflow state
    fraud_flags    one row per rule that fired for a transaction
    review_events  append-only audit trail of reviewer actions

A new connection is opened per operation, which keeps the repository safe to
share between threads (FastAPI runs sync endpoints in a thread pool). Use a
file path, not ``":memory:"``, because each connection would otherwise see
its own empty database.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from .engine import Evaluation
from .models import Location, ReviewStatus, RiskLevel, Transaction, ensure_utc

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id TEXT PRIMARY KEY,
    account_id     TEXT NOT NULL,
    amount         REAL NOT NULL,
    currency       TEXT NOT NULL,
    merchant       TEXT NOT NULL DEFAULT '',
    timestamp      TEXT NOT NULL,          -- ISO-8601 UTC, lexically sortable
    latitude       REAL,
    longitude      REAL,
    city           TEXT,
    country        TEXT,
    risk_score     REAL NOT NULL,
    risk_level     TEXT NOT NULL,
    flagged        INTEGER NOT NULL,
    review_status  TEXT,                   -- NULL unless flagged
    reviewed_by    TEXT,
    review_note    TEXT,
    reviewed_at    TEXT,
    notified_at    TEXT,                   -- when a high-risk alert was sent
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_txn_account_time ON transactions (account_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_txn_review ON transactions (flagged, review_status, timestamp);

CREATE TABLE IF NOT EXISTS fraud_flags (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL REFERENCES transactions (transaction_id) ON DELETE CASCADE,
    rule_name      TEXT NOT NULL,
    score          REAL NOT NULL,
    reason         TEXT NOT NULL,
    details        TEXT NOT NULL,          -- JSON object
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flags_txn ON fraud_flags (transaction_id);

CREATE TABLE IF NOT EXISTS review_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL REFERENCES transactions (transaction_id) ON DELETE CASCADE,
    status         TEXT NOT NULL,
    reviewer       TEXT NOT NULL,
    note           TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_txn ON review_events (transaction_id);
"""


class DuplicateTransactionError(Exception):
    """A transaction with the same id has already been stored."""


class TransactionNotFoundError(LookupError):
    """No transaction exists with the requested id."""


class InvalidReviewError(ValueError):
    """The requested review action is not allowed for this transaction."""


@dataclass(frozen=True)
class FlagRecord:
    rule_name: str
    score: float
    reason: str
    details: Mapping[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class ReviewEvent:
    status: ReviewStatus
    reviewer: str
    note: str | None
    created_at: datetime


@dataclass(frozen=True)
class TransactionRecord:
    """A stored transaction together with its fraud verdict and review state."""

    transaction: Transaction
    risk_score: float
    risk_level: RiskLevel
    flagged: bool
    review_status: ReviewStatus | None
    reviewed_by: str | None
    review_note: str | None
    reviewed_at: datetime | None
    notified_at: datetime | None
    created_at: datetime
    flags: tuple[FlagRecord, ...] = ()
    reviews: tuple[ReviewEvent, ...] = field(default=())


def _fmt(value: datetime) -> str:
    """Fixed-width UTC format so string comparison equals time comparison."""
    return ensure_utc(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SQLiteRepository:
    """Stores and queries transactions, flags and review events."""

    def __init__(self, db_path: str | Path) -> None:
        if str(db_path) == ":memory:":
            raise ValueError("use a file path; ':memory:' is not shared between connections")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")  # readers don't block the writer
            conn.executescript(SCHEMA)

    # -- writes -----------------------------------------------------------------

    def save_evaluation(self, transaction: Transaction, evaluation: Evaluation) -> None:
        """Atomically store a transaction, its verdict and one flag per fired rule."""
        if evaluation.transaction_id != transaction.transaction_id:
            raise ValueError("evaluation does not belong to this transaction")
        now = _fmt(_utcnow())
        loc = transaction.location
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO transactions (
                        transaction_id, account_id, amount, currency, merchant, timestamp,
                        latitude, longitude, city, country,
                        risk_score, risk_level, flagged, review_status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        transaction.transaction_id,
                        transaction.account_id,
                        transaction.amount,
                        transaction.currency,
                        transaction.merchant,
                        _fmt(transaction.timestamp),
                        loc.latitude if loc else None,
                        loc.longitude if loc else None,
                        loc.city if loc else None,
                        loc.country if loc else None,
                        evaluation.risk_score,
                        evaluation.risk_level.value,
                        int(evaluation.flagged),
                        ReviewStatus.OPEN.value if evaluation.flagged else None,
                        now,
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO fraud_flags (transaction_id, rule_name, score, reason, details, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            transaction.transaction_id,
                            r.rule_name,
                            r.score,
                            r.reason,
                            json.dumps(dict(r.details), default=str),
                            now,
                        )
                        for r in evaluation.triggered
                    ],
                )
        except sqlite3.IntegrityError as exc:
            if "transactions.transaction_id" in str(exc):
                raise DuplicateTransactionError(transaction.transaction_id) from exc
            raise

    def mark_notified(self, transaction_id: str, when: datetime | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE transactions SET notified_at = ? WHERE transaction_id = ?",
                (_fmt(when or _utcnow()), transaction_id),
            )

    def update_review(
        self,
        transaction_id: str,
        status: ReviewStatus,
        reviewer: str,
        note: str | None = None,
    ) -> TransactionRecord:
        """Set the review state of a flagged transaction and append an audit event.

        Allowed targets are REVIEWED, CLEARED and OPEN (re-open). Unflagged
        transactions cannot be reviewed because they never entered the queue.
        """
        status = ReviewStatus(status)
        reviewer = (reviewer or "").strip()
        if not reviewer:
            raise InvalidReviewError("reviewer is required")
        note = (note or "").strip() or None
        now = _fmt(_utcnow())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT flagged FROM transactions WHERE transaction_id = ?", (transaction_id,)
            ).fetchone()
            if row is None:
                raise TransactionNotFoundError(transaction_id)
            if not row["flagged"]:
                raise InvalidReviewError("only flagged transactions can be reviewed")
            conn.execute(
                """
                UPDATE transactions
                   SET review_status = ?, reviewed_by = ?, review_note = ?, reviewed_at = ?
                 WHERE transaction_id = ?
                """,
                (status.value, reviewer, note, now, transaction_id),
            )
            conn.execute(
                """
                INSERT INTO review_events (transaction_id, status, reviewer, note, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (transaction_id, status.value, reviewer, note, now),
            )
        return self.get_transaction(transaction_id)

    # -- reads ------------------------------------------------------------------

    def get_account_history(
        self, account_id: str, until: datetime, limit: int = 200
    ) -> list[Transaction]:
        """Most recent ``limit`` transactions of an account at or before ``until``,
        newest first. This is the ``history`` fed to the rule engine."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM transactions
                 WHERE account_id = ? AND timestamp <= ?
                 ORDER BY timestamp DESC
                 LIMIT ?
                """,
                (account_id, _fmt(until), limit),
            ).fetchall()
        return [self._row_to_transaction(r) for r in rows]

    def get_transaction(self, transaction_id: str) -> TransactionRecord:
        """Full record including flags and review history."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,)
            ).fetchone()
            if row is None:
                raise TransactionNotFoundError(transaction_id)
            flags = self._load_flags(conn, [transaction_id]).get(transaction_id, ())
            reviews = conn.execute(
                "SELECT * FROM review_events WHERE transaction_id = ? ORDER BY id",
                (transaction_id,),
            ).fetchall()
        return self._row_to_record(
            row,
            flags,
            tuple(
                ReviewEvent(
                    status=ReviewStatus(r["status"]),
                    reviewer=r["reviewer"],
                    note=r["note"],
                    created_at=_parse(r["created_at"]),
                )
                for r in reviews
            ),
        )

    def list_transactions(
        self,
        *,
        flagged_only: bool = True,
        review_status: ReviewStatus | None = None,
        risk_level: RiskLevel | None = None,
        account_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[TransactionRecord], int]:
        """Filtered page of transactions (newest first) and the total match count.

        Flags are included; review history is only loaded by :meth:`get_transaction`.
        """
        where, params = self._filters(flagged_only, review_status, risk_level, account_id)
        with self._connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM transactions {where}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM transactions {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            flags = self._load_flags(conn, [r["transaction_id"] for r in rows])
        records = [self._row_to_record(r, flags.get(r["transaction_id"], ())) for r in rows]
        return records, total

    def stats(self) -> dict[str, Any]:
        """Counters for the console header."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)                                             AS total,
                       COALESCE(SUM(flagged), 0)                            AS flagged,
                       COALESCE(SUM(review_status = 'OPEN'), 0)             AS open,
                       COALESCE(SUM(review_status = 'REVIEWED'), 0)         AS reviewed,
                       COALESCE(SUM(review_status = 'CLEARED'), 0)          AS cleared,
                       COALESCE(SUM(flagged AND risk_level = 'HIGH'), 0)    AS high_risk,
                       COALESCE(SUM(flagged AND risk_level = 'HIGH'
                                    AND review_status = 'OPEN'), 0)         AS high_risk_open,
                       COALESCE(SUM(notified_at IS NOT NULL), 0)            AS alerts_sent
                  FROM transactions
                """
            ).fetchone()
            by_rule = conn.execute(
                "SELECT rule_name, COUNT(*) AS n FROM fraud_flags GROUP BY rule_name ORDER BY n DESC"
            ).fetchall()
        result = {k: int(row[k]) for k in row.keys()}
        result["flags_by_rule"] = {r["rule_name"]: r["n"] for r in by_rule}
        return result

    # -- helpers ----------------------------------------------------------------

    @staticmethod
    def _filters(
        flagged_only: bool,
        review_status: ReviewStatus | None,
        risk_level: RiskLevel | None,
        account_id: str | None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if flagged_only:
            clauses.append("flagged = 1")
        if review_status is not None:
            clauses.append("review_status = ?")
            params.append(ReviewStatus(review_status).value)
        if risk_level is not None:
            clauses.append("risk_level = ?")
            params.append(RiskLevel(risk_level).value)
        if account_id:
            clauses.append("account_id = ?")
            params.append(account_id)
        return ("WHERE " + " AND ".join(clauses)) if clauses else "", params

    @staticmethod
    def _load_flags(
        conn: sqlite3.Connection, transaction_ids: list[str]
    ) -> dict[str, tuple[FlagRecord, ...]]:
        if not transaction_ids:
            return {}
        placeholders = ",".join("?" * len(transaction_ids))
        rows = conn.execute(
            f"SELECT * FROM fraud_flags WHERE transaction_id IN ({placeholders}) "
            "ORDER BY score DESC, id",
            transaction_ids,
        ).fetchall()
        grouped: dict[str, list[FlagRecord]] = {}
        for r in rows:
            grouped.setdefault(r["transaction_id"], []).append(
                FlagRecord(
                    rule_name=r["rule_name"],
                    score=r["score"],
                    reason=r["reason"],
                    details=json.loads(r["details"]),
                    created_at=_parse(r["created_at"]),
                )
            )
        return {k: tuple(v) for k, v in grouped.items()}

    @staticmethod
    def _row_to_transaction(row: sqlite3.Row) -> Transaction:
        location = None
        if row["latitude"] is not None and row["longitude"] is not None:
            location = Location(row["latitude"], row["longitude"], row["city"], row["country"])
        return Transaction(
            transaction_id=row["transaction_id"],
            account_id=row["account_id"],
            amount=row["amount"],
            currency=row["currency"],
            timestamp=_parse(row["timestamp"]),
            merchant=row["merchant"],
            location=location,
        )

    @classmethod
    def _row_to_record(
        cls,
        row: sqlite3.Row,
        flags: tuple[FlagRecord, ...] = (),
        reviews: tuple[ReviewEvent, ...] = (),
    ) -> TransactionRecord:
        return TransactionRecord(
            transaction=cls._row_to_transaction(row),
            risk_score=row["risk_score"],
            risk_level=RiskLevel(row["risk_level"]),
            flagged=bool(row["flagged"]),
            review_status=ReviewStatus(row["review_status"]) if row["review_status"] else None,
            reviewed_by=row["reviewed_by"],
            review_note=row["review_note"],
            reviewed_at=_parse(row["reviewed_at"]),
            notified_at=_parse(row["notified_at"]),
            created_at=_parse(row["created_at"]),
            flags=flags,
            reviews=reviews,
        )
