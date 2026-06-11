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
- Timeline items: {item_count}
- Render size: {timeline.width}x{timeline.height}
- FPS: {timeline.fps}

## Conclusiones

The edit was rendered from a deterministic `timeline.json`, so timing can be reviewed, adjusted, and rendered again. For tighter musical sync, install the optional Python dependencies in `requirements.txt` to enable `librosa` beat tracking.
"""
    path.write_text(content, encoding="utf-8")

