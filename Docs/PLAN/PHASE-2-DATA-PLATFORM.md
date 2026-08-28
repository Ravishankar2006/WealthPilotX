# Phase 2 — Data Platform (PRD Milestone M2)

**Estimated duration:** 2 weeks solo/part-time (PRD §22)
**Status:** Complete — see §11.
**Exit condition:** A scheduled job ingests daily OHLCV for every tracked asset and the five FRED
macro series into PostgreSQL, cleans and audits what it writes, survives a provider outage without
silently skipping a day, and serves the stored history through `/api/v1/market/*` — with CI green
and no `yfinance` import anywhere outside the provider layer.

---

## 1. Scope

### In scope

| PRD ref | What ships |
|---|---|
| §7.3 | `MarketDataProvider` / `EconomicDataProvider` interfaces; Yahoo, FRED and synthetic implementations |
| FR-04 | Daily OHLCV ingestion, incremental + backfill, retry with backoff, alertable failure |
| FR-05 | FRED ingestion for INFLATION / INTEREST_RATE / GDP / UNEMPLOYMENT / FX_RATE |
| FR-06 | Cleaning: missing values, duplicate removal, outlier handling, logged data-quality report |
| §12 | `assets`, `market_data`, `economic_indicators` tables (+ `ingestion_runs`, see §4) |
| §13.2 | `GET /market/assets`, `GET /market/{symbol}` — cursor-paginated per §13.1 |
| §16.4 | Ingestion state surfaced on `/health`; every job run logged with a correlation ID |
| §22 M2 | Scheduler service in Compose; jobs runnable by hand as CLI commands |

### Deliberately deferred

- **FR-07 feature engineering** (moving averages, RSI, MACD, Bollinger, volatility, momentum, lags,
  correlations) — PRD §22 places it in M3, alongside the models that consume it.
- **`GET /market/{symbol}/prediction`** — needs the XGBoost model (M3).
- **Any frontend work.** M2 is a data platform; charts and the market view are M5. The endpoints
  ship with tests and OpenAPI documentation, not with a page.
- **An economic-indicators HTTP endpoint.** §13.2 does not list one, and the only M3 consumer reads
  the table directly. It ships with M5 if the dashboard needs it.

### Judgment call to confirm

**FR-06 is split across M2 and M3.** The requirement lists cleaning *and* "normalize numerical
features / encode categorical variables / generate technical indicators" in one breath, but §22
assigns "cleaning" to M2 and "feature engineering" to M3. This phase implements the cleaning half —
the part that decides what is allowed into the database — and leaves normalization, encoding and
indicators to M3, where the model that consumes them defines their shape. Normalizing before a model
exists means guessing at the scaler and storing a number that has to be recomputed anyway.

---

## 2. Decisions to lock before coding

Answers chosen at kickoff are in **bold**. Items 1–3 were put to the maintainer directly.

1. **Asset universe** (PRD §27, deferred from Phase 1 §2.6) — **~32 liquid US symbols**: 25 ETFs
   spanning broad equity, style, international, treasury, credit, commodity, REIT and sector
   exposure, plus 7 large-cap equities. Listed in §5 below. Rationale: wide enough that the M4
   optimizer has genuine diversification to find, small enough that a daily pull is ~32 provider
   calls. Seeded as data, extensible without a migration.
2. **Job scheduling** — **CLI jobs invoked by a dedicated scheduler container.** Every job is
   `python -m app.jobs <command>` and runs identically by hand, in Compose and in CI. Nothing
   timer-driven runs inside the API process, so scheduling does not duplicate when the API scales
   horizontally (§16.4).
3. **Live provider data** — **optional live, deterministic providers in tests.** The suite never
   touches the network. This landed as `providers/synthetic.py` — seeded generators — rather than
   recorded fixture payloads, for two reasons: seeding makes the output stable enough for a test to
   assert on actual values, and a generator covers any window an incremental-ingestion test asks
   for, which a recorded payload cannot. Vendor payload *parsing* is still tested against literal
   recorded responses, in `test_providers.py`. Yahoo and FRED are used when configured.
4. **Outliers are flagged, not dropped.** A 22% single-day fall is a real market event, not bad
   data. Rows that are *structurally* impossible (non-positive prices, `high < low`, a null in an
   OHLC column) are rejected; extreme-but-coherent moves are counted in the quality report and
   stored. Dropping them would teach the M3 model that crashes do not happen.
5. **Ingestion is idempotent and incremental.** Writes are `INSERT … ON CONFLICT (asset_id, date) DO
   UPDATE`, and each run starts from the latest stored date per symbol. Re-running a job is always
   safe, which is what makes retry-after-failure a viable recovery story.
6. **Provider failure fails loudly.** A symbol that cannot be fetched after retries marks the run
   `PARTIAL` or `FAILED` and degrades `/health`. FR-04 explicitly forbids the silent skip.

---

## 3. Target layout added this phase

```
backend/app/
├── providers/                   # §7.3 — the only place a vendor SDK is imported
│   ├── base.py                  # protocols, DTOs, ProviderError hierarchy
│   ├── retry.py                 # exponential backoff with jitter
│   ├── yahoo.py                 # the sole `import yfinance` in the codebase
│   ├── fred.py                  # httpx against the FRED observations API
│   ├── synthetic.py             # deterministic offline bars and series
│   └── registry.py              # settings-driven provider selection
├── models/
│   ├── asset.py                 # assets
│   ├── market_data.py           # market_data
│   ├── economic_indicator.py    # economic_indicators
│   └── ingestion_run.py         # ingestion_runs
├── services/ingestion/
│   ├── cleaning.py              # FR-06 — the rules and the QualityReport
│   ├── market.py                # FR-04 orchestration
│   ├── economic.py              # FR-05 orchestration
│   └── runs.py                  # ingestion_runs bookkeeping + health summary
├── services/market_service.py   # read path behind the endpoints
├── api/v1/market.py             # GET /market/assets, GET /market/{symbol}
├── data/asset_universe.py       # the seed list from §5
└── jobs/
    ├── __main__.py              # `python -m app.jobs …`
    └── scheduler.py             # the scheduler container's entrypoint
```

---

## 4. Data model for this phase

From PRD §12, plus one table the PRD omits and one column it does not name:

- `assets` — id UUID PK · symbol text UNIQUE NOT NULL · name text · asset_type enum
  (EQUITY/ETF/BOND/COMMODITY/INDEX) · **asset_class** enum (EQUITY/BOND/COMMODITY/REAL_ESTATE/CASH)
  · currency text · exchange text · is_active bool · created_at
- `market_data` — id bigint PK · asset_id FK→assets ON DELETE CASCADE · date date ·
  open/high/low/close/adj_close numeric(18,6) · volume bigint · source text · ingested_at ·
  UNIQUE(asset_id, date)
- `economic_indicators` — id bigint PK · series enum(INFLATION/INTEREST_RATE/GDP/UNEMPLOYMENT/
  FX_RATE) · date date · value numeric(18,6) · source text · ingested_at · UNIQUE(series, date)
- `ingestion_runs` *(not in PRD §12)* — id UUID PK · job text · status enum(RUNNING/SUCCESS/PARTIAL/
  FAILED) · started_at · finished_at · rows_written int · symbols_ok int · symbols_failed int ·
  quality jsonb · error text · correlation_id text

**Why `asset_class` is added:** FR-11 requires "allocation constraints such as maximum weights by
asset class". `asset_type` describes the instrument (an aggregate bond ETF is an `ETF`), not the
exposure (`BOND`). Without a second column, M4 has to hard-code a symbol→class lookup table, which
is exactly the static mapping FR-10 forbids elsewhere.

**Why `ingestion_runs` is added:** FR-04 demands a *logged, alertable* failure and FR-06 demands a
data-quality report "logged for audit". A log line satisfies neither on its own — `/health` cannot
query stdout. This table is what the health endpoint reads.

Both additions should be folded back into the PRD's §12 table, as `tos_accepted_at` and
`refresh_tokens` were in Phase 1.

---

## 5. Asset universe

Seeded by `python -m app.jobs seed-assets`, idempotent, safe to re-run after editing the list.

| Class | Symbols |
|---|---|
| Broad equity | SPY, VTI, QQQ, IWM, VTV, VUG |
| International equity | VXUS, VEA, VWO |
| Treasuries | SHY, IEF, TLT |
| Credit | LQD, HYG, AGG, TIP |
| Sector equity | XLK, XLF, XLE, XLV, XLU |
| Commodity | GLD, SLV, DBC |
| Real estate | VNQ |
| Large-cap equity | AAPL, MSFT, JNJ, JPM, XOM, PG, KO |

32 symbols. `BRK-B` is deliberately absent: its Yahoo ticker punctuation differs by provider, and a
symbol that changes shape when the provider is swapped undermines the point of §7.3's abstraction.

---

## 6. Task stages

### Stage 1 — Provider abstraction (§7.3)
- [x] `MarketDataProvider` / `EconomicDataProvider` protocols with typed DTOs (`PriceBar`,
      `SeriesPoint`, `AssetMetadata`) — no pandas objects cross the interface boundary
- [x] `ProviderError` hierarchy: `ProviderUnavailable`, `SymbolNotFound`, `ProviderRateLimited`
- [x] Retry with exponential backoff + jitter, capped attempts, only on retryable errors
- [x] `YahooMarketDataProvider` — the one and only `import yfinance`
- [x] `FredEconomicDataProvider` — httpx, API key from settings, series-ID mapping
- [x] Synthetic providers for tests and offline development
- [x] Registry selecting an implementation from settings

### Stage 2 — Schema and universe
- [x] Four models, one hand-reviewed Alembic revision, downgrade drops its enum types
- [x] `asset_universe.py` seed list and an idempotent `seed-assets` job
- [x] `TRUNCATE` list in `conftest.py` extended to the new tables

### Stage 3 — Cleaning and quality (FR-06)
- [x] Reject rows with a null in a required OHLC column, non-positive prices, or `high < low`
- [x] Deduplicate on `(asset_id, date)`, last value wins
- [x] Flag outliers by absolute log return against a configurable threshold — count, do not drop
- [x] `QualityReport` (rows in/out, null rate, duplicates, outliers) logged as structured JSON and
      persisted to `ingestion_runs.quality`

### Stage 4 — Ingestion jobs (FR-04, FR-05)
- [x] `ingest-market` — incremental per symbol, `--backfill-days`, `--symbols` override
- [x] `ingest-economic` — five series, as-of dates preserved, revisions overwrite by `(series, date)`
- [x] Upserts, so a re-run never duplicates and a partial run can simply be repeated
- [x] Run bookkeeping: RUNNING → SUCCESS / PARTIAL / FAILED, with per-symbol failure counts
- [x] Scheduler container: market after the US close, economic daily

### Stage 5 — Read API (§13.2)
- [x] `GET /market/assets` — cursor-paginated, `asset_type` / `asset_class` filters
- [x] `GET /market/{symbol}` — asset metadata + cursor-paginated OHLCV, `start`/`end` window
- [x] Unknown symbol → 404 in the §13.1 envelope; unauthenticated → 401
- [x] `/health` reports per-job ingestion status and data staleness

### Stage 6 — CI and close-out
- [x] Tests green: provider retry, cleaning rules, idempotent ingest, outage handling, endpoints
- [x] `.env.example`, README and this checklist updated
- [x] Manual QA pass against §8

---

## 7. Test coverage required to exit the phase

| Layer | Must cover |
|---|---|
| Unit | Backoff schedule; cleaning rules incl. outlier flagging; cursor encode/decode; FRED and Yahoo payload parsing from fixtures; series-ID mapping |
| Integration | Seed is idempotent; ingest writes rows; re-ingest writes no duplicates; provider outage → run FAILED + retries + `/health` degraded; economic revision overwrites in place |
| API | Pagination cursors, filters, date windows, 404 unknown symbol, 401 unauthenticated |
| Security | Market endpoints require auth; job logs carry correlation IDs and no profile PII |

---

## 8. Manual QA checklist (§20 milestone gate)

1. `docker compose up` on a clean volume → migrations apply, scheduler starts, API healthy.
2. `python -m app.jobs seed-assets` → 32 assets; run it twice → still 32.
3. `python -m app.jobs ingest-market --backfill-days 400` against the fixture provider → rows land,
   quality report logged; run it again → zero new rows, zero duplicates.
4. Point the market provider at an unreachable host → run ends FAILED, backoff attempts visible in
   the logs, `/health` reports the failure rather than reporting healthy.
5. `GET /api/v1/market/assets?limit=10` → 10 rows plus a `next_cursor`; follow the cursor to the end.
6. `GET /api/v1/market/SPY?start=…&end=…` → bars in range, newest first, correct `next_cursor`.
7. `GET /api/v1/market/NOTREAL` → 404 in the standard envelope.
8. Same call with no bearer token → 401.
9. `grep -rn "^\s*import yfinance\|from yfinance" backend/app --include=*.py` → exactly one hit,
   in `app/providers/yahoo.py`. (A plain word-grep also matches prose in docstrings and comments;
   `TestProviderIsolation` does the real check by parsing each module's imports.)

---

## 9. Risks specific to this phase

| Risk | Mitigation |
|---|---|
| Yahoo changes or rate-limits without notice (§7.3) | The provider seam is the whole point; fixtures mean the suite never depends on it, and a swap touches one file |
| A silent partial ingest poisons M3's training data | Runs are recorded with per-symbol counts; PARTIAL is a distinct status from SUCCESS, and `/health` surfaces it |
| Outlier rules that are too aggressive erase real crashes | Decision §2.4: structural rejects only, extremes flagged and kept |
| Backfilling 32 symbols trips a rate limit | Sequential fetches with backoff; backfill is an explicit flag, not the default path |
| Numeric drift between provider floats and stored decimals | `numeric(18,6)` and `Decimal` end to end; no float arithmetic on prices |

---

## 10. What Phase 3 needs from this phase

M3 (ML) starts with feature engineering over `market_data` and `economic_indicators`. It depends on
this phase delivering: clean, gap-free daily series with no duplicate `(asset_id, date)` rows; the
`assets` universe with `asset_class` populated; ingestion runs it can date-bound a training set
against; and the provider seam, so a backtest can be re-run against a different data source without
touching the feature code.

---

## 11. Verification log

Recorded 2026-08-28, against the stack running in Docker.

### Green

| Check | Result |
|---|---|
| Backend suite | 162 passed (was 62 at end of M1) |
| `ruff check` / `ruff format --check` | clean |
| `mypy app` | no issues, 57 files |
| Migration up → down → up | clean, including the four new enum types |
| Models vs. migration | `alembic revision --autogenerate` produces an empty diff |
| Clean-volume `docker compose up` | 14s to a running stack; 32 assets seeded, 16,736 bars and 60 macro observations loaded, no manual step |
| Backfill 400 days, 32 symbols | SUCCESS, 9,184 rows, 3.3s against the synthetic provider |
| Re-run of the same backfill | 0 duplicate `(asset_id, date)` rows |
| Incremental re-run | 160 rows — the 5-day resync overlap, as designed |
| Provider failure (`--provider fred`, no key) | run FAILED, exit code 1, `/health` → `degraded` |
| Scheduler container | seeds on boot, registers both cron jobs, next wakeup logged |
| Bootstrap guard on restart | skips the backfill; no repeat provider calls |
| Endpoint QA (steps 5–8) | 4 pages × 10, 32 unique symbols in order; window inclusive; 404 and 401 in the standard envelope |
| §7.3 isolation | `yfinance` imported in exactly one module, enforced by a test |

### Bugs this stage caught

1. **A successful ingestion reported `-32` rows written.** psycopg reports
   `rowcount = -1` for a multi-row `INSERT`, and `result.rowcount or 0` passes `-1`
   straight through — `-1` is truthy. Every symbol contributed `-1` to the run
   total. The data was correct throughout; only the number an operator reads was
   wrong, and it was wrong in the direction that looks like nothing happened.
   Rows are now counted with `RETURNING`, with a regression test on
   `run.rows_written`.
2. **Three schema drifts between the models and the hand-written migration.**
   `mapped_column(unique=True, index=True)` renders as one unique *index*, not a
   constraint plus a plain index — the migration created both. The two composite
   `DESC` indexes existed only in the migration, so autogenerate proposed dropping
   them on the next diff. All three are fixed, and CI now fails on a non-empty
   autogenerate diff, so the next drift is caught before it lands rather than at
   the next migration.
3. **A hand-edited pagination cursor returned 500.** `date.fromisoformat` on a
   decodable-but-not-a-date cursor raised `ValueError` past the error envelope. Now
   a 400 `invalid_cursor`, like the malformed-base64 path already was.

### Also fixed while verifying

- **A fresh stack had no price history for up to 14 hours.** The scheduler seeded
  assets and then waited for the 23:30 UTC window, so "one command to a working
  app" was true of the API and false of the data. Added `python -m app.jobs
  bootstrap`, which backfills *only* when `market_data` is empty — an unconditional
  backfill would re-run on every container restart, which is precisely how an
  unofficial API decides to rate-limit you.
- The `_run` wrapper in the scheduler swallows and logs a job exception rather than
  letting it propagate: APScheduler removes a job whose callable raises, which would
  have silently disabled ingestion after one bad night — the exact failure mode
  FR-04 exists to prevent.

### Notes

- **QA step 9's word-grep matches prose.** `grep -ri yfinance` hits docstrings and
  comments in `providers/registry.py` as well as the real import. The authoritative
  check is `TestProviderIsolation`, which parses each module's import statements;
  it was verified to fail by temporarily adding `import yfinance` to
  `market_service.py`.
- **Ports** — as in M1, this machine uses 55432 / 8010 / 5183 rather than the
  conventional defaults kept in `.env.example`.
- **Branch protection** is declined as a standing decision, not outstanding work —
  see Phase 1 §10 for the reasoning and the conditions that should reopen it.
