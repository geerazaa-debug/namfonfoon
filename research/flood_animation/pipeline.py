"""Semi-automatic pipeline for the flood history clips, with two human gates.

Usage:
  .venv/Scripts/python -m research.flood_animation.pipeline stills --lang th en zh ja my
      build pages, run the claims gate, save one still per scene  -> GATE 1: a human looks at the stills
  .venv/Scripts/python -m research.flood_animation.pipeline render --lang zh ja [--reuse-voice]
      claims gate again, render MP4, add the voice-over if TTS quota allows -> GATE 2: a human approves posting
Nothing here posts anywhere. Each step runs as its own process so a long render does not hold memory.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from research.flood_animation.build import HERE, LANGS

OUT = HERE / "out"
PY = sys.executable


def run(*module_args: str) -> int:
    return subprocess.run([PY, "-m", *module_args], check=False).returncode  # noqa: S603 - own modules


def claims_gate() -> None:
    if run("research.flood_animation.check_claims") != 0:
        sys.exit("claims gate FAILED: fix the texts listed above (out/claims_report.json), then rerun")


def scene_midpoints() -> list[float]:
    scenes = json.loads((HERE / "narration.json").read_text(encoding="utf-8"))["scenes"]
    return [round((s["start"] + s["end"]) / 2, 1) for s in scenes]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("stills", "render"))
    ap.add_argument("--lang", nargs="+", choices=LANGS, default=["th"])
    ap.add_argument("--reuse-voice", action="store_true", help="reuse existing voice clips instead of new TTS calls")
    args = ap.parse_args()

    if run("research.flood_animation.build") != 0:
        sys.exit("build failed")
    claims_gate()
    report = []
    for lang in args.lang:
        suffix = "" if lang == "th" else f"_{lang}"
        if args.stage == "stills":
            if run("research.flood_animation.render_mp4", "--lang", lang, "--stills", *map(str, scene_midpoints())):
                sys.exit(f"stills failed for {lang}")
            report += [str(OUT / f"still{suffix}_{t:05.1f}.png") for t in scene_midpoints()]
            continue
        if run("research.flood_animation.render_mp4", "--lang", lang) != 0:
            sys.exit(f"render failed for {lang}")
        voice = ["--reuse"] if args.reuse_voice else []
        if run("research.flood_animation.narrate", "--lang", lang, *voice) == 0:
            report.append(f"{lang}: {OUT / f'bangkok_flood_history{suffix}_voice.mp4'} (AI voice: disclose it)")
        else:
            report.append(
                f"{lang}: {OUT / f'bangkok_flood_history{suffix}.mp4'} (silent: voice-over failed, see above)"
            )
    print("\n=== ready for human review ===")
    print("\n".join(report))
    if args.stage == "stills":
        print("GATE 1: check every still (text fits, script renders, numbers look right) before `render`.")
    else:
        print("GATE 2: watch each video before posting. Captions must say: historical records, not a forecast;")
        print("source data.bangkok.go.th (CC BY); AI voice-over if present.")


if __name__ == "__main__":
    main()
