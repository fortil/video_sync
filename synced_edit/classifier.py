#!/usr/bin/env python3
"""
Classify media under images/ and videos/ folders and write assets metadata as JSON.

This module powers the automatic tagging step of the synced-edit pipeline. It can
be used two ways:

1. Programmatically, by calling :func:`classify_folder` (this is what
   ``synced_edit.cli`` does when a project folder has no ``assets.json``).
2. Standalone from the command line::

       export OPENAI_API_KEY="..."
       python3 -m synced_edit.classifier \
           /path/to/project \
           --output /path/to/project/assets.json

The output JSON maps each asset's path (relative to the project root, e.g.
``images/IMG_0996.JPG``) to a list of tags, which is exactly the shape that
``synced_edit.asset_selection.load_asset_tags`` consumes.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import certifi
except ImportError:
    certifi = None


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm"}
DEFAULT_TAGS = [
    "flower",
    "plant",
    "nature",
    "closeup",
    "wide_shot",
    "warm",
    "cool",
    "dark",
    "bright",
    "calm",
    "sad",
    "lonely",
    "decay",
    "delicate",
    "high_energy",
    "motion",
    "dramatic",
    "euphoric",
    "gentle",
    "intense",
    "melancholic",
    "mysterious",
    "nostalgic",
    "urban",
]


def default_ssl_context() -> ssl.SSLContext:
    if certifi:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify media in images/ and videos/ and write assets.json."
    )
    parser.add_argument(
        "root_dir",
        help="Root folder containing images/ and/or videos/ subfolders.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output metadata JSON path. Defaults to <root_dir>/assets.json.",
    )
    parser.add_argument(
        "--model",
        default="gpt-4.1-mini",
        help="OpenAI vision-capable model to use.",
    )
    parser.add_argument(
        "--max-tags",
        type=int,
        default=5,
        help="Maximum number of tags per media file.",
    )
    parser.add_argument(
        "--allowed-tags",
        default=",".join(DEFAULT_TAGS),
        help="Comma-separated tags the model should prefer.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.2,
        help="Seconds to sleep between API calls.",
    )
    parser.add_argument(
        "--video-frames",
        type=int,
        default=4,
        help="Number of representative frames to extract from each video.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-classify every asset from scratch, ignoring any existing assets.json "
        "(default is incremental: only tag assets not already present).",
    )
    parser.add_argument(
        "--ffmpeg",
        default="ffmpeg",
        help="Path to ffmpeg. Defaults to resolving 'ffmpeg' from PATH.",
    )
    return parser.parse_args()


def list_assets(root_dir: Path) -> list[Path]:
    assets: list[Path] = []
    search_targets = [
        (root_dir / "images", IMAGE_EXTENSIONS),
        (root_dir / "videos", VIDEO_EXTENSIONS),
    ]

    for folder, extensions in search_targets:
        if not folder.is_dir():
            continue
        assets.extend(
            path
            for path in folder.rglob("*")
            if path.is_file() and path.suffix.lower() in extensions
        )

    return sorted(assets, key=lambda path: path.relative_to(root_dir).as_posix().lower())


def image_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def extract_video_frames(
    video_path: Path,
    *,
    frame_count: int,
    ffmpeg_binary: str,
    temp_dir: Path,
) -> list[Path]:
    if frame_count < 1:
        raise ValueError("--video-frames must be at least 1")

    ffmpeg_path = shutil.which(ffmpeg_binary) if not Path(ffmpeg_binary).exists() else ffmpeg_binary
    if not ffmpeg_path:
        raise RuntimeError("ffmpeg is required to process videos, but it was not found.")

    frame_prefix = hashlib.sha1(str(video_path).encode("utf-8")).hexdigest()[:12]
    output_pattern = temp_dir / f"{frame_prefix}_frame_%02d.jpg"
    command = [
        str(ffmpeg_path),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vf",
        f"select='eq(n,0)+gt(scene,0.12)',scale='min(1024,iw)':-2",
        "-vsync",
        "vfr",
        "-frames:v",
        str(frame_count),
        str(output_pattern),
    ]
    subprocess.run(command, check=True)

    frames = sorted(temp_dir.glob(f"{frame_prefix}_frame_*.jpg"))
    if frames:
        return frames

    fallback_pattern = temp_dir / f"{frame_prefix}_fallback_%02d.jpg"
    fallback_command = [
        str(ffmpeg_path),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vf",
        f"fps={frame_count}/10,scale='min(1024,iw)':-2",
        "-frames:v",
        str(frame_count),
        str(fallback_pattern),
    ]
    subprocess.run(fallback_command, check=True)
    return sorted(temp_dir.glob(f"{frame_prefix}_fallback_*.jpg"))


def extract_json_object(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def normalize_tags(value: object, allowed_tags: set[str], max_tags: int) -> list[str]:
    if not isinstance(value, list):
        return []

    tags: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        tag = item.strip().lower().replace(" ", "_")
        if not tag:
            continue
        if allowed_tags and tag not in allowed_tags:
            continue
        if tag not in tags:
            tags.append(tag)
        if len(tags) >= max_tags:
            break
    return tags


def classify_media(
    media_path: Path,
    *,
    image_paths: list[Path],
    api_key: str,
    model: str,
    allowed_tags: list[str],
    max_tags: int,
) -> list[str]:
    prompt = (
        "Return only valid JSON in this exact shape: "
        '{"tags":["tag_one","tag_two"]}. '
        f"Choose up to {max_tags} tags for this media file. "
        "If multiple frames are provided, infer tags for the full video, not each frame. "
        "Prefer only these tags: "
        f"{', '.join(allowed_tags)}. "
        "Use concise lowercase snake_case tags."
    )

    content = [{"type": "input_text", "text": prompt}]
    for image_path in image_paths:
        content.append({"type": "input_image", "image_url": image_data_url(image_path)})

    payload = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": content,
            }
        ],
        "text": {"format": {"type": "json_object"}},
    }

    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=90, context=default_ssl_context()) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API error for {media_path.name}: {exc.code} {body}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise RuntimeError(
                "Could not verify the HTTPS certificate for api.openai.com. "
                "Run '/Applications/Python 3.12/Install Certificates.command' "
                "or install certifi with 'python3 -m pip install certifi'."
            ) from exc
        raise

    text_parts: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                text_parts.append(content.get("text", ""))

    parsed = extract_json_object("".join(text_parts))
    return normalize_tags(parsed.get("tags"), set(allowed_tags), max_tags)


def load_existing_metadata(output_path: Path) -> dict[str, list[str]]:
    """Load an existing assets.json, tolerating a missing or corrupt file.

    Returns an empty mapping when the file does not exist or cannot be parsed,
    so a damaged metadata file never blocks (re)classification.
    """
    if not output_path.exists():
        return {}
    try:
        data = json.loads(output_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Keep only well-formed "path -> list[str]" entries.
    clean: dict[str, list[str]] = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, list):
            clean[key] = [t for t in value if isinstance(t, str)]
    return clean


def classify_folder(
    root_dir: Path | str,
    output: Path | str | None = None,
    *,
    model: str = "gpt-4.1-mini",
    max_tags: int = 5,
    allowed_tags: list[str] | None = None,
    sleep: float = 0.2,
    video_frames: int = 4,
    ffmpeg_binary: str = "ffmpeg",
    api_key: str | None = None,
    incremental: bool = True,
) -> Path:
    """Classify assets under ``root_dir`` and write an ``assets.json`` file.

    By default this is **incremental**: an existing ``assets.json`` is loaded and
    only assets that are not already keys in it are sent to the model; their tags
    are merged in and the file is rewritten. This avoids re-classifying (and
    re-paying for) media that was already tagged on a previous run. Pass
    ``incremental=False`` to ignore any existing file and re-tag everything.

    Results are written after every newly-classified asset, so a crash or Ctrl-C
    mid-run keeps prior progress and a re-run resumes where it stopped.

    Returns the path to the metadata file. Raises ``RuntimeError`` if the folder is
    invalid, no supported media is found, or — only when there is new work to do —
    no API key is available.
    """
    root_dir = Path(root_dir).expanduser().resolve()
    output_path = (
        Path(output).expanduser().resolve() if output else root_dir / "assets.json"
    )
    tags = allowed_tags if allowed_tags is not None else list(DEFAULT_TAGS)
    tags = [tag.strip().lower().replace(" ", "_") for tag in tags if tag.strip()]

    if not root_dir.is_dir():
        raise RuntimeError(f"Root folder does not exist: {root_dir}")

    media_files = list_assets(root_dir)
    if not media_files:
        raise RuntimeError(
            f"No supported assets found under {root_dir}/images or {root_dir}/videos."
        )

    metadata = load_existing_metadata(output_path) if incremental else {}

    pending = [
        media_path
        for media_path in media_files
        if media_path.relative_to(root_dir).as_posix() not in metadata
    ]

    if not pending:
        print(
            f"All {len(media_files)} assets already classified in {output_path}; "
            "nothing new to tag.",
            file=sys.stderr,
        )
        return output_path

    # Only require an API key once we know there is actually new work to do, so a
    # fully-classified folder can be reused with no key and no network calls.
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing OPENAI_API_KEY. Export it before running the classifier."
        )

    already = len(metadata)
    if already:
        print(
            f"Found {already} existing tags; classifying {len(pending)} new asset(s).",
            file=sys.stderr,
        )

    with tempfile.TemporaryDirectory(prefix="media_frames_") as temp_name:
        temp_dir = Path(temp_name)
        for index, media_path in enumerate(pending, start=1):
            asset_key = media_path.relative_to(root_dir).as_posix()
            print(f"[{index}/{len(pending)}] Classifying {asset_key}...", file=sys.stderr)
            image_paths = [media_path]
            if is_video(media_path):
                image_paths = extract_video_frames(
                    media_path,
                    frame_count=video_frames,
                    ffmpeg_binary=ffmpeg_binary,
                    temp_dir=temp_dir,
                )
                if not image_paths:
                    print(f"Skipping {asset_key}: no video frames extracted.", file=sys.stderr)
                    metadata[asset_key] = []
                    _write_metadata(output_path, metadata)
                    continue

            metadata[asset_key] = classify_media(
                media_path,
                image_paths=image_paths,
                api_key=api_key,
                model=model,
                allowed_tags=tags,
                max_tags=max_tags,
            )
            # Persist after each asset so progress survives a crash / interruption.
            _write_metadata(output_path, metadata)
            time.sleep(sleep)

    print(
        f"Wrote {len(metadata)} entries to {output_path} ({len(pending)} new).",
    )
    return output_path


def _write_metadata(output_path: Path, metadata: dict[str, list[str]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(dict(sorted(metadata.items())), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    allowed_tags = [
        tag.strip().lower().replace(" ", "_")
        for tag in args.allowed_tags.split(",")
        if tag.strip()
    ]
    try:
        classify_folder(
            args.root_dir,
            output=args.output,
            model=args.model,
            max_tags=args.max_tags,
            allowed_tags=allowed_tags,
            sleep=args.sleep,
            video_frames=args.video_frames,
            ffmpeg_binary=args.ffmpeg,
            incremental=not args.force,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
