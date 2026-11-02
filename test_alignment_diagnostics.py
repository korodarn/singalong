"""
Unit tests for Phase 7 Alignment Diagnostics, MMS-FA Forced Alignment,
Elastic Boundaries, and Enhanced Linter Rules.
"""

import json
from pathlib import Path
import numpy as np
import pytest
import soundfile as sf

from core.aligner import (
    VocalAnalyzer,
    estimate_global_lrc_drift,
    forced_align_with_mms,
    align_lrc_with_tokens,
)
from core.linter import lint_project
from core.workspace import purge_workdir


@pytest.fixture
def temp_audio(tmp_path: Path):
    """Generate synthetic 16kHz audio with voiced vowels, silence, and breath."""
    sr = 16000
    duration_s = 6.0
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)

    # 0.0 - 0.5s: silence
    # 0.5 - 0.9s: breath intake (white noise with high spectral flatness)
    # 0.9 - 3.2s: sung vowel (harmonic tone at 220Hz with high energy)
    # 3.2 - 3.6s: breath trough / silence
    # 3.6 - 5.5s: second sung phrase (harmonic tone at 330Hz)
    audio = np.zeros_like(t, dtype=np.float32)

    # Breath: moderate RMS, high flatness
    b_start, b_end = int(0.5 * sr), int(0.9 * sr)
    audio[b_start:b_end] = np.random.normal(0, 0.03, b_end - b_start).astype(np.float32)

    # Voiced vowel 1
    v1_start, v1_end = int(0.9 * sr), int(3.2 * sr)
    audio[v1_start:v1_end] = 0.25 * np.sin(2 * np.pi * 220 * t[v1_start:v1_end]).astype(np.float32)

    # Voiced vowel 2
    v2_start, v2_end = int(3.6 * sr), int(5.5 * sr)
    audio[v2_start:v2_end] = 0.25 * np.sin(2 * np.pi * 330 * t[v2_start:v2_end]).astype(np.float32)

    wav_path = tmp_path / "vocals.wav"
    sf.write(str(wav_path), audio, sr)
    return wav_path


def test_vocal_analyzer_features(temp_audio: Path):
    analyzer = VocalAnalyzer(temp_audio)
    assert analyzer.rms_db is not None
    assert analyzer.spectral_flatness is not None
    assert analyzer.onset is not None

    # Breath region (500ms - 900ms) vs voiced region (1000ms - 3000ms)
    assert analyzer.is_breath(600, 850)
    assert not analyzer.is_breath(1200, 2000)

    # Leading breath skipping: starting at 500ms should skip breath to ~900ms
    adj_s, trimmed = analyzer.find_voiced_attack(500)
    assert 850 <= adj_s <= 1000
    assert trimmed >= 350


def test_elastic_boundary_sliding(temp_audio: Path):
    analyzer = VocalAnalyzer(temp_audio)
    # Nominal LRC cuts prematurely at 2200ms while singer holds note until 3200ms
    nom_boundary = 2200
    elastic_boundary, diag = analyzer.find_elastic_inter_line_boundary(nom_boundary)
    # The boundary should slide past the sustained vowel into the acoustic trough (3200ms - 3600ms)
    assert elastic_boundary > 3000
    assert diag["shift_ms"] >= 800


def test_ransac_global_drift():
    # Simulate LRC timestamps and ASR tokens with a known constant offset of +450ms
    lrc_lines = [
        {"start": 1000, "text": "hello world"},
        {"start": 5000, "text": "this is a song"},
        {"start": 10000, "text": "singing in the rain"},
        {"start": 15000, "text": "lantern with flame"},
    ]
    tokens = [
        {"start": 1450, "end": 1800, "text": "hello"},
        {"start": 1820, "end": 2200, "text": "world"},
        {"start": 5450, "end": 5700, "text": "this"},
        {"start": 5720, "end": 5900, "text": "is"},
        {"start": 5920, "end": 6050, "text": "a"},
        {"start": 6070, "end": 6400, "text": "song"},
        {"start": 10450, "end": 10800, "text": "singing"},
        {"start": 10820, "end": 11000, "text": "in"},
        {"start": 11020, "end": 11150, "text": "the"},
        {"start": 11170, "end": 11500, "text": "rain"},
        {"start": 15450, "end": 15900, "text": "lantern"},
        {"start": 15920, "end": 16200, "text": "with"},
        {"start": 16220, "end": 16800, "text": "flame"},
    ]

    slope, intercept, inliers = estimate_global_lrc_drift(lrc_lines, tokens)
    assert abs(slope) < 0.01
    assert 400 <= intercept <= 500
    assert len(inliers) >= 3


def test_mms_forced_alignment(temp_audio: Path):
    analyzer = VocalAnalyzer(temp_audio)
    words = [
        {"text": "lantern", "space": True},
        {"text": "flame", "space": False},
    ]
    aligned = forced_align_with_mms(words, 900, 3200, analyzer, is_ja=False)
    assert aligned is not None
    assert len(aligned) == 2
    for tok in aligned:
        assert "diag" in tok
        assert tok["diag"]["source"] == "mms_fa"
        assert 0.0 <= tok["diag"]["ctc_score"] <= 1.0
        assert tok["end"] > tok["start"]


def test_align_lrc_with_tokens_and_v2_schema(temp_audio: Path):
    analyzer = VocalAnalyzer(temp_audio)
    lrc_lines = [
        {"start": 500, "text": "lantern flame"},
        {"start": 3500, "text": "second phrase"},
    ]
    tokens = [
        {"start": 950, "end": 1800, "text": "lantern"},
        {"start": 1850, "end": 3150, "text": "flame"},
        {"start": 3650, "end": 4400, "text": "second"},
        {"start": 4450, "end": 5400, "text": "phrase"},
    ]

    result = align_lrc_with_tokens(lrc_lines, tokens, analyzer)
    assert len(result) >= 1

    first_line = result[0]
    assert "diag" in first_line
    diag = first_line["diag"]
    assert diag["lrc_nominal_start"] == 500
    assert diag["alignment_method"] in ("mms_fa_ctc", "asr_anchored", "island_weighted")
    assert "mean_ctc_score" in diag
    assert "mean_rms_db" in diag
    assert "voiced_islands" in diag

    for tok in first_line["tokens"]:
        assert "diag" in tok
        assert "ctc_score" in tok["diag"]
        assert "rms_db" in tok["diag"]


def test_linter_acoustic_rules(tmp_path: Path):
    sr = 16000
    # Create audio where vocals are singing strongly at 2000-2400ms (-12 dBFS)
    audio = np.zeros(int(sr * 3.0), dtype=np.float32)
    s, e = int(0.5 * sr), int(2.4 * sr)
    audio[s:e] = 0.3 * np.sin(2 * np.pi * 440 * np.linspace(0, 1.9, e - s))
    vocals_path = tmp_path / "vocals.wav"
    sf.write(str(vocals_path), audio, sr)

    # Line that cuts off at 2000ms while audio continues singing until 2400ms
    project_json = tmp_path / "test.json"
    lines = [
        {
            "start": 500,
            "end": 2000,
            "is_backing": False,
            "tokens": [
                {
                    "text": "held",
                    "start": 500,
                    "end": 2000,
                    "diag": {
                        "source": "mms_fa",
                        "ctc_score": 0.15,  # Low CTC confidence
                    },
                }
            ],
            "diag": {
                "lrc_nominal_start": 3000,  # 2500ms shift
                "shift_from_lrc_start_ms": 2500,
            },
        }
    ]
    project_json.write_text(json.dumps(lines), encoding="utf-8")

    report = lint_project(project_json, vocals_path)
    issues = [iss["rule"] for iss in report["lines"][0]["issues"]]
    assert "ERR_CUTOFF_ACTIVE_VOCAL" in issues
    assert "WARN_LOW_CTC_CONFIDENCE" in issues
    assert "WARN_LARGE_LRC_SHIFT" in issues


def test_workspace_purge(tmp_path: Path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / ".gitkeep").write_text("")
    (workdir / "vid1.wav").write_text("audio")
    (workdir / "vid1.json").write_text("{}")
    (workdir / "vid1.asr.json").write_text("{}")
    (workdir / "vid1.netease.lrc").write_text("lrc")

    # Clean legacy artifacts only
    deleted = purge_workdir(workdir, legacy_only=True)
    assert any("vid1.asr.json" in d for d in deleted)
    assert any("vid1.netease.lrc" in d for d in deleted)
    assert (workdir / "vid1.wav").exists()
    assert (workdir / "vid1.json").exists()
    assert (workdir / ".gitkeep").exists()
