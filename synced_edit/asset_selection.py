from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .timeline import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS


MOOD_TAGS = {
    "sad": {"calm", "cool", "flower", "plant", "closeup", "nature"},
    "calm": {"calm", "warm", "nature", "wide_shot", "plant"},
    "happy": {"bright", "warm", "motion", "wide_shot"},
    "dramatic": {"motion", "high_energy", "cool", "wide_shot"},
}


@dataclass
class AssetProfile:
    path: Path
    tags: set[str]
    source_type: str
    score: float


def load_asset_tags(project_folder: Path | None) -> dict[str, set[str]]:
    if project_folder is None:
        return {}
    metadata_path = project_folder.expanduser().resolve() / "assets.json"
    if not metadata_path.exists():
        return {}

    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    tags: dict[str, set[str]] = {}
    for key, value in data.items():
        if isinstance(value, list):
            tags[key] = {str(tag).strip().lower() for tag in value if str(tag).strip()}
    return tags


def select_assets(
    assets: list[Path],
    mood: str | None = None,
    selection: str = "order",
    metadata: dict[str, set[str]] | None = None,
    project_folder: Path | None = None,
) -> list[Path]:
    if selection == "order":
        return assets
    if selection != "smart":
        raise ValueError(f"Unknown selection mode: {selection}")

    metadata = metadata or {}
    mood_tags = MOOD_TAGS.get((mood or "").lower(), set())
    profiles = [
        _profile_asset(asset, metadata=metadata, project_folder=project_folder, mood=mood, mood_tags=mood_tags)
        for asset in assets
    ]
    profiles.sort(key=lambda item: (-item.score, item.source_type, item.path.name.lower()))
    return _interleave_types(profiles)


def _profile_asset(
    asset: Path,
    metadata: dict[str, set[str]],
    project_folder: Path | None,
    mood: str | None,
    mood_tags: set[str],
) -> AssetProfile:
    tags = _tags_for_asset(asset, metadata, project_folder)
    source_type = "image" if asset.suffix.lower() in IMAGE_EXTENSIONS else "video"
    score = 0.0
    score += len(tags & mood_tags) * 4
    if "flower" in tags:
        score += 2.5
    if "plant" in tags or "nature" in tags:
        score += 1.5
    if "calm" in tags:
        score += 1.0
    if "bright" in tags:
        score += 0.6
    if "motion" in tags and source_type == "video":
        score += 1.2
    if "high_energy" in tags and mood == "sad":
        score -= 2.0
    if source_type == "video":
        score += 0.4
    return AssetProfile(path=asset, tags=tags, source_type=source_type, score=score)


def _tags_for_asset(asset: Path, metadata: dict[str, set[str]], project_folder: Path | None) -> set[str]:
    candidates = [asset.name, str(asset)]
    if project_folder:
        try:
            candidates.append(asset.relative_to(project_folder.expanduser().resolve()).as_posix())
        except ValueError:
            pass

    for candidate in candidates:
        if candidate in metadata:
            return metadata[candidate]
    return set()


def _interleave_types(profiles: list[AssetProfile]) -> list[Path]:
    videos = [profile for profile in profiles if profile.source_type == "video"]
    images = [profile for profile in profiles if profile.source_type == "image"]
    ordered: list[AssetProfile] = []

    while images or videos:
        if images:
            ordered.extend(images[:2])
            images = images[2:]
        if videos:
            ordered.append(videos.pop(0))

    return [profile.path for profile in ordered]
