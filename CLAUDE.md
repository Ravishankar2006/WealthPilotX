# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this project is

WealthPilotX is an AI-driven **financial decision-support** platform. It analyzes a user's
self-reported financial profile alongside market and macroeconomic data to produce personalized,
explainable, risk-aware portfolio recommendations.

The authoritative spec is `Docs/PRD/WealthPilotX_PRD_v2.docx`. It is a binary `.docx` with no
plaintext copy in the repo — extract it with:

```bash
python3 -c "
import zipfile
from xml.etree import ElementTree as ET
NS='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
r=ET.fromstring(zipfile.ZipFile('Docs/PRD/WealthPilotX_PRD_v2.docx').read('word/document.xml'))
print('\n'.join(''.join(t.text or '' for t in p.iter(NS+'t')) for p in r.iter(NS+'p')))"
```

Section numbers referenced below (FR-xx, §10.5, §16.2 …) are PRD sections. When a task touches a
requirement, re-read that PRD section rather than working from this summary.

## Hard non-goals — never build these

The project's regulatory safety depends on staying an educational/research tool (PRD §5, §17). Do
not implement, and push back if asked to add without an explicit scope decision:

- Trade execution or order routing of any kind.
- Bank or brokerage account linkage.
- Custody, holding, or transmission of real money.
- Anything framed as licensed financial/investment/tax/legal advice, or a guaranteed buy/sell signal.

Every recommendation and prediction surface must carry the disclaimers from §17.1. Treat those
disclaimers as part of "done", not as polish.

## Architecture

```
Yahoo Finance + FRED  →  ingestion  →  PostgreSQL  →  feature engineering
                                                            ↓
   financial profile  →  Random Forest  →  risk class       │
                                                ↓           ↓
                                          recommendation engine  ←  XGBoost (market prediction)
                                                ↓
                                          portfolio optimizer  →  FastAPI  →  React dashboard
```

Four ML surfaces, each with its own lifecycle (PRD §10):

| Surface | Model | Output |
|---|---|---|
| Risk classification | Random Forest | LOW / MEDIUM / HIGH + score + top factors |
| Market prediction | XGBoost | trend / expected return + confidence |
| Recommendation | KNN / scoring engine | candidate assets |
| Portfolio optimization | optimizer (weights sum to 1) | asset weights |

## Stack

React + TypeScript + Tailwind + Plotly · FastAPI + Pydantic + SQLAlchemy · PostgreSQL · scikit-learn
+ XGBoost · JWT auth · Docker · Pytest (backend/ML) + Vitest/RTL (frontend) + Playwright (E2E) ·
GitHub Actions CI.

**Pin Python 3.12 in Docker.** The host runs 3.14, which is ahead of reliable scikit-learn/XGBoost
wheel availability. The container is the source of truth for the runtime; don't assume host Python
matches.

## Conventions

**Commits — no attribution trailer.** Standing user preference: commit messages are subject + body
only. Never append `Co-Authored-By:`, `Claude-Session:`, or `Generated with Claude Code`. This
overrides the default Claude Code commit convention.

**API.** All endpoints under `/api/v1`. Breaking changes ship as `/api/v2`, never by mutating v1.
Errors are always `{"error": {"code", "message", "fields"}}` with a matching HTTP status. Bearer JWT
on everything except `/auth/register` and `/auth/login`. List endpoints paginate with
`?limit=&cursor=` returning `{"data": [...], "next_cursor": ...}`.

**Model traceability.** Every prediction, risk assessment, and recommendation row and response
carries the `model_version` that produced it (§10.5). A model reaches `PRODUCTION` status only after
beating the incumbent (or a naive baseline, first time) on a held-out test set.

**Sensitive data.** `income`, `savings`, `age` and derived profile data are financial PII. Never log
them, never include them in error messages or exception payloads, never return another user's
resources. Access-control tests are part of the test suite, not an afterthought (§11.2, §16.2).

**Data providers.** Yahoo Finance is unofficial and can break without notice. All market data access
goes through a provider interface so a paid provider can be swapped in without touching downstream
code (§7.3). Never call `yfinance` directly from application code.

**Definition of done** for any FR: implementation complete, unit + integration tests green in CI, and
the FR's acceptance criteria manually verified.

## Repo layout

```
Docs/PRD/     Product requirements (source of truth, .docx)
Docs/PLAN/    Phase plans — start with PHASE-1-FOUNDATION.md
```

Application code does not exist yet; Phase 1 creates it. See `Docs/PLAN/PHASE-1-FOUNDATION.md` for
the current scope, and update that file's checklist as work lands.
