"""HTTP-level tests of the REST API used by the reviewer console."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from conftest import T0
from fraud_engine.api import create_app
from fraud_engine.config import Settings


@pytest.fixture
def client(service, tmp_path):
    app = create_app(service=service, settings=Settings(db_path=str(tmp_path / "unused.db")))
    with TestClient(app) as c:
        yield c


def payload(**overrides):
    body = {
        "account_id": "acc-1",
        "amount": 42.5,
        "currency": "usd",
        "merchant": "Coffee",
        "timestamp": T0.isoformat(),
        "latitude": 40.7128,
        "longitude": -74.0060,
        "city": "New York",
        "country": "US",
    }
    body.update(overrides)
    return body


def submit(client, **overrides):
    response = client.post("/api/transactions", json=payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def submit_impossible_travel(client, txn_id="hot"):
    submit(client, transaction_id="warmup", timestamp=(T0 - timedelta(minutes=30)).isoformat())
    return submit(client, transaction_id=txn_id, latitude=51.5074, longitude=-0.1278,
                  city="London", country="GB")


class TestSubmit:
    def test_clean_transaction(self, client):
        body = submit(client)
        txn = body["transaction"]
        assert txn["transaction_id"].startswith("txn_")  # generated
        assert txn["currency"] == "USD"
        assert txn["flagged"] is False and txn["review_status"] is None
        assert {r["rule_name"] for r in body["rule_results"]} == {
            "velocity", "unusual_amount", "impossible_travel"}
        assert body["alert_sent"] is False

    def test_high_risk_transaction_is_flagged_and_alerted(self, client, notifier):
        body = submit_impossible_travel(client)
        txn = body["transaction"]
        assert txn["risk_level"] == "HIGH" and txn["review_status"] == "OPEN"
        assert txn["flags"][0]["rule_name"] == "impossible_travel"
        assert txn["flags"][0]["details"]["previous_location"] == "New York, US"
        assert body["alert_sent"] is True and txn["notified_at"] is not None
        assert len(notifier.alerts) == 1

    def test_duplicate_is_conflict(self, client):
        submit(client, transaction_id="dup")
        r = client.post("/api/transactions", json=payload(transaction_id="dup"))
        assert r.status_code == 409

    @pytest.mark.parametrize(
        "overrides",
        [
            {"amount": 0},
            {"amount": -5},
            {"currency": "DOLLARS"},
            {"account_id": ""},
            {"latitude": 91},
            {"longitude": None},  # latitude without longitude
        ],
    )
    def test_validation_errors(self, client, overrides):
        assert client.post("/api/transactions", json=payload(**overrides)).status_code == 422

    def test_location_is_optional(self, client):
        body = submit(client, latitude=None, longitude=None, city=None, country=None)
        assert body["transaction"]["latitude"] is None


class TestQueryAndReview:
    def test_list_defaults_to_flagged(self, client):
        submit_impossible_travel(client)
        page = client.get("/api/transactions").json()
        assert page["total"] == 1 and page["items"][0]["transaction_id"] == "hot"
        everything = client.get("/api/transactions", params={"flagged": "false"}).json()
        assert everything["total"] == 2

    def test_list_filters(self, client):
        submit_impossible_travel(client)
        assert client.get("/api/transactions?status=OPEN&risk_level=HIGH").json()["total"] == 1
        assert client.get("/api/transactions?status=CLEARED").json()["total"] == 0
        assert client.get("/api/transactions?account_id=other").json()["total"] == 0
        assert client.get("/api/transactions?status=BOGUS").status_code == 422
        assert client.get("/api/transactions?limit=0").status_code == 422

    def test_get_one(self, client):
        submit_impossible_travel(client)
        r = client.get("/api/transactions/hot")
        assert r.status_code == 200 and r.json()["city"] == "London"
        assert client.get("/api/transactions/missing").status_code == 404

    def test_review_flow(self, client):
        submit_impossible_travel(client)
        r = client.post("/api/transactions/hot/review",
                        json={"status": "REVIEWED", "reviewer": "ana", "note": "calling customer"})
        assert r.status_code == 200 and r.json()["review_status"] == "REVIEWED"
        r = client.post("/api/transactions/hot/review",
                        json={"status": "CLEARED", "reviewer": "ana", "note": "customer travelling"})
        body = r.json()
        assert body["review_status"] == "CLEARED" and body["reviewed_by"] == "ana"
        assert [e["status"] for e in body["reviews"]] == ["REVIEWED", "CLEARED"]
        assert client.get("/api/transactions?status=CLEARED").json()["total"] == 1

    def test_review_errors(self, client):
        submit_impossible_travel(client)
        ok = {"status": "CLEARED", "reviewer": "ana"}
        assert client.post("/api/transactions/missing/review", json=ok).status_code == 404
        assert client.post("/api/transactions/warmup/review", json=ok).status_code == 409  # not flagged
        assert client.post("/api/transactions/hot/review",
                           json={"status": "APPROVED", "reviewer": "ana"}).status_code == 422
        assert client.post("/api/transactions/hot/review",
                           json={"status": "CLEARED", "reviewer": ""}).status_code == 422


class TestMetadata:
    def test_health(self, client):
        assert client.get("/api/health").json() == {"status": "ok"}

    def test_rules(self, client):
        body = client.get("/api/rules").json()
        names = [r["name"] for r in body["rules"]]
        assert names == ["velocity", "unusual_amount", "impossible_travel"]
        velocity = body["rules"][0]
        assert velocity["parameters"]["max_transactions"] == 5
        assert body["thresholds"] == {"medium": 40, "high": 70, "alert": 70}

    def test_stats(self, client):
        submit_impossible_travel(client)
        stats = client.get("/api/stats").json()
        assert stats["total"] == 2 and stats["open"] == 1 and stats["alerts_sent"] == 1

    def test_cors_for_console_origin(self, client):
        r = client.options("/api/transactions", headers={
            "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
        assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
