# Synced Video Editor

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

Create beat-synchronized video edits from local images/videos and a local audio file.

This project unifies two pipelines:

1. **AI asset classifier** (`synced_edit.classifier`) — looks at every image and a
   few representative frames of every video and tags them (`flower`, `calm`,
   `motion`, `wide_shot`, …) using an OpenAI vision model, writing an
   `assets.json` tag map.
2. **Beat-synced renderer** (`synced_edit.cli`) — analyzes the audio, builds a
   timeline cut to the beats, optionally orders the assets by those tags
   (`--selection smart`), and renders the final video with FFmpeg.

The two are wired together: when you point the renderer at a project folder that
has no `assets.json`, it runs the classifier first and then continues straight
into the render — so a folder of raw media becomes a finished, mood-aware edit in
one command.

This project does not download copyrighted media from YouTube or YouTube Music. Export or provide audio and source media that you have the right to use, then run the pipeline locally.

## Requirements

- Python 3.11+
- FFmpeg available on `PATH` (used both for rendering and for extracting video
  frames during classification)
- `pip install -r requirements.txt`
  - `librosa` / `numpy` / `soundfile` → stronger beat detection (optional)
  - `certifi` → reliable HTTPS to the OpenAI API on macOS Python builds
- `OPENAI_API_KEY` exported in your environment — **only** required when the
  classifier actually runs (smart selection on a folder without `assets.json`)

## Unified Flow: classify → sync → render

When you pass `--project-folder`, the tag-map handling is automatic and
**incremental**:

- **`assets.json` is missing** → the AI classifier runs over the whole folder
  using `OPENAI_API_KEY`, writes `assets.json`, and the render continues with the
  freshly generated tags.
- **`assets.json` exists** → only assets that are **not already in it** are
  classified and merged in; everything already tagged is reused with no API calls.
  Drop new photos/clips into `images/`/`videos/` and re-run — just the new files
  are sent to the model, so you never re-classify (or re-pay for) the whole folder.
  When every asset is already present, no API key is needed at all.

Tags are written after every newly-classified asset, so an interrupted run keeps
its progress and a re-run resumes where it stopped. Hand-edited entries are always
preserved. To force a full re-classification from scratch, run the standalone
classifier with `--force` (see below).

```bash
export OPENAI_API_KEY="sk-..."

# First run on a raw folder: classifies, writes assets.json, then renders.
python3 -m synced_edit.cli \
  --project-folder /path/to/project \
  --audio-start 0:18 --audio-end 0:40 \
  --selection smart --mood sad

# Second run: assets.json already exists, so it renders immediately (no API cost).
python3 -m synced_edit.cli \
  --project-folder /path/to/project \
  --selection smart --mood sad
```

Control the classifier step from the renderer:

- `--no-auto-classify` — never invoke the classifier; if `assets.json` is absent,
  selection falls back to plain ordering.
- `--classify-model gpt-4.1-mini` — choose the OpenAI vision model.
- `--classify-max-tags 5` — cap tags per asset.

If `OPENAI_API_KEY` is missing (or the folder has no media), auto-classification
is skipped with a warning and the render still proceeds with plain ordering — it
never hard-fails the edit.

### Running the classifier on its own

The classifier is also a standalone entry point if you want to review or hand-edit
the tags before rendering. It is incremental too — only assets missing from
`assets.json` are tagged:

```bash
export OPENAI_API_KEY="sk-..."
python3 -m synced_edit.classifier /path/to/project \
  --output /path/to/project/assets.json \
  --max-tags 5

# Re-tag absolutely everything from scratch, ignoring the existing file:
python3 -m synced_edit.classifier /path/to/project --force
```

## Quick Start

Put media in:

```text
assets/audio/song.mp3
assets/images/
assets/videos/
```

Run:

```bash
python3 -m synced_edit.cli \
  --audio assets/audio/song.mp3 \
  --assets assets/images assets/videos \
  --beats-per-cut 4
```

Outputs:

```text
outputs/audio_analysis.json
outputs/timeline.json
outputs/final.mp4
outputs/reporte_YYYY-MM-DD_synced_edit.md
```

## External Project Folder

For a folder that contains `song.mp3`, `images/`, and `videos/`, use:

```bash
python3 -m synced_edit.cli \
  --project-folder /path/to/project \
  --audio-start 0:18 \
  --audio-end 0:40
```

Outputs are written to `/path/to/project/output/`.

For smarter asset ordering, add `--selection smart` and optionally `--mood`:

```bash
python3 -m synced_edit.cli \
  --project-folder /path/to/project \
  --audio-start 0:18 \
  --audio-end 0:40 \
  --selection smart \
  --mood sad
```

`assets.json` maps relative file paths to tags. It is generated automatically by
the classifier on the first smart run (see [Unified Flow](#unified-flow-classify--sync--render)),
but you can also write or hand-edit it yourself:

```json
{
  "images/IMG_0996.JPG": ["flower", "calm", "warm", "closeup"],
  "videos/IMG_1325.MOV": ["motion", "wide_shot", "calm"]
}
```

When assets need custom names or paths, point every output back to that same media folder:

```bash
python3 -m synced_edit.cli \
  --audio /path/to/project/song.mp3 \
  --assets /path/to/project/images /path/to/project/videos \
  --output /path/to/project/final.mp4 \
  --timeline /path/to/project/timeline.json \
  --analysis /path/to/project/audio_analysis.json \
  --report /path/to/project/reporte_YYYY-MM-DD_project.md
```

## Manual BPM

If automatic beat detection is not good enough:

```bash
python3 -m synced_edit.cli \
  --audio assets/audio/song.mp3 \
  --assets assets/images assets/videos \
  --manual-bpm 128 \
  --beats-per-cut 4
```

## Use Only Part of a Song

Render only a range from the audio, using seconds from the original file:

```bash
python3 -m synced_edit.cli \
  --audio assets/audio/song.mp3 \
  --assets assets/images assets/videos \
  --audio-start 35 \
  --audio-end 65 \
  --beats-per-cut 4
```

You can also use `MM:SS` or `HH:MM:SS`:

```bash
python3 -m synced_edit.cli \
  --audio assets/audio/song.mp3 \
  --assets assets/images assets/videos \
  --audio-start 2:30 \
  --audio-end 3:15 \
  --beats-per-cut 4
```

The generated timeline starts at `0` for the selected segment, while `audio_analysis.json` and `timeline.json` keep the original source path and selected range.

## Fast Timeline Test

Generate only JSON and report:

```bash
python3 -m synced_edit.cli \
  --audio assets/audio/song.mp3 \
  --assets assets/images \
  --skip-render
```

## Flags Reference (`synced_edit.cli`)

Every option of the renderer CLI, what it does, and its default.

### Inputs & outputs

| Flag | Default | What it does |
| --- | --- | --- |
| `--project-folder PATH` | – | Folder containing `song.mp3`, `images/`, and `videos/`. Sets sensible defaults for audio, assets, and output (everything lands in `PATH/output/`) and enables the incremental auto-classifier. |
| `--audio PATH` | – | Local audio file. Required unless `--project-folder` is given. |
| `--assets PATH [PATH ...]` | `assets/images assets/videos` | Image/video files or folders to draw clips from. |
| `--output PATH` | `outputs/final.mp4` | Final rendered video. |
| `--timeline PATH` | `outputs/timeline.json` | Where the beat-cut timeline JSON is written. |
| `--analysis PATH` | `outputs/audio_analysis.json` | Where the audio analysis JSON is written. |
| `--report PATH` | `outputs/reporte_<date>_synced_edit.md` | Markdown summary report. |

### Video size & quality

| Flag | Default | What it does |
| --- | --- | --- |
| `--width N` | `1080` | Output width in pixels. |
| `--height N` | `1920` | Output height in pixels (default is Full HD vertical). |
| `--fps N` | `30` | Output frame rate. |
| `--crf N` | `18` | Final video quality (libx264 CRF). **Lower = higher quality / larger file.** 18 = high, 23 = old default, 15 ≈ near-lossless. Per-clip intermediates are always encoded near-losslessly so only this final pass sets the delivered quality. |
| `--preset NAME` | `medium` | libx264 speed/quality preset for the final encode (`veryfast`, `medium`, `slow`, …). Slower = better compression at the same CRF. |
| `--focus {dynamic,center,face}` | `dynamic` | Image framing. `dynamic` = the original zoom + pan effects. `center` = centered zoom only, with no panning toward the edges (keeps a centered subject in frame). `face` = center the crop on the largest detected face, falling back to `center` when no face is found. **`face` needs OpenCV** — `pip install opencv-python-headless` (or `opencv-python`); without it, `face` behaves like `center`. Applies to images; video clips are always fitted. |

### Cutting & timing

| Flag | Default | What it does |
| --- | --- | --- |
| `--beats-per-cut N` | `4` | How many beats each clip spans. Lower = faster cuts (e.g. `2` for quicker changes). |
| `--max-clip-duration SECONDS` | – | Hard cap on any single clip. Longer stretches (e.g. quiet, beat-less intros that would otherwise freeze an image) are auto-split into animated cuts ≤ this length. |
| `--min-clip-duration SECONDS` | – | Floor on any single clip. Clips shorter than this (e.g. very short video sources or rapid beats) are merged with their neighbour so nothing flashes by. Must be ≤ `--max-clip-duration`. |
| `--manual-bpm N` | – | Override automatic beat detection with a fixed BPM. |
| `--audio-start TIME` | `0` | Start of the audio segment to use. Seconds, `MM:SS`, or `HH:MM:SS`. |
| `--audio-end TIME` | – | End of the audio segment to use (same formats). |
| `--max-items N` | – | Cap the number of clips/cuts in the timeline. |

### Audio mixing

| Flag | Default | What it does |
| --- | --- | --- |
| `--mix-video-audio` | off | Keep each video clip's **own** audio and play it over a quiet background song. Without this flag, only the song plays. Images contribute silence. |
| `--video-audio-volume N` | `1.0` | Volume of the video clips' own audio when `--mix-video-audio` is set. |
| `--background-audio-volume N` | `0.15` | Volume of the background song when it sits under video audio. (Ignored without `--mix-video-audio`; the song then plays at full volume.) |

### Asset selection & mood

| Flag | Default | What it does |
| --- | --- | --- |
| `--selection {order,smart}` | `order` | `order` = file order; `smart` = order assets by tags/mood using `assets.json`. |
| `--mood NAME` | – | Creative mood hint for smart selection (e.g. `sad`, `calm`, `happy`, `bittersweet`). Overrides auto-detected emotion. |
| `--no-auto-emotion` | off | Do not auto-detect emotion from the audio. With this set, smart selection needs `--mood`. |

### Auto-classification (tagging)

| Flag | Default | What it does |
| --- | --- | --- |
| `--no-auto-classify` | off | Never run the AI classifier, even if `assets.json` is missing or incomplete. Selection falls back to plain ordering. |
| `--classify-model NAME` | `gpt-4.1-mini` | OpenAI vision model used by the auto-classifier. |
| `--classify-max-tags N` | `5` | Maximum tags per asset when auto-classifying. |

### Render control

| Flag | Default | What it does |
| --- | --- | --- |
| `--skip-render` | off | Build only the analysis, timeline JSON, and report — no video file. |

### Standalone classifier (`synced_edit.classifier`)

| Flag | Default | What it does |
| --- | --- | --- |
| `root_dir` | – | Folder containing `images/` and/or `videos/`. |
| `--output PATH` | `<root_dir>/assets.json` | Output metadata JSON. |
| `--model NAME` | `gpt-4.1-mini` | OpenAI vision model. |
| `--max-tags N` | `5` | Maximum tags per asset. |
| `--allowed-tags LIST` | built-in set | Comma-separated tags the model should prefer. |
| `--sleep SECONDS` | `0.2` | Pause between API calls. |
| `--video-frames N` | `4` | Representative frames sampled per video. |
| `--force` | off | Re-classify every asset from scratch, ignoring an existing `assets.json` (default is incremental: only tag what is missing). |
| `--ffmpeg PATH` | `ffmpeg` | Path to the ffmpeg binary. |
