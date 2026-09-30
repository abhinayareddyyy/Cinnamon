# Fraud Rule Engine with Review Console

A pluggable fraud rule engine (Python / FastAPI / SQLite) that scores
transactions, persists fraud flags, alerts through **AWS SES or SNS** when a
high-risk threshold is crossed, and a **React reviewer console** where
analysts mark flagged transactions as *reviewed* or *cleared*.

```
             POST /api/transactions
                      │
                      ▼
┌──────────────────────────────── FraudService ────────────────────────────────┐
│ 1. load account history ──► SQLiteRepository                                 │
│ 2. evaluate ─────────────► RuleEngine ──► [VelocityRule, UnusualAmountRule,  │
│                                            ImpossibleTravelRule, plug-ins…]  │
│ 3. save txn + fraud_flags ─► SQLiteRepository                                │
│ 4. score ≥ alert threshold ─► Notifier (SES email │ SNS topic │ log)         │
└──────────────────────────────────────────────────────────────────────────────┘
                      │
      GET /api/transactions, POST /api/transactions/{id}/review
                      ▼
            React Review Console (Vite)
```

## Requirements coverage

| Requirement | Where |
|---|---|
| Rule engine for transaction risk evaluation | `backend/fraud_engine/engine.py` |
| Velocity rule | `backend/fraud_engine/rules/velocity.py` |
| Unusual amount rule | `backend/fraud_engine/rules/amount.py` |
| Impossible geographical location rule | `backend/fraud_engine/rules/geo.py` |
| New rules without modifying the engine | `rules/base.py` registry + `RULE_PLUGINS`; example in `backend/plugins/blocked_merchant.py` |
| Persist transactions and fraud flags | `backend/fraud_engine/repository.py` (tables `transactions`, `fraud_flags`, `review_events`) |
| React reviewer console, flagged list | `frontend/src/App.jsx`, `components/TransactionTable.jsx` |
| Mark reviewed / cleared | `components/TransactionDetail.jsx` → `POST /api/transactions/{id}/review` |
| SES / SNS notification on high risk | `backend/fraud_engine/notifications.py`, triggered in `service.py` |

## Quick start

Prerequisites: Python 3.10+, Node 20+.

```bash
# Backend
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python -m scripts.seed_demo --reset          # optional: demo data with every fraud pattern
uvicorn fraud_engine.api:create_app --factory --reload --port 8000
```

```bash
# Frontend (second terminal)
cd frontend
npm install
npm run dev                        # http://localhost:5173, proxies /api to :8000
```

To serve everything from one origin instead, run `npm run build` and start the
backend with `FRONTEND_DIST=../frontend/dist`.

Interactive API docs are at http://localhost:8000/docs.

## How scoring works

Each rule returns a `RuleResult` (`triggered`, `score` 0–100, `reason`,
JSON `details` as evidence). The engine combines the scores of the rules that
fired with a **noisy-OR**, `100 × (1 − Π(1 − weightᵢ·scoreᵢ/100))`: two
moderate signals reinforce each other (50 + 50 → 75), but the total never
exceeds 100.

| Level | Score (default) | Effect |
|---|---|---|
| LOW | < 40 | Flagged only if a rule fired |
| MEDIUM | 40 – 69.99 | Flagged → review queue |
| HIGH | ≥ 70 | Flagged → review queue **and** SES/SNS alert |

Any transaction where at least one rule fired is *flagged* and enters the
queue with status `OPEN`. Reviewers move it to `REVIEWED` (investigated;
confirmed suspicious or escalated) or `CLEARED` (false positive), and can
re-open it. Every decision is appended to `review_events` as an audit trail.

### Built-in rules (all parameters configurable)

| Rule | Triggers when | Score |
|---|---|---|
| `velocity` | more than `max_transactions` (5) from one account within `window_minutes` (10), current one included | 50 for the first over the limit, +10 each further one |
| `unusual_amount` | z-score ≥ 3 against the last 50 same-currency amounts (≥ 5 needed; std floored at 50 % of the mean), **or** amount ≥ `absolute_limit` (10,000) | 40 at z = 3, +10 per std dev; 70 for the hard limit |
| `impossible_travel` | implied speed from the previous located transaction > `max_speed_kmh` (900), ignoring moves < 100 km | 60 → 100 as speed grows; 100 for simultaneous |

Design choices worth knowing:

* **Rules are pure functions** of the transaction plus a read-only
  `RuleContext` (account history, newest first). The engine loads history once
  and shares it, so rules add no database queries.
* **Fault isolation**: a rule that raises is logged and recorded with
  `error=True`, and the other rules still score the transaction.
* **Alerts never lose data**: the transaction is stored before notifying. If
  SES or SNS fails, the failure is logged and `notified_at` stays empty, but the
  transaction is still in the queue.
* **Concurrency**: read-history → evaluate → insert is serialized so parallel
  requests for one account cannot hide each other from the velocity rule
  (single process; see *Production notes*).

## Adding a rule (no engine changes)

Create a module anywhere on the Python path:

```python
from fraud_engine.rules import Rule, register_rule

@register_rule
class NightOwlRule(Rule):
    name = "night_owl"
    description = "Flags transactions between 01:00 and 05:00 UTC."

    def __init__(self, score: float = 30):
        self.score = score

    def evaluate(self, transaction, context):
        if 1 <= transaction.timestamp.hour < 5:
            return self.hit(self.score, "Transaction at an unusual hour")
        return self.no_hit()
```

Then load it with `RULE_PLUGINS=my_package.night_owl`. It shows up in scoring,
`GET /api/rules`, stored flags and the console (rule names are humanized
automatically). The bundled example is enabled with
`RULE_PLUGINS=plugins.blocked_merchant`.

Tune or disable any rule with `RULE_CONFIG` (inline JSON or a path to a JSON file):

```json
{
  "velocity": {"window_minutes": 5, "max_transactions": 3},
  "unusual_amount": {"absolute_limit": 5000, "weight": 0.8},
  "impossible_travel": {"enabled": false}
}
```

Unknown rule names in `RULE_CONFIG` fail at startup, so typos can't go unnoticed.

## AWS notifications

| Variable | Example |
|---|---|
| `NOTIFIER_BACKEND` | `log` (default), `ses` or `sns` |
| `AWS_REGION` | `us-east-1` |
| `SES_SENDER` | `fraud-alerts@yourbank.com` (must be SES-verified) |
| `SES_RECIPIENTS` | `analyst1@yourbank.com,oncall@yourbank.com` |
| `SNS_TOPIC_ARN` | `arn:aws:sns:us-east-1:123456789012:fraud-alerts` |
| `ALERT_THRESHOLD` | defaults to `HIGH_RISK_THRESHOLD` (70) |
| `CONSOLE_URL` | `https://fraud-console.internal`: alerts deep-link to `/?txn=<id>` |

Credentials come from boto3's standard chain (env vars, `~/.aws`, IAM role).
Minimum IAM permissions: `ses:SendEmail` or `sns:Publish` on the topic. SNS
messages carry `risk_level` and `account_id` attributes, so subscribers can
use filter policies (e.g. page on-call only for `HIGH`).

Other settings: `FRAUD_DB_PATH`, `MEDIUM_RISK_THRESHOLD`,
`HIGH_RISK_THRESHOLD`, `CORS_ORIGINS`, `FRONTEND_DIST` (see `config.py`).

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/transactions` | Evaluate and store an INR transaction (201; 409 on duplicate id; 422 on invalid input) |
| GET | `/api/transactions?status=OPEN&risk_level=HIGH&account_id=&flagged=true&limit=&offset=` | Review queue |
| GET | `/api/transactions/{id}` | Transaction with flags and review history |
| POST | `/api/transactions/{id}/review` | `{"status": "REVIEWED" \| "CLEARED" \| "OPEN", "reviewer": "...", "note": "..."}` |
| GET | `/api/rules` | Active rules, parameters, thresholds |
| GET | `/api/stats` | Queue counters |
| GET | `/api/health` | Liveness |

Example:

```bash
curl -X POST localhost:8000/api/transactions -H "Content-Type: application/json" -d "{\"account_id\":\"acc-1\",\"amount\":4250,\"currency\":\"INR\",\"merchant\":\"Coffee\",\"latitude\":19.076,\"longitude\":72.8777,\"city\":\"Maharashtra\",\"country\":\"IN\"}"
```

## Testing

```bash
cd backend && python -m pytest        # 121 tests
cd frontend && npm test               # 28 tests
```

* **Backend**: every rule's edge cases (window boundaries, zero std dev,
  currency mixing, simultaneous transactions, missing locations), score
  aggregation, fault isolation, plug-in loading and config, repository
  (atomic writes, duplicates, filters, pagination, audit trail), the service
  pipeline (alert only at threshold, notifier outage, concurrency), the full
  HTTP API, and **SES/SNS requests validated against the real AWS API models**
  with botocore's `Stubber` (no network or credentials needed).
* **Frontend** (Vitest + Testing Library): API client error handling,
  formatting, the review panel (validation, remembered reviewer, error
  display, audit trail), and full App flows against an in-memory fake
  backend: list, filter, open, clear, keyboard selection, `?txn=` deep links,
  error and empty states, test-transaction submission.

> **Windows note:** if the project sits in a very deep folder, Node can fail
> with `ERR_PACKAGE_IMPORT_NOT_DEFINED ... #module-sync-enabled`. That happens
> when `node_modules\vite\package.json` exceeds the 260-character path limit.
> Move the project to a shorter path or enable Windows long paths.

## Project layout

```
backend/
  fraud_engine/
    models.py          Transaction, Location, RiskLevel, ReviewStatus
    rules/base.py      Rule contract, RuleResult, RuleContext, registry, plug-in loader
    rules/velocity.py  rules/amount.py  rules/geo.py
    engine.py          RuleEngine, score aggregation, thresholds
    repository.py      SQLite persistence
    notifications.py   SES / SNS / log notifiers
    service.py         ingest → evaluate → persist → alert pipeline
    config.py          environment settings
    api.py             FastAPI app
  plugins/blocked_merchant.py   example external rule
  scripts/seed_demo.py          demo data
  tests/                        pytest suite
frontend/
  src/App.jsx, api.js, format.js, components/, test/
```

## Production notes

This is built to run and be reviewed locally. Before real deployment:

* **Amounts** are floats; production should use `Decimal` or integer minor units.
* **SQLite + an in-process lock** suits one API process. For horizontal
  scaling, move to PostgreSQL and serialize per account with row locks or a
  partitioned queue (e.g. Kinesis/Kafka keyed by account).
* **Auth**: the API has no authentication. Put it behind SSO and take the
  reviewer's identity from the token rather than a free-text field.
* **Alerts** are sent synchronously after the commit. At volume, publish to a
  queue or outbox table and deliver asynchronously with retries.
