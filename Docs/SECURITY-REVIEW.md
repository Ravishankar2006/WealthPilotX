# Security review — M6

**Reviewed:** 2026-08-29 · **Scope:** PRD §16.2, §11.2, §17.3 · **Reviewer:** the maintainer, with
Claude Code. No external or professional security review has been performed.

This is a self-review of a solo educational project. It is written to be useful — what was checked,
what was found, what was fixed, and what was left — not to certify anything. Two of the findings
below are open on purpose, with the reasoning recorded, because closing them properly is larger than
this milestone.

---

## 1. §16.2 checklist

| Requirement | Status | Evidence |
|---|---|---|
| Password hashing (bcrypt/argon2) | Met | Argon2id via `argon2-cffi`; `app/core/security.py`. No password reaches a log — `SENSITIVE_KEYS` in `app/core/logging.py` redacts it structurally. |
| Short-lived access tokens + refresh tokens | Met | 15-minute access, 7-day refresh (`app/core/config.py`). Refresh tokens are stored hashed, not in plaintext. |
| Input validation on every endpoint | Met | Pydantic on every request body and query parameter; a validation failure returns §13.1's envelope with field detail, never a stack trace. |
| Users read/modify only their own resources | Met | `tests/test_access_control.py` asserts it route by route, from the OpenAPI schema, so a new endpoint is covered the day it is added. Another user's resource is a **404, not a 403** — a 403 confirms existence. |
| Environment-based secrets | Met | `JWT_SECRET` and `PROFILE_ENCRYPTION_KEY` are required settings with no defaults; the app refuses to start without them. |
| HTTPS in production | Partly — see Finding 2 | HSTS is now sent outside development. Terminating TLS is a deployment concern this repo does not configure. |
| Rate limiting per §13.1 | Met | 100/min default, 10/min on `/risk/analyze` and `/portfolio/generate`, keyed **per user** on authenticated routes (the M3 fix: it was silently per-IP). |
| Dependency scanning in CI | Met, and now blocking — see §3 | `pip-audit` and `npm audit`. |

---

## 2. Findings

### Finding 1 — `/fairness/report` and `/metrics` have no privilege tier · **Open, accepted**

Both are audit/operations surfaces that any authenticated user can read. `/fairness/report`
describes the whole instance's outcome distribution; `/metrics` describes its whole traffic profile.
Neither should be readable by an ordinary account in a real deployment.

**Why it is open.** This project has no role model at all, and inventing a privilege tier in the
final milestone is a larger and riskier change than the two endpoints it would protect.

**Why it is defensible for now.** Neither payload contains individual data at any threshold.
`fairness_service` suppresses groups below n = 20 *before* serialisation, and the metrics registry
records route templates rather than paths, so no symbol, id or email can reach it. Both properties
are asserted in `tests/test_access_control.py::TestAggregateSurfaces` rather than argued.

**What would close it.** A `role` column on `users` and a `RequireRole("auditor")` dependency.

### Finding 2 — the React application has no production serving configuration · **Open, disclosed**

The `web` container runs the Vite dev server. There is no nginx configuration, no production
Dockerfile stage, and therefore no Content-Security-Policy, no `X-Frame-Options` and no HSTS on the
HTML the browser actually renders. The API's headers (Finding 4) do not cover it — they are on a
different origin.

**Why it is open.** The PRD scopes this project as a research/educational tool that is not publicly
launched (§17.2), and a production serving configuration written now would be untested and would
imply a deployment path that does not exist. Writing a CSP nobody has loaded a page against is worse
than recording its absence.

**What would close it.** A production build stage plus a static server config carrying
`Content-Security-Policy`, `X-Frame-Options`, `Referrer-Policy` and HSTS — as part of a deployment
milestone, verified against the real page.

### Finding 3 — two moderate `react-router` advisories · **Assessed as not reachable, version held**

`npm audit` reports two moderate advisories against `react-router` 6.x:

- *Open redirect via backslash in `<Link>` and `useNavigate`.*
- *Arbitrary constructor injection via `deserializeErrors()` in SSR hydration.*

Neither is reachable in this codebase:

- **Open redirect.** Every `navigate()` call in the application passes a hard-coded literal
  (`/dashboard`, `/onboarding`, `/login`), and every `<Link to>` is a literal. `RequireAuth` records
  `location.pathname` in navigation state, but nothing ever reads it back to navigate. There is no
  path from user input, a query parameter or an API response to a navigation target.
- **SSR hydration.** This is a client-rendered application. `deserializeErrors` is not on any code
  path it executes.

**Why the version is held.** The fix is `react-router-dom@7`, a major version with breaking API
changes, landing in a hardening milestone with no budget to re-verify every route and guard. Taking
a breaking upgrade to close a finding that has no reachable path would trade a theoretical risk for
a real one.

**What would change this assessment**, and should trigger the upgrade immediately: any navigation
target derived from a query parameter, a path segment, an API response, or user input. If that
lands, the advisory becomes live.

### Finding 4 — no response security headers · **Fixed**

The API sent none of `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` or a CSP.
`app/core/security_headers.py` now adds them, plus `Cross-Origin-Opener-Policy`,
`Cross-Origin-Resource-Policy` and a restrictive `Permissions-Policy`.

Two decisions inside the fix are worth recording:

- **HSTS is conditional.** Sent from a development server on `localhost`, `Strict-Transport-Security`
  pins *localhost* to HTTPS for a year — for every project on that machine, persistently. It is
  therefore suppressed in `development` and `test`.
- **The middleware is registered outermost**, so headers are present on error responses too. The
  responses a client did not expect are the ones most likely to be probed, and they were the ones
  most likely to be missed. `tests/test_observability.py` asserts it on a 401.

### Finding 5 — the metrics endpoint could have leaked request paths · **Fixed before it shipped**

The first implementation recorded `request.url.path`. That would have written every symbol a user
looked at — and, on other routes, resource ids — into a surface explicitly designed to be scraped
and retained. It now records the **route template** (`/api/v1/market/{symbol}`), asserted by
`test_it_records_route_templates_not_paths`.

A second bug found while fixing it: this FastAPI version nests included routers, so the naive
template was router-relative (`/market/{symbol}`) and a future `/api/v2/health` would have shared a
series with `/api/v1/health`. The prefix is now reconstructed.

### Finding 6 — `model_monitoring` was missing from the test truncation list · **Fixed**

Test isolation, not production security, but the same class of mistake: a new table added to the
schema and not to the between-test `TRUNCATE`. Monitoring rows leaked across tests, so an assertion
about "the latest observations" could have passed on another test's data.

---

## 3. Dependency scanning

Both scanners were run against the built images on 2026-08-29.

**Python — `pip-audit`:** *No known vulnerabilities found.* (The local `wealthpilotx-backend`
package is skipped as it is not on PyPI, which is expected.)

**JavaScript — `npm audit`:** 2 moderate, both `react-router`. See Finding 3.

**CI change.** The `audit` job was `continue-on-error: true` from M2 — deliberately non-blocking on
day one so a transitive advisory could not fail an unrelated change. That was the right default for
a repository with no triage process and the wrong one to keep. It is now blocking at **high and
above**, which fails the build on anything serious while leaving the two moderate findings above
visible and non-blocking. Moderate findings are reviewed here rather than enforced by a threshold.

---

## 4. Data protection (§11.2, §17.3)

| Commitment | Status |
|---|---|
| `income` and `savings` encrypted at rest | Met — application-layer Fernet with an HKDF-derived key (`app/core/crypto.py`), so the protection travels with the data rather than depending on the host's disk encryption. |
| Never exposed in logs or error messages | Met — `RedactionFilter` redacts by key name *and* by inline pattern in free text, as a backstop rather than as the primary control. A `FinancialProfile.__repr__` deliberately omits both fields, because reprs reach logs. |
| Right to access | Met — `GET /user/profile`, offered as a JSON download from the Data & privacy page. |
| Right to erasure | Met — `DELETE /user/profile` removes the account, profile and every session. Not a soft delete; a still-valid access token stops working immediately (asserted). |
| Fairness aggregates anonymised above a minimum group size | Met — n ≥ 20, suppressed server-side. |
| Retention: profile purged within 30 days of deletion | Met more strictly than required — deletion is immediate. |

**One consequence worth naming.** Because income is encrypted at the application layer, the fairness
report must decrypt every profile in the API process to band it. Plaintext therefore exists in
memory for the duration of that request. `fairness_service._band_income` converts and discards
immediately, and no raw value is returned, logged, or retained — but the exposure is real and is the
documented cost of the M1 decision to encrypt in the application rather than rely on the host.

---

## 5. What this review did not cover

Stated so the gaps are not mistaken for clean results:

- **No penetration testing, fuzzing, or dependency-supply-chain review** (no lockfile signing, no
  provenance checks on the container base images).
- **No review of the deployment environment**, because there is not one. TLS termination, secret
  storage, network policy and database encryption at rest are all deferred to a deployment
  milestone.
- **No threat model for the ML surfaces** — model extraction through repeated `/risk/analyze` calls,
  or inference about the training population from the fairness report, were considered informally
  and not analysed. The 10/min rate limit raises the cost of the first; nothing addresses the second
  beyond the group-size threshold.
- **No authentication hardening beyond the PRD's list** — no MFA, no account lockout, no password
  breach checking, no email verification. §16.2 does not ask for them; a real launch would.
