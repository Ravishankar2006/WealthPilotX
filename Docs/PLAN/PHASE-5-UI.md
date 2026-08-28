# Phase 5 — UI (PRD Milestone M5)

**Estimated duration:** 2 weeks solo/part-time (PRD §22)
**Status:** Complete — see §9.
**Exit condition:** A logged-in user with a complete profile can see all seven FR-15 elements on one
screen without navigating, read why each holding was recommended, browse market history and
predictions, and do all of it with the §17.1 disclaimers present and the §16.5 accessibility rules
met — colour never the sole carrier of meaning.

---

## 1. Scope

### In scope

| PRD ref | What ships |
|---|---|
| FR-15 | Dashboard — risk score, risk profile, market outlook, portfolio, expected return, expected risk, explanations |
| FR-12 | Portfolio page — allocation, expected return and risk, backtest-style history |
| FR-13 | Recommendation detail — the reason per holding, and the constraint set behind it |
| §14 | Risk Profile, Market Intelligence, Asset Details, Portfolio, Recommendation Details pages |
| §15 | React + TypeScript + Tailwind + Plotly |
| §16.1 | Dashboard usable in under 2 seconds |
| §16.5 | WCAG 2.1 AA contrast on risk category and gains/losses; non-colour indicators on every chart |
| §17.1 | Disclaimer on every recommendation and prediction view |

### Deliberately deferred

- **Explainability page (SHAP/LIME)** and **Fairness page** — both §14 entries, both M6. The
  navigation does not link to pages that do not exist.
- **Any write surface beyond what M1–M4 already expose.** No trade buttons, no "accept this
  portfolio", no holdings entry. A recommendation is not a position (PRD §5).
- **Real-time updates.** §21 lists it as Could-Have. Data refreshes when the user asks.
- **A dashboard aggregate endpoint.** §13.2 does not list one; the dashboard fetches the existing
  endpoints in parallel. Adding a backend endpoint to save a round trip would widen the documented
  API surface for a problem the browser can solve.

### Judgment calls to confirm

1. **`Asset Details` is a panel on Market Intelligence, not a separate route.** §14 lists it
   separately, but everything it would show — price history, the six FR-09 metrics, the prediction —
   comes from two endpoints for one symbol. A dedicated route would be a second page that repeats
   the first one's fetch and layout. It is deep-linkable through a `?symbol=` query, so it stays
   addressable.

   *Revised during implementation:* it ships as a **master/detail layout** — the panel renders above
   the asset list — rather than as an inline expansion inside the list. See §9.
2. **`Risk Profile` is a section of the dashboard plus its own route.** FR-15 requires the risk
   score and profile to be *on the dashboard without navigation*, so the content has to be there
   anyway. The route exists for depth — the full factor breakdown and assessment history.

---

## 2. Decisions to lock before coding

Items 1–2 were put to the maintainer directly.

1. **Charts — `plotly.js-basic-dist-min`, lazy-loaded.** ~1MB raw against the full bundle's ~4.5MB,
   and it carries scatter, line, bar and pie, which is every chart this phase needs. Loaded through
   a dynamic `import()` so it stays out of the initial bundle: the dashboard shell paints, then the
   charts arrive. §16.1 allows two seconds for the dashboard, and shipping 4.5MB of plotting library
   before the first paint would spend most of that on code the user cannot yet see.

2. **Nothing expensive runs on mount.** A user with a complete profile but no assessment sees what
   is missing and a button. `/risk/analyze` and `/portfolio/generate` both sit in the 10 req/min
   expensive bucket and run models on every call — auto-running them on mount means a page refresh
   silently spends that budget, and it generates a portfolio in someone's name that they never asked
   for. FR-15's acceptance criterion presumes a recommendation already exists, so this is also the
   reading the PRD implies.

3. **Colour is never the only signal (§16.5).** Every gain/loss and every risk category carries a
   text label or a shape as well as a colour, and charts get patterns or direct labels. This is not
   only a colour-blindness accommodation: a screenshot pasted into a monochrome report, or a chart
   read at a glance in bright sun, both fail on colour alone.

4. **Numbers are never invented to fill a layout.** Where a value is unavailable — no prediction for
   an asset, too little history for a metric — the UI says so and why. FR-09 already requires
   metrics to be "returned or explicitly marked unavailable with a reason"; the UI honours that
   rather than rendering a dash that looks like zero.

5. **Money and percentages are formatted in one place.** A `format.ts` module owns every number
   the user sees. Percentages, currency and dates formatted ad hoc across twelve components is how
   a dashboard ends up showing 0.0642 in one panel and 6.42% in another.

---

## 3. Target layout added this phase

```
frontend/src/
├── api/
│   └── resources.ts            # typed wrappers for every M2–M4 endpoint
├── components/
│   ├── charts/
│   │   ├── Chart.tsx           # lazy Plotly wrapper, shared theme and a11y defaults
│   │   ├── PriceChart.tsx      # market history line
│   │   ├── AllocationChart.tsx # portfolio weights, labelled on the slices
│   │   └── ClassBreakdown.tsx  # weight by asset class against its constraint cap
│   ├── RiskBadge.tsx           # LOW/MEDIUM/HIGH — colour *and* label *and* shape
│   ├── Metric.tsx              # one number with its label and unavailable state
│   ├── Trend.tsx               # UP/DOWN/FLAT with an arrow glyph, not just colour
│   └── EmptyState.tsx          # the "nothing here yet, here is why" pattern
├── pages/
│   ├── Dashboard.tsx           # FR-15 — all seven elements
│   ├── RiskProfile.tsx         # factor breakdown, assessment history
│   ├── Market.tsx              # asset detail above a universe list
│   ├── Portfolio.tsx           # FR-12 — allocation, metrics, history
│   └── RecommendationDetail.tsx# FR-13 — one holding's reason and context
├── lib/format.ts               # every user-visible number, in one place
└── types/plotly-basic.d.ts     # the basic bundle ships no types of its own
```

---

## 4. FR-15's seven elements, and where each comes from

The acceptance criterion is that all seven render without further navigation.

| # | Element | Source |
|---|---|---|
| 1 | Risk score | `GET /risk/latest` → `risk_score` |
| 2 | Risk profile | `GET /risk/latest` → `risk_category` + `top_factors` |
| 3 | Market outlook | `GET /market/assets` + per-symbol `GET /market/{s}/prediction` for a small set |
| 4 | Recommended portfolio | `GET /portfolio/current` → `holdings` |
| 5 | Expected return | `GET /portfolio/current` → `expected_return` |
| 6 | Expected risk | `GET /portfolio/current` → `expected_risk` |
| 7 | Recommendation explanations | `GET /portfolio/current` → `holdings[].reason` |

Four requests in parallel, plus a bounded set of prediction calls for the outlook strip. A 404 from
`/risk/latest` or `/portfolio/current` is an empty state, not an error — it is the ordinary
condition for a new user.

---

## 5. Task stages

### Stage 1 — Foundations
- [x] `lib/format.ts` — percentage, currency, date, and a compact number formatter
- [x] Typed API wrappers for the M2–M4 endpoints, mirroring the backend schemas
- [x] `Chart.tsx` — lazy Plotly, shared theme, responsive, `aria-label` from the caller
- [x] `RiskBadge`, `Trend`, `Metric`, `EmptyState` primitives

### Stage 2 — Dashboard (FR-15)
- [x] All seven elements on one screen, parallel fetch, no navigation required
- [x] Empty states that name the missing step and offer the action (decision 2)
- [x] Run-risk-assessment and generate-portfolio actions with pending and error states
- [x] Rate-limit (429) handled with a readable message rather than a generic failure

### Stage 3 — Portfolio (FR-12, FR-13)
- [x] Allocation chart plus a table with weights, classes and reasons
- [x] Expected return and expected risk, labelled as estimates rather than forecasts
- [x] Portfolio history, paginated
- [x] The constraint set behind the allocation, rendered readably

### Stage 4 — Market Intelligence and Risk Profile
- [x] Asset list with class filter, price history chart per symbol
- [x] Prediction panel — trend, expected return, confidence, model version, unavailable reasons
- [x] Risk profile page — score, category, full factor breakdown

### Stage 5 — Accessibility and polish (§16.5)
- [x] Contrast audit on risk categories and gain/loss text against WCAG 2.1 AA
- [x] Every chart carries non-colour encoding and a text alternative
- [x] Keyboard navigation and focus-visible styles across new interactive elements
- [x] Disclaimers present on every prediction and recommendation view (§17.1)

### Stage 6 — Tests and close-out
- [x] Vitest coverage for each new page's loading, empty, error and populated states
- [x] A test asserting the seven FR-15 elements are present in one render
- [x] `README` and this checklist updated
- [x] Manual QA pass against §6, in a real browser

---

## 6. Manual QA checklist (§20 milestone gate)

1. New account, no profile → dashboard explains what to do first, no errors in the console.
2. Complete the profile → dashboard offers the risk assessment; nothing has run on its own.
3. Run it → risk score, category and factors appear without a page reload.
4. Generate a portfolio → allocation, expected return, expected risk and reasons all render.
5. All seven FR-15 elements visible without scrolling past the fold on a 1280×800 viewport.
6. Reload → the same data, from stored state, with no model re-run.
7. Market page → pick a symbol, see history and a prediction; pick one with no prediction and see
   the reason rather than a blank.
8. Every chart still readable in greyscale (§16.5).
9. Keyboard-only: reach and operate every action on the dashboard.
10. Dashboard interactive in under 2 seconds on a warm cache (§16.1).

---

## 7. Risks specific to this phase

| Risk | Mitigation |
|---|---|
| Plotly dominating the bundle | Basic build, dynamic import, measured in the close-out |
| The dashboard inventing numbers to look complete | Decision 4 — unavailable is a state with a reason, not a dash |
| Colour-only encoding slipping in | Decision 3, plus a greyscale check in the QA pass |
| Model outputs presented as fact | §17.1 disclaimers on every such view, and estimate-not-forecast wording next to the figures |
| Expensive endpoints called on mount | Decision 2; tests assert no model call fires without a click |
| Frontend and backend schemas drifting | Typed wrappers mirroring the Pydantic shapes; a contract test pins the field names |

---

## 8. What Phase 6 needs from this phase

M6 (Advanced AI & Hardening) adds the Explainability and Fairness pages listed in §14, plus SHAP
values on the recommendation detail view. It needs: the chart wrapper, the formatting module, the
page shell and navigation pattern established here, and the recommendation detail route to extend
rather than replace.

---

## 9. Verification log

Recorded 2026-08-28, against the stack running in Docker and driven through Chrome.

### Green

| Check | Result |
|---|---|
| Frontend suite | 49 passed (was 16 at end of M4) |
| Backend suite | 373 passed, unchanged by this phase |
| `tsc -b` | clean |
| Production build | app **68 KB gzipped**, Plotly a separate **377 KB** chunk |
| FR-15 — all seven elements in one render | verified in a test and in the browser |
| Nothing expensive on mount | asserted in a test: no `/risk/analyze` or `/portfolio/generate` call before a click |
| Empty states | a new account sees what to do next, with no error state anywhere |
| §17.1 disclaimers | present on the dashboard, portfolio, market, risk and recommendation views |
| §16.5 greyscale | dashboard fully readable with colour removed |
| §16.1 | dashboard interactive well under 2s; portfolio generation 0.2s server-side |

### Browser QA pass — all ten steps

| # | Step | Result |
|---|---|---|
| 1 | New account, no profile | Pass — the dashboard explains what to do first |
| 2 | Complete profile → offer, not auto-run | Pass — nothing ran on its own |
| 3 | Run the risk assessment | Pass — score 0.538, MEDIUM, three factors, no reload |
| 4 | Generate a portfolio | Pass — 7 holdings, 10.5% return, 6.3% risk, reasons on every holding |
| 5 | All seven FR-15 elements | Pass |
| 6 | Reload | Pass — same data from stored state, no model re-run |
| 7 | Market page, symbol with and without a prediction | Pass — six metrics, or the reason they are unavailable |
| 8 | Greyscale | Pass — see below |
| 9 | Keyboard | Pass — every dashboard action reachable and operable |
| 10 | Under two seconds | Pass |

**On greyscale specifically.** With colour removed, the market outlook still reads
"▼ Downward −4.9%", "▲ Upward +1.8%", "▬ Flat −0.3%", and the risk badge reads
"▮▮▯ Medium risk". The allocation donut labels each slice directly rather than
through a legend. Nothing in the interface depends on hue.

### Bugs this stage caught

1. **The price chart flattened every series it drew.** `fill: "tozeroy"` makes Plotly
   extend the y-axis down to zero so the fill has somewhere to land — so SPY, which
   trades between 160 and 210, rendered as a near-straight line in the top fifth of
   the chart with every actual movement compressed away. The chart's own code comment
   claimed the axis was *not* zero-based, which is how the defect survived review: the
   intent was written down and the implementation contradicted it. Fill removed.
2. **A deep link opened content 900px below the fold.** `?symbol=SPY` expanded the
   right row of a 32-row alphabetical list and left the viewport at the top, so the
   link appeared to do nothing. Two attempts to scroll to it both failed — an effect
   raced the list render, and a smooth scroll was abandoned partway when the detail
   panel finished its own fetch and re-rendered. Fixed by changing the layout instead
   of the timing: the detail now renders **above** the list, so there is nothing to
   scroll to. A test asserts that document order.
3. **`DDL()` interpolation broke the M4 weight-sum trigger under `create_all`.** Not
   strictly this phase, but found while running the suite: SQLAlchemy's `DDL` runs its
   text through printf formatting, and the plpgsql body is full of `%`. Replaced with
   a plain `after_create` callback so the SQL stays identical to the migration's copy.

### Notes and honest limitations

- **Plotly is 377 KB gzipped**, five times the rest of the application. It is
  code-split so it never blocks the first paint, but it is still the dominant cost of
  any page with a chart. §15 names Plotly, so this is the price of the specified
  stack; a lighter library would be a spec deviation to record deliberately.
- **The contrast audit was done against the palette, not with an automated checker.**
  The three risk-badge colour pairs were chosen to clear WCAG 2.1 AA 4.5:1 — the amber
  in particular is much darker than a default amber-600, which does not clear it. A
  scripted audit across every text/background pair is M6 hardening work.
- **`RiskGauge` was planned and not built.** The risk score reads better as a large
  number with its scale stated than as a dial, and a gauge would have been a chart
  that adds no information. The risk profile page uses proportion bars for the factor
  breakdown instead, where the comparison is the point.
- **Ports** — as in M1–M4, this machine uses 55432 / 8010 / 5183.
