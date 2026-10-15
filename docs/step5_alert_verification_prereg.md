# Step 5 pre-registration: how often do rain advisories precede street flooding?

Status: **PRE-REGISTERED 2026-10-16, before any verification data exists.** The collector that produces the data is
committed after this file. Nothing below may change after the confirmatory window opens (2027-05-01) except by a
dated amendment committed before the data it affects is looked at.

## 1. Question
The live advisory (`RuleBasedAnalyzer`, `config/risk_thresholds.yaml`, notify at **high** or above) tells LINE users
that heavy rain may flood their area. Its false-alarm rate has never been measured (step 4 only compared models on
daily district labels). This test measures, on unseen data:

- **Q1 (primary):** of the alert episodes the current advisory raises, what share is followed by measured street
  flooding in that district? (precision; false alarm ratio FAR = 1 − precision)
- **Q2:** of measured street-flood episodes, what share had an alert beforehand? (probability of detection, POD)
- **Q3 (decision):** would computing the same rules from BMA DDS gauges instead of ThaiWater gauges do better, so that
  alerts should switch source?

## 2. Ground truth
BMA DDS road flood-depth sensors (`weather.bangkok.go.th/Flood/PageMap/GetData`, ~247 road sensors, cm).
- A district is **flooded** at a poll when any online road sensor in it reads **≥ 10 cm** (the route-check warn depth).
- **Flood episode:** maximal run of flooded polls in one district; runs separated by < 3 h are merged.
- Only district-polls with **≥ 1 online road sensor** are eligible (coverage is recorded each poll).
- Known bias, stated now: sensors sit on main roads and known flood spots. Side-street flooding is invisible, so
  precision is a **lower bound** and POD refers to main-road flooding only. Citizen reports are **not** used as ground
  truth (self-selected, sparse).

## 3. Alerts being verified
Each poll (every 15 min, same as the alert poller) the collector evaluates every district at its polygon's
representative point with the **unchanged** live rules:
- **TW** (primary, = what users get): ThaiWater gauges via `RainAccumulator.near` (5 km, else nearest ≤ 10 km).
- **BMA** (secondary): BMA DDS gauges with the same radius rules, `rf1hr` as 1 h and `rf6hr` as 6 h rain.
- **Alert episode:** maximal run of polls at level ≥ high in one district for one source; a new episode needs a gap of
  ≥ 3 h (the live cooldown).

## 4. Matching
- An alert episode is a **hit** if a flood episode in the same district starts or is ongoing between the alert start
  and **3 h after the alert ends**. Otherwise it is a false alarm.
- A flood episode is **detected** if an alert episode of that source in the same district started between **3 h before**
  the flood start and the flood start.
- Episodes are only counted when the district had sensor coverage for ≥ 80 % of the episode's polls plus the 3 h window.

## 5. Periods
- **Pilot:** collector start → 2027-04-30. Used only to check the pipeline (missing polls, sensor faults, parsing).
  No precision/POD figures from the pilot are published or used to change anything below.
- **Confirmatory:** 2027-05-01 00:00 → 2027-10-31 23:59 Bangkok time. Analysed **once**, after 2027-11-01.
- The analysis script is committed before 2027-05-01 and run on pilot data only to prove it works.

## 6. Pre-stated results and decisions
Report for each source: alert episodes, hits, precision with a Wilson 95 % CI, flood episodes, detected, POD with CI,
and coverage (eligible district-polls / total).

| Outcome | Decision |
|---|---|
| TW alert episodes < 30 | **Inconclusive.** Report counts only; no claim about accuracy; do not extend the window without an amendment. |
| TW precision < 0.20 | User-facing text must say plainly that **most alerts are not followed by main-road flooding**; any threshold change needs a new pre-registration on a later season. |
| TW precision ≥ 0.20 | Publish the measured precision/POD with CI on `/privacy` and in the alert footer. |
| BMA POD ≥ TW POD **and** BMA precision ≥ TW precision − 0.05 (both with ≥ 30 BMA alert episodes) | **Switch** alert rain source to BMA gauges (ThaiWater as fallback). Otherwise keep ThaiWater. |

Thresholds, radii, levels and the hotspot escalation stay exactly as in `config/risk_thresholds.yaml` at the commit of
this file. Results are published whichever way they go.

## 7. What is logged (personal data: none)
Table `verification_log`, one row per district per poll: time, district, TW level, TW 1 h / 6 h rain, BMA level, BMA
1 h / 6 h rain, online road sensors in the district, max depth (cm). No users, no subscriber locations.

## 8. Threats, stated in advance
- BMA endpoints are undocumented and may change or be withdrawn; gaps are reported, not imputed.
- If BMA refuses permission (see `docs/bma_data_request_draft.md`), the test stops and is reported as abandoned.
- ThaiWater gauge coverage is ~33 stations; TW may evaluate many districts as "no fresh data" (level low). That is
  part of what users experience and is counted as such.
- One representative point per district is not every user's location; it is the same simplification for both sources.
