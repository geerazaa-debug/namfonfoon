"""Claims gate for the flood animation: every fact in the narration and on-screen strings must match the data.

Usage: .venv/Scripts/python -m research.flood_animation.check_claims [--facts-only]
1. Computes the ground-truth facts from the BMA DDS records (no LLM).
2. Sends facts + all texts (narration.json, i18n.json, th/en strings in template.html) to the
   `claims-checker` agent via `claude -p`. Numbers in narration are spelled out in five languages,
   which is why this step needs a language model rather than a regex.
3. Writes out/claims_report.json; exit code 1 on FAIL or on an unreadable verdict.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from research.flood_animation.build import END, HERE, START, load_data
from research.step3_discovery import dds_records

OUT = HERE / "out"
ROOT = HERE.parent.parent


def rain_stats() -> dict:
    """Rainfall amount and duration behind each flood record (records with parseable rain times only)."""
    hours, mm = [], []
    for (t, _, _), r in dds_records().items():
        if r["frd_total_rainfall"].strip():
            mm.append(float(r["frd_total_rainfall"]))
        for fmt in ("%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M"):
            try:
                a = datetime.strptime(f"{r['frd_rain_datestart'].strip()} {r['frd_rain_start'].strip()}", fmt)
                b = datetime.strptime(f"{r['frd_rain_datestop'].strip()} {r['frd_rain_stop'].strip()}", fmt)
            except ValueError:
                continue
            h = (b - a).total_seconds() / 3600
            if 0 < h < 48 and abs((a.date() - t.date()).days) <= 1:
                hours.append(h)
            break
    q = statistics.quantiles(hours, n=4)
    return {
        "records_with_rain_mm": len(mm),
        "rain_mm_median": round(statistics.median(mm), 1),
        "records_with_rain_duration": len(hours),
        "rain_duration_hours_p25_median_p75": [round(x, 1) for x in q],
        "share_of_records_rain_3h_or_less": round(sum(h <= 3 for h in hours) / len(hours), 2),
    }


def facts() -> dict:
    data = load_data()
    start = date.fromisoformat(data["start"])
    day = lambda k: (start + timedelta(days=k)).isoformat()  # noqa: E731
    ranked = sorted(data["districts"], key=lambda d: (-len(d["days"]), d["en"]))
    months = Counter(date.fromisoformat(day(k)).month for k, _ in data["events"])
    return {
        "what_the_data_is": "Road-flood records logged by the BMA Department of Drainage and Sewerage (DDS) on the "
        "roads it patrols. Historical only; not a forecast; absence of a record does not mean no flood.",
        "window": [START.isoformat(), END.isoformat()],
        "first_record_day": day(data["events"][0][0]),
        "last_record_day": day(data["events"][-1][0]),
        "flood_days_total": data["floodDays"],
        "district_days_total": data["districtDays"],
        "districts_total": len(data["districts"]),
        "districts_with_records": len(data["districts"]) - len(data["zeroDistricts"]),
        "worst_day": day(data["worstDay"]),
        "worst_day_district_count": len(data["worstDistricts"]),
        "flood_days_by_month_1_to_12": [months.get(m, 0) for m in range(1, 13)],
        "flood_days_per_district_desc": [{"en": d["en"], "th": d["th"], "days": len(d["days"])} for d in ranked],
        "rain": rain_stats(),
    }


def texts() -> dict:
    template = (HERE / "template.html").read_text(encoding="utf-8")
    m = re.search(r"const STR = \{(.*?)\n\}\[LANG\]", template, re.S)
    return {
        "narration_by_scene": json.loads((HERE / "narration.json").read_text(encoding="utf-8"))["scenes"],
        "on_screen_zh_ja_my": json.loads((HERE / "i18n.json").read_text(encoding="utf-8")),
        "on_screen_th_en_js_source": m.group(1) if m else "NOT FOUND",
        "note": "On-screen numbers (counters, top-10 bars, dates, 'N of M districts') are computed from the same data "
        "at render time; check only numbers and claims written into the text itself.",
    }


def ask_agent(payload: dict) -> dict:
    prompt = (
        "Check every claim in TEXTS against FACTS as your instructions describe. Reply with the JSON object only.\n\n"
        f"FACTS:\n{json.dumps(payload['facts'], ensure_ascii=False, indent=1)}\n\n"
        f"TEXTS:\n{json.dumps(payload['texts'], ensure_ascii=False, indent=1)}\n"
    )
    r = subprocess.run(  # noqa: S603 - fixed argv
        ["claude", "-p", "--agent", "claims-checker", "--tools", ""],  # noqa: S607 - claude CLI on PATH
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        timeout=900,
    )
    m = re.search(r"\{.*\}", r.stdout, re.S)
    if r.returncode != 0 or not m:
        return {"verdict": "ERROR", "issues": [], "raw": (r.stdout + r.stderr)[-2000:]}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"verdict": "ERROR", "issues": [], "raw": r.stdout[-2000:]}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts-only", action="store_true", help="print the computed facts and stop")
    args = ap.parse_args()
    payload = {"facts": facts(), "texts": texts()}
    if args.facts_only:
        print(json.dumps(payload["facts"], ensure_ascii=False, indent=1))
        return
    report = ask_agent(payload)
    OUT.mkdir(exist_ok=True)
    (OUT / "claims_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("verdict:", report.get("verdict"))
    for i in report.get("issues", []):
        print(f"- [{i.get('severity')}] {i.get('lang')} {i.get('where')}: {i.get('problem')} (fix: {i.get('fix')})")
    if report.get("verdict") != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
