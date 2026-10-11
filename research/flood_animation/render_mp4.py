"""Render flood_map.html to MP4 frame by frame (deterministic renderAt(t), headless Edge, bundled ffmpeg).

Usage:
  .venv/Scripts/python -m research.flood_animation.render_mp4 [--lang en]   # full video
  .venv/Scripts/python -m research.flood_animation.render_mp4 --lang en --stills 2 15 30 37 44
Writes research/flood_animation/out/ (gitignored).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

from research.flood_animation.build import LANGS, page_name

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
FPS = 30


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stills", type=float, nargs="*", help="only save PNGs at these times (seconds)")
    ap.add_argument("--lang", choices=LANGS, default="th")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    suffix = "" if args.lang == "th" else f"_{args.lang}"
    url = (HERE / page_name(args.lang)).as_uri() + "?render=1"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": 1080, "height": 1920})
        page.goto(url, wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        if args.stills:
            for t in args.stills:
                page.evaluate(f"renderAt({t})")
                page.screenshot(path=str(OUT / f"still{suffix}_{t:05.1f}.png"))
            browser.close()
            return
        duration = page.evaluate("window.DURATION")
        target = OUT / f"bangkok_flood_history{suffix}.mp4"
        cmd = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "image2pipe",
            "-framerate",
            str(FPS),
            "-i",
            "-",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            "-preset",
            "slow",
            "-movflags",
            "+faststart",
            str(target),
        ]
        ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)  # noqa: S603 - fixed argv, bundled ffmpeg
        n = int(duration * FPS)
        for i in range(n + 1):
            page.evaluate(f"renderAt({i / FPS})")
            ff.stdin.write(page.screenshot(type="png"))
            if i % (FPS * 5) == 0:
                print(f"{i}/{n}", flush=True)
        ff.stdin.close()
        if ff.wait() != 0:
            sys.exit("ffmpeg failed")
        browser.close()
        print(target)


if __name__ == "__main__":
    main()
