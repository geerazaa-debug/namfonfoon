# Data Availability Assessment (Step 1)

Checked 2026-10-08 by querying the APIs directly (CMR, ThaiWater, data.go.th CKAN). Area: Greater Bangkok bbox `13.40–14.10 N, 100.25–100.95 E`.

## TL;DR
- **MCDWD_L3_NRT / VCDWD_L3_NRT cannot be backtested.** LANCE NRT keeps a **rolling ~7-day archive** (CMR returns 7 granules: 2026-09-30 → 10-06). The collection metadata's "2021-03" start date refers to the product series, not retained data.
- **Historical satellite source = OPERA DSWx** (NASA/JPL, 30 m): DSWx-HLS has 3,241 Bangkok granules from **2016-01**, and DSWx-S1 (radar, sees through cloud) has 1,307 from **2024-08**.
- **ThaiWater has usable hourly/10-min water-level history from 2013**, no auth. Coverage is good in **2019–2024**, and **zero for 2011**.
- **Recommended benchmark window: monsoon seasons (Jun–Nov) 2019–2024**, with 2024 as the only season that has radar satellite coverage.
- 2011 can only be a qualitative sanity check against static flood-extent maps.

## 1. NASA satellite products

| Product | Sensor / res | Series start | Bangkok granules actually retrievable | Use |
|---|---|---|---|---|
| MCDWD_L3_NRT (+F1/F2/F3/F1C) | MODIS Terra+Aqua, 250 m | 2021-03 | **7 (rolling week)** | Live only |
| VCDWD_L3_NRT (+F1/F2/F3/F1C) | VIIRS JPSS-1/2, 250 m | 2025-04 / 2026-03 | **7 (rolling week)** | Live only; MODIS successor |
| OPERA_L3_DSWX-HLS_V1 | Landsat + Sentinel-2, 30 m, optical | 2016-01 | **3,241** | Historical labels (cloud-limited in monsoon) |
| OPERA_L3_DSWX-S1_V1 | Sentinel-1 SAR, 30 m | 2024-08 (BKK) | **1,307** | Historical + live, all-weather |
| EONET floods | event points | — | 0 events in 60 days over TH | Not usable |

Notes:
- **VNP35_L2 is a VIIRS cloud mask, not a flood product.** The VIIRS flood successor to MCDWD is **VCDWD_L3_NRT** (GeoTIFF on `nrt3.modaps.eosdis.nasa.gov`). It is also NRT-only.
- **Train/serve mismatch:** a model calibrated on DSWx (30 m) and served on MCDWD/VCDWD (250 m) is not the same feature. Options: serve on DSWx-S1 too (revisit ~6–12 days, latency hours–days), or use 250 m NRT live and accept that it was never validated.
- **Urban limitation:** 30–250 m water masks see river/peri-urban inundation, not street-level flooding in central Bangkok. The satellite factor mostly matters at the urban fringe (e.g. Lat Krabang, Nong Chok, Bang Bua Thong).
- Downloading MCDWD/DSWx files needs an Earthdata Login token (`EARTHDATA_TOKEN`). Metadata search does not.

## 2. ThaiWater (HII / สสน.) API: verified, no auth

Base: `https://api-v3.thaiwater.net/api/v1/thaiwater30/public`

| Endpoint | Returns |
|---|---|
| `/waterlevel_load` | Latest reading for 806 telemetry stations (HII 331, RID 314, FOP 89, EGAT 72) |
| `/waterlevel_graph?station_type=tele_waterlevel&station_id={id}&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` | Time series `[{datetime, value, value_out, discharge}]` (10-min or hourly) |
| `/waterlevel_yearly_graph?station_type=tele_waterlevel&station_id={id}&year=2019,2020,...` | Same, per full year |
| `/thailand_main` | Dashboard bundle (dams, rain, warnings, …), 10 MB |

Fields in `waterlevel_load` → `waterlevel_data.data[]`: `waterlevel_datetime`, `waterlevel_msl`, `situation_level`, `diff_wl_bank`, `station{id, tele_station_oldcode, tele_station_name{th,en}, tele_station_lat, tele_station_long, left_bank, right_bank, min_bank, ground_level, warning_level_m, critical_level_m, critical_level_msl}`.
Numbers come back as strings (`"5.43"`). `local_sensors._parse` must cast them, and `warning_level_m` can be null, so fall back to `min_bank`.

### Coverage: % of non-null readings per year, 22 stations in the Bangkok bbox

```
station    08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25
BKK017      0  0  0  0  0 21 95 97 71 53 24 87 93 90 98 85 99 29
BKK021      0  0  0  0  0 25 95 98 72 54 25 74 93 88 97 80 ## 95
CPY015      0  0  0  0 10 74 85 95 77 59 24 88 95 91 72 99 99 99   (Chao Phraya @ Krungthep Bridge)
C.12        0  0  0  0  0  0  0  0  0  0  0  0  0 10 77 64 64 81   (RID Samsen)
AIT001      0  0  0  0  0  0  0  0  0  0  0  0  1 89 96 71 85 91
... (full table: 22 stations)
stations >=50%:  0  0  0  0  0 11 16 14 16 13  0 16 16 17 19 22 20 16
```
- **2011: no data at any Bangkok station.** Telemetry starts in late 2012.
- **2018: network-wide gap** (~24% everywhere). Exclude it.
- 2025 is partial for several stations.

## 3. Ground-truth flood labels

| Source | What | Format | License | Note |
|---|---|---|---|---|
| ThaiWater `min_bank` vs water level | Bank overflow per station, hourly | API | Unknown, see ⚠ | Best label: same timestamps as features |
| OPERA DSWx | Water extent per pixel | GeoTIFF | NASA open | Spatial label |
| GISTDA "พื้นที่น้ำท่วมซ้ำซาก" (data.go.th) | Repeat-flood areas | API | Open Data Common | Static prior, not event labels |
| HII "ระดับน้ำท่วม" | Flood depth | SHP | **CC BY-NC** | Probably 2011; not checked |
| LDD "พื้นที่น้ำท่วมซ้ำซาก" | Repeat-flood areas | SHP | **CC BY-NC-ND** | |

flood.gistda.or.th and data.gistda.or.th timed out from here. Their yearly flood-extent archive remains **unverified**.

⚠ **Licensing blocks B2B:** several Thai flood datasets are **Non-Commercial**, and the ThaiWater API terms of use were not found. A paid B2B routing API needs written permission from HII/GISTDA, or must use only NASA-open data.

⚠ **Circularity:** if the label is "station X overflowed" and a feature is station X's water-level trend, the backtest measures how well the model predicts a sensor's future from its own past. Labels must be at a forecast horizon (e.g. overflow 3–6 h ahead). Report the score with the sensor factor removed as well.

## 4. Recommended benchmark window

| Window | Sensors | Satellite | Role |
|---|---|---|---|
| **2019–2023 Jun–Nov** | 16–22 stations | DSWx-HLS (cloudy) | **Calibration** (fit weights) |
| **2024 Jun–Nov** | 20 stations | DSWx-HLS + **DSWx-S1** | **Holdout**: untouched until weights are frozen |
| 2025 Jun–Nov | 16 stations, partial | HLS + S1 | Secondary holdout / live-shadow comparison |
| 2011 | none | none in CMR | Qualitative check vs static maps only |

Pre-register the pass criterion before opening 2024, as in the Numerai/gold projects.

## Next step (proposed)
1. Pull hourly ThaiWater series for the 22 stations, 2019–2025, into `data/cache/` (≈ 22 × 7 years).
2. Define events: `level ≥ min_bank` for ≥ 1 h. Count events per station-season, since too few events means no calibration.
3. For each event, check how many DSWx-HLS granules within ±3 days are usable (not clouded).
4. Then write the pre-registration (label, horizon, metric, pass bar) before fitting anything.
