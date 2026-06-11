from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .timeline import Timeline, TimelineItem


def render_timeline(timeline: Timeline, output_path: Path, work_dir: Path | None = None) -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to render the video")

    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    base_work_dir = work_dir.expanduser().resolve() if work_dir else None

    with tempfile.TemporaryDirectory(dir=str(base_work_dir) if base_work_dir else None) as tmp:
        tmp_path = Path(tmp)
        clip_paths = []
        for item in timeline.items:
            clip_path = tmp_path / f"clip_{item.index:04d}.mp4"
            _render_clip(item, timeline, clip_path)
            clip_paths.append(clip_path)

        concat_file = tmp_path / "concat.txt"
        concat_file.write_text(
            "".join(f"file '{path.as_posix()}'\n" for path in clip_paths),
            encoding="utf-8",
        )
        silent_video = tmp_path / "silent.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c",
                "copy",
                str(silent_video),
            ],
            check=True,
        )

        audio_path = timeline.audio["audio_path"]
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(silent_video),
                "-i",
                audio_path,
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            check=True,
        )


def _render_clip(item: TimelineItem, timeline: Timeline, output_path: Path) -> None:
    source = Path(item.source)
    if item.source_type == "image":
        _render_image_clip(source, item, timeline, output_path)
    else:
        _render_video_clip(source, item, timeline, output_path)


def _render_image_clip(source: Path, item: TimelineItem, timeline: Timeline, output_path: Path) -> None:
    frames = max(1, int(round(item.duration * timeline.fps)))
    scale = f"scale={timeline.width}:{timeline.height}:force_original_aspect_ratio=increase"
    crop = f"crop={timeline.width}:{timeline.height}"
    zoom = _zoompan_filter(item.effect, frames, timeline)
    vf = f"{scale},{crop},{zoom},format=yuv420p"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(source),
            "-t",
            f"{item.duration:.4f}",
            "-vf",
            vf,
            "-r",
            str(timeline.fps),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            str(output_path),
        ],
        check=True,
    )


def _render_video_clip(source: Path, item: TimelineItem, timeline: Timeline, output_path: Path) -> None:
    vf = (
        f"scale={timeline.width}:{timeline.height}:force_original_aspect_ratio=increase,"
        f"crop={timeline.width}:{timeline.height},fps={timeline.fps},format=yuv420p"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(source),
            "-t",
            f"{item.duration:.4f}",
            "-vf",
            vf,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            str(output_path),
        ],
        check=True,
    )


def _zoompan_filter(effect: str, frames: int, timeline: Timeline) -> str:
    size = f"{timeline.width}x{timeline.height}"
    duration = max(1, frames)
    if effect == "zoom_out":
        z = "if(lte(on,1),1.12,max(1.0,zoom-0.0015))"
    else:
        z = "min(zoom+0.0015,1.12)"

    if effect == "pan_left":
        x = "iw/2-(iw/zoom/2)-on*2"
        y = "ih/2-(ih/zoom/2)"
    elif effect == "pan_right":
        x = "iw/2-(iw/zoom/2)+on*2"
        y = "ih/2-(ih/zoom/2)"
    else:
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"

    return f"zoompan=z='{z}':x='{x}':y='{y}':d={duration}:s={size}:fps={timeline.fps}"

