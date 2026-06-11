from pathlib import Path

from synced_edit.asset_selection import select_assets
from synced_edit.audio_analysis import AudioAnalysis
from synced_edit.timecode import parse_timecode
from synced_edit.timeline import build_timeline


def test_build_timeline_from_regular_beats() -> None:
    analysis = AudioAnalysis(
        audio_path="/tmp/song.wav",
        duration=8.0,
        bpm=120.0,
        beats=[i * 0.5 for i in range(16)],
        method="test",
    )

    timeline = build_timeline(
        analysis,
        [Path("/tmp/a.jpg"), Path("/tmp/b.mp4")],
        width=720,
        height=1280,
        fps=24,
        beats_per_cut=4,
    )

    assert timeline.width == 720
    assert timeline.height == 1280
    assert timeline.fps == 24
    assert timeline.items
    assert timeline.items[0].source_type == "image"
    assert timeline.items[1].source_type == "video"


def test_parse_timecode_seconds_and_clock_formats() -> None:
    assert parse_timecode("150") == 150.0
    assert parse_timecode("2:30") == 150.0
    assert parse_timecode("01:02:03.5") == 3723.5


def test_smart_selection_prioritizes_mood_tags() -> None:
    assets = [Path("/project/images/a.jpg"), Path("/project/images/b.jpg")]
    selected = select_assets(
        assets,
        mood="sad",
        selection="smart",
        metadata={
            "images/a.jpg": {"bright"},
            "images/b.jpg": {"flower", "calm"},
        },
        project_folder=Path("/project"),
    )

    assert selected[0].name == "b.jpg"
