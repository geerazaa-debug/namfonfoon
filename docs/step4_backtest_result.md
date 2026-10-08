# Step 4 Result — Street-Flood Backtest: **FAIL**

Pre-registration: `docs/step4_backtest_prereg.md` (commit 6327058, locked before analysis). Script: `research/step4_backtest.py`. Output: `research/output/step4_backtest.json`. Run once on 2026-10-08, after a `--build-only` plumbing check that showed no label statistics.

## Gate (holdout 2025)
| | PR-AUC | Brier |
|---|---|---|
| B0: rain only | **0.1705** | 0.0601 |
| M1: rain + tide + elev + traffy | **0.1482** | 0.0615 |
| Δ (M1 − B0) | **−0.0222**, 95% CI [−0.1076, +0.0344] | |

**Pass bar: Δ ≥ +0.05 and CI lower > 0. → FAIL.** The multi-factor model is not better than rain alone. Its point estimate is worse.

Test: 1,523 district-days, 102 positives (base rate 6.7%), 169 dates. Train: 5,251 units, 218 positives (4.2%). 43 districts.
B0's PR-AUC of 0.17 is about 2.5× the base rate: some skill, but weak.

M1 standardised coefficients: rain +1.00, tide +0.16, elev **+0.40**, traffy +0.41.
- The positive elevation sign is physically backwards: higher ground should flood less. GLO-30 is a **surface** model that includes buildings, so "elevation" here measures urban density. Dense central districts are also where DDS patrols main roads. The feature learned a coverage/density pattern that did not transfer to 2025.

## Deviations from the pre-registration (stated, not corrected after seeing results)
- Traffy: 2 API days hit the 1,000 cap (2022-07-20/21). Intra-day splitting is not supported by the API (HTTP 500), so those days are undercounted.
- Tide was missing for 1,314 units (CPY015 data gaps) and Traffy for 531 units (early-2022 window <500). Both were filled with the train median as pre-registered.

## Post-hoc diagnostic (after the verdict; does not change it)
**The binding constraint is rain measurement, not the model.** Of 718 DDS flood district-days (2022–2025):
- 321 (45%) had ThaiWater R ≥ 10 mm and entered the test.
- 135 had R < 10 mm. Their **median R was 0.0 mm**, even though DDS's own gauges recorded a median of 67 mm on flood events.
- 262 had no ThaiWater rain value at all.

Only **11 of 50 districts** have a ThaiWater rain station inside them. The rest borrow the nearest station, up to 10 km away. Bangkok storms are convective and only a few km wide, so a single distant gauge misses most of them. Every model in this design is limited by this.

## Decision
- **Do not ship M1.** `risk_thresholds.yaml` stays uncalibrated, and public alerts stay disabled.
- The "multi-factor AI engine" is **not supported** by the evidence available today.
- Holdout 2025 is now spent. Any re-test needs a new holdout, the 2026 season, after it ends.

## What could change the answer (each needs a new pre-registration)
1. **Dense rainfall input:** BMA DDS rain-gauge network (~hundreds of gauges; the source of `frd_total_rainfall`) or radar QPE (TMD/BMA Nong Chok radar). Its historical availability is unverified and may need an official data request.
2. **Terrain model instead of surface model:** a DTM (e.g. FABDEM, bare-earth GLO-30), so elevation measures ground level, not buildings.
3. **Test on the 2026 season** as the new holdout.
