# Step 2 Result: Backtest Feasibility Gate

Pre-registration: `docs/step2_feasibility_prereg.md` (commit 360414c, before data). Script: `research/step2_feasibility.py`. Outputs: `research/output/step2_episodes.json`, `step2_gates.json`. Run 2026-10-08.

## Gates (as pre-registered)

| Gate | Criterion | Result | Verdict |
|---|---|---|---|
| G1 | ≥30 calib episodes, ≥5 stations with ≥3 events | 117 episodes, 10 stations | PASS |
| G2 | ≥10 holdout (2024) episodes | 28 | PASS |
| G3 | ≥15 calib episodes with usable DSWx-HLS | **15** (exactly on the bar) | PASS (nominal) |
| G4 | ≥5 holdout episodes with usable HLS/S1 | 18 (S1: 12, HLS: 6) | PASS |

Excluded by the threshold-sanity rule (bad `min_bank`): BKC002, BKC003, BKC004 (sluice gates), GLF001 (Phra Chula Chom Klao Fort, sea mouth).
Totals 2019–2025: 216 station events → 159 episodes (2019: 29, 2020: 28, 2021: 15, 2022: 17, 2023: 28, 2024: 28, 2025: 14).
Near-bank label (≥ min_bank − 0.20 m): 208 episodes.

## Post-hoc diagnostics (not part of the gate, but they change the interpretation)

1. **The label is mostly tidal, not rain flooding.**
   - Only 56 of 117 calibration episodes fall in the monsoon (Jun–Nov). Episodes occur in every month, including Jan–May, when rain flooding is negligible.
   - **70 of 117 (60%) occur only at BKK006 (Mahachai canal, tidal) and/or CPY014 (Chao Phraya at Nonthaburi).** BKK006 alone has 51 events.
   - The pre-registered label "water ≥ min_bank" therefore measures high tide/river stage. A model calibrated on it would learn tides, and the rainfall weight would be driven toward 0.
2. **Events are mostly single-station.** Only 16 of 117 episodes involve ≥2 stations, and only 6 involve ≥3. Area-wide flooding is rare in this sample.
3. **G3 passes on the wrong events.** All 15 HLS-matched episodes are dry-season or tidal (BKK006 Jan–Jun 2019, CPY014 Dec 2023, one BKK017 in Apr 2023). **There are 0 monsoon, non-tidal episodes with a clear optical image.** This matches expectation: optical imagery is clouded during rain floods. The cloud check is also tile-level, so the true count is at most this.
4. DSWx-S1 (radar) matched 12 of 28 holdout episodes. It is the only satellite source that sees through monsoon cloud, but it starts in 2024-08, so it has no calibration-period data.

## Conclusion
- **Sensor-based model: feasible.** There are enough events (G1/G2), but the label must be redefined to separate rain-driven from tidal overflow.
- **Satellite factor: NO-GO for calibration in practice**, despite the nominal G3 pass. With HLS, `satellite_water` cannot be calibrated on rain floods in 2019–2023. Keep its weight as a fixed prior (or 0) and collect DSWx-S1 from 2024-08 onward. It can be calibrated once ≥2 monsoon seasons of S1 exist (end of 2026 season at the earliest).
- The gates were not altered after seeing the data. Fixing the tidal-label flaw requires a **new pre-registration (Amendment 1)** before any weight fitting.

## Proposed Amendment 1 (to be approved before fitting)
- Rain-driven label: overflow (or near-bank) episode **with ≥ X mm rainfall in the preceding 24 h at the nearest ThaiWater rain station**, or exclude tidal stations (BKK006, CPY014, CPY015, C.12) from the label and keep them only as a tide/river-stage *feature*.
- Add tide/river stage as an explicit feature, since it is currently missing from `RiskInput`.
- Re-run the counts under the amended label with the same G1/G2 bars. If <30 rain-driven calibration episodes remain, extend to near-bank or to more stations (wider bbox: Pathum Thani, Samut Prakan).
