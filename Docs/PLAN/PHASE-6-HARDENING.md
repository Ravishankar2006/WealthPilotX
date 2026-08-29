# Phase 6 — Advanced AI & Hardening (PRD Milestone M6)

**Estimated duration:** 2–3 weeks solo/part-time (PRD §22)
**Status:** Complete — see §7. The three PRD gaps found in the closing audit are also closed; see
§7's "Closing the last three PRD gaps".
**Exit condition:** Every model output the product serves can be traced to the inputs that produced
it; the system reports its own drift, its own request metrics and its own group-level disparities;
the dependency and access-control surface has been reviewed and written down; and the test suite has
a measured coverage floor that CI enforces.

---

## 1. Scope

### In scope

| PRD ref | What ships |
|---|---|
| FR-13 (advanced) | SHAP attributions on market predictions, surfaced through an Explainability page |
| FR-14 | `GET /api/v1/fairness/report` and the Fairness page — group statistics with n ≥ 20 suppression |
| §10.5 | Drift monitoring — input-feature stability and rolling prediction error, with alert thresholds |
| §16.4 | Basic metrics — request latency, error rate, ingestion success rate, prediction latency |
| §16.2 | Security review: dependency scan promoted to a blocking gate, response security headers, access-control audit |
| §20 | Test coverage pass — measured, with a floor CI enforces |
| §14 | Explainability and Fairness pages, the last two §14 entries |

### Deliberately out of scope

- **LLM financial assistant.** See §2.4 — declined, with reasons, not deferred.
- **Automated retraining.** §21 Could-Have. Drift *detection* ships; the response stays a human
  decision, which is what §10.5 asks for in the MVP ("reviewed manually before promotion").
- **Real-time market updates**, **deep learning**, **reinforcement learning** — §21 Could-Have,
  none of them hardening.
- **An admin/auditor role.** The fairness report needs one conceptually; inventing a privilege tier
  in the last milestone is a larger change than the endpoint it would protect. See §2.3.

---

## 2. Decisions to lock before coding

### 2.1 SHAP goes where the model is the decision — nowhere else

FR-13 says the advanced implementation *can* use SHAP or LIME. It ships on exactly one surface:

**Market prediction (XGBoost) — yes.** A gradient-boosted ensemble over ~20 correlated technical and
macro features has no closed form. TreeSHAP gives exact Shapley values for tree models in polynomial
time, so this is attribution, not approximation.

It ships **without the `shap` package**. XGBoost implements the same TreeSHAP algorithm natively —
`Booster.predict(..., pred_contribs=True)` returns per-feature contributions plus a bias term that
sum exactly to the model's output. Verified before writing any of it: contributions summed to the
prediction to float32 precision. The `shap` package would add numba and llvmlite to the image to
reimplement a routine the library already ships.

**Risk classification — no, and that is the stronger answer.** The risk *score* the user is served
is the rubric's own weighted sum (`app/ml/risk/rubric.py`), so each factor's contribution is already
known exactly and is already returned in `top_factors`. Running SHAP here would produce a sampled
approximation of a forest that itself approximates a rule this repo wrote — strictly less accurate
than the decomposition already on screen, while looking more sophisticated. That trade is the wrong
way round.

The risk model still needs *validation* attribution rather than serving attribution: does the forest
rely on the factors the rubric says matter? That question is answered with scikit-learn's
`permutation_importance` — model-agnostic, and unlike the impurity importances already stored, not
biased toward high-cardinality features. If the forest leans on `age` when the rubric weights it at
0.15, that is a finding about the model, and it belongs in the model card, not in a user-facing
panel.

### 2.2 Drift thresholds are declared up front, not tuned to look calm

- **Input stability:** Population Stability Index per feature, recent window vs the training window.
  PSI < 0.10 stable · 0.10–0.25 watch · > 0.25 alert. These are the conventional bands; picking them
  before seeing the numbers is the point.
- **Prediction error:** rolling RMSE over realised horizons vs the RMSE recorded at training time.
  Alert at > 1.5×.
- An alert is a structured log line at WARNING and a stored row. It does not retrain, unregister or
  demote anything automatically.

### 2.3 The fairness report is authenticated, aggregate-only, and suppressed below n = 20

§11.2 sets the minimum group size. Below it, the group is reported as `suppressed` with its metrics
null — never as a zero, which would read as a measured result.

Access is plain user auth, matching §13.1's "Bearer JWT on everything". That is weaker than it
should be: an audit surface belongs behind an auditor role. What makes it defensible for now is that
the response contains no individual-level data at any threshold — suppression happens before
serialisation, not in the UI. Recorded as a limitation, not as a solved problem.

**Grouping attributes:** age band, income band, financial literacy, investment experience. Age and
income are already model inputs, so disparity across them is *expected* — the audit question is
whether it is proportionate to the rubric's declared weights, not whether it is zero. Literacy and
experience are the interesting ones: they are self-reported proxies for access and education, and a
system that routes less-experienced users to systematically worse expected outcomes would be doing
something worth catching.

### 2.4 The LLM assistant is declined, not deferred

§22 lists it as optional within M6 and §21 as Could-Have. It should not be built here, and probably
not later:

A free-text assistant over a user's own financial profile answers "should I buy Tesla?" the moment
someone asks, and answers it *personally*, because it has their age, income and risk category in
context. That is the exact activity §17.2 says triggers registered-adviser obligations in many
jurisdictions, and it is the first item on this repo's hard non-goals list. A disclaimer under the
chat box does not change what the paragraph above it said. Every other surface in this product
constrains its own output shape — a category, a weight vector, a scored reason — and that
constraint is what keeps it on the educational side of the line. A chat box removes it.

If it is ever wanted, the safe form is retrieval over the product's own documentation and glossary
with no access to the user's profile, which is a documentation feature rather than an AI assistant.

### 2.5 Coverage is a floor, not a target

`pytest --cov` with a `--cov-fail-under` floor set at the measured value, rounded down. The number
exists to stop coverage falling, not to be chased upward: tests written to cover a line rather than
a behaviour make the suite slower and the number prettier.

---

## 3. What gets added

```
backend/app/
  ml/
    explain.py             TreeSHAP attributions for the prediction model
    monitoring.py          PSI + rolling error, thresholds, alert records
  services/
    explanation_service.py read path for the explainability endpoints
    fairness_service.py    group aggregation, suppression, disparity metrics
    metrics_service.py     in-process counters, snapshot for /metrics
  api/v1/
    fairness.py            GET /fairness/report
    explain.py             GET /market/{symbol}/prediction/explanation
    metrics.py             GET /metrics
  models/
    model_monitoring.py    drift observations
  core/
    security_headers.py    HSTS, nosniff, frame-deny, referrer policy, CSP
frontend/src/pages/
  Explainability.tsx
  Fairness.tsx
Docs/
  SECURITY-REVIEW.md       §16.2 review, findings and what was and was not fixed
  MODELS/*.md              updated with SHAP-vs-rubric validation results
```

One migration: `model_monitoring`.

---

## 4. Task stages

### Stage 1 — SHAP explainability (FR-13)
- [x] `ml/explain.py` — TreeSHAP via XGBoost's own `pred_contribs`, no new dependency
- [x] Explain the *stored* prediction with the model version that made it, not the current one
- [x] `GET /api/v1/market/{symbol}/prediction/explanation` with `model_version` and the §17.1 disclaimer
- [x] Risk-model validation: permutation importance vs rubric weights, written into the model card
- [x] Tests: attributions plus base value sum to the prediction; unavailable model → 503, not 500

### Stage 2 — Fairness report (FR-14)
- [x] `fairness_service.py` — banding, group aggregation, suppression at n < 20
- [x] Disparity metric: four-fifths ratio on HIGH-risk assignment (equity weight is reported per group, not as a ratio — see §7)
- [x] `GET /api/v1/fairness/report`
- [x] Tests: a group of 19 is suppressed; suppression happens before serialisation; no raw
      income/savings value can appear in the response at any group size

### Stage 3 — Model monitoring (§10.5)
- [x] `model_monitoring` table + migration
- [x] `ml/monitoring.py` — PSI per feature, rolling prediction RMSE vs training RMSE
- [x] `python -m app.jobs monitor`, wired into the scheduler
- [x] Alerts as WARNING log lines with the feature and the measured value
- [x] Tests: a deliberately shifted distribution trips the threshold; a stable one does not

### Stage 4 — Metrics and headers (§16.4, §16.2)
- [x] `metrics_service.py` — request count, latency histogram, error rate, prediction latency
- [x] Ingestion job success rate from the existing `ingestion_runs` table
- [x] `GET /api/v1/metrics`
- [x] Security headers middleware; HSTS only when not in development
- [x] Tests: counters move; headers present on every response including errors

### Stage 5 — Frontend (§14)
- [x] Explainability page — SHAP contributions for a chosen asset, rubric factors for the user's risk
- [x] Fairness page — group tables, suppression stated in words, disparity metrics
- [x] Navigation entries for both
- [x] Tests: suppressed groups render as "not reported", never as 0

### Stage 6 — Security review and coverage (§16.2, §20)
- [x] `pip-audit` and `npm audit` run; findings triaged and written into `Docs/SECURITY-REVIEW.md`
- [x] CI audit job promoted from `continue-on-error` to blocking on high severity
- [x] Access-control matrix: every endpoint × (anonymous, other user, owner) asserted in tests
- [x] `pytest --cov` with a floor; frontend coverage measured
- [x] Close-out: README, model cards, verification log

---

## 5. Manual QA checklist (§20 milestone gate)

1. Prediction explanation lists contributions that sum to the prediction and names the model version.
2. Fairness page loads on a near-empty database and says every group is suppressed — no zeros, no
   empty chart, no crash.
3. `python -m app.jobs monitor` writes rows and logs a stable verdict on unshifted data.
4. An artificially shifted feature produces a WARNING line naming that feature.
5. `/metrics` reflects traffic just generated, and rejects an unauthenticated caller.
6. Security headers present on a 200, a 404 and a 500.
7. A second user cannot read the first user's profile, risk assessment, portfolio, or explanation.
8. Greyscale pass on both new pages (§16.5).
9. Disclaimers present on the explainability view (§17.1).
10. Full suite green, coverage at or above the floor.

---

## 6. Risks specific to this phase

| Risk | Handling |
|---|---|
| ~~`shap` pulls a heavy dependency tree~~ | Resolved before coding: XGBoost's built-in `pred_contribs` is the same exact TreeSHAP, no new dependency. See §2.1 |
| Fairness metrics computed on almost no users read as findings | Suppression is the mechanism; the page states group sizes and says plainly that nothing is measurable yet |
| Drift thresholds tuned until they look calm | Bands declared in §2.2 before any data was looked at |
| Coverage pass turns into test theatre | Floor set at the measured value; new tests target uncovered *behaviour* — access control, error paths — not uncovered lines |
| Metrics endpoint becomes an information leak | Aggregate counters only; no paths with identifiers, no per-user series |

---

## 7. Verification log

Recorded 2026-08-29, against the stack running in Docker.

### Green

| Check | Result |
|---|---|
| Backend suite | **479 passed, 6 skipped** (was 373 at the end of M5) |
| Backend coverage | **89.71%**, floor set at 89 in CI |
| Frontend suite | **67 passed** (was 49) |
| Frontend coverage | 77.5% statements / 86.3% branches, floors at 75/80 |
| `mypy app` | clean, 105 files |
| `ruff check` / `ruff format --check` | clean |
| Migration round-trip | `downgrade base` → `upgrade head` clean; autogenerate diff empty |
| Production build | 71.18 kB gzipped app + 377.51 kB Plotly chunk (unchanged split) |
| `pip-audit` | no known vulnerabilities |
| `npm audit` | 2 moderate, both `react-router`, assessed as unreachable — see `Docs/SECURITY-REVIEW.md` Finding 3 |

The 6 skips are the public routes in the access-control parametrisation
(`/auth/register`, `/auth/login`, `/health`, …), skipped by design because §13.1 exempts them.

### API verification pass

Run against the development stack with a freshly registered account.

1. **Security headers on a 200.** All seven present: `nosniff`, `DENY`, `no-referrer`,
   `same-origin`, `same-site`, the `Permissions-Policy` denial list, and
   `default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`.
2. **Security headers on a 401.** Present — the middleware runs outside the error handling.
3. **HSTS absent locally.** After the fix in §7's bug list; it was present before.
4. **Prediction explanation for SPY.** `model_version: v1`, `reproduced: true`, predicted −5.31%
   against a +0.46% baseline, 6 of 19 contributions served. All six negative, led by
   "Correlation with the market (60-day)" at −0.0257 — coherent with a bearish prediction rather
   than a mix that happens to sum the right way.
5. **The Shapley identity, over the full feature set.**
   `base +0.0046034297` + `Σ contributions −0.0576563747` = `−0.0530529450` against a prediction of
   `−0.0530529432`. **Residual −1.8 × 10⁻⁹**, tolerance 10⁻⁵.
6. **Fairness report on a near-empty instance.** Population 0; all 15 groups across 4 dimensions
   suppressed; every dimension carries the note explaining why no ratio could be computed; no
   suppressed group carries a non-null metric.
7. **Metrics.** Route templates only — a request to `/market/SPY/prediction/explanation` recorded
   under `{symbol}`, and no key anywhere contains "SPY". Ingestion success rates read from the run
   table: `ingest_market` 1/1, `ingest_economic` 2/2.
8. **`python -m app.jobs monitor`.** 19 feature rows plus one error row; worst-first ordering; exit
   code 0 with alerts present, because a drift finding is not a job failure.

### Bugs this phase caught

**The drift monitor's first real run produced three alerts, and all three were artefacts.**
`inflation`, `interest_rate` and `unemployment` each reported PSI between 9.9 and 14.6 — forty to
sixty times the 0.25 alert threshold. Nothing had drifted. The macro series are monthly values
forward-filled onto trading days, so `inflation` has 11 distinct values across the two-year training
window and **2** across the recent 90 days. All the recent mass lands in one or two reference
deciles and PSI explodes mechanically. It would have produced the same three alerts every night
forever, which is how a monitor teaches its operator to ignore it. PSI now requires at least as many
distinct values as it has bins in *each* window; below that the check reports `INSUFFICIENT_DATA`
naming the distinct counts, and records both windows' ranges — which is the comparison that is
actually meaningful for a level series, and which shows the genuine shift here (inflation moved from
[326.8, 329.2] to [330.5, 331.1]) without pretending PSI measured it.

**`GET /fairness/report` returned 500 on any instance with enough users to report.** The endpoint
called `vars()` on `Disparity`, which is a `slots=True` dataclass and therefore has no `__dict__`.
Reaching that line needs two groups of twenty-plus users, so the service tests — which build and
assert on `Disparity` objects directly — passed, the endpoint tests — which only ever ran against an
empty instance, where `disparity` is `None` — passed, and the frontend test passed against a
hand-written fixture the server could not actually produce. Three layers of green over a line that
had never executed. Found by seeding 96 users and opening the page, which showed "Could not load the
fairness report" — the error state working correctly, on a report that should have loaded. Fixed with
`dataclasses.asdict`, and `test_a_populated_instance_serialises_its_disparity` now covers the branch;
it was confirmed to fail against the old code before the fix was kept.

**HSTS was being sent from the development stack.** The guard was written as "not in
`{development, test}`", and this repo's environments are `local | test | staging | production` —
`development` is not one of them, so the condition matched nothing. A local server was telling every
browser that touched it to refuse plain HTTP on `localhost` for a year, affecting every other
project on the machine. Found by curling the running API, not by the test, which asserted against
the same invented name. The guard is now an allow-list of the deployed environments, a test covers
every name the settings `Literal` permits, and a further test pins the two lists together so adding
an environment fails loudly rather than silently defaulting to no HSTS.

**The Explainability page rendered every contribution 100× too large.** `signedPercent` already
scales by 100, and the page multiplied first — so a +1.21% contribution displayed as +121%. Caught
by the first frontend test written against it.

**An asset universe without SPY could not train at all.** `BENCHMARK_SYMBOL`'s docstring said an
absent benchmark meant "correlation features are simply omitted rather than the whole pipeline
failing". It did not: `benchmark_correlation_60` sat in `MARKET_FEATURE_COLUMNS`, which
`usable_feature_columns` never drops, so an all-NaN correlation column took every row with it when
the warm-up rows were trimmed. Found because a test seeded a single non-SPY asset. Fixed by giving
that one column macro-like treatment — it is the only market feature that depends on a *second*
asset's history — and by filtering the required-column list through the usable set, since dropping a
column achieves nothing if it is still required two lines later.

**The metrics endpoint would have logged every symbol a user looked at.** The first version recorded
`request.url.path`. That writes user-chosen symbols, and on other routes resource ids, into a
surface designed to be scraped and retained. Fixed before it shipped; a test asserts it. A second
bug surfaced while fixing it: this FastAPI version nests included routers, so the naive template was
router-relative and `/api/v2/health` would one day have shared a series with `/api/v1/health`.

**`model_monitoring` was missing from the test truncation list.** Test isolation only, but the same
shape of mistake as the schema drift M2 found — a new table added in one place and not the other.

**I wiped the development database.** The migration round-trip check (`downgrade base` then
`upgrade head`) was run against the development database rather than a scratch one. Nothing
irreplaceable was lost — the data is regenerated by `bootstrap`, `train-*` and `predict` — but the
check belongs on a throwaway database and was rebuilt by hand instead.

### Browser QA pass

Run after the rest of the phase, once the Chrome extension was available. Driven against the
development stack.

1. **Dashboard still renders after the routing and navigation changes**, with the two new nav entries
   in place. Risk 0.476 MEDIUM, portfolio 11.6% expected return / 6.6% expected risk, 6 holdings.
2. **Explainability, SPY.** −5.31% predicted against a +0.46% baseline, model v1, prediction dated
   28 Aug 2026, six contributions with ▲/▼ glyphs and signed values, "Showing the 6 largest of 19
   features" and the §17.1 inline disclaimer beneath.
3. **Explainability, AGG** — chosen deliberately because its top-six contains a positive
   contribution, so both directions are seen: `Volatility (60-day) ▲ +0.46%` draws a green bar
   *rightward* from the centre line while the negatives draw dark red *leftward*. The diverging
   layout behaves as designed.
4. **The risk panel** renders its exact contributions with the note explaining why it does not use
   SHAP, alongside the Medium-risk badge.
5. **Fairness on a one-user instance.** Population 1, 0 in reportable groups, every metric withheld
   with the group size still shown — which is what justifies the withholding.
6. **Fairness on a 96-user instance** (seeded for this pass, removed afterwards). Four age bands all
   reportable, the "Below four-fifths · 0.10" chip, risk splits as `L 8% · M 8% · H 83%`, and the
   sentence explaining that a difference across age is expected because age feeds the rubric. The
   `under 50k` income band, at zero users, correctly reads "not reported" rather than 0%, and bands
   whose members hold no portfolio read "no portfolios yet" rather than 0.0%.

### Scripted contrast audit (§16.5)

Flagged as outstanding at the end of M5, done here. A WCAG 2.1 relative-luminance audit run in the
page: every visible text element, its computed colour against its nearest opaque ancestor
background, against the 4.5:1 threshold (3:1 for large text).

| Page | Elements checked | Failures | Minimum ratio |
|---|---|---|---|
| `/dashboard` | 228 | **0** | 5.19:1 |
| `/risk` | 86 | **0** | 5.19:1 |
| `/explainability` | 175 | **0** | — |
| `/fairness` | 323 | **0** | — |

The 5.19:1 floor is the muted body colour `rgb(92,109,106)` on the card background, comfortably above
AA's 4.5:1.

**Colour independence** was checked programmatically rather than by eye: all six contribution rows on
the Explainability page carry a ▲ or ▼ glyph *and* a signed percentage in their text content, and the
fairness chip's text reads "Below four-fifths · 0.10". No element on either page depends on hue.

### A tooling note

The greyscale check could not be done the way M5 did it. Applying `filter: grayscale(100%)` puts the
tab into a state where `Page.captureScreenshot` times out after 30 seconds and does not recover until
the page is reloaded; `save_to_disk` triggers the same stall. This is an extension/CDP limitation,
not an application fault — page JavaScript stayed responsive throughout, and a fresh tab on the same
URL rendered normally. The scripted contrast and glyph audit above is what replaced it, and is a
stronger check than reading a greyscale JPEG: it produces numbers rather than an impression.

### Closing the last three PRD gaps

Added after the phase was first closed, when the project was audited against §23's definition of
done and three named deliverables turned out never to have been built.

**1. The backtest reaches a user (§23 item 9, FR-12).** §19's metrics existed from M4 but only
through `python -m app.jobs backtest` — computed correctly and visible to nobody. §13.2 specifies no
backtest endpoint, so §23 asked for something the API section never gave a surface for; that is now
`GET /api/v1/portfolio/backtest`, in the 10 req/min expensive bucket because it simulates a year of
daily returns for every holding.

The computation moved to `services/backtest_service` so the CLI and the endpoint share one path — a
backtest that answered differently depending on who asked would be worse than none. The Portfolio
page renders all five metrics beside the benchmark, plus a growth-of-1 chart where the portfolio is
solid and the benchmark dotted, so the two series are distinguishable without colour.
`sample_equity_curve` keeps the final point exact rather than whatever the stride lands on: verified
end to end, the curve's last value reconciles with the reported total return to four decimal places.

**2. End-to-end tests (§20).** Playwright, three specs, run against the Compose stack. The PRD says
"against a staging environment"; there is none, and §17.2 keeps this project unlaunched, so the
config says so rather than letting someone discover that "staging" meant localhost. Nothing is
stubbed — that is the entire point of the layer.

**3. Terms of Service and Privacy Policy (§17.1).** The registration checkbox already read "I accept
the Terms of Service and Privacy Policy" and gated the submit button, and the server already
required and stored the flag. **Neither document existed.** Users were consenting to nothing, which
is worse than no checkbox, because it creates a record of consent to terms that were never written.
Both now exist as public unguarded routes, linked from the checkbox and from the persistent footer,
and both say plainly that they were written by the project and not reviewed by a lawyer — §17.2
requires that review before any public launch and it has not happened.

### Bugs the three additions caught

**The E2E suite failed on its first run because `.invalid` is a reserved TLD** that the API's email
validation rejects. That is FR-01's field-level 422 behaving exactly as specified, surfaced through
the real form — and it is precisely the class of thing no unit test would find, since every unit
fixture already used `example.com`.

**One of my own E2E assertions was vacuous.** The backtest check accepted `[role="note"]` anywhere
on the page as the "explained why not" branch — and the inline §17.1 disclaimer on that page carries
`role="note"`, so it matched unconditionally and could never fail. Now scoped to the backtest
section.

**Two unnamed tables on one page.** Adding the backtest table made `findByRole("table")` ambiguous
and broke an existing test. The fix was not to disambiguate in the test but to give both tables an
`aria-label` — they were equally ambiguous to a screen reader, and the test was reporting a real
accessibility gap.

**Volatility rendered as `+6.8%`.** `signedPercent` on a magnitude implies a gain of volatility.
Returns and drawdown are signed; volatility is not, and a Sharpe ratio is not a percentage at all.

**The frontend coverage floor did its job.** The new pages dropped statements to 74.27%, under the
75% floor, and CI would have failed. The response was to write tests for the new code, not to lower
the number — coverage is now 78.31%, above where it stood before the additions.

### Notes and honest limitations

- **The fairness report has still never been exercised on real users.** It has now been seen with a
  populated instance, but that population was seeded by hand and deliberately skewed to make the
  four-fifths flag fire. The suppression, banding and disparity paths are exercised; what the report
  would say about actual users is unknown, and on this instance it will say "not reported" for a very
  long time.
- **The 89.71% coverage figure is held down by code that is untestable by design.**
  `app/providers/yahoo.py` sits at 29% because §7.3 forbids CI from calling a third-party API, and
  `app/providers/fred.py` at 62% for the same reason. Those two files are most of the uncovered
  lines. The floor is set at 89 to stop it falling, not as a target to chase.
- **The rubric-alignment check found a real divergence and it was left alone.** The forest relies on
  the savings ratio at roughly half its declared weight. The cause traces to the rubric's own
  saturation point rather than to the model, and changing that would be tuning a rule about people's
  finances to make a metric look better — the trap §18 warns against. Recorded in the model card
  instead.
- **The prediction-error monitor has never returned a measurement.** Every run so far reports
  `INSUFFICIENT_DATA`, because no stored prediction has reached the end of its 20-day horizon on this
  instance. That is the correct answer and it is also, so far, the only answer that half of the
  monitor has given.
- **Nothing here has been reviewed by a security professional or a financial professional.** The risk
  rubric, the constraint bands, the scoring weights, the fairness groupings and the disparity
  threshold are all engineering judgments made inside this repository.

