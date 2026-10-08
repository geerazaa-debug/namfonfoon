# Step 2 — Amendment 1 Pre-registration: Pluvial (rain-driven) Event Label

Approved by the project owner 2026-10-08. Written and committed **before** any rainfall data was downloaded.
Supersedes the label in `step2_feasibility_prereg.md`. All other rules (hourly series, QC, ≥2 h runs, 24 h merge, 24 h episode clustering, threshold-sanity exclusion) are unchanged.

## Changes
1. **Tidal stations excluded from event counting:** BKK006, CPY014, CPY015, C.12.
2. **Pluvial filter, X = 35 mm.** A station overflow event counts only if rainfall ≥ 35 mm fell near the station shortly before it.
   - **Data constraint (discovered while designing, before download):** ThaiWater rainfall history (`public/rain_monthly_graph?station_id&month&year`) is **daily totals only**, with no hourly history. "≥35 mm in the 24 h before" is therefore implemented as:
     `max(daily[D−1], daily[D]) ≥ 35 mm`, where D = local (UTC+7) date of the event start.
   - "Near" = **any** ThaiWater rain station within **10 km** of the water-level station, taking the maximum across them, since convective rain is patchy. Sensitivity reported (not gated): nearest station only.
   - If no rain station within 10 km has a non-null value for D−1 or D, the event is **rain-unknown**. It is excluded from the pluvial count and reported separately.
   - Rain-station list = stations present in `public/thailand_main` → `rain` on 2026-10-08. Stations that have been decommissioned since are missed (accepted limitation).
3. **Episodes** are clustered from pluvial station events only.

## Gates (same bars as before; satellite gates dropped, since satellite calibration is already NO-GO)
| ID | Window | Criterion |
|---|---|---|
| G1 | 2019–2023 | ≥ 30 pluvial episodes **and** ≥ 5 stations with ≥ 3 pluvial events |
| G2 | 2024 | ≥ 10 pluvial episodes |

## Expansion trigger (pre-approved)
If G1 fails on the Bangkok bbox (13.40–14.10 N, 100.25–100.95 E), re-run automatically on the expanded bbox **13.40–14.30 N, 100.25–101.00 E** (adds Pathum Thani and Samut Prakan; the bbox is an approximation of the province boundaries). The tidal exclusion for the new stations follows a fixed rule:
- exclude codes starting with `CPY` or `C.` (Chao Phraya main stem, RID main-river gauges), **and**
- exclude any station whose overflow hours (≥ min_bank, 2019–2025) fall ≥ 50% in Jan–May (dry-season-dominated, i.e. tidal/managed).
Same G1/G2 bars. The expanded run is final for this step: no further expansion without a new amendment.

Secondary label (reported, not gated): near-bank (≥ min_bank − 0.20 m) with the same pluvial filter.
