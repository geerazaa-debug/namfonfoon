"""Build runtime config for the rule-based engine from BMA DDS records (CC BY) and geoBoundaries (CC BY 3.0 IGO).

Usage: .venv/Scripts/python -m research.build_hotspots
Writes config/district_hotspots.json and config/bangkok_districts.geojson.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from shapely.geometry import mapping

from research.step3_discovery import dds_records
from research.step4_backtest import load_districts

ROOT = Path(__file__).resolve().parent.parent
SIMPLIFY_DEG = 0.0003  # ~30 m
HOTSPOT_QUANTILE = 0.75


def pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[int(len(s) * p)]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    districts = load_districts()
    recs = dds_records()
    days: defaultdict[str, set] = defaultdict(set)
    depth: defaultdict[str, list[float]] = defaultdict(list)
    roads: defaultdict[str, Counter] = defaultdict(Counter)
    rain = []
    for (t, road, _), r in recs.items():
        th = r["district"].strip().removeprefix("เขต").strip()
        days[th].add(t.date())
        roads[th][road] += 1
        if r["frd_flood_height"].strip():
            depth[th].append(float(r["frd_flood_height"]))
        if r["frd_total_rainfall"].strip():
            rain.append(float(r["frd_total_rainfall"]))
    counts = [len(days[th]) for th in districts]
    cut = pct(counts, HOTSPOT_QUANTILE)
    hotspots = {
        th: {
            "en": d["en"],
            "flood_days_2022_2025": len(days[th]),
            "median_depth_cm": statistics.median(depth[th]) if depth[th] else None,
            "top_roads": [r for r, _ in roads[th].most_common(3)],
            "hotspot": len(days[th]) >= cut and len(days[th]) > 0,
        }
        for th, d in districts.items()
    }
    meta = {
        "source": "BMA DDS frd_dds (data.bangkok.go.th, CC BY), 2022-2025, main roads patrolled by DDS only",
        "hotspot_rule": f"flood_days >= {HOTSPOT_QUANTILE:.0%} quantile across 50 districts = {cut}",
        "dds_event_rain_mm": {p: pct(rain, q) for p, q in (("p10", 0.1), ("p25", 0.25), ("p50", 0.5))},
    }
    (ROOT / "config" / "district_hotspots.json").write_text(
        json.dumps({"meta": meta, "districts": hotspots}, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    features = [
        {
            "type": "Feature",
            "properties": {"th": th, "en": d["en"]},
            "geometry": mapping(d["geom"].simplify(SIMPLIFY_DEG, preserve_topology=True)),
        }
        for th, d in districts.items()
    ]
    (ROOT / "config" / "bangkok_districts.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "attribution": "geoBoundaries THA ADM2 (CC BY 3.0 IGO), release 9469f09; simplified",
                "features": features,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(json.dumps(meta, indent=1, ensure_ascii=False))
    print("hotspots:", sorted(th for th, v in hotspots.items() if v["hotspot"]))


if __name__ == "__main__":
    main()
