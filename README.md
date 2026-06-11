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

When you pass `--project-folder`, the tag-map handling is automatic:

- **`assets.json` exists** → it is used directly. No API calls are made.
- **`assets.json` is missing** → the AI classifier runs over the folder using
  `OPENAI_API_KEY`, writes `assets.json`, and the render continues with the
  freshly generated tags.

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
the tags before rendering:

```bash
export OPENAI_API_KEY="sk-..."
python3 -m synced_edit.classifier /path/to/project \
  --output /path/to/project/assets.json \
  --max-tags 5
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
