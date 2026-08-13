"""Audio property extraction for Task 3.

Required by the brief: duration, sample rate (kHz), bitrate, loudness (dB).
Bonus: a rough noise/quality estimate.

Two layers, deliberately:

1. ``ffprobe`` (container/codec truth)  -> bitrate, codec, channels, declared
   sample rate. Bitrate genuinely cannot be computed from decoded samples: a
   WAV is uncompressed (bitrate = sr x bits x channels) while an Opus/WebM blob
   from the browser only knows its bitrate from the container header.
2. ``librosa``/numpy (signal truth)     -> RMS loudness, peak, clipping,
   silence ratio, and an SNR estimate.

Loudness is reported as dBFS (0 dB = full scale, so speech typically lands
between -35 and -12 dB). It is NOT LUFS: proper EBU R128 loudness needs
K-weighting, and dBFS RMS is the honest, defensible approximation here.

SNR estimate: frame the signal, take frame RMS, treat the 10th percentile of
frames as the noise floor and the 90th percentile as signal, and report the
ratio in dB. This is the classic "silence-percentile" estimator - crude, but it
degrades sensibly and needs no reference recording.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

EPS = 1e-12
TARGET_SR = 16_000        # analysis rate; speech energy lives well below 8 kHz
FRAME_MS = 30


@dataclass
class AudioProperties:
    duration_sec: float | None = None
    sample_rate_khz: float | None = None
    bitrate_kbps: float | None = None
    loudness_db: float | None = None
    peak_db: float | None = None
    est_snr_db: float | None = None
    clipping_pct: float | None = None
    silence_pct: float | None = None
    zcr_mean: float | None = None
    spectral_flatness: float | None = None
    channels: int | None = None
    codec: str | None = None
    quality_label: str | None = None
    quality_notes: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _ffprobe(path: Path) -> dict:
    if not shutil.which("ffprobe"):
        return {}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        return json.loads(out)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}


def _db(x: float) -> float:
    return float(20.0 * np.log10(max(float(x), EPS)))


def extract(path: str | Path) -> AudioProperties:
    path = Path(path)
    props = AudioProperties()

    # ---- container-level facts -------------------------------------------
    probe = _ffprobe(path)
    astream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), None)
    fmt = probe.get("format", {})
    if astream:
        props.codec = astream.get("codec_name")
        props.channels = int(astream.get("channels") or 0) or None
        if astream.get("sample_rate"):
            props.sample_rate_khz = round(int(astream["sample_rate"]) / 1000.0, 3)
        for src in (astream.get("bit_rate"), fmt.get("bit_rate")):
            if src:
                props.bitrate_kbps = round(int(src) / 1000.0, 2)
                break
    if fmt.get("duration"):
        props.duration_sec = round(float(fmt["duration"]), 3)

    # ---- signal-level facts ----------------------------------------------
    y, sr = _load_mono(path)
    if y is None or len(y) == 0:
        props.quality_label = "unreadable"
        props.quality_notes = "audio could not be decoded"
        return props

    if props.duration_sec is None:
        props.duration_sec = round(len(y) / sr, 3)
    if props.sample_rate_khz is None:
        props.sample_rate_khz = round(sr / 1000.0, 3)

    # WebM/Opus from MediaRecorder often reports no bitrate; derive the real
    # average from file size instead of leaving it null.
    if props.bitrate_kbps is None and props.duration_sec:
        props.bitrate_kbps = round((path.stat().st_size * 8) / props.duration_sec / 1000.0, 2)

    rms = float(np.sqrt(np.mean(np.square(y))))
    peak = float(np.max(np.abs(y)))
    props.loudness_db = round(_db(rms), 2)
    props.peak_db = round(_db(peak), 2)
    props.clipping_pct = round(float(np.mean(np.abs(y) >= 0.999)) * 100.0, 4)

    frame = int(sr * FRAME_MS / 1000)
    hop = frame // 2
    frames = _frame_rms(y, frame, hop)
    if frames.size >= 4:
        noise = float(np.percentile(frames, 10))
        signal = float(np.percentile(frames, 90))
        props.est_snr_db = round(_db(signal) - _db(noise), 2)
        # "silence" = frames more than 30 dB below the loud percentile
        props.silence_pct = round(float(np.mean(frames < signal * 10 ** (-30 / 20))) * 100.0, 2)

    props.zcr_mean = round(float(np.mean(np.abs(np.diff(np.sign(y))) > 0)), 5)
    props.spectral_flatness = _spectral_flatness(y, frame, hop)

    label, notes = _grade(props)
    props.quality_label, props.quality_notes = label, notes
    return props


def _load_mono(path: Path):
    """Decode to mono float32 at TARGET_SR.

    ffmpeg is used as the decoder rather than ``librosa.load``: browsers hand us
    WebM/Opus, which librosa can only open through the deprecated ``audioread``
    path (it emits a removal warning and is slower). ffmpeg is already a hard
    dependency here because of ffprobe. soundfile is the fallback for plain
    WAV/FLAC when ffmpeg is unavailable.
    """
    if shutil.which("ffmpeg"):
        try:
            raw = subprocess.run(
                ["ffmpeg", "-v", "quiet", "-i", str(path), "-ac", "1",
                 "-ar", str(TARGET_SR), "-f", "f32le", "pipe:1"],
                capture_output=True, timeout=120, check=True,
            ).stdout
            if raw:
                return np.frombuffer(raw, dtype=np.float32), TARGET_SR
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
    try:
        import soundfile as sf

        y, sr = sf.read(str(path), dtype="float32", always_2d=True)
        return y.mean(axis=1), int(sr)
    except Exception:
        return None, 0


def _frame_rms(y: np.ndarray, frame: int, hop: int) -> np.ndarray:
    if len(y) < frame or frame <= 0:
        return np.array([float(np.sqrt(np.mean(np.square(y))))]) if len(y) else np.array([])
    n = 1 + (len(y) - frame) // hop
    idx = np.arange(frame)[None, :] + hop * np.arange(n)[:, None]
    return np.sqrt(np.mean(np.square(y[idx]), axis=1))


def _spectral_flatness(y: np.ndarray, frame: int, hop: int) -> float | None:
    """Flatness ~1 => noise-like (hiss/static); ~0 => tonal (voice, music)."""
    try:
        import librosa

        sf_ = librosa.feature.spectral_flatness(y=y, n_fft=max(256, frame), hop_length=max(1, hop))
        return round(float(np.mean(sf_)), 5)
    except Exception:
        return None


def _grade(p: AudioProperties) -> tuple[str, str]:
    """Rule-based quality gate. Deliberately explainable: an ops person has to
    be able to tell a worker WHY their recording was rejected."""
    notes: list[str] = []
    score = 100

    if p.est_snr_db is not None:
        if p.est_snr_db < 10:
            score -= 40; notes.append(f"very noisy (SNR ~{p.est_snr_db} dB)")
        elif p.est_snr_db < 20:
            score -= 15; notes.append(f"moderate background noise (SNR ~{p.est_snr_db} dB)")
    if p.loudness_db is not None:
        if p.loudness_db < -45:
            score -= 30; notes.append(f"very quiet ({p.loudness_db} dBFS)")
        elif p.loudness_db < -35:
            score -= 10; notes.append(f"quiet ({p.loudness_db} dBFS)")
        elif p.loudness_db > -8:
            score -= 15; notes.append(f"very hot ({p.loudness_db} dBFS), risk of distortion")
    if p.clipping_pct and p.clipping_pct > 0.1:
        score -= 25; notes.append(f"clipping on {p.clipping_pct}% of samples")
    if p.silence_pct is not None and p.silence_pct > 70:
        score -= 25; notes.append(f"{p.silence_pct}% of the clip is near-silent")
    if p.duration_sec is not None and p.duration_sec < 1.0:
        score -= 30; notes.append("clip shorter than 1 second")
    if p.sample_rate_khz is not None and p.sample_rate_khz < 8:
        score -= 20; notes.append(f"sample rate {p.sample_rate_khz} kHz is below telephone quality")
    if p.spectral_flatness is not None and p.spectral_flatness > 0.4:
        score -= 15; notes.append("spectrum looks noise-like rather than speech-like")

    label = "good" if score >= 80 else "fair" if score >= 55 else "poor"
    return label, "; ".join(notes) if notes else "no problems detected"


if __name__ == "__main__":  # pragma: no cover - manual probe
    import sys

    for f in sys.argv[1:]:
        print(f)
        for k, v in extract(f).as_dict().items():
            print(f"  {k:<18} {v}")
