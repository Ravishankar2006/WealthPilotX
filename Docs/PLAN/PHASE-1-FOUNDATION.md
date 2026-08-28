# Phase 1 — Foundation (PRD Milestone M1)

**Estimated duration:** 1–2 weeks solo/part-time (PRD §22)
**Status:** Not started
**Exit condition:** A registered user can log in, save and retrieve a validated financial profile,
delete their account, and see the required legal disclaimers — end to end, in Docker, with CI green.

---

## 1. Scope

### In scope

| PRD ref | What ships |
|---|---|
| §22 M1 | Repo scaffold, Docker Compose (api + web + postgres), migrations, CI |
| FR-01 | Register, login, logout, refresh, delete account — JWT, hashed passwords |
| FR-02 | Financial profile: create/read/update with full field validation |
| §11.2 | Account + profile erasure (`DELETE /api/v1/user/profile`), retention note |
| §13.1 | API conventions enforced globally: `/api/v1`, error envelope, rate limiting |
| §16.2 | Password hashing, env-based secrets, per-endpoint validation, ownership checks |
| §16.4 | `/health`, structured JSON logs, per-request correlation ID |
| §17.1 | Disclaimer component + ToS/Privacy acceptance at registration |
| §14 | Landing, Login, Register, Onboarding, Settings/Privacy pages (shell-level) |

### Deliberately deferred

Market/economic ingestion (M2), all ML and the risk model (M3) — including the *scoring* half of
FR-03, recommendations and optimization (M4), the real dashboard (M5), SHAP/fairness (M6).

### Judgment call to confirm

The PRD lists only "authentication" under M1, but **FR-02 (financial profile) is included here**
because it is pure validated CRUD with no ML dependency, it forces the `users` ↔ `financial_profiles`
schema and the encryption-at-rest decision early, and it unblocks M3 from starting cold. If you'd
rather hold M1 to auth alone, cut §5 Stage 4 below and the phase shortens by ~2 days.

---

## 2. Decisions to lock before coding

These are PRD §27 open questions that Phase 1 cannot avoid. Proposed answers in **bold**; change them
here before Stage 1 if you disagree.

1. **Profile encryption at rest** — **application-layer encryption on `income` and `savings`**
   (envelope encryption with a key from env), not provider disk encryption. Reason: the deployment
   target isn't chosen yet, and retrofitting column encryption after data exists is far more painful
   than starting with it. Cost: those columns become non-aggregatable in SQL, so the fairness
   dashboard (§FR-14) must aggregate in application code. Accept or reject now — not in M6.
2. **Python runtime** — **3.12 pinned in the Dockerfile.** Host is 3.14, which outruns dependable
   scikit-learn/XGBoost wheels. The container is the runtime of record from day one.
3. **Migrations** — **Alembic**, autogenerate reviewed by hand, never applied blind.
4. **Token lifetimes** — **15-minute access token, 7-day refresh token**, refresh rotation on use.
5. **Logout semantics** — with stateless JWT, `POST /auth/logout` **revokes the refresh token via a
   server-side denylist table**; the access token is left to expire. Anything else is theater.
6. **Asset universe** — not needed in Phase 1, but decide it before M2 starts.

---

## 3. Target layout at end of phase

```
wealthpilotx/
├── docker-compose.yml
├── .env.example                 # every secret named, no values
├── backend/
│   ├── Dockerfile               # python:3.12-slim
│   ├── pyproject.toml
│   ├── alembic/versions/
│   └── app/
│       ├── main.py              # app factory, middleware, router mount
│       ├── core/                # config, security, logging, errors, rate limit
│       ├── db/                  # session, base
│       ├── models/              # SQLAlchemy: user, financial_profile, refresh_token
│       ├── schemas/             # Pydantic request/response
│       ├── api/v1/              # auth.py, user.py, health.py
│       └── services/            # auth_service.py, profile_service.py
│   └── tests/                   # unit + integration (FastAPI TestClient)
├── frontend/
│   ├── Dockerfile
│   ├── package.json             # vite + react-ts + tailwind + vitest
│   └── src/
│       ├── api/client.ts        # token attach + refresh-on-401
│       ├── components/Disclaimer.tsx
│       ├── pages/               # Landing, Login, Register, Onboarding, Settings
│       └── routes.tsx           # protected-route wrapper
└── .github/workflows/ci.yml
```

---

## 4. Data model for this phase

Only the tables Phase 1 actually needs (PRD §12), plus one the PRD omits:

- `users` — id UUID PK · email text UNIQUE NOT NULL · password_hash text NOT NULL ·
  tos_accepted_at timestamptz NOT NULL · created_at timestamptz
- `financial_profiles` — id UUID PK · user_id UUID FK→users UNIQUE ON DELETE CASCADE ·
  age int CHECK 18–120 · income (encrypted) · savings (encrypted) ·
  risk_appetite enum · investment_goal enum(RETIREMENT|GROWTH|WEALTH_CREATION) ·
  investment_horizon int years · experience enum · financial_literacy enum · updated_at
- `refresh_tokens` *(not in PRD §12 — required by decision 5)* — id UUID PK · user_id UUID FK ·
  token_hash text · expires_at · revoked_at nullable

`tos_accepted_at` is likewise absent from the PRD's `users` table but is required by §17.1's
"accepted at registration". Both additions should be folded back into the PRD's §12 table.

---

## 5. Task stages

Each stage is independently committable and leaves the repo green.

### Stage 1 — Skeleton that runs (≈1 day)
- [ ] `docker compose up` brings up postgres + api + web; api answers `GET /api/v1/health` 200
- [ ] `.env.example` with every var named; app refuses to boot on a missing required secret
- [ ] Alembic wired, empty baseline revision applied
- [ ] Vite React-TS app served, hitting `/api/v1/health` from the browser

### Stage 2 — Cross-cutting API contract (≈1 day)
Do this *before* the first real endpoint, so no endpoint ever ships non-conforming.
- [ ] Global exception handlers emitting `{"error": {"code", "message", "fields"}}` for 400/401/403/404/409/422/500
- [ ] `RequestValidationError` mapped to 422 with field-level detail
- [ ] Correlation-ID middleware (accept inbound header, generate otherwise, echo in response + logs)
- [ ] Structured JSON logging with a redaction filter that drops `income`, `savings`, `password`, `token`
- [ ] Rate limiting: 100 req/min default, 429 with the standard envelope

### Stage 3 — Authentication, FR-01 (≈3 days)
- [ ] `POST /auth/register` — argon2 hash, unique email, requires `tos_accepted: true`
- [ ] Duplicate email → **409**; weak/invalid input → **422**
- [ ] `POST /auth/login` → access + refresh token pair
- [ ] `POST /auth/refresh` with rotation; reuse of a rotated token revokes the family
- [ ] `POST /auth/logout` → refresh token denylisted
- [ ] Protected-route dependency: missing/expired/malformed token → **401**
- [ ] Tests: the four FR-01 acceptance criteria, plus a test asserting no plaintext password or hash
      appears in any log line

### Stage 4 — Financial profile, FR-02 (≈2 days)
- [ ] `GET` / `PUT /api/v1/user/profile` — one profile per user, upsert semantics
- [ ] Field validation: age 18–120, income ≥ 0, savings ≥ 0, horizon ≥ 1, enums closed
- [ ] Out-of-range → **422** with `fields` populated per offending field
- [ ] A `profile_completeness` helper listing missing required fields — the thing FR-03 will call to
      block risk assessment in M3
- [ ] `DELETE /api/v1/user/profile` — erasure per §11.2, cascades profile and refresh tokens
- [ ] Ownership test: user A cannot read or write user B's profile (§16.2)

### Stage 5 — Frontend shell + disclaimers (≈2 days)
- [ ] Tailwind configured; `<Disclaimer />` in persistent (footer) and inline (per-view) variants
- [ ] Landing / Login / Register / Onboarding / Settings-&-Privacy pages
- [ ] Register form gates submit on ToS + Privacy checkbox
- [ ] Onboarding = the FR-02 profile form, field-level errors bound to the API's `fields` object
- [ ] Settings page exposes export-my-data (stub is fine) and delete-my-account (wired to the real
      endpoint, behind a typed confirmation)
- [ ] Protected routes redirect to login; client refreshes once on 401 then gives up
- [ ] Vitest: renders login, renders register, disclaimer present on every authenticated layout

### Stage 6 — CI and close-out (≈1 day)
- [ ] GitHub Actions: ruff + mypy, pytest against a service-container postgres, vitest, docker build
- [ ] Dependency scan (`pip-audit` + `npm audit`) — non-blocking to start, reported
- [ ] Branch protection: CI must pass to merge (§23)
- [ ] `README.md` with a genuine one-command local setup
- [ ] Manual QA pass against §7 below

---

## 6. Test coverage required to exit the phase

| Layer | Must cover |
|---|---|
| Unit | Password hash/verify, token encode/decode/expiry, profile validators, completeness helper |
| Integration | All FR-01 and FR-02 acceptance criteria via `TestClient` against a real test DB |
| Security | Cross-user access denied; 401 paths; no PII/credentials in captured log output |
| Frontend | Page renders, register-form ToS gate, 401 → redirect |

Coverage percentage is not a Phase 1 gate; the acceptance-criteria tests are.

---

## 7. Manual QA checklist (§20 milestone gate)

1. Fresh `docker compose up` on a clean volume → app reachable, no manual steps.
2. Register a new account → succeeds; register the same email again → 409 with a readable message.
3. Log in → dashboard shell loads; disclaimer visible without scrolling.
4. Submit profile with age 15 and negative income → both fields flagged inline, nothing saved.
5. Submit a valid profile → reload the page → values persist.
6. Let the access token expire → next action silently refreshes rather than bouncing to login.
7. Log out → refresh token no longer works.
8. Delete account → profile and tokens gone; login with those credentials fails.
9. `grep` the container logs for the submitted income figure → **zero hits**.

---

## 8. Risks specific to this phase

| Risk | Mitigation |
|---|---|
| Encrypted `income`/`savings` blocks later SQL aggregation for §FR-14 | Decided knowingly in §2.1; aggregate in app code, revisit only with the fairness work |
| Refresh-token rotation is easy to get subtly wrong | Cover reuse-detection with a test in Stage 3, not by inspection |
| Python 3.14 host vs 3.12 container drift | Never run backend commands on the host; always `docker compose exec api` |
| Scaffolding sprawl into M2/M3 concerns | Stage list above is the boundary — no ingestion or ML packages installed this phase |

---

## 9. What Phase 2 needs from this phase

M2 (Data Platform) starts by writing a `MarketDataProvider` interface (§7.3). It depends on Phase 1
delivering: working migrations, the config/secrets pattern, structured logging with correlation IDs
(background jobs reuse it), and the error envelope. Nothing else in M2 is blocked by M1.
