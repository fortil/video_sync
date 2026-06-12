from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import tempfile
import wave
from dataclasses import asdict, dataclass, field
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
    onset_strength: list[float] = field(default_factory=list)
    sections: list[dict] = field(default_factory=list)
    detected_emotion: str = ""
    emotion_confidence: float = 0.0

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


def detect_emotion(analysis: AudioAnalysis) -> tuple[str, float]:
    """Infer the dominant emotion of the song from audio analysis features.

    Uses BPM, onset strength statistics, and section energy distribution.
    Returns (emotion_name, confidence) where confidence is 0–1.
    """
    bpm = analysis.bpm
    onset = analysis.onset_strength
    sections = analysis.sections

    if onset:
        max_onset = max(onset) or 1.0
        onset_norm = [v / max_onset for v in onset]
        rms_mean_norm = sum(onset_norm) / len(onset_norm)
        variance = sum((v - rms_mean_norm) ** 2 for v in onset_norm) / len(onset_norm)
        rms_variance_norm = variance
    else:
        rms_mean_norm = 0.4
        rms_variance_norm = 0.3

    high_count = sum(1 for s in sections if s.get("energy_level") == "high")
    total = len(sections) if sections else 1
    high_ratio = high_count / total

    emotion = "calm"
    confidence = 0.0

    if bpm >= 130 and rms_mean_norm > 0.6:
        emotion = "intense"
        c1 = min(1.0, (bpm - 130) / 30)
        c2 = min(1.0, (rms_mean_norm - 0.6) / 0.4)
        confidence = (c1 + c2) / 2

    elif bpm >= 110 and high_ratio > 0.4:
        emotion = "happy"
        c1 = min(1.0, (bpm - 110) / 30)
        c2 = min(1.0, (high_ratio - 0.4) / 0.6)
        confidence = (c1 + c2) / 2

    elif bpm >= 110:
        emotion = "dramatic"
        confidence = min(1.0, (bpm - 110) / 40)

    elif bpm < 80 and rms_variance_norm < 0.2:
        emotion = "calm"
        c1 = min(1.0, (80 - bpm) / 20)
        c2 = min(1.0, (0.2 - rms_variance_norm) / 0.2) if rms_variance_norm < 0.2 else 0.0
        confidence = (c1 + c2) / 2

    elif bpm < 80 and rms_mean_norm < 0.35:
        emotion = "melancholic"
        c1 = min(1.0, (80 - bpm) / 20)
        c2 = min(1.0, (0.35 - rms_mean_norm) / 0.35)
        confidence = (c1 + c2) / 2

    elif bpm < 95 and rms_variance_norm > 0.5:
        emotion = "sad"
        c1 = min(1.0, (95 - bpm) / 25)
        c2 = min(1.0, (rms_variance_norm - 0.5) / 0.5)
        confidence = (c1 + c2) / 2

    else:
        emotion = "calm"
        confidence = 0.3

    if not onset:
        confidence *= 0.4

    return emotion, round(min(1.0, max(0.0, confidence)), 3)


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
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    duration = float(librosa.get_duration(y=y, sr=sr))
    bpm = float(tempo[0] if hasattr(tempo, "__len__") else tempo)

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)

    if len(beat_times) < 4:
        beats = _regular_beats(duration, bpm if bpm > 0 else 120)
        onset_at_beats: list[float] = []
    else:
        filtered_pairs = [
            (round(float(t), 4), float(onset_env[min(int(f), len(onset_env) - 1)]))
            for t, f in zip(beat_times, beat_frames)
            if 0 <= float(t) <= duration
        ]
        beats = [t for t, _ in filtered_pairs]
        onset_at_beats = [o for _, o in filtered_pairs]

    sections = _compute_sections(beats, onset_at_beats, duration)

    return AudioAnalysis(
        audio_path=str(audio_path),
        duration=duration,
        bpm=round(bpm, 2),
        beats=beats,
        method="librosa",
        onset_strength=onset_at_beats,
        sections=sections,
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
    onset_at_beats = _onset_strength_from_envelope(envelope, peaks, hop_seconds=0.046)
    bpm = _estimate_bpm(peaks)

    if not peaks or bpm <= 0:
        bpm = 120.0
        peaks = _regular_beats(duration, bpm)
        onset_at_beats = []

    sections = _compute_sections(peaks, onset_at_beats, duration)

    return AudioAnalysis(
        audio_path=str(audio_path),
        duration=round(duration, 4),
        bpm=round(bpm, 2),
        beats=[round(t, 4) for t in peaks if 0 <= t <= duration],
        method="ffmpeg-energy",
        onset_strength=onset_at_beats,
        sections=sections,
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


def _onset_strength_from_envelope(
    envelope: list[float], peaks: list[float], hop_seconds: float
) -> list[float]:
    if len(envelope) < 2:
        return []
    onset = []
    for t in peaks:
        idx = min(int(t / hop_seconds), len(envelope) - 1)
        prev_idx = max(0, idx - 1)
        onset.append(max(0.0, envelope[idx] - envelope[prev_idx]))
    return onset


def _compute_sections(
    beats: list[float], onset_strength: list[float], duration: float
) -> list[dict]:
    if len(onset_strength) < 2:
        return [{"start": 0.0, "end": duration, "energy_level": "medium"}]

    n_onset = len(onset_strength)
    window = 8
    windows: list[dict] = []

    for i in range(0, min(len(beats), n_onset), window):
        chunk = onset_strength[i : i + window]
        if not chunk:
            continue
        mean_onset = sum(chunk) / len(chunk)
        start = beats[i]
        next_i = min(i + window, len(beats) - 1)
        end = beats[next_i] if i + window < len(beats) else duration
        windows.append({"start": start, "end": end, "mean": mean_onset})

    if not windows:
        return [{"start": 0.0, "end": duration, "energy_level": "medium"}]

    sorted_means = sorted(w["mean"] for w in windows)
    n = len(sorted_means)
    threshold_low = sorted_means[max(0, n // 3 - 1)]
    threshold_high = sorted_means[min(n - 1, (2 * n) // 3)]

    raw: list[dict] = []
    for w in windows:
        if w["mean"] <= threshold_low:
            level = "low"
        elif w["mean"] >= threshold_high:
            level = "high"
        else:
            level = "medium"
        raw.append({"start": w["start"], "end": w["end"], "energy_level": level})

    # Merge consecutive same-level sections
    merged = [{"start": raw[0]["start"], "end": raw[0]["end"], "energy_level": raw[0]["energy_level"]}]
    for section in raw[1:]:
        if section["energy_level"] == merged[-1]["energy_level"]:
            merged[-1]["end"] = section["end"]
        else:
            merged.append({"start": section["start"], "end": section["end"], "energy_level": section["energy_level"]})

    merged[0]["start"] = 0.0
    merged[-1]["end"] = duration
    return merged


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
