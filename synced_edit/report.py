from __future__ import annotations

from datetime import date
from pathlib import Path

from .timeline import Timeline


def write_report(path: Path, timeline: Timeline, output_video: Path, author: str = "Codex") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    item_count = len(timeline.items)
    duration = float(timeline.audio["duration"])
    bpm = float(timeline.audio["bpm"])
    method = timeline.audio["method"]

    detected_emotion = timeline.audio.get("detected_emotion", "")
    emotion_confidence = timeline.audio.get("emotion_confidence", 0.0)
    effective_mood = timeline.audio.get("effective_mood", "")

    has_onset = bool(timeline.audio.get("onset_strength"))
    cut_method = "adaptive (rhythm-based)" if has_onset else "uniform (beats_per_cut)"

    xfade_count = sum(
        1 for item in timeline.items if getattr(item, "transition_hint", "cut") == "xfade"
    )

    emotion_line = ""
    if detected_emotion:
        emotion_line = (
            f"- Detected emotion: {detected_emotion} (confidence: {emotion_confidence:.0%})\n"
        )
    mood_line = f"- Effective mood: {effective_mood}\n" if effective_mood else ""

    content = f"""# Synced Edit Report

Date: {today}
Author: {author}

## Resumen

Generated a beat-synchronized video edit from local media assets and a local audio file.

## Detalle

- Output video: `{output_video}`
- Audio duration: {duration:.2f} seconds
- Estimated BPM: {bpm:.2f}
- Beat analysis method: `{method}`
- Cut timing: {cut_method}
{emotion_line}{mood_line}- Timeline items: {item_count}
- Crossfade transitions: {xfade_count}
- Render size: {timeline.width}x{timeline.height}
- FPS: {timeline.fps}

## Conclusiones

The edit was rendered from a deterministic `timeline.json`, so timing can be reviewed, adjusted, and rendered again. For tighter musical sync, install the optional Python dependencies in `requirements.txt` to enable `librosa` beat tracking.
"""
    path.write_text(content, encoding="utf-8")
