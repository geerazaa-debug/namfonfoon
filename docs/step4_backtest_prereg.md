# Step 4 Pre-registration — Street-Flood Backtest (LOCKED)

Approved by the project owner 2026-10-08 (rain-day 10 mm, +0.05 PR-AUC margin with CI excluding 0, Copernicus GLO-30).
Committed **before** any label × feature analysis. Already seen beforehand: DDS/Traffy availability counts only.
One shot: 2025 is evaluated once. Any change after seeing results needs a new amendment, and 2025 then stops being a holdout.

## Question
Does the multi-factor model (M1) predict DDS street flooding better than rain alone (B0)?

## Data
- **Label:** BMA DDS `frd_dds` (CC BY), deduplicated as in `research/step3_discovery.py`. District = `district` with the `เขต` prefix stripped. Date = local date of `frd_flood_date_begin`.
- **District polygons:** geoBoundaries THA ADM2 (CC BY 3.0 IGO, release 9469f09). The 50 Bangkok districts are matched by a fixed Thai↔English table in code. Area in km² uses an equal-area projection.
- **Rain:** ThaiWater `rain_monthly_graph` daily values, for the rain stations listed in `thailand_main` on 2026-10-08.
- **Tide:** ThaiWater CPY015 (Chao Phraya, Krungthep Bridge) hourly level from the Step 2 cache. `min_bank` = 2.16 m.
- **DEM:** Copernicus GLO-30 tiles N13E100 and N14E100 (AWS open data).
- **Traffy:** tickets tagged `น้ำท่วม` from `publicapi.traffy.in.th/share/teamchadchart/search`, pulled one day at a time (the API caps at 1,000 per request; a day at the cap is split into halves). Assigned to districts by point-in-polygon on `coords`.

## Units
- Districts: the 50 BMA districts **with ≥ 1 DDS record in 2022–2024** (selected without 2025).
- Days: 2022-01-01 → 2025-12-31.
- Rain stations for district d: those inside the polygon. If none, the nearest station to the polygon centroid within 10 km. If none, the district is excluded.
- **R(d, D)** = max over d's stations of max(rain[D], rain[D+1]) in mm. Rationale: ThaiWater's daily accumulation window is unverified (likely 07:00–07:00), so rain[D+1] may hold the afternoon of D. B0 and M1 use the same R.
- **Unit included iff R ≥ 10 mm.** Units with no non-null rain are excluded.
- **Label y = 1** if ≥ 1 DDS record for (d, D), else 0.
- Train = units in 2022–2024. Test = units in 2025.

## Features
| Name | Definition | Missing |
|---|---|---|
| rain | log1p(R) | — (unit excluded) |
| tide | max hourly CPY015 level on local date D ÷ 2.16 | train median |
| elev | median GLO-30 elevation (m) of pixels inside the district polygon | — |
| traffy | log1p(100 × district share of all Bangkok `น้ำท่วม` tickets in the 365 days ending D−1 ÷ district area km²). Missing if fewer than 500 tickets in the window | train median |

Features are standardised with train mean and std. DDS `frd_total_rainfall` and any other DDS field are **never** used as features.

## Models
- **B0:** logistic regression on [rain].
- **M1:** logistic regression on [rain, tide, elev, traffy].
- scikit-learn `LogisticRegression(penalty=None, max_iter=1000)`, fit on train only.

## Metric and decision (test = 2025)
- PR-AUC = `average_precision_score`.
- Δ = PR-AUC(M1) − PR-AUC(B0). 95% CI: bootstrap by resampling unique test dates with replacement, 2,000 resamples, seed 20261008, percentile interval.
- **PASS iff Δ ≥ 0.05 and CI lower bound > 0.** Otherwise **FAIL**.
- Reported but not gated: base rate, B0/M1 PR-AUC, Brier scores, M1 coefficients, unit counts, excluded districts.
- PASS → M1 coefficients become the basis for the `risk_thresholds.yaml` weights (separate change). FAIL → the multi-factor engine is not shipped. B0's absolute PR-AUC is reported to inform a rain-only advisory decision.

## Known limitations (accepted in advance)
- DDS covers patrolled main roads only, and its negatives are noisy.
- Traffy tags are applied by BMA staff and may lag.
- District-level resolution, and a daily rain proxy.
