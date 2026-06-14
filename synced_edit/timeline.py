from __future__ import annotations

import json
import math
import random
import sys
from collections import deque
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
    # For videos reused more than once: which appearance this is (0, 1, 2, ...).
    # The renderer seeks to a different fragment of the source per appearance so a
    # repeated video never shows the same moment twice. Always 0 for images.
    fragment: int = 0


@dataclass
class Timeline:
    audio: dict
    width: int
    height: int
    fps: int
    items: list[TimelineItem]
    selection: dict | None = None
    focus: str = "dynamic"

    def to_json(self) -> dict:
        return {
            "audio": self.audio,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "focus": self.focus,
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
    max_image_duration: float | None = None,
    min_video_duration: float | None = None,
    max_asset_uses: int | None = 3,
    focus: str = "dynamic",
    seed: int = 0,
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

    song_end = cut_points[-1]
    segments = list(zip(cut_points, cut_points[1:]))

    def _is_image(path: Path) -> bool:
        return path.suffix.lower() in IMAGE_EXTENSIONS

    images = [a for a in assets if _is_image(a)]
    videos = [a for a in assets if not _is_image(a)]
    cap = max_asset_uses if max_asset_uses and max_asset_uses > 0 else None

    # Coarsen the grid ONLY when images alone can't cover the cuts under the cap and
    # there are no videos to absorb the overflow. Videos may repeat freely (each
    # appearance shows a different fragment), so when any video exists the grid never
    # needs coarsening — extra slots simply go to videos.
    if cap and not videos and len(segments) > cap * len(images):
        capacity = cap * len(images)
        print(
            f"Only {len(images)} image(s) for {len(segments)} cuts: coarsening to "
            f"{capacity} clips so none repeats more than {cap}x "
            "(add more media or shorten the audio range for more variety).",
            file=sys.stderr,
        )
        segments = _downsample_segments(segments, capacity)

    work: deque[tuple[float, float]] = deque(segments)

    # "center"/"face" framing keeps the subject in frame, so drop the panning
    # effects that drift the crop toward the edges (and into empty backgrounds).
    if focus in ("center", "face"):
        effects = ["zoom_in", "zoom_out"]
    else:
        effects = ["zoom_in", "zoom_out", "pan_left", "pan_right"]

    # Assignment rules:
    #   * Images are capped at `cap` appearances and may never repeat back-to-back.
    #   * Videos are NOT capped: a repeat just gets the next fragment index so the
    #     renderer seeks to a different moment of the source. They also avoid
    #     immediate repeats when an alternative exists.
    #   * max_image_duration: a long IMAGE slot is filled with a SEQUENCE of
    #     different photos (each <= the max), never the same one twice in a row.
    #   * min_video_duration: a short VIDEO grows by eating following beats.
    min_gap = 0.35
    img_use: dict[str, int] = {}
    vid_frag: dict[str, int] = {}
    items: list[TimelineItem] = []
    effect_index = 0
    last_source: str | None = None
    cap_exceeded = False

    # Draw assets a shuffled "round" at a time rather than in fixed round-robin
    # order. Each round is one appearance of every still-eligible asset; reshuffling
    # per round means consecutive cycles are NOT identical, so a folder with few
    # assets no longer plays the same 2-minute sequence on repeat. The first round
    # keeps the given (smart/mood) order so the opening still follows the ranking.
    rng = random.Random(seed)
    round_bag: list[Path] = []
    rounds_done = 0

    def _pick() -> Path:
        """Next asset: one per shuffled round, never an immediate repeat.

        A round contains every image still under the cap plus every video. When all
        images are capped and there is no video to fill the slot, the cap is relaxed
        (images repeat, shuffled) rather than freezing one photo.
        """
        nonlocal round_bag, rounds_done, cap_exceeded
        if not round_bag:
            eligible = [
                a for a in assets
                if not (_is_image(a) and cap and img_use.get(str(a), 0) >= cap)
            ]
            if not eligible:
                eligible = list(assets)
                cap_exceeded = True
            if rounds_done > 0:
                rng.shuffle(eligible)
            round_bag = eligible
            rounds_done += 1
        for i in range(len(round_bag)):
            if str(round_bag[i]) != last_source:
                return round_bag.pop(i)
        # Only the previous source is left this round (e.g. all images are capped and
        # the lone video would otherwise chain into a tail). Relax the cap and take a
        # different asset so nothing repeats back-to-back; repeat only when there is
        # genuinely a single asset.
        others = [a for a in assets if str(a) != last_source]
        if not others:
            return round_bag.pop(0)
        rng.shuffle(others)
        choice = others[0]
        if _is_image(choice) and cap and img_use.get(str(choice), 0) >= cap:
            cap_exceeded = True
        return choice

    while work:
        start, end = work.popleft()
        source = _pick()
        skey = str(source)

        if not _is_image(source):
            # Video: grow to the minimum if too short, then place with its fragment.
            if min_video_duration and (end - start) < min_video_duration:
                target = min(start + min_video_duration, song_end)
                while end < target - 1e-9 and work:
                    end = work.popleft()[1]
                if end - target >= min_gap:
                    work.appendleft((target, end))
                    end = target
            frag = vid_frag.get(skey, 0)
            items.append(
                _make_item(len(items), source, "video", start, end, "fit", analysis, fragment=frag)
            )
            vid_frag[skey] = frag + 1
            last_source = skey
            continue

        # Image. If the slot is longer than the max hold, give this photo only the
        # first piece and hand the rest back so the NEXT (different) photo fills it.
        span = end - start
        parts = 1
        if max_image_duration and span > max_image_duration:
            # Bound the piece count so each piece stays >= min_gap: a tiny
            # max_image_duration can't explode the clip count or round a sub-clip
            # down to zero duration.
            parts = min(math.ceil(span / max_image_duration), max(1, int(span / min_gap)))
        if parts >= 2:
            piece_end = start + span / parts
            items.append(
                _make_item(
                    len(items), source, "image", start, piece_end,
                    effects[effect_index % len(effects)], analysis,
                )
            )
            work.appendleft((piece_end, end))
        else:
            items.append(
                _make_item(
                    len(items), source, "image", start, end,
                    effects[effect_index % len(effects)], analysis,
                )
            )
        img_use[skey] = img_use.get(skey, 0) + 1
        effect_index += 1
        last_source = skey

    if cap_exceeded:
        print(
            f"Not enough images to keep every photo under {cap} uses; some repeat "
            "more often. Add more photos for more variety.",
            file=sys.stderr,
        )

    if max_items and len(items) > max_items:
        # Truncate, then stretch the kept final clip back to the song end so the
        # video still covers the full audio (otherwise -shortest would clip it).
        items = items[:max_items]
        items[-1].end = round(song_end, 4)
        items[-1].duration = round(items[-1].end - items[-1].start, 4)

    return Timeline(
        audio=analysis.to_json(),
        width=width,
        height=height,
        fps=fps,
        items=items,
        focus=focus,
    )


def _downsample_segments(
    segments: list[tuple[float, float]], target: int
) -> list[tuple[float, float]]:
    """Merge adjacent segments down to ``target`` of them, spread evenly.

    Keeps the first (0.0) and last (song end) boundaries so coverage and continuity
    are preserved; intermediate boundaries are sampled at even fractions.
    """
    if target >= len(segments) or target < 1:
        return segments
    boundaries = [segments[0][0]] + [seg[1] for seg in segments]
    total = len(segments)
    keep = sorted({round(i * total / target) for i in range(target + 1)})
    points = [boundaries[i] for i in keep]
    return list(zip(points, points[1:]))


def _make_item(
    index: int,
    source: Path,
    source_type: str,
    start: float,
    end: float,
    effect: str,
    analysis: AudioAnalysis,
    fragment: int = 0,
) -> TimelineItem:
    return TimelineItem(
        index=index,
        source=str(source),
        source_type=source_type,
        start=round(start, 4),
        end=round(end, 4),
        duration=round(end - start, 4),
        effect=effect,
        transition_hint=_transition_hint_for(start, analysis.sections),
        fragment=fragment,
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
        focus=data.get("focus", "dynamic"),
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
    _land_on_duration(deduped, duration, min_gap)

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
    _land_on_duration(deduped, duration, 0.35)
    return deduped


def _land_on_duration(deduped: list[float], duration: float, min_gap: float) -> None:
    """Ensure the cut points end exactly at ``duration`` without a sub-floor sliver.

    Appends ``duration`` only when it is at least ``min_gap`` past the last kept cut;
    otherwise snaps the last cut to ``duration``. This avoids a final (last_beat,
    duration) pair only microseconds apart, which would round to a zero-duration clip.
    """
    if deduped[-1] >= duration:
        return
    if duration - deduped[-1] >= min_gap or len(deduped) == 1:
        deduped.append(duration)
    else:
        deduped[-1] = duration
