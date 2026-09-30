"""REST API consumed by the React reviewer console.

Run with::

    uvicorn fraud_engine.api:create_app --factory --reload

Endpoints (all JSON):

    GET  /api/health                     liveness probe
    GET  /api/rules                      active rules and their parameters
    GET  /api/stats                      counters for the console header
    POST /api/transactions               evaluate + store a transaction
    GET  /api/transactions               list (flagged by default) with filters
    GET  /api/transactions/{id}          one transaction with flags + review history
    POST /api/transactions/{id}/review   mark REVIEWED / CLEARED / re-OPEN
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from .config import Settings
from .models import Location, ReviewStatus, RiskLevel, Transaction
from .repository import (
    DuplicateTransactionError,
    InvalidReviewError,
    TransactionNotFoundError,
    TransactionRecord,
)
from .rules.base import RuleResult
from .service import FraudService, build_service

logger = logging.getLogger(__name__)


# -- request / response schemas --------------------------------------------------


class TransactionIn(BaseModel):
    """Payload for submitting a transaction. ``transaction_id`` and
    ``timestamp`` are generated when omitted."""

    transaction_id: str | None = Field(None, min_length=1, max_length=64)
    account_id: str = Field(min_length=1, max_length=64)
    amount: float = Field(gt=0, le=1_000_000_000)
    currency: Literal["INR"] = "INR"
    merchant: str = Field("", max_length=200)
    timestamp: datetime | None = None
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    city: str | None = Field(None, max_length=100)
    country: str | None = Field(None, max_length=100)

    @model_validator(mode="after")
    def _coordinates_together(self) -> "TransactionIn":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self

    def to_domain(self) -> Transaction:
        location = None
        if self.latitude is not None and self.longitude is not None:
            location = Location(self.latitude, self.longitude, self.city, self.country)
        return Transaction(
            transaction_id=self.transaction_id or f"txn_{uuid.uuid4().hex[:16]}",
            account_id=self.account_id,
            amount=self.amount,
            currency=self.currency,
            timestamp=self.timestamp or datetime.now(timezone.utc),
            merchant=self.merchant,
            location=location,
        )


class FlagOut(BaseModel):
    rule_name: str
    score: float
    reason: str
    details: dict[str, Any]
    created_at: datetime


class ReviewEventOut(BaseModel):
    status: ReviewStatus
    reviewer: str
    note: str | None
    created_at: datetime


class TransactionOut(BaseModel):
    transaction_id: str
    account_id: str
    amount: float
    currency: str
    merchant: str
    timestamp: datetime
    latitude: float | None
    longitude: float | None
    city: str | None
    country: str | None
    risk_score: float
    risk_level: RiskLevel
    flagged: bool
    review_status: ReviewStatus | None
    reviewed_by: str | None
    review_note: str | None
    reviewed_at: datetime | None
    notified_at: datetime | None
    created_at: datetime
    flags: list[FlagOut]
    reviews: list[ReviewEventOut]

    @classmethod
    def from_record(cls, rec: TransactionRecord) -> "TransactionOut":
        t, loc = rec.transaction, rec.transaction.location
        return cls(
            transaction_id=t.transaction_id,
            account_id=t.account_id,
            amount=t.amount,
            currency=t.currency,
            merchant=t.merchant,
            timestamp=t.timestamp,
            latitude=loc.latitude if loc else None,
            longitude=loc.longitude if loc else None,
            city=loc.city if loc else None,
            country=loc.country if loc else None,
            risk_score=rec.risk_score,
            risk_level=rec.risk_level,
            flagged=rec.flagged,
            review_status=rec.review_status,
            reviewed_by=rec.reviewed_by,
            review_note=rec.review_note,
            reviewed_at=rec.reviewed_at,
            notified_at=rec.notified_at,
            created_at=rec.created_at,
            flags=[FlagOut(**vars(f)) for f in rec.flags],
            reviews=[ReviewEventOut(**vars(r)) for r in rec.reviews],
        )


class RuleResultOut(BaseModel):
    rule_name: str
    triggered: bool
    score: float
    reason: str
    details: dict[str, Any]
    error: bool

    @classmethod
    def from_result(cls, r: RuleResult) -> "RuleResultOut":
        return cls(
            rule_name=r.rule_name, triggered=r.triggered, score=r.score,
            reason=r.reason, details=dict(r.details), error=r.error,
        )


class ProcessingOut(BaseModel):
    transaction: TransactionOut
    rule_results: list[RuleResultOut]
    alert_sent: bool


class TransactionPage(BaseModel):
    items: list[TransactionOut]
    total: int
    limit: int
    offset: int


class ReviewIn(BaseModel):
    status: ReviewStatus
    reviewer: str = Field(min_length=1, max_length=100)
    note: str | None = Field(None, max_length=2000)


class RuleOut(BaseModel):
    name: str
    description: str
    weight: float
    parameters: dict[str, Any]


class ThresholdsOut(BaseModel):
    medium: float
    high: float
    alert: float


class RulesOut(BaseModel):
    rules: list[RuleOut]
    thresholds: ThresholdsOut


# -- application factory -----------------------------------------------------------


def create_app(service: FraudService | None = None, settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app. Tests inject a ``service``; production builds one
    from environment ``settings``."""
    settings = settings or Settings.from_env()
    service = service or build_service(settings)

    app = FastAPI(
        title="Fraud Rule Engine",
        version="1.0.0",
        description="Evaluates transactions against pluggable fraud rules and "
        "supports a human review workflow.",
    )
    app.state.service = service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/rules", response_model=RulesOut)
    def list_rules() -> RulesOut:
        t = service.engine.thresholds
        return RulesOut(
            rules=[
                RuleOut(name=r.name, description=r.description, weight=r.weight,
                        parameters=r.parameters())
                for r in service.engine.rules
            ],
            thresholds=ThresholdsOut(medium=t.medium, high=t.high, alert=service.alert_threshold),
        )

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        return service.repository.stats()

    @app.post("/api/transactions", response_model=ProcessingOut, status_code=status.HTTP_201_CREATED)
    def submit_transaction(payload: TransactionIn) -> ProcessingOut:
        try:
            result = service.process_transaction(payload.to_domain())
        except DuplicateTransactionError:
            raise HTTPException(status.HTTP_409_CONFLICT, "transaction_id already processed")
        return ProcessingOut(
            transaction=TransactionOut.from_record(result.record),
            rule_results=[RuleResultOut.from_result(r) for r in result.evaluation.results],
            alert_sent=result.alert_sent,
        )

    @app.get("/api/transactions", response_model=TransactionPage)
    def list_transactions(
        flagged: bool = Query(True, description="Only transactions flagged by a rule"),
        review_status: ReviewStatus | None = Query(None, alias="status"),
        risk_level: RiskLevel | None = None,
        account_id: str | None = None,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> TransactionPage:
        records, total = service.repository.list_transactions(
            flagged_only=flagged, review_status=review_status, risk_level=risk_level,
            account_id=account_id, limit=limit, offset=offset,
        )
        return TransactionPage(
            items=[TransactionOut.from_record(r) for r in records],
            total=total, limit=limit, offset=offset,
        )

    @app.get("/api/transactions/{transaction_id}", response_model=TransactionOut)
    def get_transaction(transaction_id: str) -> TransactionOut:
        try:
            return TransactionOut.from_record(service.repository.get_transaction(transaction_id))
        except TransactionNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "transaction not found")

    @app.post("/api/transactions/{transaction_id}/review", response_model=TransactionOut)
    def review_transaction(transaction_id: str, payload: ReviewIn) -> TransactionOut:
        try:
            record = service.review_transaction(
                transaction_id, payload.status, payload.reviewer, payload.note
            )
        except TransactionNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "transaction not found")
        except InvalidReviewError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        return TransactionOut.from_record(record)

    # Optionally serve the built React console from the same origin.
    if settings.frontend_dist and Path(settings.frontend_dist).is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="console")

    return app
