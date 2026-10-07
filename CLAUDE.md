# FloodWatch AI Engine — Project Rules

Hyper-local flood risk scoring (NASA EONET/CMR + DEM + local sensors) with LINE alerts and a B2B routing API.

## Commands
- Setup: `python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt`
- Test: `.venv/Scripts/python -m pytest -q`
- Lint: `.venv/Scripts/python -m ruff check . && .venv/Scripts/python -m ruff format --check .`
- Run API: `.venv/Scripts/python -m uvicorn src.api.server:app --reload`

## Hard rules
1. **Zero secrets in git.** Keys come from env vars only (`.env`, gitignored). Never put a key, token, channel secret, or real LINE userId in code, YAML, tests, logs, or commit messages. Tests use obvious fakes (`"tok"`, `"k1"`).
2. **Type hints everywhere**; Python 3.12. Public functions/classes fully annotated.
3. **Pydantic at every boundary**: external API responses, API request bodies, and engine inputs/outputs are Pydantic models. No raw dicts crossing module boundaries (LINE Flex payloads are the one exception: they are the wire format).
4. **Mandatory tests.** Every new module or behavior change ships with pytest tests in `tests/`. No network in tests — use `httpx.MockTransport` / `fastapi.testclient`. `pytest` and `ruff check` must pass before commit.
5. **Concise code.** No speculative abstractions, no dead code, comments only for non-obvious *why*.
6. **Config over constants.** Endpoints, coordinates, thresholds, weights live in `config/*.yaml`, loaded via `src/config.py`.
7. **Async I/O** through `httpx.AsyncClient` + `src/retry.request_with_retry` (handles 429/5xx + Retry-After).
8. **Privacy.** User locations and LINE userIds are personal data: never log them; store only what alerting needs.

## Honesty constraints
- `RiskAnalyzer` is a weighted-factor heuristic, **not** a trained model. Weights in `config/risk_thresholds.yaml` are uncalibrated. Do not describe it as "AI prediction" in user-facing text, or enable public alerts, until it has been backtested against historical flood events (e.g. Bangkok 2011, 2021–2025 seasons) with a pre-registered pass criterion.
- `local_sensors.py` assumes a placeholder JSON schema until a real provider is wired.

## Layout
- `src/data/` ingestion (NASA, sensors) + `validator.py` (GeoPoint, BoundingBox, integrity checks)
- `src/engine/` elevation, risk scoring, geofencing — pure, sync, no I/O
- `src/alerts/` LINE push + Flex card builder
- `src/api/server.py` FastAPI: `/webhook/line` (signature-verified), `/v1/*` (X-API-Key)

## Agents
- `.claude/agents/scout.md` — read-only codebase / API-schema exploration
- `.claude/agents/builder.md` — implements a scoped feature with tests
- `.claude/agents/refuter.md` — bug + secrets/security audit; run before every commit
- `/build-feature <description>` runs scout → builder → refuter
