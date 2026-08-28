# Model card — portfolio optimizer and recommendation engine

**Surfaces:** FR-10 (recommendation), FR-11 (optimization), FR-13 (explanation)
**Method:** content-based asset scoring → KNN candidate selection → mean-variance optimization
**Output:** asset weights summing to 1, expected return, expected risk, a reason per holding.

---

## What this is not

**It is not advice, and it is not a position.** The system generates a recommendation and stores
it; nothing here tracks what anyone owns, and nothing should. Holdings tracking is the first step
toward the custody and execution surfaces that are permanent non-goals (PRD §5).

**The recommender is content-based, not collaborative.** §10.3 names KNN, and it is used — but over
the *asset feature space*, not over user–item interactions, because there are none. No user has ever
held or rated an asset. Manufacturing implicit feedback from previously generated portfolios would
be a recommender learning from its own output, converging on its own prior rather than on anything
true.

---

## The pipeline

```
risk class (M3) ─┐
investment goal ─┼→ suitability scoring ─→ KNN candidate set ─→ mean-variance solve ─→ weights
horizon ─────────┘         (FR-10)             (§10.3)              (FR-11)
                                                                        │
                                          per-holding reason strings ←──┘  (FR-13)
```

### Scoring

Five weighted components: expected return (0.35), volatility fit (0.30), momentum (0.15),
prediction confidence (0.10), goal fit (0.10).

Volatility is scored as **distance from a target**, not "less is better". A LOW-risk investor is not
best served by the single least volatile instrument available, and a HIGH-risk one is not best
served by the most volatile.

### Candidate selection

Nearest neighbours to a risk-appropriate target point in a standardised feature space, with
per-class quotas. Pure score-ranking would happily return six large-cap equities; the optimizer
cannot diversify across a candidate set that is all one thing.

The quotas are derived from the constraint floors that will later be applied (see below) — the
candidate set must be capable of satisfying its own constraints.

### Optimization

```
maximise   μᵀw − λ·wᵀΣw
subject to Σw = 1,  0 ≤ wᵢ ≤ max_weight,  floor_c ≤ Σ_{i∈c} wᵢ ≤ cap_c
```

SLSQP, long-only. λ by risk class: LOW 12.0, MEDIUM 5.0, HIGH 2.0.

---

## Where the numbers come from, and how much to trust them

**μ is shrunk, deliberately.** Mean-variance is far more sensitive to expected returns than to
anything else about it, and M3's predictor has an R² near zero. Feeding raw predictions in would
optimise on noise and produce a portfolio that is 80% one ticker.

```
μᵢ = ωᵢ·μ_ml,ᵢ + (1 − ωᵢ)·μ_hist,ᵢ      ωᵢ = 0.35 · confidenceᵢ
```

clipped to [−30%, +40%] annualised. An unconfident prediction decays toward the historical mean, so
the system degrades to a dull portfolio rather than a reckless one.

**Σ is Ledoit-Wolf shrunk**, not the sample covariance. With 32 assets and a few hundred
observations the sample matrix is near-singular, and mean-variance inverts it — which is precisely
where its reputation for nonsense weights comes from.

**Expected return and expected risk are model estimates from historical data. They are not
forecasts.** They describe what the optimizer believed at generation time, given inputs that are
themselves estimates. Treat the *relative* ordering across risk classes as meaningful and the
absolute figures as illustrative.

---

## Constraints, and why they are constraints

Risk class, goal and horizon adjust the **constraint set**, not the objective:

| Risk | λ | Max/asset | Equity band | Bond band |
|---|---|---|---|---|
| LOW | 12.0 | 20% | 10–35% | 45–80% |
| MEDIUM | 5.0 | 25% | 35–65% | 20–50% |
| HIGH | 2.0 | 35% | 60–90% | 5–30% |

RETIREMENT shifts the equity band −10%, GROWTH +10%. A horizon under five years shifts it a further
−15%: below that, volatility stops being a fluctuation and starts being a realised loss.

Encoding these as constraints rather than objective terms keeps the objective one interpretable
expression and makes the constraint set an inspectable list of statements — which is the form a
user's actual question takes. The set in force is stored on the portfolio row, so "why is this only
12% equities?" stays answerable later rather than being recomputed from settings that may have
changed.

**A note on capacity.** A class can hold only as much as its assets are individually permitted to.
Bonds capped at 80% with three holdings at a 20% per-asset cap top out at 60% — and a band set whose
nominal caps sum above 100% can still be unsatisfiable. This was a real defect: a LOW-risk retiree
received an "optimization failed" for an entirely ordinary profile. Candidate selection now sizes
each class to make the portfolio fillable, and the feasibility check uses effective caps.

---

## Explanations (FR-13)

Every holding gets a reason string derived from the score components that actually drove it, quoting
the measured volatility and expected return used.

**Reasons are never composed to sound convincing.** A recommendation surface that generates
persuasive text detached from the computation is worse than no explanation at all: it manufactures
confidence the system has not earned, on a subject where PRD §17 exists because misplaced confidence
has consequences. The phrasing stays descriptive for the same reason — "its volatility is close to
the level targeted for your risk profile" is a statement about the calculation; "this is a great fit
for you" would be a claim about outcomes this system does not get to make.

SHAP/LIME is FR-13's advanced option and lands in M6.

---

## Backtesting (§19)

Walk-forward, monthly rebalancing, benchmarked against SPY, with **10 bps per side on turnover**
applied and reported. §19 was amended specifically so results are not misleadingly frictionless.

The backtest period must not overlap the model's training window, and the job refuses if it would —
a backtest over data the model was fitted on measures memorisation, and would produce the most
flattering number in the system. `train-prediction --holdout-days N` reserves a period so there is
genuinely out-of-sample data to measure against.

The benchmark is reported alongside every run, never optionally. Omitting the comparison when it is
unflattering is the exact failure mode the requirement exists to prevent.

---

## Limitations

- No transaction costs, taxes, liquidity constraints or minimum lot sizes in the *recommendation* —
  only in the backtest.
- Expected returns and covariances are estimated from historical data and assume the relationship
  persists. They will not across a regime change.
- The asset universe is 32 liquid US instruments (Phase 2 plan §5). It is not the investable
  universe, and its composition bounds everything the optimizer can conclude.
- The scoring weights and constraint bands have not been reviewed by a qualified financial
  professional. As with the risk rubric, that review is the highest-value improvement available and
  does not require touching any code.
- No fairness evaluation across groups (FR-14, M6).
- Output is educational, not advice, and is not a buy or sell signal (§17.1, §17.2, §5).
