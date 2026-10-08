# Step 2 Amendment 1 Result: Pluvial Label → **NO-GO**

Pre-registration: `docs/step2_amendment1_prereg.md` (commit 986ae32, before rainfall download). Script: `research/step2_amendment1.py`. Output: `research/output/step2_amendment1.json`. Run 2026-10-08.

## Gates

| Run | Label | G1 calib 2019–23 (≥30 eps, ≥5 stations ≥3) | G2 holdout 2024 (≥10) | Verdict |
|---|---|---|---|---|
| Bangkok bbox | overflow + rain ≥35 mm | **15** eps, 5 stations | **1** | **FAIL** |
| Expanded (Pathum Thani / Samut Prakan) | overflow + rain ≥35 mm | **15** eps, 5 stations | **1** | **FAIL** |
| *(secondary, not gated)* | near-bank + rain ≥35 mm | 22 eps, 8 stations | 8 | would fail |

- Of 108 non-tidal overflow station events, **35 are pluvial**, 70 are non-pluvial and 3 are rain-unknown.
- Pluvial episodes by year: 2019 0, 2020 2, 2021 5, 2022 6, 2023 2, **2024 1**, 2025 1. All fall in the monsoon (Apr–Nov), as expected for a rain label.
- Nearest-station-only sensitivity: 14 calibration / 1 holdout. The result does not depend on the 10 km max rule.
- **The expansion added nothing.** ThaiWater has only 5 extra telemetry stations in the expanded bbox. One (CPY016) was excluded as tidal, one (HDA005) by the threshold-sanity rule, and the remaining three produced 0 pluvial overflow events. Per the pre-registration, no further expansion is allowed under this amendment.

## Interpretation
**Canal-bank overflow is the wrong ground truth for Bangkok rain flooding.** Bangkok's canals are pumped and drawn down ahead of storms. Streets flood because drainage capacity is exceeded (~60 mm/h), while canals often stay below bank. That is why rain ≥35 mm and canal overflow rarely coincide: 35 events in 7 years. The remaining non-tidal overflows are mostly managed or gate-driven, not rain-driven.

## Decision (per pre-registration)
- **NO-GO for weight calibration** on ThaiWater canal-overflow labels.
- `risk_thresholds.yaml` weights stay uncalibrated. Public alerts stay disabled (CLAUDE.md rule unchanged).
- The tide factor was added to `RiskInput` (weight 0.0) as approved. It is still useful as a feature once a valid label exists.

## Options for the next step (needs a new decision)
1. **Change the ground truth to street-level flood reports**, which is what users actually experience. Candidates to verify: Traffy Fondue complaints tagged flooding (has coordinates and timestamps, likely 2022+), BMA flood-point records (จุดน้ำท่วมขัง / DDS). This needs a new pre-registration with the same G1/G2 bars.
2. **Ship without calibration** as a transparent rule-based rain alert (e.g. ThaiWater 1 h/3 h rain above thresholds near a user), labelled as an advisory, not a prediction. Cheap, but it is not the "AI engine" pitch.
3. **Pause the project** until a label source exists.
