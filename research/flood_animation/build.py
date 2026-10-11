"""Build the Bangkok road-flood history animation (BMA DDS records, CC BY) as a self-contained HTML page.

Usage: .venv/Scripts/python -m research.flood_animation.build
Writes research/flood_animation/flood_map.html (Thai) and flood_map_en.html (English);
open in a browser, or render with render_mp4.py.
Historical records only: no forecast, no model output.
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from shapely.geometry import shape

from research.step3_discovery import dds_records

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
START = date(2021, 10, 1)
END = date(2025, 12, 31)
BOX_W, BOX_H = 1000.0, 860.0  # map area inside the 1080x1920 stage
LANGS = ("th", "en")


def page_name(lang: str) -> str:
    return "flood_map.html" if lang == "th" else f"flood_map_{lang}.html"


def project(geoms: list) -> tuple:
    """Equirectangular projection scaled to fit BOX_W x BOX_H, returned as a point transform."""
    minx = min(g.bounds[0] for g in geoms)
    miny = min(g.bounds[1] for g in geoms)
    maxx = max(g.bounds[2] for g in geoms)
    maxy = max(g.bounds[3] for g in geoms)
    k = math.cos(math.radians((miny + maxy) / 2))
    s = min(BOX_W / ((maxx - minx) * k), BOX_H / (maxy - miny))
    ox = (BOX_W - (maxx - minx) * k * s) / 2
    oy = (BOX_H - (maxy - miny) * s) / 2
    return lambda x, y: (ox + (x - minx) * k * s, oy + (maxy - y) * s)


def svg_path(geom, tf) -> str:
    polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    out = []
    for p in polys:
        for ring in [p.exterior, *p.interiors]:
            pts = [tf(x, y) for x, y in ring.coords]
            out.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z")
    return "".join(out)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    geo = json.loads((ROOT / "config" / "bangkok_districts.geojson").read_text(encoding="utf-8"))
    feats = [(f["properties"]["th"], f["properties"]["en"], shape(f["geometry"])) for f in geo["features"]]
    tf = project([g for _, _, g in feats])

    days: defaultdict[str, set[date]] = defaultdict(set)
    for (t, _, _), r in dds_records().items():
        days[r["district"].strip().removeprefix("เขต").strip()].add(t.date())
    unknown = set(days) - {th for th, _, _ in feats}
    if unknown:
        raise ValueError(f"DDS districts missing from the map: {sorted(unknown)}")

    districts = []
    for th, en, g in feats:
        c = g.representative_point()
        cx, cy = tf(c.x, c.y)
        idx = sorted((d - START).days for d in days[th] if START <= d <= END)
        districts.append(
            {"th": th, "en": en, "path": svg_path(g, tf), "cx": round(cx, 1), "cy": round(cy, 1), "days": idx}
        )

    by_day: defaultdict[int, list[int]] = defaultdict(list)
    for i, d in enumerate(districts):
        for k in d["days"]:
            by_day[k].append(i)
    worst_day, worst = max(by_day.items(), key=lambda kv: (len(kv[1]), kv[0]))
    data = {
        "start": START.isoformat(),
        "totalDays": (END - START).days,
        "districts": districts,
        "events": sorted(by_day.items()),
        "worstDay": worst_day,
        "worstDistricts": worst,
        "floodDays": len(by_day),
        "districtDays": sum(len(v) for v in by_day.values()),
        "zeroDistricts": [d["th"] for d in districts if not d["days"]],
    }
    template = (HERE / "template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for lang in LANGS:
        html = template.replace("/*DATA*/null", payload).replace('/*LANG*/"th"', json.dumps(lang))
        (HERE / page_name(lang)).write_text(html, encoding="utf-8")
    top = sorted(districts, key=lambda d: -len(d["days"]))[:10]
    print(f"flood days {data['floodDays']}, district-days {data['districtDays']}")
    print(f"worst day index {worst_day} ({len(worst)} districts)")
    print("top10:", [(d["th"], len(d["days"])) for d in top])
    print("zero:", data["zeroDistricts"])


if __name__ == "__main__":
    main()
