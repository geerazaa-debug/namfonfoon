# FloodWatch: Bangkok Heavy-Rain Advisory

Rule-based heavy-rain alerts per Bangkok district, combined with official BMA road-flood hotspot statistics, pushed over LINE and exposed as a B2B API.

> **Status: v0.2 rule-based advisory.** This is not a flood forecast. A pre-registered backtest (2022–24 train, 2025 test) found no multi-factor model that beats rain-only. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the evidence chain and the rules.

## Data sources
| Source | Used for | Licence |
|---|---|---|
| BMA DDS `frd_dds` road-flood records 2022–25 | District hotspot table (`config/district_hotspots.json`), rain thresholds | CC BY: "สำนักการระบายน้ำ กรุงเทพมหานคร" |
| geoBoundaries THA ADM2 | District polygons (`config/bangkok_districts.geojson`) | CC BY 3.0 IGO |
| ThaiWater `public/rain_24h` (live, hourly) | `rain_1h` per station → rolling 6 h totals | not stated: permission needed before paid use |
| LINE Messaging API | Push alerts, location sharing via webhook | channel token + secret |

Research-only sources (ThaiWater history, Traffy Fondue, NASA CMR/OPERA, Copernicus DEM) are documented in `docs/`.

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
| POST | `/v1/risk/assess` | `X-API-Key` | Rain advisory level for a point from rain observations |
| POST | `/v1/zones/nearby` | `X-API-Key` | Risk zones within radius of a point |
| POST | `/v1/route/check` | `X-API-Key` | Does a route polyline cross any risk zone? |

## Alert rules
`level = max(rule_1h, rule_6h)`, then +1 level in DDS hotspot districts when rain is already ≥ moderate. Alerts are sent at **high** or above, and only on observations less than 90 minutes old.

| Level | 1 h | 6 h |
|---|---|---|
| moderate | ≥ 10 mm | ≥ 36 mm |
| high | ≥ 30 mm | ≥ 49 mm |
| critical | ≥ 60 mm | ≥ 67 mm |

The 6 h thresholds are the 10th, 25th and 50th percentiles of rainfall in BMA-recorded road floods. The false-alarm rate is **unmeasured**. Values live in `config/risk_thresholds.yaml`.

## Running the alert poller
Set `POLLER_ENABLED=1` (and `LINE_CHANNEL_ACCESS_TOKEN` to actually send). Every 15 min it pulls ThaiWater rain, builds rolling 6 h totals, assesses each LINE subscriber's shared location, and pushes an advisory at **high** or above. The same user + district is not re-alerted within 3 h unless the level rises.

Subscribers, rain history and cooldowns persist in SQLite (`data/floodwatch.db`, WAL mode, auto-created), so a restart does not lose the 6 h totals or re-send alerts. Run **one** process only.

## Not yet built
- Radar QPE input. A data request draft to BMA is in `docs/bma_data_request_draft.md`
- Multi-instance deployment
- Predictive model: paused. See "Path back to prediction" in `docs/ARCHITECTURE.md`
