# Phase 3 — ML (PRD Milestone M3)

**Estimated duration:** 2–3 weeks solo/part-time (PRD §22)
**Status:** Complete — see §11.
**Exit condition:** A user with a complete profile gets a deterministic LOW/MEDIUM/HIGH risk
classification with its top-3 contributing factors; every tracked asset has an expected return, a
trend and a confidence produced by a trained XGBoost model; both carry the `model_version` that
produced them; and no model reaches `PRODUCTION` without beating the incumbent — or, first time, a
naive baseline — on a held-out test set.

---

## 1. Scope

### In scope

| PRD ref | What ships |
|---|---|
| FR-07 | Feature engineering: MA, RSI, MACD, Bollinger, volatility, momentum, lags, correlation |
| FR-03 | Risk classification — Random Forest, LOW/MEDIUM/HIGH, score, top-3 factors |
| FR-08 | Market prediction — XGBoost, 20-day expected return, trend, confidence |
| FR-09 | Asset analysis — the six metrics, served on the prediction endpoint |
| §10.5 | Model registry, versioning, promotion gate, `models` table |
| §18 | Evaluation: MAE/RMSE/R² for prediction, accuracy/precision/recall/F1 for risk |
| §12 | `predictions`, `risk_assessments`, `models` tables |
| §13.2 | `POST /risk/analyze`, `GET /risk/latest`, `GET /market/{symbol}/prediction` |
| §13.1 | 10 req/min on `/risk/analyze` — the expensive bucket, already built in M1 |
| §17.1 | Prediction and risk views carry the "does not guarantee future results" disclaimer |
| §20 | Model tests: output shape/range checks, and regression tests on a fixed evaluation set |

### Deliberately deferred

- **Recommendation, portfolio optimization and backtesting** (FR-10, FR-11, §19) — M4. This phase
  produces the inputs they consume and stops there.
- **SHAP/LIME** (FR-13 advanced) — M6. FR-03's "top-3 contributing factors" ships here using the
  Random Forest's own feature importances, which is the baseline the PRD asks for.
- **Automated retraining** — §10.5 makes it a Nice-to-Have and requires manual review before
  promotion in the MVP. Training and promotion are CLI jobs a human runs.
- **The dashboard.** M5. These endpoints ship with tests and OpenAPI docs, not with a page.
- **Drift monitoring** (§10.5) — the `models` table and the metrics it stores are the groundwork;
  the monitor itself lands in M6 with the rest of the hardening.

### Judgment calls to confirm

1. **FR-09 is served on `GET /market/{symbol}/prediction` rather than a new endpoint.** Its six
   metrics — expected return, volatility, momentum, trend, risk, prediction confidence — are exactly
   the feature-pipeline and prediction outputs for one asset, and §13.2 does not list a separate
   asset-analysis route. Adding one would widen the documented API surface to say something the
   prediction endpoint already has in hand.
2. **The second half of FR-06 lands here.** M2 implemented the cleaning half (what is allowed into
   the database) and deferred normalization, encoding and technical indicators on the grounds that
   the consuming model defines their shape. That model now exists, so they ship in this phase's
   feature pipeline.

---

## 2. Decisions to lock before coding

Items 1–2 were put to the maintainer directly; the rest follow from the PRD.

1. **Risk labels — a documented rubric over a synthetic population.** There is no labelled dataset
   of real users' risk classes, and there will not be one before a launch this project is not going
   to make. So: write an explicit, reviewable scoring rubric over the eight profile fields, sample a
   synthetic population, label it with the rubric, and train the Random Forest on that.

   **This must be stated plainly wherever the metrics appear.** The model is a learned approximation
   of a rule we wrote. Its accuracy measures fidelity to that rubric, not correctness about real
   people. PRD §18's "reported metrics discipline" exists precisely to stop a number like "94.8%
   accuracy" travelling without that context, and a 99% score here would mean the RF memorised a
   deterministic function — an unsurprising result, not a good one. The rubric itself is the actual
   domain artifact and belongs under review; the model is what makes it servable with feature
   importances attached.

2. **Prediction target — 20-day forward log return, regression.** `log(adj_close[t+20] /
   adj_close[t])`, an XGBoost regressor, with a q10/q90 quantile pair for confidence. Trend is the
   sign of the prediction with a ±1% dead band, so "flat" is an answer the model can give rather
   than a coin flip reported as a direction. One month is long enough that the signal is not pure
   noise, and unlike a direction classifier it yields the expected-return number FR-09 lists and the
   M4 optimizer consumes. This closes the first PRD §27 open question.

3. **Walk-forward splits, purged by the horizon.** A random train/test split leaks: overlapping
   20-day windows put near-identical samples on both sides, and the reported R² becomes fiction.
   Splits are chronological, with a 20-day embargo between train and test. §18 asks for the split
   methodology and leakage checks to be published; this is that methodology.

4. **The registry is a table plus a directory.** `models` in Postgres for metadata, joblib artifacts
   under a mounted volume, keyed by `name/version`. §10.5 explicitly permits this for the MVP. No
   MLflow — an experiment tracker for a single developer running scripted jobs is infrastructure to
   operate, not leverage.

5. **Promotion is a deliberate act, never a side effect of training.** Training writes an
   `EXPERIMENT` row. A separate `promote` command compares it against the incumbent on the held-out
   test set and refuses if it does not win. §10.5 requires manual review in the MVP, and a training
   script that silently promotes is how an unreviewed model reaches users.

6. **Risk classification must be deterministic.** FR-03's second acceptance criterion requires the
   same profile to yield the same category and score. Fixed seeds, a pinned model version per
   assessment, and no randomness at inference.

---

## 3. Target layout added this phase

```
backend/app/
├── ml/
│   ├── features/
│   │   ├── technical.py         # FR-07 indicators — pure functions over price series
│   │   └── market.py            # assembles the per-asset training matrix
│   ├── risk/
│   │   ├── rubric.py            # the documented scoring rule (decision 1)
│   │   ├── dataset.py           # sampling, labelling, and FR-06 profile encoding
│   │   └── model.py             # Random Forest train / predict / factors
│   ├── prediction/
│   │   ├── dataset.py           # walk-forward splits, purged by the horizon
│   │   └── model.py             # XGBoost regressor + quantile pair
│   ├── registry.py              # §10.5 — save, load, promote, resolve production
│   ├── evaluation.py            # §18 metrics for both tasks
│   └── artifacts.py             # joblib persistence, checksums, paths
├── models/
│   ├── prediction.py            # predictions
│   ├── risk_assessment.py       # risk_assessments
│   └── model_record.py          # models
├── services/
│   ├── risk_service.py          # FR-03 orchestration + completeness gate
│   └── prediction_service.py    # FR-08 / FR-09 read path
├── api/v1/risk.py               # POST /risk/analyze, GET /risk/latest
└── jobs/                        # train-risk, train-prediction, predict, promote
```

---

## 4. Data model for this phase

The three remaining PRD §12 tables:

- `models` — id UUID PK · name text · version text · training_data_range daterange ·
  metrics jsonb · status enum(EXPERIMENT/PRODUCTION/RETIRED) · created_at ·
  **artifact_path** text · **git_commit** text · UNIQUE(name, version)
- `risk_assessments` — id UUID PK · user_id FK→users ON DELETE CASCADE · model_version text ·
  risk_score numeric · risk_category enum(LOW/MEDIUM/HIGH) · top_factors jsonb · created_at
- `predictions` — id UUID PK · asset_id FK→assets · model_version text · prediction_date date ·
  predicted_return numeric · trend enum(UP/DOWN/FLAT) · confidence numeric 0–1 · created_at ·
  UNIQUE(asset_id, prediction_date, model_version)

**Additions to §12's `models`:** `artifact_path` (the registry is useless if a row cannot find its
artifact) and `git_commit` (§10.5 names "git commit hash" as part of the version identity but the
table omits a column for it). The unique constraint on `(name, version)` is what stops two
artifacts claiming one version.

**`predictions` gains a unique key** on `(asset_id, prediction_date, model_version)` so re-running
the predict job is idempotent, exactly as ingestion is — and so a model change produces a new row
rather than overwriting the history that made an old recommendation explainable.

**`risk_assessments` is append-only.** `GET /risk/latest` reads the newest row. Keeping the history
is what lets a user see that their risk class changed when their profile did, and it is the audit
trail §10.5 wants behind a served result.

---

## 5. The risk rubric (decision 1)

Six weighted components over the eight FR-02 profile fields, each normalised to [0, 1]:

| Component | Weight | Direction |
|---|---|---|
| Stated risk appetite | 0.30 | CONSERVATIVE 0 · MODERATE 0.5 · AGGRESSIVE 1 |
| Investment horizon | 0.20 | Longer horizon → higher capacity, saturating at 25 years |
| Age | 0.15 | Younger → higher capacity, over an 18–75 band |
| Savings-to-income ratio | 0.15 | Larger buffer → higher capacity, saturating at 3× income |
| Experience | 0.10 | NONE 0 → ADVANCED 1 |
| Financial literacy | 0.10 | LOW 0 · MEDIUM 0.5 · HIGH 1 |

`LOW < 0.40 ≤ MEDIUM ≤ 0.70 < HIGH`.

Stated appetite carries the most weight because it is the only field that is a direct expression of
preference; the rest are capacity proxies. Capacity and willingness are genuinely different things,
and the rubric deliberately lets a young, wealthy, conservative respondent land in MEDIUM rather
than overriding what they told us.

The synthetic population samples each field independently from plausible marginals, which means it
contains combinations that would be rare in reality. That is intentional — the classifier has to
behave sensibly across the whole input space the API accepts, not only the populated corner of it.

---

## 6. Task stages

### Stage 1 — Feature engineering (FR-07, FR-06 remainder)
- [x] Technical indicators as pure functions over ordered price series: SMA/EMA, RSI, MACD,
      Bollinger bands, realised volatility, momentum, lagged returns
- [x] Range assertions in tests: RSI ∈ [0, 100], bands ordered low ≤ mid ≤ high, no look-ahead
- [x] A leakage test per indicator: value at `t` must not change when data after `t` is appended
- [x] Cross-asset correlation features against a market proxy
- [x] Profile encoding/normalization for the risk model (landed in `risk/dataset.py`,
      alongside the sampling it serves, rather than a separate `features/profile.py`)
- [x] Feature assembly emits a matrix with zero nulls in required columns (FR-06 criterion)

### Stage 2 — Model registry (§10.5)
- [x] `models` table and migration
- [x] Save an artifact with its metrics, training range, git commit and a content checksum
- [x] `resolve_production(name)` — what inference calls; a clear error when nothing is promoted
- [x] `promote` compares against the incumbent on the held-out test set and refuses a regression
- [x] First promotion compares against the naive baseline instead (§10.5)

### Stage 3 — Risk classification (FR-03)
- [x] Rubric from §5, as reviewable code with its weights as named constants
- [x] Synthetic population sampling, seeded and reproducible
- [x] Random Forest, fixed seed, trained and evaluated on a held-out split
- [x] Top-3 contributing factors from feature importances, as human-readable strings
- [x] `POST /risk/analyze` blocks on an incomplete profile and lists the missing fields (FR-02 AC)
- [x] `GET /risk/latest` returns the newest assessment, 404 when there is none
- [x] Determinism test: same profile twice → identical category and score

### Stage 4 — Market prediction (FR-08, FR-09)
- [x] Walk-forward split with a 20-day purge; a test asserting no train/test date overlap
- [x] XGBoost regressor plus q10/q90 quantile models for confidence
- [x] Naive baseline (predict the trailing mean) for the first promotion gate
- [x] `predict` job writes a row per asset per day, idempotent on re-run
- [x] `GET /market/{symbol}/prediction` returns expected return, trend, confidence, model version,
      and FR-09's remaining metrics
- [x] Disclaimer (§17.1) on every prediction and risk response

### Stage 5 — Evaluation and governance (§18)
- [x] MAE / RMSE / R² for prediction; accuracy / precision / recall / F1 for risk
- [x] Metrics stored on the `models` row, not only printed
- [x] A model card documenting the rubric's circularity (decision 1) in plain language
- [x] Regression test against a fixed evaluation set to catch silent quality drops (§20)

### Stage 6 — CI and close-out
- [x] Training jobs run in CI against fixture data, fast enough to stay in the pipeline
- [x] `.env.example`, README and this checklist updated
- [x] Manual QA pass against §8

---

## 7. Test coverage required to exit the phase

| Layer | Must cover |
|---|---|
| Unit | Every indicator against hand-computed values; rubric boundaries; encoding; split purging |
| Model | Output shape and range (score ∈ [0,1], confidence ∈ [0,1], trend in the enum); determinism; a fixed-evaluation-set regression test |
| Leakage | No indicator uses future data; no train/test date overlap after purging |
| Integration | Both FR-03 acceptance criteria; FR-08's traceability criterion; the incomplete-profile block |
| Security | Risk endpoints reject cross-user access; no profile values in logs or model artifacts |
| Governance | Promotion refuses a model that loses to the incumbent; inference fails clearly with no production model |

---

## 8. Manual QA checklist (§20 milestone gate)

1. Fresh stack → `train-risk` → a `models` row appears as EXPERIMENT with real metrics.
2. `promote` it → status PRODUCTION; promote a deliberately worse model → refused.
3. `POST /risk/analyze` with an incomplete profile → blocked, missing fields listed.
4. Complete the profile → returns category, score, top-3 factors, model version.
5. Call it twice unchanged → byte-identical category and score.
6. `GET /risk/latest` → the assessment just created; another user's token → their own, never yours.
7. `train-prediction` then `predict` → every tracked asset has a prediction row.
8. `GET /market/SPY/prediction` → expected return, trend, confidence, model version, disclaimer.
9. Delete the production model row → inference returns a clear error, not a 500.
10. `grep` the logs and the artifact directory for a submitted income figure → zero hits.

---

## 9. Risks specific to this phase

| Risk | Mitigation |
|---|---|
| The risk model is circular — it learns a rule we wrote | Stated in the plan, the model card and the API docs; the rubric is reviewed as the real artifact |
| Look-ahead leakage inflates prediction metrics | Purged walk-forward splits, plus a per-indicator leakage test rather than trusting review |
| A 20-day equity return is mostly noise; R² may be near zero | Report it honestly (§18); the promotion gate is "beats the naive baseline", not "is impressive" |
| Model artifacts drift from the rows describing them | Checksum stored on the row; loading verifies it |
| Training in CI makes the pipeline slow | Fixture-scale data in CI; full training is a local or scheduled job |
| Profile PII reaching a model artifact | Artifacts store fitted parameters, never training rows; a test asserts it |

---

## 10. What Phase 4 needs from this phase

M4 (Recommendation) starts from a user's risk category and the per-asset expected return, risk and
confidence. It depends on this phase delivering: a production risk model resolvable by name; a
`predictions` row per tracked asset carrying `model_version`; the feature pipeline it will reuse for
asset scoring; and `asset_class` on `assets` (delivered in M2) for FR-11's per-class weight caps.

---

## 11. Verification log

Recorded 2026-08-28, against the stack running in Docker.

### Green

| Check | Result |
|---|---|
| Backend suite | 283 passed (was 162 at end of M2) |
| Frontend suite | 16 passed |
| `ruff check` / `ruff format --check` | clean |
| `mypy app` | no issues, 79 files |
| Migration base → head → base → head | clean, including the three new enum types |
| Models vs. migration | `alembic revision --autogenerate` produces an empty diff |
| `train-risk` → EXPERIMENT row, never promoted | as designed (§10.5) |
| Promotion gate, worse model | refused: `does not beat the incumbent v1 on f1_macro (0.548 vs 0.919)` |
| Promotion gate, first model losing to baseline | refused: `did not beat the naive baseline` |
| `predict`, then re-run | 32 predictions, 32 rows — idempotent |
| FR-03 acceptance criteria | category + score + top-3 factors; identical on repeat |
| FR-02 gate | incomplete profile → 422 with the missing fields listed |
| §16.2 ownership | a second user's `/risk/latest` is their own 404, never another user's row |
| §13.1 expensive bucket | 10/min enforced, per user |
| §17.1 disclaimer | present on every risk and prediction response |
| No production model | 503 `model_unavailable` with an actionable message, not a 500 |
| **PII sweep** — grep API logs, scheduler logs and the artifact volume for a submitted income | **0 hits in all three** |

### Measured metrics

Published per §18's reported-metrics discipline. Read the model cards before quoting either.

**`risk_classifier` v1** — 20,000 synthetic profiles, stratified 80/20 hold-out:

| accuracy | precision (macro) | recall (macro) | F1 (macro) | majority-class baseline F1 |
|---|---|---|---|---|
| 0.9435 | 0.9550 | 0.8902 | 0.9186 | 0.2532 |

These measure **fidelity to `app/ml/risk/rubric.py`**, not correctness about real
people. The model is trained on labels that rule generated. A high score here is the
expected result, not evidence of quality.

**`market_predictor` v1** — 32 symbols, 3 years of synthetic prices, chronological
split with a 40-day purge:

| MAE | RMSE | R² | baseline RMSE | beats baseline |
|---|---|---|---|---|
| 0.0444 | 0.0562 | **−0.048** | 0.0551 | **no** |

**The model lost to predicting the training mean, and was correctly refused
promotion.** That is the right outcome and worth stating plainly: the synthetic
provider generates a geometric random walk with no learnable structure, so no model
can beat the mean on it. A number resembling the source report's R² = 0.95 would have
been evidence of leakage, not of success. It was force-promoted afterwards purely to
exercise the serving path for QA — a real deployment would train on ingested Yahoo
data and let the gate decide.

### Bugs this stage caught

1. **Missing macro data emptied the entire feature matrix.** Macro columns were
   mandatory, so `dropna` discarded every row whenever `economic_indicators` was
   empty — training silently produced nothing on any deployment where the FR-05 job
   had not run. Worse in the partial case: with 12 months of macro against 3 years of
   prices, 787 usable rows per asset became 240, every asset fell below the minimum,
   and the error told the operator to backfill *market* data they already had. Fixed
   three ways — market features are required and macro is not (XGBoost handles NaN
   natively, and imputing would state a CPI figure for a date before one existed),
   `ingest-economic` gained `--backfill-days`, and the error message now names the
   real cause.
2. **The expensive rate-limit bucket was keyed by IP, not by user.** `RateLimit`
   keys on `request.state.user_id`, which `get_current_user` sets — but declared as a
   route-level dependency it ran *before* that, so the key silently fell back to the
   source address. §13.1 specifies 10 requests/minute **per user**; as shipped, two
   people behind one office NAT shared a single budget and either could exhaust the
   other's. Found because a freshly registered user was rate-limited on its first
   request. `UserRateLimit` takes `CurrentUser` as a parameter, which guarantees the
   ordering. The regression test was verified to fail against the old wiring.
3. **`predict` would have crashed on its success path.** `loaded.name` does not
   exist on `LoadedModel` — the attribute is `loaded.record.name`. Caught by mypy
   before it ever ran; it would have raised `AttributeError` after doing all the work.
4. **A precarious profile was classified HIGH risk.** The rubric returned the
   *maximum* savings-buffer score for anyone with no income and any savings at all,
   on the reasoning that their savings were infinite relative to their income. A
   21-year-old with 2,000 saved and nothing coming in scored 0.725 and came out HIGH
   capacity — the most exposed profile the input space allows, rated the safest. With
   no income the ratio is undefined, not infinite, so the buffer is now measured
   against a nominal income. Found by running the rubric over representative profiles
   rather than by reading it, which is the argument for doing that on any future
   change to those weights.

### Also fixed while verifying

- **The job CLI printed tracebacks at operators.** A refused promotion — the gate
  working exactly as designed — dumped a Python stack trace instead of its message.
  `AppError` is now caught in `main`, printing the message to stderr and exiting 1.
- **The Docker image pulled 252 MB of NVIDIA NCCL.** The default `xgboost` wheel
  depends on it for multi-GPU training, which PRD §7.2's "single modest workstation"
  will never use. Switched to `xgboost-cpu` behind a platform marker; the image went
  from ~1.4 GB to 1.1 GB and the build lost a very slow download.
- **`filterwarnings = ["error::DeprecationWarning"]` made the ML suite unrunnable.**
  joblib assigns to `array.shape`, which NumPy 2.5 deprecated, on every artifact
  load. Scoped an ignore to that one message in that one module rather than
  weakening the project-wide policy.

### Notes

- **The risk model is circular by construction** and this is documented in three
  places — the plan, `Docs/MODELS/risk-classifier.md`, and the `label_source` field
  stored in the metrics themselves, so the caveat travels with the number wherever
  the row is read.
- **Ports** — as in M1 and M2, this machine uses 55432 / 8010 / 5183.
- **Branch protection** remains declined by decision, not outstanding (Phase 1 §10).
