from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import tempfile
import wave
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class AudioAnalysis:
    audio_path: str
    duration: float
    bpm: float
    beats: list[float]
    method: str
    source_audio_path: str | None = None
    source_audio_start: float | None = None
    source_audio_end: float | None = None

    def to_json(self) -> dict:
        return asdict(self)


def analyze_audio(audio_path: Path, manual_bpm: float | None = None) -> AudioAnalysis:
    audio_path = audio_path.expanduser().resolve()
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    if manual_bpm:
        duration = probe_duration(audio_path)
        return AudioAnalysis(
            audio_path=str(audio_path),
            duration=duration,
            bpm=manual_bpm,
            beats=_regular_beats(duration, manual_bpm),
            method="manual-bpm",
        )

    try:
        return _analyze_with_librosa(audio_path)
    except Exception:
        return _analyze_with_energy(audio_path)


def write_analysis(path: Path, analysis: AudioAnalysis) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(analysis.to_json(), indent=2) + "\n", encoding="utf-8")


def trim_audio(source: Path, output: Path, start: float = 0.0, end: float | None = None) -> Path:
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    if start < 0:
        raise ValueError("audio start must be >= 0")
    if end is not None and end <= start:
        raise ValueError("audio end must be greater than audio start")

    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.4f}", "-i", str(source)]
    if end is not None:
        cmd.extend(["-t", f"{end - start:.4f}"])
    cmd.extend(["-vn", "-acodec", "pcm_s16le", str(output)])
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return output


def probe_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    output = subprocess.check_output(cmd, text=True).strip()
    return float(output)


def _analyze_with_librosa(audio_path: Path) -> AudioAnalysis:
    import librosa  # type: ignore

    y, sr = librosa.load(str(audio_path), mono=True)
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    beats = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    duration = float(librosa.get_duration(y=y, sr=sr))
    bpm = float(tempo[0] if hasattr(tempo, "__len__") else tempo)
    if len(beats) < 4:
        beats = _regular_beats(duration, bpm if bpm > 0 else 120)
    return AudioAnalysis(
        audio_path=str(audio_path),
        duration=duration,
        bpm=round(bpm, 2),
        beats=[round(float(t), 4) for t in beats if 0 <= float(t) <= duration],
        method="librosa",
    )


def _analyze_with_energy(audio_path: Path) -> AudioAnalysis:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required when librosa is not installed")

    with tempfile.TemporaryDirectory() as tmp:
        wav_path = Path(tmp) / "audio.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(audio_path),
                "-ac",
                "1",
                "-ar",
                "22050",
                "-f",
                "wav",
                str(wav_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        samples, sample_rate = _read_wav_mono(wav_path)

    duration = len(samples) / sample_rate if sample_rate else probe_duration(audio_path)
    envelope = _rms_envelope(samples, sample_rate, frame_seconds=0.046)
    peaks = _pick_energy_peaks(envelope, hop_seconds=0.046, duration=duration)
    bpm = _estimate_bpm(peaks)

    if not peaks or bpm <= 0:
        bpm = 120.0
        peaks = _regular_beats(duration, bpm)

    return AudioAnalysis(
        audio_path=str(audio_path),
        duration=round(duration, 4),
        bpm=round(bpm, 2),
        beats=[round(t, 4) for t in peaks if 0 <= t <= duration],
        method="ffmpeg-energy",
    )


def _read_wav_mono(path: Path) -> tuple[list[float], int]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    if sample_width != 2:
        raise ValueError("Expected 16-bit PCM WAV from ffmpeg")

    count = len(frames) // 2
    values = struct.unpack(f"<{count}h", frames)
    if channels == 1:
        return [v / 32768.0 for v in values], sample_rate

    mono = []
    for i in range(0, len(values), channels):
        mono.append(sum(values[i : i + channels]) / channels / 32768.0)
    return mono, sample_rate


def _rms_envelope(samples: list[float], sample_rate: int, frame_seconds: float) -> list[float]:
    frame = max(1, int(sample_rate * frame_seconds))
    envelope = []
    for index in range(0, len(samples), frame):
        chunk = samples[index : index + frame]
        if not chunk:
            continue
        envelope.append(math.sqrt(sum(s * s for s in chunk) / len(chunk)))
    return envelope


def _pick_energy_peaks(envelope: list[float], hop_seconds: float, duration: float) -> list[float]:
    if len(envelope) < 8:
        return []

    mean = sum(envelope) / len(envelope)
    variance = sum((x - mean) ** 2 for x in envelope) / len(envelope)
    threshold = mean + math.sqrt(variance) * 0.55
    min_gap = 0.24
    peaks: list[float] = []
    last_time = -min_gap

    for i in range(1, len(envelope) - 1):
        is_peak = envelope[i] > threshold and envelope[i] >= envelope[i - 1] and envelope[i] >= envelope[i + 1]
        time = i * hop_seconds
        if is_peak and time - last_time >= min_gap and time <= duration:
            peaks.append(time)
            last_time = time

    return peaks


def _estimate_bpm(peaks: list[float]) -> float:
    if len(peaks) < 3:
        return 0.0

    intervals = [b - a for a, b in zip(peaks, peaks[1:]) if 0.25 <= b - a <= 2.0]
    if not intervals:
        return 0.0

    intervals.sort()
    median = intervals[len(intervals) // 2]
    bpm = 60.0 / median
    while bpm < 80:
        bpm *= 2
    while bpm > 180:
        bpm /= 2
    return bpm


def _regular_beats(duration: float, bpm: float) -> list[float]:
    step = 60.0 / bpm
    beats = []
    current = 0.0
    while current < duration:
        beats.append(round(current, 4))
        current += step
    return beats
