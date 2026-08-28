# Phase 4 — Recommendation (PRD Milestone M4)

**Estimated duration:** 2–3 weeks solo/part-time (PRD §22)
**Status:** Complete — see §9.
**Exit condition:** A user with a risk classification can generate a portfolio whose weights are
produced by an optimizer rather than a lookup table, sum to 1.0 ± 0.001, respect per-asset and
per-class caps, and arrive with a plain-language reason per holding — and that portfolio can be
backtested against a benchmark over a period the models never saw, with transaction costs stated.

---

## 1. Scope

### In scope

| PRD ref | What ships |
|---|---|
| FR-10 | Portfolio recommendation combining risk class, goal, horizon, predictions, asset risk, diversification |
| FR-11 | Mean-variance optimizer — weights sum to 1, per-asset and per-class caps, infeasibility reported |
| FR-12 | Portfolio value, expected return, expected risk, allocation, historical performance — as API |
| FR-13 | A plain-language reason attached to every recommendation before it is served |
| §19 | Backtesting: train/backtest split, benchmark comparison, five metrics, stated transaction costs |
| §18 | Recommendation metrics — Precision@K, Recall@K, NDCG against a rubric-derived relevance set |
| §12 | `portfolios`, `portfolio_assets`, `recommendations` tables |
| §13.2 | `POST /portfolio/generate`, `GET /portfolio/current`, `GET /portfolio/history`, `GET /recommendation/{id}/explanation` |
| §13.1 | 10 req/min on `/portfolio/generate` — per user, via `UserRateLimit` |
| §16.1 | Optimization under 8 seconds for the 32-asset universe |
| §17.1 | Disclaimer on every recommendation and portfolio response |

### Deliberately deferred

- **The dashboard and interactive charts** (FR-12's "use interactive charts", FR-15) — M5. This
  phase ships the numbers those charts will draw; §16.1's render target is an M5 measurement.
- **SHAP/LIME** (FR-13's "advanced implementation") — M6. FR-13's acceptance criterion asks for a
  rule-derived reason string as the baseline, and that is what ships here.
- **The fairness dashboard** (FR-14) — M6.
- **Automated rebalancing or any notion of a live position.** A generated portfolio is a
  recommendation, not a holding. Nothing in this phase tracks what a user actually owns, because
  doing so is the first step toward the custody and execution surfaces that are permanent
  non-goals (PRD §5).

### Judgment calls to confirm

1. **The recommendation engine is content-based scoring with a KNN candidate step, not
   collaborative filtering.** §10.3 says "KNN / Recommendation Engine". Collaborative filtering
   needs user–item interaction history, and this system has none — no user has ever rated or held
   an asset, and inventing implicit feedback from generated portfolios would be a recommender
   learning from its own output. So: score assets on features conditioned on risk class and goal,
   use KNN in that feature space to select the candidate set around a risk-appropriate target, and
   let the optimizer weight them. That reads §10.3 literally while staying honest about what data
   exists.
2. **`portfolio_assets` gets a real database check, not only the application-layer one §12
   specifies.** The PRD says the weights-sum-to-1 constraint is "enforced at application layer",
   presumably because it spans rows. A deferred constraint trigger can enforce it in the database
   too, and FR-11's acceptance criterion is precise enough (1.0 ± 0.001) to be worth holding at
   both levels — the application check catches it early with a good message, the database check
   makes a malformed portfolio unstorable.

---

## 2. Decisions to lock before coding

Items 1–2 were put to the maintainer directly; the rest follow from the PRD and from M3.

1. **Optimizer — mean-variance with risk aversion set by risk category.**

   ```
   maximise   μᵀw − λ · wᵀΣw
   subject to Σw = 1,  0 ≤ wᵢ ≤ max_weight,  Σ_{i∈c} wᵢ ≤ cap_c
   ```

   Solved with SLSQP (scipy, already present via scikit-learn). This reads FR-11 almost literally,
   and λ is where the risk class does real work: LOW 12.0, MEDIUM 5.0, HIGH 2.0. The alternative —
   one tangency portfolio scaled three ways — would leave the goal and horizon inputs FR-10 lists
   doing nothing.

   Σ is a **Ledoit-Wolf shrunk covariance**, not the sample covariance. With 32 assets and a few
   hundred observations the sample matrix is close to singular, and inverting something
   near-singular is how mean-variance produces its famous nonsense weights.

2. **Expected returns — ML predictions shrunk toward the historical mean.**

   ```
   μᵢ = ωᵢ·μ_ml,ᵢ + (1 − ωᵢ)·μ_hist,ᵢ        ωᵢ = 0.35 · confidenceᵢ
   ```

   then clipped to [−30%, +40%] annualised. Mean-variance amplifies estimation error into corner
   solutions, and M3's predictor has an R² near zero — feeding it in raw would optimise on noise
   and produce a portfolio that is 80% one ticker. Shrinkage scaled by the model's own confidence
   means an unconfident prediction decays toward the historical mean, so the system degrades to a
   sane portfolio rather than a wild one. FR-10 still gets its "market predictions" input.

3. **Goal and horizon adjust the constraints, not the objective.** RETIREMENT tightens the equity
   cap and raises the bond floor; GROWTH does the reverse; WEALTH_CREATION sits between. A short
   horizon tightens caps further. Encoding them as constraints rather than as extra objective terms
   keeps the objective interpretable and the constraint set inspectable — and it is the constraint
   set that a user will actually ask about.

4. **Backtesting is walk-forward, monthly-rebalanced, with stated costs.** The backtest period never
   overlaps the model's training window (§19). Rebalancing monthly matches M3's 20-day horizon —
   rebalancing daily on a monthly signal is churn. Transaction costs are **10 bps per side on
   turnover**, applied on every rebalance and reported with the results, because §19 exists
   specifically so results are not misleadingly frictionless.

5. **A portfolio is immutable once generated.** `POST /portfolio/generate` always creates a new
   row; `GET /portfolio/current` reads the newest. An explanation must still be true a month later,
   and it cannot be if the portfolio it explains was edited underneath it.

6. **Recommendation relevance for §18's metrics comes from the rubric, not from users.** There is
   no ground truth about which assets a user "should" have been shown. Precision@K, Recall@K and
   NDCG are therefore measured against a rule-derived relevance set — the same circularity as M3's
   risk labels, and it gets the same treatment: stated plainly wherever the numbers appear.

---

## 3. Target layout added this phase

```
backend/app/
├── ml/
│   ├── recommendation/
│   │   ├── scoring.py           # per-asset suitability score, conditioned on risk + goal
│   │   ├── candidates.py        # KNN selection in asset-feature space (§10.3)
│   │   └── reasons.py           # FR-13 rule-derived reason strings
│   ├── portfolio/
│   │   ├── inputs.py            # μ (decision 2) and Σ (Ledoit-Wolf)
│   │   ├── constraints.py       # caps by risk class, goal and horizon (decision 3)
│   │   └── optimizer.py         # FR-11 mean-variance under constraints
│   └── backtest.py              # §19 walk-forward, benchmark, five metrics, costs
├── models/
│   ├── portfolio.py             # portfolios, portfolio_assets
│   └── recommendation.py        # recommendations
├── services/portfolio_service.py
├── api/v1/portfolio.py          # generate, current, history
└── api/v1/recommendation.py     # explanation
```

---

## 4. Data model for this phase

The last three PRD §12 tables:

- `portfolios` — id UUID PK · user_id FK→users ON DELETE CASCADE · expected_return numeric ·
  expected_risk numeric · created_at · **risk_category** enum · **model_version** text ·
  **objective** jsonb (λ, caps and μ source actually used)
- `portfolio_assets` — portfolio_id FK (PK part) · asset_id FK (PK part) · weight numeric ·
  CHECK(weight ≥ 0 AND weight ≤ 1)
- `recommendations` — id UUID PK · user_id FK · asset_id FK · score numeric · reason text ·
  model_version text · created_at · **portfolio_id** FK nullable

**Additions to §12.** `risk_category` and `model_version` on `portfolios` because §10.5 requires
every recommendation to name what produced it, and a portfolio is a recommendation. `objective`
because "why is this 12% and not 20%?" is answerable only if the λ and caps in force at generation
time were recorded — recomputing them later from current settings would answer a different
question. `portfolio_id` on `recommendations` ties a reason to the portfolio it justifies, which
`GET /recommendation/{id}/explanation` needs.

---

## 5. Task stages

### Stage 1 — Optimizer inputs (FR-10 inputs, decision 2)
- [x] Historical annualised returns and Ledoit-Wolf covariance from stored prices
- [x] μ blending ML predictions with the historical mean, weighted by model confidence, then clipped
- [x] Tests: μ decays to the historical mean as confidence → 0; Σ is symmetric positive semi-definite

### Stage 2 — Constraints (FR-11, decision 3)
- [x] Per-asset max weight; per-asset-class caps and floors by risk category
- [x] Goal and horizon adjustments layered on top
- [x] Feasibility check *before* solving, returning which constraint makes the set infeasible
- [x] Test: caps that cannot sum to 1 produce a clear error, never a malformed portfolio

### Stage 3 — Optimizer (FR-11)
- [x] SLSQP mean-variance under the constraint set, long-only
- [x] Weights sum to 1.0 ± 0.001 and no position exceeds its cap — asserted on every solve
- [x] Deterministic for identical inputs
- [x] Solver failure is reported as a failure, never as a best-effort weight vector

### Stage 4 — Recommendation engine (FR-10, FR-13, §10.3)
- [x] Per-asset suitability scoring conditioned on risk class and goal
- [x] KNN candidate selection around a risk-appropriate target in feature space
- [x] A reason string per holding, drawn from the factors that actually drove the score
- [x] Test: no static allocation table exists anywhere in the path (FR-10's acceptance criterion)

### Stage 5 — API and persistence (§13.2)
- [x] `POST /portfolio/generate` — 10/min per user, blocks without a risk assessment
- [x] `GET /portfolio/current`, `GET /portfolio/history` — cursor-paginated per §13.1
- [x] `GET /recommendation/{id}/explanation` — 404 for another user's recommendation
- [x] Weights-sum check at both the application and database layers (judgment call 2)
- [x] Disclaimer on every response (§17.1)

### Stage 6 — Backtesting (§19)
- [x] Walk-forward over a period disjoint from model training
- [x] Benchmark comparison against SPY
- [x] Total return, annualised return, volatility, Sharpe, maximum drawdown
- [x] Transaction costs applied and reported
- [x] `backtest` CLI job writing a readable summary

### Stage 7 — CI and close-out
- [x] §18 recommendation metrics (Precision@K / Recall@K / NDCG), with their circularity stated
- [x] `.env.example`, README, model card and this checklist updated
- [x] Manual QA pass against §6

---

## 6. Manual QA checklist (§20 milestone gate)

1. Generate a portfolio without a risk assessment → blocked with a clear reason.
2. Run risk analysis, then generate → weights sum to 1.000, no position over its cap.
3. Generate for a LOW and a HIGH profile → materially different allocations, not the same mix rescaled.
4. Every holding carries a reason string that names something true about that asset.
5. `GET /portfolio/current` returns the newest; `/history` paginates and is ordered newest first.
6. Another user's portfolio and explanation are unreachable (§16.2).
7. Set an infeasible cap set → clear 422, no portfolio stored.
8. Run the backtest → five metrics plus the benchmark, and the transaction-cost assumption printed.
9. `grep` logs and responses for a submitted income figure → zero hits.
10. Time `POST /portfolio/generate` → under the §16.1 eight-second target.

---

## 7. Risks specific to this phase

| Risk | Mitigation |
|---|---|
| Mean-variance produces extreme corner weights | Shrunk μ (decision 2), shrunk Σ, and per-asset caps — three independent brakes |
| A backtest that looks good because it peeked | Backtest period disjoint from training; benchmark reported alongside, never omitted |
| Frictionless backtest overstates returns | Costs applied on turnover and printed with the result (§19) |
| Recommendation metrics look authoritative but are rubric-derived | Stated in the plan, the model card and the stored metrics, as with M3's risk labels |
| The optimizer silently returns a non-converged solution | Solver status checked; failure raises rather than returning best-effort weights |
| Scope drifting toward tracking real holdings | Decision 5 and §1 — a portfolio is a recommendation, never a position |

---

## 8. What Phase 5 needs from this phase

M5 (UI) needs: a portfolio with weights, expected return and expected risk; per-holding reasons for
the explanation panel; portfolio history for the performance view; and backtest results for the
comparison chart. All of it is API-shaped in this phase so M5 is a rendering problem, not a
modelling one.

---

## 9. Verification log

Recorded 2026-08-28, against the stack running in Docker.

### Green

| Check | Result |
|---|---|
| Backend suite | 368 passed (was 283 at end of M3) |
| Frontend suite | 16 passed |
| `ruff check` / `ruff format --check` | clean |
| `mypy app` | no issues, 95 files |
| Migration up → down → up | clean; `risk_category` correctly left to the M3 revision |
| Models vs. migration | `alembic revision --autogenerate` produces an empty diff |
| FR-11 weights sum to 1.0 ± 0.001 | holds for every risk class, at unit and API level |
| FR-11 per-asset cap | no position exceeds its cap |
| FR-11 infeasible constraints | reported with the offending constraint named, never a malformed portfolio |
| FR-11 database guarantee | a deferred trigger refuses a stored portfolio whose weights drift |
| FR-13 reasons | every holding, derived from its own score components, quoting the figures used |
| §16.1 optimization under 8s | **0.18–0.21s** for the 32-asset universe |
| §16.2 ownership | another user's portfolio, history and explanation are all unreachable |
| §19 overlap guard | a backtest inside the training window is refused with the dates named |
| **PII sweep** — API logs, scheduler logs, artifact volume | **0 hits in all three** |

### Portfolios by risk profile

Generated against the live stack, showing the risk gradient the design is for:

| Profile | Holdings | Allocation | Expected return | Expected risk |
|---|---|---|---|---|
| LOW · retiree · 4y · RETIREMENT | 9 | 70% bond, 10% equity, 10% commodity, 10% REIT | 11.4% | 7.5% |
| LOW · cautious · 20y | 9 | 46% bond, 35% equity, 10% commodity, 9% REIT | 15.0% | 6.8% |
| MEDIUM · balanced · 15y | 6 | 60% equity, 25% bond, 15% commodity | 19.6% | 8.4% |
| HIGH · young · 35y · GROWTH | 5 | 75% equity, 20% commodity, 5% bond | 16.2% | 9.9% |

The two LOW rows are the point: same risk class, different horizon, materially
different equity weight. Expected risk rises monotonically with risk class.

### §19 backtest — out of sample

Trained with `--holdout-days 200`, so the window below is data the model never saw.

| metric | portfolio | benchmark (SPY) |
|---|---|---|
| total return | 6.42% | 11.90% |
| annualised return | 6.47% | 12.00% |
| volatility | **10.21%** | 19.27% |
| Sharpe ratio | 0.438 | 0.519 |
| max drawdown | **−6.57%** | −14.52% |

Transaction costs 10 bps per side on turnover, 11 rebalances, 0.14% total drag.

The portfolio **underperformed the benchmark on return** and is reported that way,
because §19 exists so the comparison is made rather than omitted when unflattering.
It did so at roughly half the volatility and less than half the drawdown, which is
what a risk-constrained diversified portfolio should look like. On synthetic
random-walk data the return comparison carries no information about quality; the
volatility and drawdown figures show the mechanics work.

### §18 recommendation metrics

| risk | goal | P@10 | R@10 | NDCG | relevant |
|---|---|---|---|---|---|
| LOW | RETIREMENT | 0.300 | 0.429 | 0.264 | 7 |
| LOW | GROWTH | 0.000 | 0.000 | 0.000 | 0 |
| LOW | WEALTH_CREATION | 0.100 | 0.143 | 0.079 | 7 |
| MEDIUM | RETIREMENT | 0.800 | 0.364 | 0.870 | 22 |
| MEDIUM | GROWTH | 1.000 | 0.400 | 1.000 | 25 |
| HIGH | GROWTH | 1.000 | 0.400 | 1.000 | 25 |

Relevance is rule-derived, so these measure agreement between the ranker and a
second rule in this repository — internal consistency, not correctness.

**Two findings worth stating rather than tuning away:**

1. **The ranker is poorly matched to LOW-risk users.** P@10 of 0.300 means only
   three of its top ten are suitable for a conservative investor: the scoring weights
   put 0.35 on expected return against 0.30 on volatility fit, so high-return
   equities outrank bonds even for someone who should hold bonds. The *portfolios*
   are still correct — the candidate quotas and the optimizer's per-class floors
   force the allocation regardless — but the ordering is weak. Adjusting the weights
   to improve this number would be tuning to a rubric-derived metric, which is the
   trap §18 warns about; it is recorded as a candidate improvement instead.
2. **LOW + GROWTH has no relevant assets at all.** The relevance rule wants
   low volatility *and* a growth-suited class, and nothing is both. That is a real
   tension in the domain rather than a bug — low capacity and a growth objective are
   in conflict — and the optimizer resolves it through the constraint bands. The
   metric simply has nothing to measure there.

### Bugs this stage caught

1. **A LOW-risk retiree could not get a portfolio at all.** Three separate defects
   stacked into one 422:
   - `check_feasible` summed the *nominal* class caps. Bonds capped at 80% with only
     three holdings at a 20% per-asset cap top out at 60%, so the reachable total was
     90% and no fully invested portfolio existed — but the sum of caps was 1.10 and
     the check passed.
   - Candidate selection ran before the constraints were known, so it could hand the
     optimizer a set that could not satisfy its own floors.
   - SLSQP was started from an equal-weight vector, which violates a 10% equity cap
     immediately. The solver failed with "Positive directional derivative for
     linesearch" — an error that tells an operator nothing.

   Fixed by computing effective caps, sizing the candidate set from the constraint
   floors *and* the capacity needed to reach 100%, and starting the solver from a
   vector that already satisfies the bands.
2. **The test database lacked a guarantee the migrated database had.** The
   weights-sum-to-1 trigger existed only in the migration, and the suite builds its
   schema with `create_all` — so the test asserting the database rejects a malformed
   portfolio passed vacuously against a schema with no trigger. Now attached to
   `after_create` as well, with the SQL deliberately duplicated in both places and a
   note to change them together.
3. **The synthetic universe could not represent a conservative portfolio.** Every
   asset used one volatility distribution, so a treasury ETF was as volatile as a
   growth stock (~19% annualised across the board) while the risk classes target 8%
   / 14% / 22%. §18's metrics reported zero relevant assets for every LOW
   combination, and the LOW path — the one that had already broken — could not be
   exercised offline at all. The provider is now class-aware: bonds 3–4%, equities
   15–21%.
4. **A PII test passed on a coincidence.** It searched the response for the fixture's
   `25000` savings, which matched inside a weight of `0.25000000`. A check that fires
   on a coincidence is one nobody trusts the next time it fires; it now uses digit
   strings that cannot collide.

### Also fixed while verifying

- **§19 was unsatisfiable in practice.** The predictor trained through the last
  available date, leaving no out-of-sample period, so every backtest either
  overlapped training or ran against a model with no recorded window. Added
  `train-prediction --holdout-days N` to reserve a period, and the backtest now
  starts *after* the training window rather than refusing a range the operator had
  no way to know was wrong.
- `DDL()` runs its text through printf interpolation, and the trigger body is full
  of `%` — plpgsql RAISE placeholders and `:=`. Replaced with a plain `after_create`
  callback executing `text()`, so the SQL stays identical to the migration's copy.

### Notes

- **Ports** — as in M1–M3, this machine uses 55432 / 8010 / 5183.
- **Branch protection** remains declined by decision, not outstanding (Phase 1 §10).
