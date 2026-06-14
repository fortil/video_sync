from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .timeline import Timeline, TimelineItem

try:  # OpenCV is only needed for --focus face; it is an optional dependency.
    import cv2
except ImportError:  # pragma: no cover - exercised only when cv2 is absent
    cv2 = None

_FACE_WARNED = False


_XFADE_AVAILABLE: bool | None = None

_XFADE_DURATION = 0.25

# Per-clip intermediates are encoded near-losslessly so the ONLY meaningful
# compression is the final assembly encode (controlled by crf/preset). This avoids
# the visible quality loss from compounding two lossy generations. These temp files
# are larger on disk but live only inside the auto-cleaned work dir.
_INTERMEDIATE_CRF = 12
_INTERMEDIATE_PRESET = "veryfast"


def render_timeline(
    timeline: Timeline,
    output_path: Path,
    work_dir: Path | None = None,
    mix_video_audio: bool = False,
    video_audio_volume: float = 1.0,
    background_audio_volume: float = 0.15,
    crf: int = 18,
    preset: str = "medium",
) -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to render the video")

    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    base_work_dir = work_dir.expanduser().resolve() if work_dir else None
    if base_work_dir is not None:
        base_work_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=str(base_work_dir) if base_work_dir else None) as tmp:
        tmp_path = Path(tmp)
        clip_paths = []
        for item in timeline.items:
            clip_path = tmp_path / f"clip_{item.index:04d}.mp4"
            _render_clip(item, timeline, clip_path)
            clip_paths.append(clip_path)

        silent_video = tmp_path / "silent.mp4"
        xfade_result = None
        if _check_xfade_support():
            xfade_result = _build_xfade_filtergraph(clip_paths, timeline.items, timeline.fps)

        if xfade_result is not None:
            filter_complex, out_label = xfade_result
            cmd = ["ffmpeg", "-y"]
            for cp in clip_paths:
                cmd.extend(["-i", str(cp)])
            cmd.extend([
                "-filter_complex", filter_complex,
                "-map", f"[{out_label}]",
                "-c:v", "libx264",
                "-preset", preset,
                "-crf", str(crf),
                "-pix_fmt", "yuv420p",
                "-an",
                str(silent_video),
            ])
            subprocess.run(cmd, check=True)
        else:
            concat_file = tmp_path / "concat.txt"
            concat_file.write_text(
                "".join(f"file '{path.as_posix()}'\n" for path in clip_paths),
                encoding="utf-8",
            )
            # Re-encode instead of "-c copy": the per-clip rendered files can differ
            # in timebase/SAR (image zoompan clips vs video clips), which makes the
            # concat demuxer with stream copy fail or produce broken timestamps.
            # Re-encoding through libx264 normalizes everything into one clean stream.
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
                    "-vf",
                    "format=yuv420p,setsar=1",
                    "-c:v",
                    "libx264",
                    "-preset",
                    preset,
                    "-crf",
                    str(crf),
                    "-pix_fmt",
                    "yuv420p",
                    "-an",
                    str(silent_video),
                ],
                check=True,
            )

        audio_path = timeline.audio["audio_path"]
        _mux_audio(
            silent_video,
            output_path,
            timeline,
            audio_path,
            tmp_path,
            mix_video_audio=mix_video_audio,
            video_audio_volume=video_audio_volume,
            background_audio_volume=background_audio_volume,
        )


def _mux_audio(
    silent_video: Path,
    output_path: Path,
    timeline: Timeline,
    song: str,
    tmp_path: Path,
    mix_video_audio: bool,
    video_audio_volume: float,
    background_audio_volume: float,
) -> None:
    """Attach audio to the silent video.

    Default (mix_video_audio=False): the song is the only audio, played at full
    volume — today's proven behavior.

    Opt-in (mix_video_audio=True): each video clip's own audio is laid onto a
    full-length silent bed at its timeline position and mixed ABOVE the song, which
    sits quietly underneath the whole edit. The full-length silent base means every
    amix sees full-length inputs, so it cannot duck/pump (the interference heard in
    the earlier attempt).
    """
    video_items = [it for it in timeline.items if it.source_type == "video"]

    if not mix_video_audio or video_audio_volume <= 0 or not video_items:
        _mux_song_only(silent_video, output_path, song)
        return

    # Extract each video clip's own audio, mirroring how the video was rendered:
    # _render_video_clip uses "-stream_loop -1 -i src -t duration" with NO -ss, so
    # the audio must come from the source's own t=0 for `duration` seconds (looped),
    # NOT seeking to item.start (which is a SONG-timeline offset, not a seek point).
    auds: list[tuple[TimelineItem, Path]] = []
    for it in video_items:
        src = Path(it.source)
        if not _has_audio_stream(src):
            continue
        ap = tmp_path / f"aud_{it.index:04d}.wav"
        subprocess.run(
            [
                "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src),
                "-t", f"{it.duration:.4f}", "-vn",
                "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(ap),
            ],
            check=True,
        )
        auds.append((it, ap))

    if not auds:
        _mux_song_only(silent_video, output_path, song)
        return

    total = _probe_duration(silent_video)
    bed = _build_video_audio_bed(auds, total, tmp_path)

    # Final mix: video audio (loud) over the song (quiet). normalize=0 keeps the
    # video audio at full level; alimiter only catches rare peaks (does not pump).
    fc = (
        f"[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        f"volume={video_audio_volume}[vid];"
        f"[2:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        f"volume={background_audio_volume}[song];"
        f"[vid][song]amix=inputs=2:normalize=0:dropout_transition=0,alimiter=limit=0.95[mix]"
    )
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(silent_video),
            "-i", str(bed),
            "-i", str(song),
            "-filter_complex", fc,
            "-map", "0:v:0", "-map", "[mix]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart",
            str(output_path),
        ],
        check=True,
    )


def _mux_song_only(silent_video: Path, output_path: Path, song: str) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(silent_video),
            "-i", str(song),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac",
            "-shortest", "-movflags", "+faststart",
            str(output_path),
        ],
        check=True,
    )


def _has_audio_stream(path: Path) -> bool:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=index", "-of", "csv=p=0", str(path),
        ],
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def _build_video_audio_bed(
    auds: list[tuple[TimelineItem, Path]],
    total: float,
    tmp_path: Path,
    batch: int = 24,
) -> Path:
    """Lay each clip's audio at its timeline offset on a full-length silent base.

    For many video clips, mix in batches so the filtergraph stays small (same
    robustness-over-cleverness rationale as the 50-clip xfade cap). Each batch bed
    is itself full-length and already placed, so the second pass just sums them.
    """
    if len(auds) <= batch:
        out = tmp_path / "bed.wav"
        _mix_chunk_to_bed(auds, total, out)
        return out

    beds: list[Path] = []
    for start in range(0, len(auds), batch):
        sub = tmp_path / f"bed_{start:04d}.wav"
        _mix_chunk_to_bed(auds[start:start + batch], total, sub)
        beds.append(sub)

    out = tmp_path / "bed.wav"
    cmd = ["ffmpeg", "-y"]
    for b in beds:
        cmd += ["-i", str(b)]
    labels = "".join(f"[{k}:a]" for k in range(len(beds)))
    cmd += [
        "-filter_complex",
        f"{labels}amix=inputs={len(beds)}:normalize=0:dropout_transition=0[bed]",
        "-map", "[bed]", "-t", f"{total:.4f}", "-c:a", "pcm_s16le", str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


def _mix_chunk_to_bed(
    chunk: list[tuple[TimelineItem, Path]],
    total: float,
    out: Path,
) -> None:
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-t", f"{total:.4f}",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
    ]
    for _it, ap in chunk:
        cmd += ["-i", str(ap)]

    parts: list[str] = []
    labels = ["0:a"]
    for k, (it, _ap) in enumerate(chunk, start=1):
        delay = max(0, int(round(it.start * 1000)))
        parts.append(
            f"[{k}:a]aresample=async=1:first_pts=0,adelay={delay}:all=1[a{k}]"
        )
        labels.append(f"a{k}")
    mix_inputs = "".join(f"[{lbl}]" for lbl in labels)
    parts.append(
        f"{mix_inputs}amix=inputs={len(labels)}:normalize=0:dropout_transition=0[bed]"
    )
    cmd += [
        "-filter_complex", ";".join(parts),
        "-map", "[bed]", "-t", f"{total:.4f}", "-c:a", "pcm_s16le", str(out),
    ]
    subprocess.run(cmd, check=True)


def _check_xfade_support() -> bool:
    global _XFADE_AVAILABLE
    if _XFADE_AVAILABLE is not None:
        return _XFADE_AVAILABLE
    result = subprocess.run(
        ["ffmpeg", "-filters"],
        capture_output=True,
        text=True,
    )
    _XFADE_AVAILABLE = "xfade" in result.stdout
    return _XFADE_AVAILABLE


def _build_xfade_filtergraph(
    clip_paths: list[Path],
    items: list[TimelineItem],
    fps: int,
) -> tuple[str, str] | None:
    if len(items) < 2:
        return None

    # Skip xfade for large clip counts; ffmpeg's filter graph becomes unstable
    # with 50+ inputs and complex nested filters. Fallback to plain concat.
    if len(items) > 50:
        return None

    needs_xfade = any(
        i > 0
        and getattr(item, "transition_hint", "cut") == "xfade"
        and item.duration >= _XFADE_DURATION * 2
        for i, item in enumerate(items)
    )
    if not needs_xfade:
        return None

    filters: list[str] = []

    # Normalize every input link before chaining. Image clips (zoompan/loop) and
    # video clips (re-encoded with fps) can end up with different timebases, which
    # makes xfade fail with "input link timebases do not match". Force a common
    # fps-tied timebase (settb=1/fps), fps, SAR, and pixel format on each input.
    # Use 1/fps rather than AVTB (1/1000000): the concat filter below resets its
    # output timebase, and if that landed back on AVTB while the next xfade input
    # is fps-derived, xfade would reject the mismatch and re-open the crash.
    norm_labels: list[str] = []
    for i in range(len(items)):
        label = f"n{i:04d}"
        filters.append(
            f"[{i}:v]fps={fps},settb=1/{fps},format=yuv420p,setsar=1[{label}]"
        )
        norm_labels.append(label)

    prev_label = norm_labels[0]
    cumulative = items[0].duration

    for i in range(1, len(items)):
        item = items[i]
        hint = getattr(item, "transition_hint", "cut")
        next_label = f"v{i:04d}"
        use_xfade = (
            hint == "xfade"
            and item.duration >= _XFADE_DURATION * 2
            # avoid xfade on the very last clip to prevent audio cutoff
            and i < len(items) - 1
        )

        if use_xfade:
            offset = max(0.0, cumulative - _XFADE_DURATION)
            filters.append(
                f"[{prev_label}][{norm_labels[i]}]"
                f"xfade=transition=fade:duration={_XFADE_DURATION}:offset={offset:.4f}"
                f",settb=1/{fps}[{next_label}]"
            )
            cumulative += item.duration - _XFADE_DURATION
        else:
            # Re-stamp the timebase after concat: the concat filter resets its output
            # timebase, which would otherwise mismatch the next xfade input.
            filters.append(
                f"[{prev_label}][{norm_labels[i]}]concat=n=2:v=1:a=0,settb=1/{fps}[{next_label}]"
            )
            cumulative += item.duration

        prev_label = next_label

    return ";".join(filters), prev_label


def _render_clip(item: TimelineItem, timeline: Timeline, output_path: Path) -> None:
    source = Path(item.source)
    if item.source_type == "image":
        _render_image_clip(source, item, timeline, output_path)
    else:
        _render_video_clip(source, item, timeline, output_path)


def _render_image_clip(source: Path, item: TimelineItem, timeline: Timeline, output_path: Path) -> None:
    frames = max(1, int(round(item.duration * timeline.fps)))
    scale = f"scale={timeline.width}:{timeline.height}:force_original_aspect_ratio=increase"
    crop = _crop_filter(source, timeline)
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
            _INTERMEDIATE_PRESET,
            "-crf",
            str(_INTERMEDIATE_CRF),
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
            _INTERMEDIATE_PRESET,
            "-crf",
            str(_INTERMEDIATE_CRF),
            str(output_path),
        ],
        check=True,
    )


def _crop_filter(source: Path, timeline: Timeline) -> str:
    """Crop the scaled image down to the output size.

    For ``focus="face"`` the crop window is centered on the largest detected face
    so the subject is framed instead of an empty background; it falls back to a
    centered crop when no face is found (or OpenCV is unavailable). Every other
    focus mode uses a centered crop.
    """
    width, height = timeline.width, timeline.height
    if getattr(timeline, "focus", "dynamic") == "face":
        center = _detect_face_center(source)
        if center is not None:
            nx, ny = center
            # iw/ih here are the SCALED image dims (input to the crop filter); the
            # normalized face center is scale-invariant because scaling is uniform.
            x_expr = f"clip({nx:.4f}*iw-{width}/2,0,iw-{width})"
            y_expr = f"clip({ny:.4f}*ih-{height}/2,0,ih-{height})"
            return f"crop={width}:{height}:x='{x_expr}':y='{y_expr}'"
    return f"crop={width}:{height}"


def _detect_face_center(source: Path) -> tuple[float, float] | None:
    """Return the normalized (x, y) center of the largest detected face, or None.

    Uses OpenCV's Haar cascade when available. Returns None — so the caller falls
    back to centered framing — when OpenCV is not installed, the image cannot be
    read, or no face is found.
    """
    global _FACE_WARNED
    if cv2 is None:
        if not _FACE_WARNED:
            print(
                "--focus face needs OpenCV; install it with 'pip install opencv-python'. "
                "Falling back to centered framing.",
                file=sys.stderr,
            )
            _FACE_WARNED = True
        return None

    image = cv2.imread(str(source))
    if image is None:
        return None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda f: int(f[2]) * int(f[3]))
    ih, iw = gray.shape[:2]
    return ((x + w / 2) / iw, (y + h / 2) / ih)


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
