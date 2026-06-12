from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .audio_analysis import AudioAnalysis


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}


@dataclass
class TimelineItem:
    index: int
    source: str
    source_type: str
    start: float
    end: float
    duration: float
    effect: str
    transition_hint: str = "cut"


@dataclass
class Timeline:
    audio: dict
    width: int
    height: int
    fps: int
    items: list[TimelineItem]
    selection: dict | None = None

    def to_json(self) -> dict:
        return {
            "audio": self.audio,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "selection": self.selection or {},
            "items": [asdict(item) for item in self.items],
        }


def collect_assets(paths: list[Path]) -> list[Path]:
    assets: list[Path] = []
    for path in paths:
        path = path.expanduser().resolve()
        if not path.exists():
            continue
        if path.is_dir():
            for child in sorted(path.iterdir()):
                if child.suffix.lower() in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
                    assets.append(child.resolve())
        elif path.suffix.lower() in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
            assets.append(path)
    return assets


def build_timeline(
    analysis: AudioAnalysis,
    assets: list[Path],
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    beats_per_cut: int = 4,
    max_items: int | None = None,
) -> Timeline:
    if not assets:
        raise ValueError("No image or video assets found")
    if beats_per_cut < 1:
        raise ValueError("beats_per_cut must be >= 1")

    if analysis.onset_strength:
        cut_points = _adaptive_cut_points(
            analysis.beats, analysis.onset_strength, analysis.duration, beats_per_cut
        )
    else:
        cut_points = _cut_points(analysis.beats, analysis.duration, beats_per_cut)

    if max_items:
        cut_points = cut_points[: max_items + 1]

    items: list[TimelineItem] = []
    effects = ["zoom_in", "zoom_out", "pan_left", "pan_right"]
    for index, (start, end) in enumerate(zip(cut_points, cut_points[1:])):
        source = assets[index % len(assets)]
        source_type = "image" if source.suffix.lower() in IMAGE_EXTENSIONS else "video"
        hint = _transition_hint_for(start, analysis.sections)
        items.append(
            TimelineItem(
                index=index,
                source=str(source),
                source_type=source_type,
                start=round(start, 4),
                end=round(end, 4),
                duration=round(end - start, 4),
                effect=effects[index % len(effects)] if source_type == "image" else "fit",
                transition_hint=hint,
            )
        )

    return Timeline(
        audio=analysis.to_json(),
        width=width,
        height=height,
        fps=fps,
        items=items,
    )


def write_timeline(path: Path, timeline: Timeline) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(timeline.to_json(), indent=2) + "\n", encoding="utf-8")


def load_timeline(path: Path) -> Timeline:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Timeline(
        audio=data["audio"],
        width=int(data["width"]),
        height=int(data["height"]),
        fps=int(data["fps"]),
        selection=data.get("selection", {}),
        items=[TimelineItem(**item) for item in data["items"]],
    )


def _transition_hint_for(start_time: float, sections: list[dict]) -> str:
    for section in sections:
        if section.get("start", 0.0) <= start_time < section.get("end", float("inf")):
            return "xfade" if section.get("energy_level") == "low" else "cut"
    return "cut"


def _adaptive_cut_points(
    beats: list[float],
    onset_strength: list[float],
    duration: float,
    beats_per_cut: int,
) -> list[float]:
    if not onset_strength or len(onset_strength) < len(beats):
        return _cut_points(beats, duration, beats_per_cut)

    max_onset = max(onset_strength) or 1.0
    onset_norm = [v / max_onset for v in onset_strength]

    sorted_norm = sorted(onset_norm)
    n = len(sorted_norm)
    threshold_high = sorted_norm[int(0.70 * n)]
    threshold_low = sorted_norm[int(0.30 * n)]

    points = [0.0]
    last_cut_idx = 0
    last_cut_time = 0.0
    min_gap = 0.35

    for i, beat_time in enumerate(beats):
        if beat_time <= 0 or beat_time >= duration:
            continue

        beats_since = i - last_cut_idx
        strength = onset_norm[i] if i < len(onset_norm) else 0.5

        if strength >= threshold_high:
            interval = 2
        elif strength <= threshold_low:
            interval = 6
        else:
            interval = beats_per_cut

        is_downbeat = (i % 4 == 0)
        should_cut_by_interval = beats_since >= interval
        should_cut_by_downbeat = is_downbeat and beats_since >= 2

        if (should_cut_by_interval or should_cut_by_downbeat) and beat_time - last_cut_time >= min_gap:
            points.append(beat_time)
            last_cut_idx = i
            last_cut_time = beat_time

    if not points or points[-1] < duration:
        points.append(duration)

    # Deduplication with 350ms floor
    deduped = []
    for point in points:
        if not deduped or point - deduped[-1] >= min_gap:
            deduped.append(point)
    if deduped[-1] < duration:
        deduped.append(duration)

    return deduped


def _cut_points(beats: list[float], duration: float, beats_per_cut: int) -> list[float]:
    if not beats:
        beats = [0.0]
    points = [0.0]
    normalized = [beat for beat in beats if 0 < beat < duration]
    points.extend(normalized[beats_per_cut - 1 :: beats_per_cut])
    if points[-1] < duration:
        points.append(duration)

    deduped = []
    for point in points:
        if not deduped or point - deduped[-1] >= 0.35:
            deduped.append(point)
    if deduped[-1] < duration:
        deduped.append(duration)
    return deduped
