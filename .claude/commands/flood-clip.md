---
description: Make or refresh the Bangkok flood history clips (claims gate + two human gates)
argument-hint: <languages, e.g. "th en" or "zh ja my">
---

Languages: $ARGUMENTS (default `th`; valid: th en zh ja my).

1. Check memory before rendering: free RAM should be at least ~3 GB (`Get-CimInstance Win32_OperatingSystem`).
   Render in the foreground, one language per command, so a long render is never reaped as an idle background job.
2. Run `.venv/Scripts/python -m research.flood_animation.pipeline stills --lang <langs>`.
   It rebuilds the pages from the BMA data and runs the `claims-checker` gate first. If the gate FAILS, show the
   issues, propose exact text fixes in `narration.json` / `i18n.json` / `template.html`, and stop.
3. **GATE 1:** Read every still it lists and show them to the user. Point out overflow, missing glyphs (CJK,
   Burmese) or anything wrong. Wait for the user's go-ahead. Do not continue on your own.
4. For each language, run `.venv/Scripts/python -m research.flood_animation.pipeline render --lang <one lang>`
   (add `--reuse-voice` when the narration text for that language has not changed since its voice clips were made).
   If TTS fails on quota (free tier ~10 requests/day per model), report it and deliver the silent video.
5. **GATE 2:** Send the finished videos to the user. Draft captions per language that say: historical records,
   not a forecast; source data.bangkok.go.th (CC BY); "AI voice-over" if narrated. Never post anything yourself.
6. Suggest a commit for any text or code changes.
