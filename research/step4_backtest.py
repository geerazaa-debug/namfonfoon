"""Step 4 street-flood backtest. Implements docs/step4_backtest_prereg.md (commit 6327058) exactly.

Usage: .venv/Scripts/python -m research.step4_backtest
Requires requirements-research.txt. Caches under data/cache/.
"""

from __future__ import annotations

import json
import statistics
import sys
import threading
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import rasterio
import rasterio.mask
from pyproj import Transformer
from shapely.geometry import Point, mapping, shape
from shapely.ops import transform
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss

from research.step2_amendment1 import RainStation, km, load_rain_stations, rain_month
from research.step2_feasibility import BKK_TZ, OUT, build_hourly, get_json, load_stations
from research.step3_discovery import dds_records

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
GB_URL = (
    "https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/THA/ADM2/geoBoundaries-THA-ADM2.geojson"
)
DEM_URL = "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{t}_DEM/Copernicus_DSM_COG_10_{t}_DEM.tif"
DEM_TILES = ("N13_00_E100_00", "N14_00_E100_00")
TRAFFY = "https://publicapi.traffy.in.th/share/teamchadchart/search"
TRAFFY_CAP = 1000
START, END = date(2022, 1, 1), date(2025, 12, 31)
TRAIN_YEARS, TEST_YEAR = (2022, 2023, 2024), 2025
RAIN_MIN_MM, TRAFFY_MIN_WINDOW = 10.0, 500
CPY015_MIN_BANK = 2.16
N_BOOT, SEED = 2000, 20261008

DISTRICTS_TH_EN = {
    "พระนคร": "Phra Nakhon",
    "ดุสิต": "Dusit",
    "หนองจอก": "Nong Chok",
    "บางรัก": "Bang Rak",
    "บางเขน": "Bang Khen",
    "บางกะปิ": "Bang Kapi",
    "ปทุมวัน": "Pathum Wan",
    "ป้อมปราบศัตรูพ่าย": "Pom Prap Sattru Phai",
    "พระโขนง": "Phra Khanong",
    "มีนบุรี": "Min Buri",
    "ลาดกระบัง": "Lat Krabang",
    "ยานนาวา": "Yan Nawa",
    "สัมพันธวงศ์": "Samphanthawong",
    "พญาไท": "Phaya Thai",
    "ธนบุรี": "Thon Buri",
    "บางกอกใหญ่": "Bangkok Yai",
    "ห้วยขวาง": "Huai Khwang",
    "คลองสาน": "Khlong San",
    "ตลิ่งชัน": "Taling Chan",
    "บางกอกน้อย": "Bangkok Noi",
    "บางขุนเทียน": "Bang Khun Thian",
    "ภาษีเจริญ": "Phasi Charoen",
    "หนองแขม": "Nong Khaem",
    "ราษฎร์บูรณะ": "Rat Burana",
    "บางพลัด": "Bang Phlat",
    "ดินแดง": "Din Daeng",
    "บึงกุ่ม": "Bueng Kum",
    "สาทร": "Sathon",
    "บางซื่อ": "Bang Sue",
    "จตุจักร": "Chatuchak",
    "บางคอแหลม": "Bang Kho Laem",
    "ประเวศ": "Prawet",
    "คลองเตย": "Khlong Toei",
    "สวนหลวง": "Suan Luang",
    "จอมทอง": "Chom Thong",
    "ดอนเมือง": "Don Mueang",
    "ราชเทวี": "Ratchathewi",
    "ลาดพร้าว": "Lat Phrao",
    "วัฒนา": "Vadhana",
    "บางแค": "Bang Khae",
    "หลักสี่": "Lak Si",
    "สายไหม": "Sai Mai",
    "คันนายาว": "Khan Na Yao",
    "สะพานสูง": "Saphan Sung",
    "วังทองหลาง": "Wang Thonglang",
    "คลองสามวา": "Khlong Sam Wa",
    "บางนา": "Bang Na",
    "ทวีวัฒนา": "Thawi Watthana",
    "ทุ่งครุ": "Thung Khru",
    "บางบอน": "Bang Bon",
}


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def daterange(a: date, b: date) -> list[date]:
    return [a + timedelta(days=i) for i in range((b - a).days + 1)]


# ---------- districts ----------
def load_districts() -> dict[str, dict]:
    path = CACHE / "geo" / "geoBoundaries-THA-ADM2.geojson"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(get_json(GB_URL, timeout=300)), encoding="utf-8")
    gj = json.loads(path.read_text(encoding="utf-8"))
    to_area = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True).transform
    en_th = {v: k for k, v in DISTRICTS_TH_EN.items()}
    out: dict[str, dict] = {}
    for f in gj["features"]:
        en = f["properties"]["shapeName"]
        geom = shape(f["geometry"])
        c = geom.centroid
        if en in en_th and 13.45 <= c.y <= 13.97 and 100.32 <= c.x <= 100.95:
            th = en_th[en]
            if th in out:
                raise ValueError(f"duplicate district polygon {en}")
            out[th] = {"en": en, "geom": geom, "area_km2": transform(to_area, geom).area / 1e6}
    missing = set(DISTRICTS_TH_EN) - set(out)
    if missing:
        raise ValueError(f"districts without polygon: {missing}")
    return out


# ---------- rain ----------
def district_rain_stations(districts: dict, stations: list[RainStation]) -> dict[str, list[RainStation]]:
    out = {}
    for th, d in districts.items():
        inside = [s for s in stations if d["geom"].contains(Point(s.lon, s.lat))]
        if not inside:
            c = d["geom"].centroid
            near = sorted((km(c.y, c.x, s.lat, s.lon), s.id, s) for s in stations)
            inside = [near[0][2]] if near and near[0][0] <= 10 else []
        out[th] = inside
    return out


def prefetch_rain(stations: set[RainStation]) -> None:
    months = sorted({(d.year, d.month) for d in daterange(START, END + timedelta(days=1))})
    jobs = [(s, y, m) for s in stations for y, m in months]
    log(f"rain: {len(jobs)} station-months")
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda j: rain_month(*j), jobs))


def rain_value(s: RainStation, d: date) -> float | None:
    v = rain_month(s, d.year, d.month).get(d)
    return None if v is None else float(v)


# ---------- tide ----------
def tide_daily() -> dict[date, float]:
    st = next(s for s in load_stations() if s.code == "CPY015")
    build_hourly(st)
    daily: dict[date, float] = {}
    for h, v in st.hourly.items():
        d = h.astimezone(BKK_TZ).date()
        daily[d] = max(v, daily.get(d, v))
    return {d: v / CPY015_MIN_BANK for d, v in daily.items()}


# ---------- DEM ----------
def district_elevation(districts: dict) -> dict[str, float]:
    tiles = []
    for t in DEM_TILES:
        p = CACHE / "dem" / f"{t}.tif"
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            with urllib.request.urlopen(DEM_URL.format(t=t), timeout=600) as r:  # noqa: S310 - fixed https host
                p.write_bytes(r.read())
        tiles.append(p)
    out = {}
    for th, d in districts.items():
        vals: list[np.ndarray] = []
        for p in tiles:
            with rasterio.open(p) as src:
                try:
                    arr, _ = rasterio.mask.mask(src, [mapping(d["geom"])], crop=True, filled=False)
                except ValueError:  # polygon does not overlap this tile
                    continue
                vals.append(arr.compressed())
        allv = np.concatenate(vals) if vals else np.array([])
        if allv.size == 0:
            raise ValueError(f"no DEM pixels for {th}")
        out[th] = float(np.median(allv))
    return out


# ---------- Traffy ----------
_traffy_lock = threading.Lock()


def traffy_day(d: date) -> tuple[list[tuple[str, float, float]], bool]:
    path = CACHE / "traffy" / f"{d:%Y-%m-%d}.json"
    if not path.exists():
        url = (
            TRAFFY
            + "?"
            + urllib.parse.urlencode(
                {"problem_type_abdul": "น้ำท่วม", "start": f"{d}", "end": f"{d}", "limit": TRAFFY_CAP}
            )
        )
        res = get_json(url, timeout=240).get("results", [])
        rows = [
            [r["timestamp"], float(r["coords"][0]), float(r["coords"][1])]
            for r in res
            if r.get("coords") and len(r["coords"]) == 2
        ]
        with _traffy_lock:
            path.write_text(json.dumps({"n_raw": len(res), "rows": rows}), encoding="utf-8")
    data = json.loads(path.read_text(encoding="utf-8"))
    return [tuple(x) for x in data["rows"]], data["n_raw"] >= TRAFFY_CAP


def traffy_daily_counts(districts: dict) -> tuple[dict[date, dict[str, int]], dict[date, int], list[date]]:
    """Per local date: tickets per district, total Bangkok tickets. Also the list of capped API days."""
    (CACHE / "traffy").mkdir(parents=True, exist_ok=True)
    days = daterange(START - timedelta(days=366), END)
    log(f"traffy: {len(days)} API days")
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(traffy_day, days))
    seen: set[tuple[str, float, float]] = set()
    per: defaultdict[date, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    tot: defaultdict[date, int] = defaultdict(int)
    capped = [d for d, (_, c) in zip(days, results, strict=True) if c]
    items = [(th, d["geom"]) for th, d in districts.items()]
    for rows, _ in results:
        for row in rows:
            if row in seen:
                continue
            seen.add(row)
            ts, lon, lat = row
            local = datetime.fromisoformat(ts.replace("+00", "+00:00")).astimezone(BKK_TZ).date()
            pt = Point(lon, lat)
            th = next((t for t, g in items if g.contains(pt)), None)
            if th is None:
                continue  # outside Bangkok
            per[local][th] += 1
            tot[local] += 1
    return per, tot, capped


def traffy_feature(per, tot, districts, unit_dates: set[date]) -> dict[tuple[str, date], float | None]:
    """log1p(100 * district share of trailing-365d tickets ending D-1 / area_km2); None if window < 500."""
    out: dict[tuple[str, date], float | None] = {}
    all_days = daterange(START - timedelta(days=366), END)
    cum_d: dict[str, list[int]] = {th: [0] for th in districts}
    cum_t = [0]
    for d in all_days:
        for th in districts:
            cum_d[th].append(cum_d[th][-1] + per.get(d, {}).get(th, 0))
        cum_t.append(cum_t[-1] + tot.get(d, 0))
    idx = {d: i for i, d in enumerate(all_days)}  # cum[i] = sum of days before all_days[i]
    for D in unit_dates:
        i_end, i_start = idx[D], idx[D] - 365  # window = [D-365, D-1]
        total = cum_t[i_end] - cum_t[i_start]
        for th, d in districts.items():
            if total < TRAFFY_MIN_WINDOW:
                out[(th, D)] = None
            else:
                share = (cum_d[th][i_end] - cum_d[th][i_start]) / total
                out[(th, D)] = float(np.log1p(100 * share / d["area_km2"]))
    return out


# ---------- evaluation ----------
def pr_auc_diff_ci(y, p0, p1, dates, rng) -> tuple[float, float]:
    uniq = np.unique(dates)
    by_date = {d: np.where(dates == d)[0] for d in uniq}
    diffs = []
    for _ in range(N_BOOT):
        idx = np.concatenate([by_date[d] for d in rng.choice(uniq, size=len(uniq), replace=True)])
        if y[idx].sum() == 0:
            continue
        diffs.append(average_precision_score(y[idx], p1[idx]) - average_precision_score(y[idx], p0[idx]))
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return float(lo), float(hi)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    districts = load_districts()
    log(f"districts with polygons: {len(districts)}")

    recs = dds_records()
    labels: set[tuple[str, date]] = set()
    unmapped: defaultdict[str, int] = defaultdict(int)
    for (t, _, _), r in recs.items():
        th = r["district"].strip().removeprefix("เขต").strip()
        if th in districts:
            labels.add((th, t.date()))
        else:
            unmapped[th] += 1
    train_districts = sorted({th for th, d in labels if d.year in TRAIN_YEARS})
    log(f"DDS district-days {len(labels)}; unmapped {dict(unmapped)}; unit districts {len(train_districts)}")

    rain_st = district_rain_stations(districts, load_rain_stations())
    no_rain = [th for th in train_districts if not rain_st[th]]
    unit_districts = [th for th in train_districts if rain_st[th]]
    prefetch_rain({s for th in unit_districts for s in rain_st[th]})

    units = []  # (district, date, R)
    for th in unit_districts:
        for D in daterange(START, END):
            vals = [
                v
                for s in rain_st[th]
                for v in (rain_value(s, D), rain_value(s, D + timedelta(days=1)))
                if v is not None
            ]
            if vals and max(vals) >= RAIN_MIN_MM:
                units.append((th, D, max(vals)))
    log(f"units (R >= {RAIN_MIN_MM} mm): {len(units)}")

    tide = tide_daily()
    elev = district_elevation(districts)
    per, tot, capped = traffy_daily_counts(districts)
    traffy = traffy_feature(per, tot, districts, {D for _, D, _ in units})

    y = np.array([int((th, D) in labels) for th, D, _ in units])
    years = np.array([D.year for _, D, _ in units])
    dates = np.array([D.toordinal() for _, D, _ in units])
    train, test = np.isin(years, TRAIN_YEARS), years == TEST_YEAR

    raw_tide = [tide.get(D) for _, D, _ in units]
    raw_traffy = [traffy[(th, D)] for th, D, _ in units]
    med_tide = statistics.median(v for v, t in zip(raw_tide, train, strict=True) if t and v is not None)
    med_traffy = statistics.median(v for v, t in zip(raw_traffy, train, strict=True) if t and v is not None)
    X = np.column_stack(
        [
            np.log1p([R for _, _, R in units]),
            [med_tide if v is None else v for v in raw_tide],
            [elev[th] for th, _, _ in units],
            [med_traffy if v is None else v for v in raw_traffy],
        ]
    )
    mu, sd = X[train].mean(axis=0), X[train].std(axis=0)
    Xs = (X - mu) / sd

    if "--build-only" in sys.argv:  # plumbing check: no label statistics, no model fit
        diag = {
            "units_train": int(train.sum()),
            "units_test": int(test.sum()),
            "unit_districts": len(unit_districts),
            "excluded_no_rain_station": no_rain,
            "rain_stations_per_district": {th: len(rain_st[th]) for th in unit_districts},
            "dds_unmapped_districts": dict(unmapped),
            "missing_tide_units": sum(v is None for v in raw_tide),
            "missing_traffy_units": sum(v is None for v in raw_traffy),
            "traffy_capped_api_days": [str(d) for d in capped],
            "traffy_tickets_assigned": int(sum(tot.values())),
            "feature_mean": np.round(mu, 3).tolist(),
            "feature_std": np.round(sd, 3).tolist(),
            "elev_range_m": [round(min(elev.values()), 2), round(max(elev.values()), 2)],
            "area_km2_range": [
                round(min(d["area_km2"] for d in districts.values()), 1),
                round(max(d["area_km2"] for d in districts.values()), 1),
            ],
        }
        print(json.dumps(diag, indent=1, ensure_ascii=False))
        return

    b0 = LogisticRegression(penalty=None, max_iter=1000).fit(Xs[train][:, [0]], y[train])
    m1 = LogisticRegression(penalty=None, max_iter=1000).fit(Xs[train], y[train])
    p0, p1 = b0.predict_proba(Xs[test][:, [0]])[:, 1], m1.predict_proba(Xs[test])[:, 1]
    ap0, ap1 = average_precision_score(y[test], p0), average_precision_score(y[test], p1)
    delta = ap1 - ap0
    lo, hi = pr_auc_diff_ci(y[test], p0, p1, dates[test], np.random.default_rng(SEED))
    verdict = "PASS" if delta >= 0.05 and lo > 0 else "FAIL"

    result = {
        "verdict": verdict,
        "test_2025": {
            "pr_auc_B0": round(ap0, 4),
            "pr_auc_M1": round(ap1, 4),
            "delta": round(delta, 4),
            "delta_ci95": [round(lo, 4), round(hi, 4)],
            "brier_B0": round(brier_score_loss(y[test], p0), 4),
            "brier_M1": round(brier_score_loss(y[test], p1), 4),
            "units": int(test.sum()),
            "positives": int(y[test].sum()),
            "base_rate": round(float(y[test].mean()), 4),
            "dates": int(len(np.unique(dates[test]))),
        },
        "train": {
            "units": int(train.sum()),
            "positives": int(y[train].sum()),
            "base_rate": round(float(y[train].mean()), 4),
        },
        "m1_coef_standardised": dict(
            zip(["rain", "tide", "elev", "traffy"], np.round(m1.coef_[0], 4).tolist(), strict=True)
        ),
        "b0_coef_standardised": {"rain": round(float(b0.coef_[0][0]), 4)},
        "feature_train_mean": dict(zip(["rain", "tide", "elev", "traffy"], np.round(mu, 4).tolist(), strict=True)),
        "feature_train_std": dict(zip(["rain", "tide", "elev", "traffy"], np.round(sd, 4).tolist(), strict=True)),
        "missing": {
            "tide_units": sum(v is None for v in raw_tide),
            "traffy_units": sum(v is None for v in raw_traffy),
        },
        "unit_districts": len(unit_districts),
        "excluded_no_rain_station": no_rain,
        "dds_unmapped_districts": dict(unmapped),
        "dds_labels_outside_units": sum(1 for th, D in labels if th not in unit_districts and START <= D <= END),
        "traffy_capped_api_days": [str(d) for d in capped],
        "district_elevation_m": {th: round(v, 2) for th, v in sorted(elev.items(), key=lambda x: x[1])},
    }
    print(json.dumps(result, indent=1, ensure_ascii=False))
    (OUT / "step4_backtest.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
