"""Step 2 feasibility gate - implements docs/step2_feasibility_prereg.md exactly.

Usage: .venv/Scripts/python research/step2_feasibility.py
Raw ThaiWater responses are cached in data/cache/thaiwater/ (gitignored).
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "thaiwater"
OUT = ROOT / "research" / "output"
TW = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public"
CMR = "https://cmr.earthdata.nasa.gov/search/granules.umm_json"
BKK_TZ = timezone(timedelta(hours=7))
BBOX = (13.40, 14.10, 100.25, 100.95)
YEARS = list(range(2019, 2026))
CALIB, HOLDOUT = range(2019, 2024), 2024

QC_MEDIAN_M, SPIKE_M, NEAR_BANK_M = 6.0, 1.0, 0.20
MIN_RUN_H, MERGE_H, EPISODE_H, BAD_THRESHOLD_FRAC = 2, 24, 24, 0.20
HLS_WINDOW, S1_WINDOW = timedelta(days=3), timedelta(days=6)


def get_json(url: str, timeout: int = 180, attempts: int = 4) -> dict:
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - fixed https hosts
                return json.load(r)
        except (TimeoutError, OSError) as exc:
            if i == attempts - 1:
                raise
            print(f"  retry {i + 1} after {type(exc).__name__}: {url[:90]}", flush=True)
            time.sleep(5 * 2**i)
    raise AssertionError("unreachable")


@dataclass
class Station:
    id: int
    code: str
    name: str
    lat: float
    lon: float
    min_bank: float
    hourly: dict[datetime, float] = field(default_factory=dict)  # UTC hour -> level


@dataclass
class Event:
    station: Station
    start: datetime
    end: datetime  # last overflow hour


def load_stations(bbox: tuple[float, float, float, float] = BBOX) -> list[Station]:
    rows = get_json(f"{TW}/waterlevel_load")["waterlevel_data"]["data"]
    out = []
    for r in rows:
        s = r["station"]
        lat, lon = float(s["tele_station_lat"] or 0), float(s["tele_station_long"] or 0)
        if bbox[0] <= lat <= bbox[1] and bbox[2] <= lon <= bbox[3]:
            mb = s.get("min_bank")
            out.append(
                Station(
                    id=s["id"],
                    code=s.get("tele_station_oldcode") or str(s["id"]),
                    name=(s["tele_station_name"] or {}).get("th", ""),
                    lat=lat,
                    lon=lon,
                    min_bank=float(mb) if mb not in (None, "") else float("nan"),
                )
            )
    return out


def fetch_year(st: Station, year: int) -> list[dict]:
    path = CACHE / f"{st.id}_{year}.json"
    if not path.exists():
        url = f"{TW}/waterlevel_yearly_graph?station_type=tele_waterlevel&station_id={st.id}&year={year}"
        data = get_json(url)["data"]["graph_data"]
        series = data[0]["graph_data"] if data else []
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(series), encoding="utf-8")
        tmp.replace(path)
    return json.loads(path.read_text(encoding="utf-8"))


def build_hourly(st: Station) -> None:
    raw: list[tuple[datetime, float]] = []
    for y in YEARS:
        for p in fetch_year(st, y):
            if p["value"] is None:
                continue
            t = datetime.strptime(p["datetime"], "%Y-%m-%d %H:%M").replace(tzinfo=BKK_TZ).astimezone(UTC)
            raw.append((t, float(p["value"])))
    if not raw:
        return
    med = statistics.median(v for _, v in raw)
    hourly: dict[datetime, float] = {}
    for t, v in raw:
        if abs(v - med) > QC_MEDIAN_M:
            continue
        h = t.replace(minute=0, second=0, microsecond=0)
        hourly[h] = max(v, hourly.get(h, v))
    one = timedelta(hours=1)
    spikes = [
        h
        for h, v in hourly.items()
        if (h - one) in hourly
        and (h + one) in hourly
        and abs(v - hourly[h - one]) > SPIKE_M
        and abs(v - hourly[h + one]) > SPIKE_M
    ]
    for h in spikes:
        del hourly[h]
    st.hourly = dict(sorted(hourly.items()))


def station_events(st: Station, threshold: float) -> list[Event]:
    one = timedelta(hours=1)
    runs: list[list[datetime]] = []
    for h, v in st.hourly.items():
        if v < threshold:
            continue
        if runs and h - runs[-1][-1] == one:
            runs[-1].append(h)
        else:
            runs.append([h])
    events: list[Event] = []
    for run in (r for r in runs if len(r) >= MIN_RUN_H):
        if events and run[0] - events[-1].end <= timedelta(hours=MERGE_H):
            events[-1].end = run[-1]
        else:
            events.append(Event(st, run[0], run[-1]))
    return events


def bad_threshold_years(st: Station) -> list[int]:
    bad = []
    for y in YEARS:
        vals = [v for h, v in st.hourly.items() if h.astimezone(BKK_TZ).year == y]
        if vals and sum(v >= st.min_bank for v in vals) / len(vals) > BAD_THRESHOLD_FRAC:
            bad.append(y)
    return bad


def cluster_episodes(events: list[Event]) -> list[list[Event]]:
    episodes: list[list[Event]] = []
    end = None
    for ev in sorted(events, key=lambda e: e.start):
        if episodes and ev.start - end <= timedelta(hours=EPISODE_H):
            episodes[-1].append(ev)
            end = max(end, ev.end)
        else:
            episodes.append([ev])
            end = ev.end
    return episodes


def year_of(ep: list[Event]) -> int:
    return ep[0].start.astimezone(BKK_TZ).year


def cmr_granules(short_name: str, ep: list[Event], window: timedelta) -> list[dict]:
    lats = [e.station.lat for e in ep]
    lons = [e.station.lon for e in ep]
    start = min(e.start for e in ep) - window
    end = max(e.end for e in ep) + window
    params = {
        "short_name": short_name,
        "bounding_box": f"{min(lons)},{min(lats)},{max(lons)},{max(lats)}",
        "temporal": f"{start:%Y-%m-%dT%H:%M:%SZ},{end:%Y-%m-%dT%H:%M:%SZ}",
        "page_size": 200,
    }
    items = get_json(f"{CMR}?{urllib.parse.urlencode(params)}", timeout=60).get("items", [])
    return [{a["Name"]: a["Values"][0] for a in i["umm"].get("AdditionalAttributes", [])} for i in items]


def satellite_match(ep: list[Event]) -> dict[str, int]:
    hls = cmr_granules("OPERA_L3_DSWX-HLS_V1", ep, HLS_WINDOW)
    usable_hls = [
        g for g in hls if float(g.get("CloudCoverage", 100)) <= 30 and float(g.get("SpatialCoverage", 0)) >= 50
    ]
    s1 = []
    if max(e.end for e in ep) >= datetime(2024, 8, 1, tzinfo=UTC) - S1_WINDOW:
        s1 = [g for g in cmr_granules("OPERA_L3_DSWX-S1_V1", ep, S1_WINDOW) if float(g.get("SpatialCoverage", 0)) >= 50]
    return {"hls_total": len(hls), "hls_usable": len(usable_hls), "s1_usable": len(s1)}


def analyse(stations: list[Station], threshold_offset: float) -> dict:
    valid = [s for s in stations if s.hourly and s.min_bank == s.min_bank]
    excluded = {s.code: bad_threshold_years(s) for s in valid}
    excluded = {k: v for k, v in excluded.items() if v}
    used = [s for s in valid if s.code not in excluded]
    events = [e for s in used for e in station_events(s, s.min_bank - threshold_offset)]
    episodes = cluster_episodes(events)
    return {"used": used, "excluded": excluded, "events": events, "episodes": episodes}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    CACHE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    stations = load_stations()
    print(f"stations in bbox: {len(stations)}")
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(build_hourly, stations))

    print("\n== Station coverage / thresholds")
    for s in stations:
        n = len(s.hourly)
        above = sum(v >= s.min_bank for v in s.hourly.values())
        print(f"{s.code:8} min_bank={s.min_bank:6.2f}  valid_h={n:6}  overflow_h={above:5}  {s.name}")

    main_res = analyse(stations, 0.0)
    near_res = analyse(stations, NEAR_BANK_M)
    print(f"\nexcluded (threshold sanity, >20% overflow hours in a year): {main_res['excluded']}")

    for label, res in (("OVERFLOW (>= min_bank)", main_res), ("NEAR-BANK (>= min_bank - 0.20 m)", near_res)):
        eps = res["episodes"]
        print(f"\n== {label}: {len(res['events'])} station events, {len(eps)} episodes")
        print("episodes by year:", dict(sorted(Counter(year_of(e) for e in eps).items())))
        months = Counter(e[0].start.astimezone(BKK_TZ).month for e in eps)
        print("episodes by month:", dict(sorted(months.items())))
        per_station = Counter(e.station.code for e in res["events"] if e.start.astimezone(BKK_TZ).year in CALIB)
        print("calib station events:", dict(per_station.most_common()))

    eps = main_res["episodes"]
    print(f"\n== Satellite matching for {len(eps)} overflow episodes ...")
    with ThreadPoolExecutor(4) as pool:
        sats = list(pool.map(satellite_match, eps))

    calib = [(e, s) for e, s in zip(eps, sats, strict=True) if year_of(e) in CALIB]
    hold = [(e, s) for e, s in zip(eps, sats, strict=True) if year_of(e) == HOLDOUT]
    calib_stations = Counter(ev.station.code for e, _ in calib for ev in e)
    g1 = len(calib) >= 30 and sum(c >= 3 for c in calib_stations.values()) >= 5
    g2 = len(hold) >= 10
    g3_n = sum(s["hls_usable"] > 0 for _, s in calib)
    g4_n = sum(s["hls_usable"] > 0 or s["s1_usable"] > 0 for _, s in hold)
    gates = {
        "G1": {"pass": g1, "episodes": len(calib), "stations_ge3": sum(c >= 3 for c in calib_stations.values())},
        "G2": {"pass": g2, "episodes": len(hold)},
        "G3": {"pass": g3_n >= 15, "episodes_with_usable_hls": g3_n},
        "G4": {"pass": g4_n >= 5, "episodes_with_usable_sat": g4_n},
    }
    print(json.dumps(gates, indent=2))

    rows = [
        {
            "year": year_of(e),
            "start_utc": min(x.start for x in e).isoformat(),
            "end_utc": max(x.end for x in e).isoformat(),
            "hours": int((max(x.end for x in e) - min(x.start for x in e)).total_seconds() // 3600) + 1,
            "stations": sorted({x.station.code for x in e}),
            **s,
        }
        for e, s in zip(eps, sats, strict=True)
    ]
    (OUT / "step2_episodes.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    (OUT / "step2_gates.json").write_text(
        json.dumps({"gates": gates, "excluded": main_res["excluded"]}, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
