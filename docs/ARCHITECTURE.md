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
| `src/api/server.py` | **Live:** `/v1/risk/assess`, zones/route endpoints, LINE webhook |
| `src/engine/geofence.py` | Live utility (zones/routes). No zone producer yet |
| `src/data/nasa_client.py` | Paused: satellite factor NO-GO (no usable monsoon history) |
| `src/engine/elevation_mapper.py` | Paused: waits for a bare-earth DEM (FABDEM) |
| `src/data/local_sensors.py` | Placeholder schema |
| `research/` | Offline studies + `build_hotspots.py` (regenerates the config tables) |

## Not built yet
1. **Live rain source.** ThaiWater `thailand_main` exposes `rain_1h` and `rain_24h` per station but not `rain_6h`. Radar QPE (TMD/BMA) access is unverified. The engine takes `RainObservation` from any source.
2. Poller/scheduler + per-user cooldown (`alerts.cooldown_min`).
3. Persistent subscriber store (currently in-memory).

## Path back to prediction (paused)
Preconditions before a new pre-registered test:
- Dense rain data: a BMA DDS gauge network history (official request) or radar QPE.
- A bare-earth DEM (FABDEM).
- A new holdout: the 2026 monsoon season, used once.

## Data licences
- BMA DDS `frd_dds`: CC BY. Attribute "สำนักการระบายน้ำ กรุงเทพมหานคร".
- geoBoundaries: CC BY 3.0 IGO.
- Traffy Fondue and ThaiWater: licence not stated. Get written permission before any commercial or B2B use.
