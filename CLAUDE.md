# FloodWatch — Project Rules

Rule-based heavy-rain advisory for Bangkok districts (rain thresholds + BMA DDS flood-hotspot stats) with LINE alerts and a B2B API. Read `docs/ARCHITECTURE.md` first.

## Commands
- Setup: `python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt`
- Test: `.venv/Scripts/python -m pytest -q`
- Lint: `.venv/Scripts/python -m ruff check . && .venv/Scripts/python -m ruff format --check .`
- Run API: `.venv/Scripts/python -m uvicorn src.api.server:app --reload`
- Research deps: `.venv/Scripts/python -m pip install -r requirements-research.txt`
- Rebuild district/hotspot config: `.venv/Scripts/python -m research.build_hotspots`

## Hard rules
1. **Zero secrets in git.** Keys come from env vars only (`.env`, gitignored). Never put a key, token, channel secret, or real LINE userId in code, YAML, tests, logs, or commit messages. Tests use obvious fakes (`"tok"`, `"k1"`).
2. **Type hints everywhere**; Python 3.12. Public functions/classes fully annotated.
3. **Pydantic at every boundary**: external API responses, API request bodies, and engine inputs/outputs are Pydantic models. No raw dicts crossing module boundaries (LINE Flex payloads are the one exception: they are the wire format).
4. **Mandatory tests.** Every new module or behavior change ships with pytest tests in `tests/`. No network in tests — use `httpx.MockTransport` / `fastapi.testclient`. `pytest` and `ruff check` must pass before commit.
5. **Concise code.** No speculative abstractions, no dead code, comments only for non-obvious *why*.
6. **Config over constants.** Endpoints, coordinates, thresholds, weights live in `config/*.yaml`, loaded via `src/config.py`.
7. **Async I/O** through `httpx.AsyncClient` + `src/retry.request_with_retry` (handles 429/5xx + Retry-After).
8. **Privacy.** User locations and LINE userIds are personal data: never log them; store only what alerting needs (SQLite `users` keeps coordinates rounded to ~1 km). Never commit `data/*.db`.

## Honesty constraints
- **The system is a rule-based rain advisory, not a flood predictor.** Step 4 (`docs/step4_backtest_result.md`): the multi-factor model did not beat rain-only (PR-AUC 0.148 vs 0.171), and rain-only itself is weak. Never call it "AI", "prediction" or "forecast" in user-facing text. Alert cards must say it is an advisory based on rainfall.
- The predictive path is **paused**. Reviving it requires dense rain data (BMA gauges or radar QPE), a bare-earth DEM (FABDEM), and a **new pre-registered test on the 2026 season**. The 2025 holdout is spent.
- Any new model claim needs a pre-registration committed before the data is analysed. No post-hoc threshold or feature changes on a spent holdout.
- Data licences: DDS and geoBoundaries are CC BY (attribution required). Traffy and ThaiWater are unstated, so no commercial or B2B use without written permission.
- `local_sensors.py` assumes a placeholder JSON schema until a real provider is wired.

## Layout
- `src/data/` ingestion (NASA, sensors) + `validator.py` (GeoPoint, BoundingBox, integrity checks)
- `src/engine/` rule-based analyzer, district lookup, geofencing (live); elevation mapper (paused): pure, sync, no I/O
- `research/` offline studies and pre-registrations (results in `docs/step*_*.md`); never imported by `src/`
- `src/alerts/` LINE push + Flex card builder
- `src/api/server.py` FastAPI: `/webhook/line` (signature-verified), `/v1/*` (X-API-Key) except `/v1/public/*`, `/map`, `/report`, PWA assets (public, no identities) and `/v1/reports` (LINE ID token)
- `src/engine/community.py` + `src/alerts/report_messages.py` + `src/alerts/line_login.py`: citizen water-report flow (chat + LIFF), map view
- `src/api/static/`: `/map` (PWA), `/report` (LIFF), manifest, service worker, icons

## Agents
- `.claude/agents/scout.md` — read-only codebase / API-schema exploration
- `.claude/agents/builder.md` — implements a scoped feature with tests
- `.claude/agents/refuter.md` — bug + secrets/security audit; run before every commit
- `/build-feature <description>` runs scout → builder → refuter
