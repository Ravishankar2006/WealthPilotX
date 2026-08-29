# Model card — market predictor

**Model name:** `market_predictor` · **Task:** FR-08, market prediction
**Algorithm:** XGBoost regressor, plus a q10/q90 quantile pair for confidence
**Output:** expected 20-day log return, a trend (UP / DOWN / FLAT), and a confidence in [0, 1].

---

## Target and horizon

PRD §27 left the horizon and target variable open, to be settled during experimentation. They are
settled here:

```
target = log(adj_close[t + 20] / adj_close[t])
```

Twenty trading days, roughly one month.

- **Log return, not simple return.** Log returns add across time and treat a +25% and a −25% move
  with equal severity. Simple returns are asymmetric and would under-weight losses, which is the
  wrong bias for a tool whose whole subject is risk.
- **Regression, not direction classification.** A classifier gives a cleaner confidence story, but
  produces no expected-return figure — and FR-09 lists one, while the M4 optimizer consumes one.
- **One month, not one week.** Weekly equity returns are close to noise. A month is long enough that
  there is some signal to find, and short enough to be a decision-relevant horizon.

## Trend and confidence

Trend is the sign of the prediction with a **±1% dead band**, so FLAT is an answer the model can
actually give. Without it, a predicted +0.02% would be reported as "UP" in the same vocabulary as a
predicted 8% rally, overstating what the model said.

**Confidence is derived from the width of the model's own q10–q90 predictive interval**, normalised
against a 20% ceiling. A narrow interval means the model's answer is stable for this input; a wide
one means it is not.

Read that carefully, because "confidence" is the easiest number in this system to over-read:

> It is a statement about the **model's internal consistency**, not about the market, and not a
> probability that the prediction is correct. The model does not know whether it is right.

---

## Features

Nineteen columns, all scale-free by construction — ratios, returns and bounded oscillators, never
raw price levels — so that a single pooled model can span a $9 ETF and a $400 one. Full list in
`app/ml/features/market.py`.

Technical (FR-07): SMA and EMA ratios, RSI(14), MACD histogram, Bollinger position, realised
volatility at 20 and 60 days, momentum at 20 and 60 days, lagged returns at 1/5/10/20 days, and
60-day correlation against SPY.

Macro (FR-05): inflation, interest rate, unemployment — **forward-filled, never interpolated.** A
CPI figure published in January is the most recent *known* value every day until February's release.
Interpolating between them would place a number in the feature matrix before it existed, which is
look-ahead leakage in a respectable disguise.

---

## Split methodology and leakage checks

This is the part §18 asks to be published, and the part that determines whether any metric below
means anything.

A 20-day forward return means each row's target window overlaps the next 19 rows'. Under a random
train/test split, near-identical samples land on both sides and the reported R² becomes fiction.
Two defences:

1. **Chronological split.** Test data comes strictly after training data — the only arrangement that
   resembles how the model is actually used.
2. **A purge gap of two horizons between them.** Even a chronological split leaks at the seam: the
   last training row's target reaches 20 days forward, into the test period. Those rows are dropped
   entirely.

Splitting is by **date**, not row index: the matrix is pooled across ~32 assets that all contribute
rows for the same day, and an index split would cut through the middle of a date.

Both properties are enforced by tests (`tests/test_prediction.py::TestSplitDiscipline`), and each
indicator has its own no-look-ahead test that appends future data and checks earlier values do not
move (`tests/test_features.py::TestNoLookAhead`). Leakage is invisible to every other kind of test,
so it is checked rather than reviewed.

---

## Evaluation

Metrics per §18: MAE, RMSE, R². Baseline for the §10.5 first-promotion gate: predict the training
mean — what you get for predicting nothing at all.

Promotion compares on **RMSE**, not R². R² against a near-zero-signal target is unstable and can go
negative, which makes "higher is better" comparisons behave strangely at exactly the moment you need
them to be trustworthy.

**A near-zero R² is the expected result, and reporting it is the point.** Predicting one-month
equity returns from public price history is close to the definition of a hard problem; the honest
outcome is a model that barely beats the mean. §18's reported-metrics discipline exists because the
source report quoted R² = 0.95, which is not a number this task produces on real data. If a run here
ever reports something like it, the first assumption should be leakage, not success.

Measured values live on the `models` row per version — `python -m app.jobs models`.

---

## Explainability (added M6)

Every stored prediction can be decomposed with **TreeSHAP**, served at
`GET /api/v1/market/{symbol}/prediction/explanation` and rendered on the Explainability page.

Three properties are load-bearing:

- **The contributions are exact, not sampled.** XGBoost implements TreeSHAP natively; the
  `shap` package is not a dependency. `base_value + Σ contributions == predicted_return`, asserted
  in `app/ml/explain.py` and in the test suite rather than assumed. If the identity ever fails the
  API returns 503 instead of serving numbers that do not add up.
- **The explanation uses the model version that made the prediction**, not whatever is in production
  now. The features are rebuilt as of the stored prediction's own date, the artifact is loaded by
  that version, and the recomputed value is checked against the stored one — a mismatch is reported
  as `reproduced: false` rather than quietly presented as a decomposition of a different number.
- **Only the point model is explained.** The q10/q90 pair produces the confidence figure, and "why
  is the model unsure?" is a different question. Attributing an interval width to features would
  invite reading it as a second prediction.

## Drift monitoring (added M6)

`python -m app.jobs monitor` runs nightly (01:00 UTC) and writes to `model_monitoring`:

- **Population Stability Index** per fitted feature, the training window against the last 90 days,
  binned on the *reference* distribution's deciles. Bands: < 0.10 stable, 0.10–0.25 watch, > 0.25
  alert.
- **Rolling RMSE** over predictions whose horizon has actually elapsed, against the RMSE recorded at
  training. Bands: < 1.2× stable, 1.2–1.5× watch, > 1.5× alert. Predictions whose horizon has not
  closed are excluded rather than part-scored — comparing a 20-day forecast against a 2-day move
  would report the mismatch as model error.

The bands were written into `Docs/PLAN/PHASE-6-HARDENING.md` §2.2 **before any measurement was
taken**. A drift monitor whose thresholds are chosen after seeing the first run reports that
everything is fine, permanently, by construction.

An alert writes a row and logs at WARNING. It does not retrain, demote or unregister anything:
§10.5 requires promotion to be reviewed manually in the MVP, and a monitor that acts on its own is
that review removed.

A check that cannot run records `INSUFFICIENT_DATA` with a reason, never `STABLE`. On a dashboard,
"measured and fine" and "not measured" are indistinguishable unless the code refuses to conflate
them — and on a model this close to noise, "not measured" is the common case early on.

---

## Limitations

- Public price and macro history only. No fundamentals, no news, no order flow, no alternative data.
- Trained pooled across the universe, so it captures cross-sectional regularities rather than
  anything specific to one instrument.
- Assumes the historical relationship persists (PRD §7.1). It will not across a regime change, and
  the model has no way to signal that it has stopped applying — drift monitoring is M6 work.
- No transaction costs, liquidity or slippage are modelled; those enter with the M4 backtest (§19).
- Output is educational, not advice, and is not a buy or sell signal (§17.1, §5).
