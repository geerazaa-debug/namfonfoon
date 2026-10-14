# FloodWatch: Bangkok Heavy-Rain Advisory

Rule-based heavy-rain alerts per Bangkok district, combined with official BMA road-flood hotspot statistics, pushed over LINE and exposed as a B2B API.

> **Status: v0.2 rule-based advisory.** This is not a flood forecast. A pre-registered backtest (2022–24 train, 2025 test) found no multi-factor model that beats rain-only. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the evidence chain and the rules.

## Data sources
| Source | Used for | Licence |
|---|---|---|
| BMA DDS `frd_dds` road-flood records 2022–25 | District hotspot table (`config/district_hotspots.json`), rain thresholds | CC BY: "สำนักการระบายน้ำ กรุงเทพมหานคร" |
| geoBoundaries THA ADM2 | District polygons (`config/bangkok_districts.geojson`) | CC BY 3.0 IGO |
| ThaiWater `public/rain_24h` (live, hourly) | `rain_1h` per station → rolling 6 h totals | not stated: permission needed before paid use |
| LINE Messaging API | Push alerts, location sharing via webhook | channel token + secret |

Research-only sources (ThaiWater history, Traffy Fondue, NASA CMR/OPERA, Copernicus DEM) are documented in `docs/`.

## Quick start
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
cp .env.example .env   # fill in values
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m uvicorn src.api.server:app --reload
```

## API
| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | — | Liveness |
| POST | `/webhook/line` | `X-Line-Signature` | Subscriptions, citizen reports, route checks (acks at once, handles events in the background) |
| POST | `/v1/risk/assess` | `X-API-Key` | Rain advisory level for a point from rain observations |
| POST | `/v1/zones/nearby` | `X-API-Key` | Risk zones within radius of a point |
| POST | `/v1/route/check` | `X-API-Key` | Does a route polyline cross any risk zone? |
| GET | `/v1/public/reports` | — | Live citizen water reports (no reporter identity) |
| GET | `/v1/public/rain` | — | Last hour's rain at wet ThaiWater gauges (empty unless the poller runs) |
| GET | `/map` | — | Public map of citizen reports + gauge rain; `#from=lat,lon&to=lat,lon` draws a checked route (Thai, large type, mobile-first, installable PWA, works offline) |
| GET | `/report` | LIFF login | Report page that opens inside LINE (big buttons + map pin) |
| POST | `/v1/reports` | LINE ID token (`Authorization: Bearer`) | Submit a report from the LIFF page |
| GET | `/v1/public/app-config` | — | LIFF id / report link / OSRM URL for the pages |

## Alert rules
`level = max(rule_1h, rule_6h)`, then +1 level in DDS hotspot districts when rain is already ≥ moderate. Alerts are sent at **high** or above, and only on observations less than 90 minutes old.

| Level | 1 h | 6 h |
|---|---|---|
| moderate | ≥ 10 mm | ≥ 36 mm |
| high | ≥ 30 mm | ≥ 49 mm |
| critical | ≥ 60 mm | ≥ 67 mm |

The 6 h thresholds are the 10th, 25th and 50th percentiles of rainfall in BMA-recorded road floods. The false-alarm rate is **unmeasured**. Values live in `config/risk_thresholds.yaml`.

## Running the alert poller
Set `POLLER_ENABLED=1` (and `LINE_CHANNEL_ACCESS_TOKEN` to actually send). Every 15 min it pulls ThaiWater rain, builds rolling 6 h totals, assesses each LINE subscriber's shared location, and pushes an advisory at **high** or above. The same user + district is not re-alerted within 3 h unless the level rises.

Subscribers, rain history and cooldowns persist in SQLite (`data/floodwatch.db`, WAL mode, auto-created), so a restart does not lose the 6 h totals or re-send alerts. Run **one** process only.

## Citizen water reports ("น้ำถึงไหนแล้ว?")
Anyone following the LINE OA can report street water in Bangkok with buttons only:
type **รายงานน้ำ** (or a rich-menu postback `report=start`) → pick a level by body height
(✅ น้ำลดแล้ว / 🦶 ตาตุ่ม / 🦵 เข่า / 🧍 เอว) → tap **📍 ส่งตำแหน่ง**. All steps use LINE *replies*, which are free on every plan.

- Reports show on `/map` for 3 h. A report is **confirmed** when ≥ 2 distinct reporters within 300 m picked a level within ±1 step; "gone" never confirms "flooded".
- Coordinates are rounded to ~110 m. The reporter's userId is kept only while the report is live (rate limit 4/h, distinct-reporter count), then nulled; unfollow nulls it at once.
- A location shared *without* a picked level is still the home-area alert subscription.
- These are unverified public observations, not measurements; every reply and the map say so. Using them to measure the rain advisory's false-alarm rate needs a pre-registration first.
- Settings: `community_reports` in `config/settings.yaml` (set `public_map_url` to put the map link in replies).

**LIFF report page** (`/report`): same flow with large buttons and a draggable map pin, inside LINE, no install.
Setup: create a LINE Login channel **under the same provider** as the Messaging API channel (otherwise its userIds differ
and the chat and web rate limits split), add a LIFF app (size Full, endpoint `https://<host>/report`, scope `openid`),
then set `LIFF_ID` and `LINE_LOGIN_CHANNEL_ID`. The server verifies each ID token with LINE. Without them, `/report`
says it is not open yet and `/map` hides its report button.

**PWA**: `/map` has a manifest + service worker (`/sw.js`), so it can be added to the home screen (Android prompt button,
iOS "Share → Add to Home Screen" hint). Offline, it shows the last loaded reports with a banner. OSM tiles are not cached
(tile policy). Web push for neighbour alerts is not built yet.

## Route check ("เช็คเส้นทาง")
Type **เช็คเส้นทาง** (or rich-menu postback `route=start`) → tap **📍 จุดเริ่มต้น** → tap **🏁 ปลายทาง**. LINE's location
picker lets people search a place name or drag the map, so no typing of addresses. The reply lists, in route order:

- the wettest gauge per district within 3 km of the driving route (ThaiWater, last hour, ignored if older than 90 min),
- live citizen water reports within 300 m of it (not "water gone"),
- DDS flood-hotspot districts it passes, when there is rain or water,
- a `/map` link that draws the route.

Verdict: **warn** at ≥ 10 mm/h at any gauge or a knee-deep report; **caution** for any rain or report; **no data** when no
fresh gauge is near the route. It is measured rain and public reports, not a forecast, and the reply says so.

- **Gauges are sparse:** about 33 ThaiWater stations cover the Greater Bangkok box, so a 20 km route may have 2-3 nearby.
  The reply says how many gauges were checked. Dense coverage needs BMA gauges or radar (see "Not yet built").
- Routing: OSRM public demo server (car profile, fair use, no SLA). If it fails, the check runs along the straight line
  and says so. Both points must be inside `area_of_interest`.
- Privacy: the start point is held rounded to ~110 m only until the destination arrives (10 min). Route points are never
  logged, and the map link carries them in the `#fragment`, which browsers do not send to the server: `/map` asks OSRM
  for the line itself.
- Settings: `route_check` in `config/settings.yaml`.

## Deploy
Free always-on hosting on Oracle Cloud: see [`docs/DEPLOY.md`](docs/DEPLOY.md) and `deploy/`.

## Not yet built
- Radar QPE input. A data request draft to BMA is in `docs/bma_data_request_draft.md`
- Multi-instance deployment
- Predictive model: paused. See "Path back to prediction" in `docs/ARCHITECTURE.md`
