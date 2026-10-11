"""Add a Gemini TTS voice-over to a rendered flood history video.

Usage: .venv/Scripts/python -m research.flood_animation.narrate --lang th|en
Needs GEMINI_API_KEY (env var, or the Windows user environment). Reads narration.json,
synthesises one clip per scene, checks it fits the scene, and muxes them into
out/bangkok_flood_history[_en]_voice.mp4. The voice is AI-generated: say so when posting.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import imageio_ffmpeg
from google import genai
from google.genai import types

from research.flood_animation.build import LANGS

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
# Free tier allows ~10 requests/day per model, so fall through every TTS model before giving up.
MODELS = (
    "gemini-3.8-flash-tts",
    "gemini-3.1-flash-tts-preview",
    "gemini-3.8-flash-lite-tts",
    "gemini-2.5-flash-preview-tts",
    "gemini-2.5-pro-preview-tts",
)
RATE = 24_000  # Gemini TTS returns 24 kHz 16-bit mono PCM
RETRIES = 3
MAX_TEMPO = 1.15  # speed-up beyond this is audible


def api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key and sys.platform == "win32":
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
            key = winreg.QueryValueEx(h, "GEMINI_API_KEY")[0]
    if not key:
        sys.exit("GEMINI_API_KEY is not set")
    return key


def synthesise(client: genai.Client, text: str, voice: str) -> bytes:
    config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))
        ),
    )
    last: Exception | None = None
    for model in MODELS:
        try:
            r = client.models.generate_content(model=model, contents=text, config=config)
            return r.candidates[0].content.parts[0].inline_data.data
        except Exception as e:  # noqa: BLE001 - try the next model, report the last error
            last = e
    raise RuntimeError(f"all TTS models failed: {last}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=LANGS, default="th")
    ap.add_argument("--reuse", action="store_true", help="reuse existing out/voice_*.wav clips (saves TTS quota)")
    args = ap.parse_args()
    suffix = "" if args.lang == "th" else f"_{args.lang}"
    video = OUT / f"bangkok_flood_history{suffix}.mp4"
    if not video.exists():
        sys.exit(f"render the video first: {video}")
    spec = json.loads((HERE / "narration.json").read_text(encoding="utf-8"))
    client = genai.Client(api_key=api_key())

    clips: list[tuple[Path, float, float]] = []
    too_long = []
    for i, s in enumerate(spec["scenes"]):
        slot = s["end"] - s["start"]
        path = OUT / f"voice{suffix}_{i}.wav"
        if args.reuse and path.exists():
            with wave.open(str(path), "rb") as w:
                pcm = w.readframes(w.getnframes())
        else:
            # TTS pacing varies run to run: retry and keep the shortest take. No style prefix: it gets read aloud.
            pcm = synthesise(client, s[args.lang], spec["voice"])
            for _ in range(RETRIES):
                if len(pcm) / 2 / RATE <= slot:
                    break
                pcm = min(pcm, synthesise(client, s[args.lang], spec["voice"]), key=len)
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes(pcm)
        secs = len(pcm) / 2 / RATE
        tempo = max(1.0, secs / slot)
        print(f"scene {i}: {secs:.1f}s / slot {slot:.1f}s" + (f" -> speed x{tempo:.2f}" if tempo > 1 else ""))
        if tempo > MAX_TEMPO:
            too_long.append(i)
        clips.append((path, s["start"], tempo))
    if too_long:
        sys.exit(f"scenes {too_long} are too long even at x{MAX_TEMPO}: shorten the text in narration.json")

    target = OUT / f"bangkok_flood_history{suffix}_voice.mp4"
    inputs = ["-i", str(video)]
    for p, _, _ in clips:
        inputs += ["-i", str(p)]
    delays = "".join(
        f"[{n + 1}:a]atempo={tempo:.3f},adelay={int(start * 1000)}:all=1[a{n}];"
        for n, (_, start, tempo) in enumerate(clips)
    )
    mix = "".join(f"[a{n}]" for n in range(len(clips))) + f"amix=inputs={len(clips)}:normalize=0,loudnorm[out]"
    total = f"{spec['scenes'][-1]['end']:.2f}"  # last scene ends with the video; pad audio silence up to it
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", *inputs,
           "-filter_complex", delays + mix, "-map", "0:v", "-map", "[out]",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-t", total, str(target)]  # fmt: skip
    subprocess.run(cmd, check=True)  # noqa: S603 - fixed argv, bundled ffmpeg
    print(target)


if __name__ == "__main__":
    main()
