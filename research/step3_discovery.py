"""Step 3 data discovery: BMA DDS road-flood records + Traffy Fondue flood tickets (counts only).

Usage: .venv/Scripts/python -m research.step3_discovery
Writes research/output/step3_discovery.json and caches DDS CSVs in data/cache/bma/.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from research.step2_feasibility import OUT, get_json

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "bma"
CKAN = "https://data.bangkok.go.th/api/3/action/package_show?id="
TRAFFY = "https://publicapi.traffy.in.th/share/teamchadchart/search"
YEARS = range(2021, 2027)


def dds_records() -> dict[tuple, dict]:
    """Deduplicated DDS road-flood records keyed by (flood start, road, area).

    Files mix m/d/Y and d/m/Y; the format is detected per file (a first field > 12 means d/m/Y).
    """
    recs: dict[tuple, dict] = {}
    for res in get_json(CKAN + "frd_dds")["result"]["resources"]:
        path = CACHE / f"frd_dds_{res['id'][:8]}.csv"
        if not path.exists():
            with urllib.request.urlopen(res["url"], timeout=120) as r:  # noqa: S310 - fixed https host
                path.write_bytes(r.read())
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp874")
        rows = [r for r in csv.DictReader(io.StringIO(text)) if r["frd_flood_date_begin"].strip()]
        parts = [r["frd_flood_date_begin"].strip().split("/") for r in rows]
        dmy = max(int(p[0]) for p in parts) > 12
        if dmy and max(int(p[1]) for p in parts) > 12:
            raise ValueError(f"{path.name}: ambiguous date format")
        fmt = "%d/%m/%Y %H:%M" if dmy else "%m/%d/%Y %H:%M"
        for r in rows:
            t = datetime.strptime(f"{r['frd_flood_date_begin'].strip()} {r['frd_flood_begin'].strip() or '0:00'}", fmt)
            recs[(t, r["frd_road"].strip(), r["frd_area"].strip())] = r
    return recs


def traffy_total(**params: str) -> int | None:
    url = TRAFFY + "?" + urllib.parse.urlencode({**params, "limit": 1})
    return get_json(url).get("total")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    CACHE.mkdir(parents=True, exist_ok=True)
    recs = dds_records()
    days: defaultdict[int, set] = defaultdict(set)
    district_days: defaultdict[int, set] = defaultdict(set)
    for t, _, _ in recs:
        days[t.year].add(t.date())
    for (t, _, _), r in recs.items():
        district_days[t.year].add((t.date(), r["district"].strip()))
    rain = [float(r["frd_total_rainfall"]) for r in recs.values() if r["frd_total_rainfall"].strip()]
    depth = sorted(float(r["frd_flood_height"]) for r in recs.values() if r["frd_flood_height"].strip())
    dds = {
        "records": len(recs),
        "records_by_year": dict(sorted(Counter(t.year for t, _, _ in recs).items())),
        "flood_days_by_year": {y: len(s) for y, s in sorted(days.items())},
        "district_days_by_year": {y: len(s) for y, s in sorted(district_days.items())},
        "records_by_month": dict(sorted(Counter(t.month for t, _, _ in recs).items())),
        "districts": len({r["district"].strip() for r in recs.values()}),
        "roads": len({k[1] for k in recs}),
        "rainfall_filled": f"{len(rain)}/{len(recs)}",
        "rainfall_mm_median": sorted(rain)[len(rain) // 2],
        "depth_cm_median": depth[len(depth) // 2],
        "depth_cm_p90": depth[int(len(depth) * 0.9)],
    }
    traffy = {
        tag: {y: traffy_total(problem_type_abdul=tag, start=f"{y}-01-01", end=f"{y}-12-31") for y in YEARS}
        for tag in ("น้ำท่วม", "ท่อระบายน้ำ")
    }
    traffy["all_tickets"] = {y: traffy_total(start=f"{y}-01-01", end=f"{y}-12-31") for y in YEARS}
    result = {"bma_dds_road_flood": dds, "traffy_totals": traffy}
    print(json.dumps(result, indent=1, ensure_ascii=False, default=str))
    (OUT / "step3_discovery.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
