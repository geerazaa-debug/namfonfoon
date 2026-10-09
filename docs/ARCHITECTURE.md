# Architecture: FloodWatch Rain Advisory (v0.2)

## Current position (2026-10-08)
FloodWatch is a **rule-based rain advisory**: "heavy rain near you, and this district's main roads flood often". It is **not** a flood-prediction engine. The predictive ("AI") path is **paused** until better data exists.

### Why: evidence chain
| Step | Question | Result | Doc |
|---|---|---|---|
| 1 | Which data exists? | NASA flood NRT is a 7-day rolling archive. DSWx is historical. ThaiWater has station history | `data_availability.md` |
| 2 | Enough canal-overflow events? | Passed nominally, but 60% were tidal | `step2_feasibility_result.md` |
| 2-A1 | Enough *rain-driven* canal overflows? | **NO-GO**: 15/30 calibration, 1/10 holdout | `step2_amendment1_result.md` |
| 3 | Street-level flood truth? | BMA DDS `frd_dds` (CC BY), 197 flood days 2022–25 | `step3_street_flood_data.md` |
| 4 | Does multi-factor beat rain-only? | **FAIL**: PR-AUC 0.148 vs 0.171, Δ −0.022 | `step4_backtest_result.md` |

Root causes (Step 4 post-hoc):
- ThaiWater has a rain gauge inside only 11 of 50 districts, and missed 55% of DDS flood days (rain < 10 mm or no data).
- Copernicus GLO-30 is a surface model (it includes buildings), so its "elevation" measures urban density.

## Runtime flow
```
rain observations (gauge / radar, per user location)        config/risk_thresholds.yaml
              │                                                       │
              ▼                                                       ▼
   AlertInput ──► RuleBasedAnalyzer.assess() ◄── DistrictIndex (config/bangkok_districts.geojson
              │        level = max(rule_1h, rule_6h)                  + config/district_hotspots.json)
              │        +1 level in DDS hotspot districts (if ≥ moderate)
              ▼
         Assessment (level, notify, stale, reasons, district)
              │
              ├─► /v1/risk/assess (B2B, X-API-Key)
              └─► payload_builder.build_rain_alert() ─► LineNotifier.push()
```

### Rules (`config/risk_thresholds.yaml`)
| Level | 1 h rain | 6 h rain | Basis |
|---|---|---|---|
| moderate | ≥ 10 mm | ≥ 36 mm | 6 h: 90% of DDS-recorded road floods had ≥ 36 mm |
| high (notify) | ≥ 30 mm | ≥ 49 mm | 6 h: 75% of recorded floods |
| critical | ≥ 60 mm | ≥ 67 mm | 6 h: median recorded flood. 1 h: commonly cited drainage capacity (unverified) |

- **Hotspot:** a district in the top quartile of DDS flood days (≥ 25 days, 2022–25; 13 districts). It raises an alert one level when rain is already ≥ moderate.
- **Stale data:** observations older than 90 minutes are ignored, and no alert is sent without fresh data.
- **Known weakness:** the thresholds were chosen for *sensitivity* to recorded floods. The false-alarm rate is unmeasured, and Step 4 suggests it is high. All user-facing text says "advisory, not a flood forecast".

## Modules
| Module | Status |
|---|---|
| `src/engine/risk_analyzer.py` | **Live:** `RuleBasedAnalyzer` |
| `src/engine/districts.py` | **Live:** point → district + hotspot info |
| `src/alerts/payload_builder.py` | **Live:** `build_rain_alert` (rain advisory card). `build_flood_alert` is for geofenced zones |
| `src/alerts/line_notifier.py` | **Live:** push with retry key, webhook signature check |
| `src/alerts/dispatcher.py` | **Live:** `AlertPipeline` poller + `CooldownTracker` |
| `src/data/thaiwater_rain.py` | **Live:** ThaiWater client + rolling 6 h `RainAccumulator` |
| `src/data/db.py` | **Live:** SQLite store (rain_logs, alert_cooldowns, users) |
| `src/api/server.py` | **Live:** `/v1/risk/assess`, zones/route endpoints, LINE webhook |
| `src/engine/geofence.py` | Live utility (zones/routes). No zone producer yet |
| `src/data/nasa_client.py` | Paused: satellite factor NO-GO (no usable monsoon history) |
| `src/engine/elevation_mapper.py` | Paused: waits for a bare-earth DEM (FABDEM) |
| `src/data/local_sensors.py` | Placeholder schema |
| `research/` | Offline studies + `build_hotspots.py` (regenerates the config tables) |

## Live pipeline (v0.2.2)
```
every 15 min (POLLER_ENABLED=1, FastAPI lifespan)
ThaiWater public/rain_24h ──► RainAccumulator ──► SQLite rain_logs (per-station hourly rain_1h, 24 h kept)
                                 │ rain_6h = sum of last 6 hourly slots (needs ≥ 5 present)
                                 ▼
SQLite users (LINE location, ~1 km rounded) ──► near(): stations ≤ 5 km, else nearest ≤ 10 km
                                 ▼
                         RuleBasedAnalyzer ──► notify?
                                 ▼
               CooldownTracker → SQLite alert_cooldowns: key (user, district), 180 min; level increase bypasses
                                 ▼
                 build_rain_alert ──► LineNotifier.push (retry key); failure → retried next poll
```
- With no `LINE_CHANNEL_ACCESS_TOKEN`, the pipeline runs **dry**: it assesses and logs counts, and never pushes.
- Live check 2026-10-08: 34 ThaiWater stations in the area of interest; 49/50 district points have a station within 5 km or a fallback within 10 km.

### Persistence (`src/data/db.py`)
SQLite in WAL mode at `data/floodwatch.db` (override with env `FLOODWATCH_DB`). The schema is auto-created and migrated via `PRAGMA user_version`.

| Table | Key | Purpose |
|---|---|---|
| `rain_logs` | (station_id, observed_at UTC) | Hourly `rain_1h` + station lat/lon + district. Re-polls overwrite. Pruned to 24 h |
| `alert_cooldowns` | (line_user_id, district_name) | Last level/time sent. Per user, so a new subscriber in an alerted district still gets their first alert |
| `users` | line_user_id | District + coordinates **rounded to 0.01° (~1.1 km)**. Deleted (with cooldowns) on unfollow |

A restart keeps the 6 h totals, the cooldowns and the subscribers.

### Known limitations
- **Single process only.** SQLite plus an in-process poller: running several API workers would run several pollers and send duplicate alerts.
- After an outage longer than ~1 h, the 6 h total for those hours is missing. The rule needs ≥ 5 of 6 slots and falls back to the 1 h rule otherwise.
- ThaiWater licence is not stated: fine for a free public advisory, but needs permission before any paid or B2B use.
- LINE push quota: the free plan has a small monthly message limit. Check it before onboarding users.
- `users` holds personal data (LINE userId + ~1 km location). The DB file must not be committed (gitignored) or shared.

## Not built yet
1. Radar QPE input (access unverified, see `docs/bma_data_request_draft.md`).
2. Multi-instance deployment (would need a shared DB and leader election for the poller).

## Path back to prediction (paused)
Preconditions before a new pre-registered test:
- Dense rain data: a BMA DDS gauge network history (official request) or radar QPE.
- A bare-earth DEM. **FABDEM is CC BY-NC-SA (no commercial use)**, so for B2B use the BMA DTM/LiDAR requested in `docs/bma_data_request_draft.md`.
- A new holdout: the 2026 monsoon season, used once.

## Data licences
- BMA DDS `frd_dds`: CC BY. Attribute "สำนักการระบายน้ำ กรุงเทพมหานคร".
- geoBoundaries: CC BY 3.0 IGO.
- Traffy Fondue and ThaiWater: licence not stated. Get written permission before any commercial or B2B use.
