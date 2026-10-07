# FloodWatch AI Engine

Hyper-local flood risk scoring and alerting: merges NASA satellite flood data with ground elevation and local water-level sensors, pushes LINE alerts to users near risk zones, and exposes a B2B flood-aware routing API.

> **Status: scaffold.** The risk model is an uncalibrated weighted-factor heuristic. Do not use it for public safety alerts until it is backtested against historical flood events.

## Data sources
| Source | Used for | Auth |
|---|---|---|
| NASA EONET v3 (`category=floods`) | Flood event points | none |
| NASA CMR granule search (`MCDWD_L3_NRT`, MODIS NRT Global Flood Product) | Satellite flood-map granule metadata | none for search; Earthdata token to download |
| DEM samples (SRTM / Copernicus GLO-30) | Low-lying factor | — |
| Local sensor feed (placeholder schema) | Water-level trend | `LOCAL_SENSOR_API_KEY` |
| LINE Messaging API | Push alerts, location sharing via webhook | channel token + secret |

## Quick start
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
cp .env.example .env   # fill in values
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m uvicorn src.api.server:app --reload
```

## API
| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | — | Liveness |
| POST | `/webhook/line` | `X-Line-Signature` | Registers a user's shared location; removes on unfollow |
| POST | `/v1/risk/assess` | `X-API-Key` | Score one point from factor inputs |
| POST | `/v1/zones/nearby` | `X-API-Key` | Risk zones within radius of a point |
| POST | `/v1/route/check` | `X-API-Key` | Does a route polyline cross any risk zone? |

## Risk score
`score = 100 * Σ wᵢ·factorᵢ`, each factor in [0, 1]:
- `satellite_water` — fraction of nearby pixels flagged as water
- `low_elevation` — metres below the local mean elevation, scaled
- `rainfall` — 24h rainfall / saturation level
- `water_level_trend` — sensor level projected `projection_horizon_h` ahead (least-squares rise rate) / warning level

Weights and level cut-offs live in `config/risk_thresholds.yaml`.

## Not yet built
- Scheduler/poller that fetches data, builds `FloodZone`s, and fans out alerts (with cooldown)
- Raster sampling of flood-product granules into `satellite_water_fraction`
- Persistent storage (zones and subscribers are in memory)
- Detour routing provider (payload builder accepts a `detour_url`)
- Historical backtest of the risk model
