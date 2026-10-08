# Step 3: Street-Level Flood Data Discovery

Checked 2026-10-08 against primary sources. Reproduce with `python -m research.step3_discovery` (→ `research/output/step3_discovery.json`).
Note: the Gemini bridge claimed DDS publishes no structured road-flood history. **That is wrong**: the dataset below exists and was downloaded.

## TL;DR
| Source | What | Years | Volume | Coords | License |
|---|---|---|---|---|---|
| **BMA DDS `frd_dds`** — สถิติน้ำท่วมขังบนถนนสายหลัก | Official road-flood records: start/end time, depth (cm), length, lanes, **event rainfall (mm)** | 2022–2025 (2021: Feb only) | 1,437 records, **197 flood days**, 718 district-days | ❌ district + road + location text | **CC BY** ✅ commercial OK with attribution |
| **Traffy Fondue API** | Citizen tickets tagged `น้ำท่วม` / `ท่อระบายน้ำ` | 2022–2025 | 12–20k flood tickets/yr | ✅ lon/lat, UTC timestamp | ⚠ not stated |
| BMA `flood_risk26` (จุดเสี่ยงน้ำท่วม 68) | 215 static risk points with lon/lat | 2025 list | 215 | ✅ | not stated |
| BMA `floodpoint` KML | Static weak points | 2021 | — | ✅ | not stated |

## 1. BMA DDS road-flood records (`data.bangkok.go.th/dataset/frd_dds`)
- One CSV per year. Columns: `frd_rain_datestart/start/datestop/stop, district, frd_road, frd_area, frd_flood_height (cm), frd_flood_distance (m), frd_flood_lane, frd_flood_date_begin/begin/date_end/end, frd_flood_total (hh:mm), frd_total_rainfall (mm)`.
- **Data quirks (handled):** the 2023 data is published twice, once as UTF-8 and once as TIS-620/cp874. Files mix **m/d/Y and d/m/Y** dates, so the format is detected per file. Without dedup, 2023 would be double-counted (489 → 248).

| Year | Records | Flood days | District-days |
|---|---|---|---|
| 2021 | 24 | 4 | 10 (partial: Feb only) |
| 2022 | 324 | 43 | 160 |
| 2023 | 248 | 40 | 133 |
| 2024 | 213 | 44 | 130 |
| 2025 | 628 | 70 | 295 |

- 45 districts and 136 main roads. Median depth 15 cm (p90 20 cm). Median event rainfall 67 mm. Records peak in May and Sep–Oct.
- **Positives only.** A day without a record does not prove there was no flood: coverage is DDS-patrolled main roads, and completeness is unknown. Negatives must come from "rain fell, no DDS record", which is a noisy negative.
- No coordinates. **District-level** analysis avoids geocoding. Road-level analysis would need geocoding of `road + area` (Nominatim/Google ToS) or manual mapping.

## 2. Traffy Fondue public API
`GET https://publicapi.traffy.in.th/share/teamchadchart/search` (no key; returns HTTP 201)
- Working params: `problem_type_abdul=<tag>`, `start=YYYY-MM-DD`, `end=YYYY-MM-DD`, `limit` (`keyword` and `type` are ignored; `offset` timed out and `state` returned 500).
- Fields: `ticket_id, type, problem_type_abdul[], coords[lon,lat], timestamp (UTC), address, description, state, last_activity, photo_url, after_photo, org, star, count_reopen, note`.

| Tag | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|
| น้ำท่วม | 19,860 | 12,906 | 12,374 | 16,055 |
| ท่อระบายน้ำ | 9,450 | 16,081 | 17,044 | 17,298 |
| all tickets | 195,954 | 280,425 | 311,571 | 382,417 |

- ⚠ **2026 tickets are untagged** (`problem_type_abdul` = `['']`, so the tag filter returns nothing). Live use would need our own text classifier on `description`.
- ⚠ **Noisy label:** many tickets are chronic complaints (e.g. "no drain pipe here, floods when it rains"), not reports of an event. The timestamp is the report time, not the flood time. Before use as a label, measure how bursty tickets are on DDS flood days vs dry days.
- Pre-2022 history is not in the API (2021: 14 tickets total).
- License not stated. Needs BMA/NECTEC permission before commercial B2B use.

## 3. Implications for the backtest design
- **Primary label = DDS district-day.** It is official, timestamped, has depth, is CC BY, and has 197 flood days and 718 district-days. That is ~4× the bar that failed in Step 2.
- **Traffy = secondary / spatial prior** (chronic hot-spot density from prior years) and a possible live signal. It is not the primary label, because of noise and the licensing question.
- **Natural split:** calibrate on **2022–2024** (127 flood days, 423 district-days), hold out **2025** (70 days, 295 district-days).
- DDS `frd_total_rainfall` is BMA's own gauge rainfall for the event. It must **not** be used as a feature (it is only known after the fact). Features must come from independent, real-time-available sources (ThaiWater rain, tide, DEM).

## Next: draft pre-registration
See `docs/step4_backtest_prereg_DRAFT.md`. It needs owner approval of the open parameters before any label/feature cross-analysis is run.
