# Step 4 Pre-registration — DRAFT (not approved, not yet binding)

**Status: DRAFT.** Items marked ❓ need owner decisions. Once approved, this file is renamed to `step4_backtest_prereg.md` and committed **before** any label × feature analysis.
Already seen (cannot be blinded): DDS and Traffy availability counts per year (`step3_street_flood_data.md`). Not yet seen: any relationship between labels and rain, tide, or elevation.

## Why the gate changes from "enough events" to "beats a baseline"
Availability is already established (127 calibration flood days vs the earlier bar of 30), so a count gate would be a formality. The real question is whether the multi-factor model predicts street flooding **better than rain alone**. If it doesn't, the "AI engine" adds nothing over a rain alert.

## Unit and label
- **Unit:** district × day, for the 45 DDS districts, on days with ThaiWater daily rain ≥ 10 mm at any station within the district (❓ 10 mm). Dry days are trivially negative and would inflate scores.
- **Positive:** ≥ 1 DDS `frd_dds` record in that district starting that day.
- **Negative:** a unit with no DDS record. This is a noisy negative, an accepted limitation, stated in the result.

## Features (must be real-time available; DDS `frd_total_rainfall` is forbidden)
1. ThaiWater daily rain: max over stations within the district / 5 km.
2. Tide / Chao Phraya stage: ThaiWater CPY015 (Krungthep Bridge) daily max, as fraction of bank.
3. Elevation: district median DEM (Copernicus GLO-30 via OPERA DSWx B10_DEM or SRTM) ❓ source.
4. Traffy prior: flood-tagged tickets per km² in the district during the **previous** calendar year (no leakage).
- Satellite is excluded (Step 2: no usable monsoon coverage before 2024-08).

## Models
- **Baseline B0:** logistic regression on rain only.
- **Model M1:** logistic regression on features 1–4. Its standardised coefficients map onto `risk_thresholds.yaml` weights.
- Fit on 2022–2024 only. 2025 stays untouched until the coefficients are frozen and committed.

## Metric and pass bar (holdout 2025)
- Primary: **PR-AUC** (positives are rare).
- ❓ **PASS** if M1 PR-AUC ≥ B0 PR-AUC + **0.05**, **and** the 95% bootstrap CI of the difference (district-day block bootstrap by date, 2,000 resamples) excludes 0.
- **FAIL** → do not ship M1. Ship (or not) the rain-only advisory based on B0's absolute performance, reported honestly.
- Secondary (reported, not gated): Brier score, calibration plot, precision at the top 5% of district-days.
- One shot: no re-fitting after seeing 2025. Any change requires a new amendment, and 2025 can no longer serve as holdout.

## Open decisions for the owner ❓
1. Rain-day threshold for including a unit: 10 mm/day?
2. Pass margin: +0.05 PR-AUC with CI excluding 0?
3. DEM source: SRTM (simple, 30 m) or Copernicus GLO-30 (better, also needs no Earthdata login)?
