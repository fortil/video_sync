import re
from pathlib import Path

from synced_edit.renderer import _build_xfade_filtergraph, _uses_incoming_xfade
from synced_edit.timeline import TimelineItem


def _item(index: int, duration: float, transition_hint: str = "cut") -> TimelineItem:
    return TimelineItem(
        index=index,
        source=f"/tmp/asset_{index}.jpg",
        source_type="image",
        start=0.0,
        end=duration,
        duration=duration,
        effect="zoom_in",
        transition_hint=transition_hint,
    )


def test_xfade_filtergraph_preserves_schedule_duration(monkeypatch) -> None:
    import synced_edit.renderer as renderer

    monkeypatch.setattr(renderer, "_XFADE_AVAILABLE", True)

    # Item 3 carries an "xfade" hint too, but as the last item it must never
    # actually transition (avoids cutting off trailing audio) — matching the
    # existing _uses_incoming_xfade rule.
    items = [
        _item(0, 3.0, "cut"),
        _item(1, 2.0, "xfade"),
        _item(2, 2.5, "xfade"),
        _item(3, 1.8, "xfade"),
    ]
    clip_paths = [Path(f"/tmp/clip_{i:04d}.mp4") for i in range(len(items))]

    result = _build_xfade_filtergraph(clip_paths, items, fps=30)
    assert result is not None
    filter_complex, _out_label = result

    offsets = [float(v) for v in re.findall(r"offset=([\d.]+)", filter_complex)]
    # cumulative: 3.0 -> xfade at (3.0-0.25) -> 5.0 -> xfade at (5.0-0.25) -> 7.5
    assert offsets == [2.75, 4.75]

    # The last transition (item 3) falls back to plain concat, not xfade.
    assert "concat=n=2:v=1:a=0" in filter_complex
    assert filter_complex.count("xfade=transition=fade") == 2


def test_uses_incoming_xfade_boundary_conditions() -> None:
    total = 4

    # First item never xfades — it has no predecessor to blend with.
    assert _uses_incoming_xfade(_item(0, 3.0, "xfade"), 0, total) is False

    # Last item never xfades — avoids cutting off trailing audio.
    assert _uses_incoming_xfade(_item(3, 3.0, "xfade"), 3, total) is False

    # Too short to blend (below 2x the xfade duration).
    assert _uses_incoming_xfade(_item(1, 0.4, "xfade"), 1, total) is False

    # A plain "cut" hint never xfades, regardless of position/duration.
    assert _uses_incoming_xfade(_item(1, 3.0, "cut"), 1, total) is False

    # Eligible middle item.
    assert _uses_incoming_xfade(_item(1, 3.0, "xfade"), 1, total) is True
