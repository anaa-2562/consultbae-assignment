"""Audio extraction tests. Signals are synthesised so the expected answers are known."""
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.audio_features import extract  # noqa: E402

SR = 44_100


def _speechlike(seconds=2.0, amp=0.3, noise=0.002):
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    sig = amp * (np.sin(2 * np.pi * 180 * t) + 0.5 * np.sin(2 * np.pi * 360 * t))
    sig *= 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)          # syllable-rate envelope
    return sig + noise * np.random.default_rng(0).standard_normal(len(t))


@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    p = tmp_path_factory.mktemp("audio") / "clean.wav"
    sf.write(p, _speechlike().astype("float32"), SR)
    return p


def test_required_four_properties_are_present(clean):
    p = extract(clean)
    assert p.duration_sec == pytest.approx(2.0, abs=0.05)
    assert p.sample_rate_khz == 44.1
    assert p.bitrate_kbps and p.bitrate_kbps > 0
    assert p.loudness_db is not None and -60 < p.loudness_db < 0


def test_loudness_tracks_amplitude(tmp_path):
    loud, quiet = tmp_path / "loud.wav", tmp_path / "quiet.wav"
    sf.write(loud, _speechlike(amp=0.4).astype("float32"), SR)
    sf.write(quiet, (_speechlike(amp=0.4) * 0.01).astype("float32"), SR)
    a, b = extract(loud).loudness_db, extract(quiet).loudness_db
    assert a - b == pytest.approx(40, abs=3)              # 0.01x  ==  -40 dB


def test_noise_lowers_the_snr_estimate(tmp_path):
    noisy = tmp_path / "noisy.wav"
    sf.write(noisy, (_speechlike(amp=0.02) + 0.15 * np.random.default_rng(1).standard_normal(int(SR * 2))).astype("float32"), SR)
    assert extract(noisy).est_snr_db < 10
    assert extract(noisy).quality_label == "poor"


def test_clipping_is_detected(tmp_path):
    p = tmp_path / "clipped.wav"
    sf.write(p, np.clip(_speechlike() * 8, -1, 1).astype("float32"), SR)
    props = extract(p)
    assert props.clipping_pct > 1
    assert "clipping" in props.quality_notes


def test_compressed_formats_decode(tmp_path, clean):
    """Browsers send WebM/Opus, not WAV - the extractor must handle it."""
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    out = tmp_path / "clip.webm"
    subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-i", str(clean), "-c:a", "libopus",
                    "-b:a", "32k", str(out)], check=True)
    p = extract(out)
    assert p.codec == "opus"
    assert p.duration_sec == pytest.approx(2.0, abs=0.1)
    assert 10 < p.bitrate_kbps < 80                       # real container bitrate, not PCM's 700+
    assert p.quality_label == "good"


def test_unreadable_file_does_not_crash(tmp_path):
    p = tmp_path / "not_audio.wav"
    p.write_bytes(b"this is not audio")
    assert extract(p).quality_label == "unreadable"
