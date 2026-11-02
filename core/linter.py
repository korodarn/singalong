"""
core.linter - Automated Pre-Render QA / Linter for Singalong JSON timings.
"""

import json
import math
from pathlib import Path

import numpy as np
import soundfile as sf

_AUDIO_CACHE: dict[str, tuple[float, np.ndarray, int]] = {}


def load_audio_mono(wav_path: Path | None):
    if not wav_path or not wav_path.exists():
        return None, None
    key = str(wav_path.resolve())
    mtime = wav_path.stat().st_mtime
    cached = _AUDIO_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1], cached[2]
    data, sr = sf.read(str(wav_path), dtype="float32")
    if data.ndim > 1:
        data = np.mean(data, axis=1)
    _AUDIO_CACHE[key] = (mtime, data, sr)
    return data, sr


def rms_dbfs(audio: np.ndarray | None, sr: int | None, start_ms: int, end_ms: int) -> float:
    if audio is None or sr is None or end_ms <= start_ms:
        return 0.0
    s_idx = max(0, int(start_ms * sr / 1000))
    e_idx = min(len(audio), int(end_ms * sr / 1000))
    if e_idx <= s_idx:
        return -100.0
    segment = audio[s_idx:e_idx]
    rms = float(np.sqrt(np.mean(np.square(segment)) + 1e-12))
    return 20.0 * math.log10(rms)


def lint_project(json_path: Path, vocals_path: Path | None = None) -> dict:
    lines = json.loads(json_path.read_text(encoding="utf-8"))
    if vocals_path is None:
        stem = json_path.stem
        workdir = json_path.parent
        cand = workdir / "htdemucs" / stem / "vocals.wav"
        if not cand.exists():
            cand = workdir / f"{stem}.wav"
        if cand.exists():
            vocals_path = cand

    audio, sr = load_audio_mono(vocals_path)

    report_lines = []
    total_warnings = 0
    scores = []
    prev_end = {False: -1, True: -1}

    for idx, ln in enumerate(lines):
        toks = [t for t in ln.get("tokens", []) if not t.get("is_deleted")]
        is_backing = bool(ln.get("is_backing", False))
        if not toks:
            report_lines.append({
                "line_index": idx,
                "start": ln.get("start", 0),
                "end": ln.get("end", 0),
                "is_backing": is_backing,
                "text": "(deleted line)",
                "score": 100,
                "issues": [],
            })
            continue

        ln_start = toks[0]["start"]
        ln_end = toks[-1]["end"]
        text_preview = "".join(
            t["text"] + (" " if t.get("space") else "") for t in toks
        ).strip()

        issues = []
        n_micro = 0
        n_ghost = 0
        n_runaway = 0
        is_uniform = False
        has_bleed = False

        if prev_end[is_backing] > ln_start:
            overlap_ms = prev_end[is_backing] - ln_start
            has_bleed = True
            issues.append({
                "rule": "ERR_LINE_BOUNDARY_BLEED",
                "detail": f"Overlaps previous {'backing' if is_backing else 'lead'} line by {overlap_ms}ms",
            })
        prev_end[is_backing] = max(prev_end[is_backing], ln_end)

        durations = [t["end"] - t["start"] for t in toks]
        if len(durations) >= 4:
            std_d = float(np.std(durations))
            if std_d < 5.0:
                is_uniform = True
                issues.append({
                    "rule": "WARN_UNIFORM_FALLBACK",
                    "detail": f"Syllable durations are nearly identical (std={std_d:.1f}ms across {len(durations)} tokens)",
                })

        n_cutoff = 0
        n_breath = 0
        n_low_ctc = 0
        n_large_shift = 0

        # Check for cutoff active vocals in the 300ms following ln_end
        if audio is not None:
            # Only test if there is space before the next line starts
            is_gap = True
            if idx + 1 < len(lines):
                nxt_toks = [t for t in lines[idx + 1].get("tokens", []) if not t.get("is_deleted")]
                if nxt_toks and nxt_toks[0]["start"] <= ln_end + 100:
                    is_gap = False
            if is_gap:
                cutoff_db = rms_dbfs(audio, sr, ln_end, ln_end + 300)
                if cutoff_db > -22.0:
                    n_cutoff += 1
                    issues.append({
                        "rule": "ERR_CUTOFF_ACTIVE_VOCAL",
                        "detail": f"Line ends while vocals are still active ({cutoff_db:.1f} dBFS in next 300ms)",
                    })

        # Check leading token breath
        if audio is not None and toks:
            t0 = toks[0]
            check_dur = min(250, t0["end"] - t0["start"])
            if check_dur >= 70:
                breath_db = rms_dbfs(audio, sr, t0["start"], t0["start"] + check_dur)
                if breath_db < -34.0:
                    n_breath += 1
                    issues.append({
                        "rule": "WARN_LEADING_BREATH",
                        "detail": f"First token starts on breath/silence ({breath_db:.1f} dBFS in first {check_dur}ms)",
                    })

        # Check for large LRC shift from diag
        ln_diag = ln.get("diag", {})
        shift_s = abs(ln_diag.get("shift_from_lrc_start_ms") or 0)
        shift_e = abs(ln_diag.get("shift_from_lrc_end_ms") or 0)
        if shift_s > 1200 or shift_e > 1200:
            n_large_shift += 1
            issues.append({
                "rule": "WARN_LARGE_LRC_SHIFT",
                "detail": f"Line shifted >1200ms from nominal LRC (start: {shift_s}ms, end: {shift_e}ms)",
            })

        for t in toks:
            d_i = t["end"] - t["start"]
            t_label = t["text"]

            ctc_val = t.get("diag", {}).get("ctc_score")
            if ctc_val is not None and ctc_val < 0.25:
                n_low_ctc += 1
                issues.append({
                    "rule": "WARN_LOW_CTC_CONFIDENCE",
                    "detail": f"Token '{t_label}' has low forced-alignment confidence ({ctc_val:.2f} < 0.25)",
                })

            if d_i < 60:
                n_micro += 1
                issues.append({
                    "rule": "WARN_MICRO_SYLLABLE",
                    "detail": f"Token '{t_label}' duration is {d_i}ms (< 60ms)",
                })

            if audio is not None:
                mean_db = rms_dbfs(audio, sr, t["start"], t["end"])
                if mean_db < -45.0:
                    n_ghost += 1
                    issues.append({
                        "rule": "WARN_SILENCE_GHOST",
                        "detail": f"Token '{t_label}' [{t['start']}..{t['end']}ms] in silence ({mean_db:.1f} dBFS)",
                    })

                if d_i > 2500:
                    tail_db = rms_dbfs(audio, sr, t["start"] + 800, t["end"])
                    if tail_db < -42.0:
                        n_runaway += 1
                        issues.append({
                            "rule": "WARN_RUNAWAY_HOLD",
                            "detail": f"Token '{t_label}' held for {d_i}ms with silent tail ({tail_db:.1f} dBFS)",
                        })

        penalty = (
            (25 if is_uniform else 0)
            + (25 if has_bleed else 0)
            + 30 * n_cutoff
            + 15 * n_micro
            + 20 * n_ghost
            + 20 * n_runaway
            + 10 * n_breath
            + 15 * n_low_ctc
            + 10 * n_large_shift
        )
        score = max(0, 100 - penalty)
        scores.append(score)
        total_warnings += len(issues)

        report_lines.append({
            "line_index": idx,
            "start": ln_start,
            "end": ln_end,
            "is_backing": is_backing,
            "text": text_preview,
            "score": score,
            "issues": issues,
        })

    avg_score = float(np.mean(scores)) if scores else 100.0
    flagged = [r for r in report_lines if r["score"] < 80 or r["issues"]]

    return {
        "json_path": str(json_path),
        "vocals_path": str(vocals_path) if vocals_path else None,
        "total_lines": len(report_lines),
        "flagged_lines": len(flagged),
        "total_issues": total_warnings,
        "mean_confidence": round(avg_score, 1),
        "lines": report_lines,
    }


def fmt_time(ms: int) -> str:
    s = ms / 1000.0
    return f"{int(s // 60):02d}:{s % 60:05.2f}"
