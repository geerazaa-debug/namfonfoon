# Step 2 Pre-registration: Backtest Feasibility Gate

Written and committed **before** any historical water-level data was downloaded or counted.
This gate decides only whether calibration is *feasible*. It says nothing about whether the model works.

## Data
- ThaiWater `public/waterlevel_yearly_graph`, the 22 `tele_waterlevel` stations inside bbox 13.40–14.10 N, 100.25–100.95 E (station list = `waterlevel_load` on 2026-10-08), years 2019–2025.
- Threshold per station = `min_bank` (MSL m) from current station metadata. Historical bank changes are not knowable; accepted limitation.
- Timestamps are Asia/Bangkok (UTC+7) and converted to UTC for satellite matching.

## Event definition (fixed)
1. **Hourly series** = max of non-null readings in each clock hour.
2. **QC:** drop a reading if it is more than 6 m from the station's 2019–2025 median. Drop an hourly value if it differs by more than 1.0 m from **both** neighbouring hours (single-hour spike).
3. **Overflow hour:** hourly value ≥ `min_bank`.
4. **Station event:** ≥ 2 consecutive overflow hours. Events on the same station that start within 24 h of the previous event's end are merged.
5. **Episode (independent sample):** station events from any stations whose time spans lie within 24 h of each other are clustered into one episode, since one storm or tide hits many stations.
6. **Threshold sanity rule:** a station whose overflow hours exceed 20% of its valid hours in any year has a mis-specified `min_bank` (e.g. gate/sluice stations). It is excluded from the counts and listed.
7. Secondary label, reported but not gated: "near-bank" = ≥ `min_bank − 0.20 m`, same rules.

## Satellite matching
- Search CMR for `OPERA_L3_DSWX-HLS_V1` granules containing the station point (`point=lon,lat`), from 3 days before the episode start to 3 days after its end.
- **Usable HLS** = granule `CloudCoverage` ≤ 30 and `SpatialCoverage` ≥ 50. This is a tile-level proxy (~110 km tile), so it is an **upper bound** on pixel-level usability. Pixel CLOUD/CONF layers require Earthdata credentials, which are not configured.
- `OPERA_L3_DSWX-S1_V1` (radar, no cloud issue) within ±6 days, `SpatialCoverage` ≥ 50: counted from 2024-08 on.

## Gates
| ID | Window | Criterion | If fail |
|---|---|---|---|
| G1 | Calibration 2019–2023 | ≥ **30 episodes**, and ≥ **5 stations** each with ≥ 3 station events | **NO-GO** for any calibration |
| G2 | Holdout 2024 | ≥ **10 episodes** | NO-GO: no untouched holdout. Calibration must wait for the 2025/2026 season as holdout |
| G3 | Calibration 2019–2023 | ≥ **15 episodes** with ≥ 1 usable DSWx-HLS granule | `satellite_water` weight **not calibrated**. Sensor + rain + elevation model only; satellite stays a fixed prior or 0 |
| G4 | Holdout 2024 | ≥ **5 episodes** with usable HLS or S1 | Satellite factor cannot be evaluated on holdout |

The 2025 counts are reported for information only. Holdout data (2024) is used **only for counting** here; no model or weight is fitted on it.
