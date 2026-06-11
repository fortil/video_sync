from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from .asset_selection import load_asset_tags, select_assets
from .classifier import DEFAULT_TAGS, classify_folder
from .audio_analysis import analyze_audio, trim_audio, write_analysis
from .renderer import render_timeline
from .report import write_report
from .timecode import parse_timecode
from .timeline import build_timeline, collect_assets, write_timeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Create beat-synchronized video edits from local media.")
    parser.add_argument(
        "--project-folder",
        type=Path,
        default=None,
        help="Folder containing song.mp3, images/, and videos/. Outputs go to output/.",
    )
    parser.add_argument("--audio", required=False, type=Path, help="Local audio file.")
    parser.add_argument(
        "--assets",
        nargs="+",
        type=Path,
        default=[Path("assets/images"), Path("assets/videos")],
        help="Image/video files or folders.",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--timeline", type=Path, default=None)
    parser.add_argument("--analysis", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--beats-per-cut", type=int, default=4)
    parser.add_argument("--selection", choices=["order", "smart"], default="order")
    parser.add_argument("--mood", default=None, help="Creative mood hint for smart selection, e.g. sad, calm, happy.")
    parser.add_argument("--manual-bpm", type=float, default=None)
    parser.add_argument("--audio-start", default="0", help="Start time in seconds, MM:SS, or HH:MM:SS.")
    parser.add_argument("--audio-end", default=None, help="End time in seconds, MM:SS, or HH:MM:SS.")
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--skip-render", action="store_true")
    parser.add_argument(
        "--no-auto-classify",
        action="store_true",
        help="Do not run the AI classifier even if --project-folder has no assets.json.",
    )
    parser.add_argument(
        "--classify-model",
        default="gpt-4.1-mini",
        help="OpenAI vision model used by the auto-classifier.",
    )
    parser.add_argument(
        "--classify-max-tags",
        type=int,
        default=5,
        help="Maximum tags per asset when auto-classifying.",
    )
    args = parser.parse_args()
    args = _resolve_project_defaults(args)
    _ensure_assets_metadata(args)
    audio_start = parse_timecode(args.audio_start) or 0.0
    audio_end = parse_timecode(args.audio_end)

    audio_path = args.audio
    if audio_start or audio_end is not None:
        audio_path = Path("work") / "trimmed_audio.wav"
        trim_audio(args.audio, audio_path, start=audio_start, end=audio_end)

    analysis = analyze_audio(audio_path, manual_bpm=args.manual_bpm)
    analysis.source_audio_path = str(args.audio.expanduser().resolve())
    analysis.source_audio_start = audio_start
    analysis.source_audio_end = audio_end
    write_analysis(args.analysis, analysis)

    assets = collect_assets(args.assets)
    asset_metadata = load_asset_tags(args.project_folder)
    assets = select_assets(
        assets,
        mood=args.mood,
        selection=args.selection,
        metadata=asset_metadata,
        project_folder=args.project_folder,
    )
    timeline = build_timeline(
        analysis,
        assets,
        width=args.width,
        height=args.height,
        fps=args.fps,
        beats_per_cut=args.beats_per_cut,
        max_items=args.max_items,
    )
    timeline.audio.update(
        {
            "source_audio_path": analysis.source_audio_path,
            "source_audio_start": analysis.source_audio_start,
            "source_audio_end": analysis.source_audio_end,
        }
    )
    timeline.selection = {
        "mode": args.selection,
        "mood": args.mood,
        "metadata_file": str(args.project_folder.expanduser().resolve() / "assets.json") if args.project_folder else None,
    }
    write_timeline(args.timeline, timeline)

    if not args.skip_render:
        render_timeline(timeline, args.output, work_dir=Path("work"))

    report_path = args.report or Path("outputs") / f"reporte_{date.today().isoformat()}_synced_edit.md"
    write_report(report_path, timeline, args.output)

    print(f"Analysis: {args.analysis}")
    print(f"Timeline: {args.timeline}")
    print(f"Video: {args.output}")
    print(f"Report: {report_path}")


def _ensure_assets_metadata(args: argparse.Namespace) -> None:
    """Auto-generate assets.json via the AI classifier when it is missing.

    Unified flow for project folders:
      * If <project>/assets.json exists -> use it directly (no API calls).
      * If it does not exist -> classify the project folder with OPENAI_API_KEY,
        write assets.json, then continue with the render.

    Only runs when --project-folder is set. Disabled by --no-auto-classify. If the
    classifier cannot run (e.g. no API key or no media), the pipeline continues
    without tags and falls back to plain ordering instead of failing the render.
    """
    if args.project_folder is None or args.no_auto_classify:
        return

    project = args.project_folder.expanduser().resolve()
    metadata_path = project / "assets.json"
    if metadata_path.exists():
        print(f"Using existing assets.json: {metadata_path}", file=sys.stderr)
        return

    print(
        f"No assets.json found in {project}; running the AI classifier...",
        file=sys.stderr,
    )
    try:
        classify_folder(
            project,
            output=metadata_path,
            model=args.classify_model,
            max_tags=args.classify_max_tags,
            allowed_tags=list(DEFAULT_TAGS),
        )
    except RuntimeError as exc:
        print(
            f"Auto-classification skipped: {exc}\n"
            "Continuing without tags (selection falls back to plain ordering).",
            file=sys.stderr,
        )


def _resolve_project_defaults(args: argparse.Namespace) -> argparse.Namespace:
    if args.project_folder is None:
        if args.audio is None:
            raise SystemExit("--audio is required unless --project-folder is provided")
        args.output = args.output or Path("outputs/final.mp4")
        args.timeline = args.timeline or Path("outputs/timeline.json")
        args.analysis = args.analysis or Path("outputs/audio_analysis.json")
        return args

    project = args.project_folder.expanduser().resolve()
    output_dir = project / "output"
    safe_name = project.name.replace(" ", "_")
    range_suffix = _range_suffix(args.audio_start, args.audio_end)

    args.audio = args.audio or project / "song.mp3"
    args.assets = args.assets if args.assets != [Path("assets/images"), Path("assets/videos")] else [
        project / "images",
        project / "videos",
    ]
    args.output = args.output or output_dir / f"{safe_name}{range_suffix}.mp4"
    args.timeline = args.timeline or output_dir / f"{safe_name}{range_suffix}_timeline.json"
    args.analysis = args.analysis or output_dir / f"{safe_name}{range_suffix}_audio_analysis.json"
    args.report = args.report or output_dir / f"reporte_{date.today().isoformat()}_{safe_name}.md"
    return args


def _range_suffix(start: str, end: str | None) -> str:
    if start in {"0", "0.0", "0:00"} and end is None:
        return ""
    safe_start = start.replace(":", "-").replace(".", "_")
    safe_end = "end" if end is None else end.replace(":", "-").replace(".", "_")
    return f"_{safe_start}_{safe_end}"


if __name__ == "__main__":
    main()
