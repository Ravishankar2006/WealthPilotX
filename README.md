# WealthPilotX

An AI-driven financial decision-support platform. It analyses a self-reported financial profile
alongside market and macroeconomic data to produce personalised, explainable, risk-aware portfolio
recommendations.

**WealthPilotX is an educational and research tool.** It does not provide licensed financial,
investment, tax or legal advice, does not execute trades, and does not hold funds. Model outputs and
past performance do not guarantee future results.

---

## Current status — all six milestones complete

| Milestone | Scope | Status |
|---|---|---|
| M1 — Foundation | Docker, PostgreSQL, FastAPI, React, authentication, financial profile, disclaimers, CI | Complete |
| M2 — Data Platform | Market and economic ingestion behind a provider interface | Complete |
| M3 — ML | Feature engineering, risk model, market prediction, model registry | Complete |
| M4 — Recommendation | Asset scoring, recommendation engine, optimisation, backtesting | Complete |
| M5 — UI | Dashboard, charts, risk and portfolio visualisation | Complete |
| M6 — Advanced AI & Hardening | SHAP explainability, fairness report, drift monitoring, metrics, security review | Complete |

Phase plans: [`PHASE-1-FOUNDATION.md`](Docs/PLAN/PHASE-1-FOUNDATION.md) ·
[`PHASE-2-DATA-PLATFORM.md`](Docs/PLAN/PHASE-2-DATA-PLATFORM.md) ·
[`PHASE-3-ML.md`](Docs/PLAN/PHASE-3-ML.md) ·
[`PHASE-4-RECOMMENDATION.md`](Docs/PLAN/PHASE-4-RECOMMENDATION.md) ·
[`PHASE-5-UI.md`](Docs/PLAN/PHASE-5-UI.md) ·
[`PHASE-6-HARDENING.md`](Docs/PLAN/PHASE-6-HARDENING.md). Model cards are in
[`Docs/MODELS/`](Docs/MODELS/), and the M6 security review is in
[`SECURITY-REVIEW.md`](Docs/SECURITY-REVIEW.md). The product specification is
`Docs/PRD/WealthPilotX_PRD_v2.docx`.

## Running it locally

You need Docker and Docker Compose. Nothing else — the container is the runtime of record, and
backend commands should never be run against a host interpreter.

```bash
cp .env.example .env

# Fill in the two secrets. Generate each with:
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

docker compose up --build
```

That is the whole setup. Migrations run automatically on API start, and the scheduler container
seeds the tracked asset universe before taking over ingestion on its cron.

If ports 5432, 8000 or 5173 are already taken on your machine, override `POSTGRES_PORT`,
`API_PORT` and `WEB_PORT` in `.env` — and update `VITE_API_BASE_URL` and `CORS_ORIGINS` to match
the ports you chose.

Once it is up: register, complete the financial profile, then run the risk assessment and generate
a portfolio from the dashboard. Neither runs on its own — both call models and share a 10 req/min
budget, so they wait for an explicit click.

| Service | URL |
|---|---|
| Web | http://localhost:5173 |
| API | http://localhost:8000/api/v1 |
| API docs | http://localhost:8000/docs (disabled in production) |
| Health | http://localhost:8000/api/v1/health |

## Market and economic data

Ingestion is a set of CLI jobs. The scheduler container runs them on a cron; you can run any of them
by hand exactly as it does.

```bash
# Insert or refresh the 32 tracked symbols. Idempotent.
docker compose exec api python -m app.jobs seed-assets

# Daily OHLCV. Resumes from the last stored bar unless you ask for history.
docker compose exec api python -m app.jobs ingest-market
docker compose exec api python -m app.jobs ingest-market --backfill-days 730
docker compose exec api python -m app.jobs ingest-market --symbols SPY QQQ

# The five FRED macro series.
docker compose exec api python -m app.jobs ingest-economic
```

Exit codes are meaningful: `0` success, `1` nothing ingested, `2` partial — some symbols failed.
A partial run is deliberately not a success, because the gap it leaves is otherwise invisible.

### Providers

All market and economic access goes through the interfaces in `app/providers/` (PRD §7.3). Yahoo
Finance is unofficial and can change without notice, so **nothing outside `app/providers/yahoo.py`
may import `yfinance`** — a test enforces this.

| Setting | Values | Default |
|---|---|---|
| `MARKET_DATA_PROVIDER` | `synthetic`, `yahoo` | `synthetic` |
| `ECONOMIC_DATA_PROVIDER` | `synthetic`, `fred` | `synthetic` |
| `FRED_API_KEY` | required only for `fred` | unset |

`synthetic` is the default so a fresh clone has working data with no network and no API keys. It is
a seeded random walk — deterministic, useful for development and tests, and never a basis for a
model that ships. Rows it produces are stamped `source = "synthetic"` so they are one query away
from being found.

Ingestion state is reported on `/api/v1/health` under `ingestion`: a failed or stale job sets
`status` to `degraded` while leaving the HTTP code at 200, because the API still serves stored data
perfectly well and taking it out of rotation over a background job would turn a freshness problem
into an outage.

## Models

Training and promotion are separate commands, deliberately. PRD §10.5 requires manual review before
a model is promoted, and a training script that promotes what it just built is how an unreviewed
model reaches users.

```bash
# Train. Writes an EXPERIMENT row plus an artifact — never promotes.
docker compose exec api python -m app.jobs train-risk
docker compose exec api python -m app.jobs train-prediction

# Review what was measured, then promote. Refused if it loses to the incumbent.
docker compose exec api python -m app.jobs models
docker compose exec api python -m app.jobs promote risk_classifier v1

# Write a prediction row per tracked asset. Idempotent.
docker compose exec api python -m app.jobs predict

# Reserve a period so §19's backtest has genuinely out-of-sample data.
docker compose exec api python -m app.jobs train-prediction --holdout-days 200

# Backtest the latest portfolio against SPY, with transaction costs reported.
docker compose exec api python -m app.jobs backtest --months 12
```

Artifacts live in a named Docker volume shared by the API and the scheduler, with their metadata,
training range, git commit and a SHA-256 checksum on the `models` table. Loading verifies the
checksum: a file replaced underneath its row would otherwise serve predictions the stored metrics
never described.

**Read [`Docs/MODELS/risk-classifier.md`](Docs/MODELS/risk-classifier.md) before quoting any risk
metric.** The risk model is trained on labels generated by a rubric in this repository, so its
accuracy measures fidelity to that rubric — not whether the rubric is right about real people. The
[market predictor card](Docs/MODELS/market-predictor.md) is equally direct about a near-zero R²
being the expected, honest outcome for one-month return prediction, and the
[portfolio optimizer card](Docs/MODELS/portfolio-optimizer.md) covers why expected return and risk
are estimates rather than forecasts.

### A note on `PROFILE_ENCRYPTION_KEY`

Income and savings are encrypted at the application layer before they reach PostgreSQL. Changing
this key makes every stored value undecryptable — re-encrypting existing rows is a migration, not an
environment change.

## Tests

```bash
# Backend — the test database is created automatically on first run
docker compose run --rm api pytest -q

# Frontend
docker compose run --rm --no-deps web npm test
```

Backend tests run against real PostgreSQL, not SQLite. The schema uses native enums and UUID
columns, so a SQLite substitute would exercise a different schema than the one that ships.

They run against a separate `_test` database and truncate tables between tests. `conftest.py`
refuses to start against any database whose name does not contain `test`, so a misconfigured
`TEST_DATABASE_URL` fails loudly instead of wiping your development data.

## Migrations

```bash
docker compose exec api alembic revision --autogenerate -m "describe the change"
docker compose exec api alembic upgrade head
```

Always read an autogenerated migration before applying it. Alembic misses enum changes, server
defaults and index renames often enough that unreviewed migrations are a reliable source of
production surprises.

## A note on the interface

Charts use `plotly.js-basic-dist-min`, loaded through a dynamic import so the ~377 KB (gzipped) of
plotting library never blocks the first paint — the application itself is 68 KB.

Colour is never the only carrier of meaning (PRD §16.5). Risk categories show a filled-segment glyph
and a word alongside the colour; predicted trends show an arrow and a direction; allocation slices
are labelled directly rather than through a legend. The interface is fully readable in greyscale,
which is checked as part of the QA pass.

Where a number cannot be computed the interface says so and why, rather than rendering a dash that
reads as zero. The portfolio page reports no portfolio value at all, because WealthPilotX does not
hold funds or track holdings — inventing a balance would be the alternative.

## How the models explain themselves

Two model surfaces, two different kinds of explanation, and the difference is deliberate.

**Market predictions are decomposed with TreeSHAP.** A gradient-boosted ensemble over twenty
correlated features has no rule to read off, so the attribution has to come from the model. The
contributions are exact — XGBoost implements TreeSHAP natively, so the `shap` package is not a
dependency — and they sum to the prediction. Every explanation names the model version that made the
prediction and rebuilds the features as of that prediction's own date, rather than explaining what
today's model would say.

**Risk scores are not decomposed with SHAP**, because they do not need to be. The score served to a
user is a weighted sum of six documented factors, so each factor's contribution is already exact.
Approximating a number that is already known would look more sophisticated and be less accurate.

The system also watches itself. `python -m app.jobs monitor` runs nightly and records feature-drift
(PSI) and rolling prediction error against thresholds that were written down before any measurement
was taken. An alert logs and stores a row; it never retrains or promotes anything on its own.

## A note on the fairness report

`/fairness/report` reports risk and allocation outcomes across age, income, literacy and experience
bands. Groups smaller than 20 users are withheld — server-side, before serialisation — and rendered
as "not reported" rather than as zero, because a 0% rate on a group of three describes three
identifiable people.

Age and income feed the risk rubric by design, so a difference across those bands is expected and
the page says so. Reading a disparity there as evidence of a fault would manufacture a finding out of
the product working as documented.

## Testing

| Layer | What it covers | Command |
|---|---|---|
| Backend unit, model and integration | Business logic, feature engineering, model shape and range checks, API endpoints against a real PostgreSQL | `docker compose exec api pytest` |
| Frontend component | Rendering and the key flows against mocked responses | `docker compose exec web npm test` |
| End-to-end | Register → profile → risk → recommendation → dashboard, against the running stack with nothing stubbed | `cd frontend && npm run e2e` |

Coverage floors are enforced in CI — 89% backend, 75% frontend statements. They exist to stop
coverage falling, not to be chased: the uncovered remainder is mostly the Yahoo and FRED providers,
which CI is forbidden to call (§7.3).

The end-to-end layer needs the stack up and a promoted risk model. `npm run e2e:install` fetches the
browser the first time. It is the only suite that exercises the browser, the API, the database and
the models together — every other test stubs one side of a boundary, which is how a 500 on
`/fairness/report` once passed three green test layers at the same time.

## What is deliberately not here

Trade execution, brokerage or bank account linkage, custody of funds, and anything framed as
licensed financial advice are permanent non-goals (PRD §5). The educational positioning is what keeps
the project outside investment-adviser regulation — see PRD §17 before changing how the product
describes itself.

## Layout

```
backend/          FastAPI service
  app/core/       Config, security, crypto, logging, errors, rate limiting
  app/models/     SQLAlchemy models
  app/api/v1/     Routes — auth, user, health, market, risk, portfolio, recommendation
  app/providers/  Data-provider abstraction (§7.3) — the only place a vendor SDK lives
  app/ml/         Features, models, registry, evaluation, optimizer, backtesting
  app/services/   Business logic, including the ingestion pipeline
  app/jobs/       CLI jobs and the scheduler entrypoint
  tests/          Pytest — unit, integration, security
frontend/         React + TypeScript + Tailwind + Plotly
  src/api/        Typed client with refresh-on-401, and per-endpoint wrappers
  src/components/ Charts (lazy Plotly) and the accessible primitives
  src/lib/        format.ts — every user-visible number, in one place
  src/pages/      Landing, Login, Register, Onboarding, Dashboard, Risk,
                  Market, Portfolio, Recommendation, Settings
Docs/PRD/         Product requirements (source of truth)
Docs/PLAN/        Phase plans
Docs/MODELS/      Model cards — what each model is, and what its metrics do not mean
```
