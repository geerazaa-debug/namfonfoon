"""Step 2 Amendment 1 — pluvial event count. Implements docs/step2_amendment1_prereg.md.

Usage: .venv/Scripts/python -m research.step2_amendment1
"""

from __future__ import annotations

import json
import math
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from research.step2_feasibility import (
    BBOX,
    BKK_TZ,
    CALIB,
    HOLDOUT,
    NEAR_BANK_M,
    OUT,
    TW,
    YEARS,
    Event,
    Station,
    bad_threshold_years,
    build_hourly,
    cluster_episodes,
    get_json,
    load_stations,
    station_events,
    year_of,
)

RAIN_CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "thaiwater_rain"
EXPANDED_BBOX = (13.40, 14.30, 100.25, 101.00)
TIDAL = {"BKK006", "CPY014", "CPY015", "C.12"}
X_MM, RADIUS_KM = 35.0, 10.0


@dataclass(frozen=True)
class RainStation:
    id: int
    code: str
    lat: float
    lon: float


def km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def load_rain_stations() -> list[RainStation]:
    d = get_json(f"{TW}/thailand_main")["rain"]["data"]
    rows = d if isinstance(d, list) else d["data"]
    return [
        RainStation(s["id"], s.get("tele_station_oldcode") or "", s["tele_station_lat"], s["tele_station_long"])
        for s in (r["station"] for r in rows)
        if s.get("tele_station_lat") and s.get("tele_station_long")
    ]


_rain_memo: dict[tuple[int, int, int], dict[date, float | None]] = {}
_rain_locks: defaultdict[tuple[int, int, int], threading.Lock] = defaultdict(threading.Lock)


def rain_month(rs: RainStation, year: int, month: int) -> dict[date, float | None]:
    """Daily rain for one station-month. Per-key lock: each file is fetched/read once, never concurrently."""
    key = (rs.id, year, month)
    with _rain_locks[key]:
        if key not in _rain_memo:
            path = RAIN_CACHE / f"{rs.id}_{year}_{month:02d}.json"
            if not path.exists():
                data = get_json(f"{TW}/rain_monthly_graph?station_id={rs.id}&month={month}&year={year}")
                path.write_text(json.dumps(data.get("data") or []), encoding="utf-8")
            rows = json.loads(path.read_text(encoding="utf-8"))
            _rain_memo[key] = {date.fromisoformat(r["rainfall_datetime"][:10]): r["rainfall_value"] for r in rows}
        return _rain_memo[key]


def daily_rain(rs: RainStation, d: date) -> float | None:
    v = rain_month(rs, d.year, d.month).get(d)
    return None if v is None else float(v)


def rain_for_event(ev: Event, nearby: list[RainStation]) -> tuple[float | None, float | None]:
    """(max over stations within radius, nearest-with-data) of max(daily[D-1], daily[D])."""
    d = ev.start.astimezone(BKK_TZ).date()
    best_any: float | None = None
    nearest: float | None = None
    for rs in nearby:  # sorted by distance
        vals = [v for v in (daily_rain(rs, d - timedelta(days=1)), daily_rain(rs, d)) if v is not None]
        if not vals:
            continue
        m = max(vals)
        best_any = m if best_any is None else max(best_any, m)
        if nearest is None:
            nearest = m
    return best_any, nearest


def dry_season_dominated(st: Station) -> bool:
    over = [h for h, v in st.hourly.items() if v >= st.min_bank]
    return bool(over) and sum(h.astimezone(BKK_TZ).month <= 5 for h in over) / len(over) >= 0.5


def run(
    bbox: tuple[float, float, float, float], label: str, rain_stations: list[RainStation], auto_tidal: bool
) -> dict:
    stations = load_stations(bbox)
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(build_hourly, stations))
    valid = [s for s in stations if s.hourly and s.min_bank == s.min_bank]
    sanity = {s.code: y for s in valid if (y := bad_threshold_years(s))}
    tidal = {s.code for s in valid if s.code in TIDAL}
    if auto_tidal:
        new = [s for s in valid if not (BBOX[0] <= s.lat <= BBOX[1] and BBOX[2] <= s.lon <= BBOX[3])]
        tidal |= {s.code for s in new if s.code.startswith(("CPY", "C.")) or dry_season_dominated(s)}
    used = [s for s in valid if s.code not in sanity and s.code not in tidal]

    out: dict = {
        "label": label,
        "stations_in_bbox": len(stations),
        "excluded_sanity": sanity,
        "excluded_tidal": sorted(tidal),
        "used": [s.code for s in used],
    }
    for name, offset in (("overflow", 0.0), ("near_bank", NEAR_BANK_M)):
        events = [e for s in used for e in station_events(s, s.min_bank - offset)]
        near = {
            s.code: sorted(
                (r for r in rain_stations if km(s.lat, s.lon, r.lat, r.lon) <= RADIUS_KM),
                key=lambda r: km(s.lat, s.lon, r.lat, r.lon),
            )
            for s in used
        }
        with ThreadPoolExecutor(4) as pool:
            rains = list(pool.map(lambda e, near=near: rain_for_event(e, near[e.station.code]), events))
        pluvial = [e for e, (m, _) in zip(events, rains, strict=True) if m is not None and m >= X_MM]
        pluvial_nearest = [e for e, (_, n) in zip(events, rains, strict=True) if n is not None and n >= X_MM]
        unknown = sum(m is None for m, _ in rains)
        eps = cluster_episodes(pluvial)
        eps_nearest = cluster_episodes(pluvial_nearest)
        calib_eps = [e for e in eps if year_of(e) in CALIB]
        per_station = Counter(ev.station.code for ep in calib_eps for ev in ep)
        res = {
            "station_events_all": len(events),
            "station_events_rain_unknown": unknown,
            "station_events_pluvial": len(pluvial),
            "episodes_by_year": dict(sorted(Counter(year_of(e) for e in eps).items())),
            "episodes_by_month": dict(sorted(Counter(e[0].start.astimezone(BKK_TZ).month for e in eps).items())),
            "calib_station_events": dict(per_station.most_common()),
            "calib_episodes_multi_station": sum(len({x.station.code for x in e}) >= 2 for e in calib_eps),
            "G1_episodes": len(calib_eps),
            "G1_stations_ge3": sum(c >= 3 for c in per_station.values()),
            "G2_episodes": sum(year_of(e) == HOLDOUT for e in eps),
            "sensitivity_nearest_only": {
                "calib": sum(year_of(e) in CALIB for e in eps_nearest),
                "holdout": sum(year_of(e) == HOLDOUT for e in eps_nearest),
            },
            "rain_stations_within_10km": {k: len(v) for k, v in near.items()},
        }
        res["G1_pass"] = res["G1_episodes"] >= 30 and res["G1_stations_ge3"] >= 5
        res["G2_pass"] = res["G2_episodes"] >= 10
        out[name] = res
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    RAIN_CACHE.mkdir(parents=True, exist_ok=True)
    rain_stations = load_rain_stations()
    print(f"rain stations nationwide: {len(rain_stations)}; years {YEARS[0]}-{YEARS[-1]}")
    results = [run(BBOX, "bangkok", rain_stations, auto_tidal=False)]
    if not results[0]["overflow"]["G1_pass"]:
        print("G1 failed on Bangkok bbox -> pre-approved expansion run")
        results.append(run(EXPANDED_BBOX, "expanded_pathum_samutprakan", rain_stations, auto_tidal=True))
    for r in results:
        print(json.dumps(r, indent=1, ensure_ascii=False, default=str))
    (OUT / "step2_amendment1.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
