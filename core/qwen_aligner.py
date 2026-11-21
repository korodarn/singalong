"""
core.qwen_aligner - Local Qwen3-ForcedAligner-0.6B backend for multilingual (EN/ZH/JA/KO)
lyric-to-audio forced alignment.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import torch

from core.phonetics import has_kana, is_han, is_hangul

if TYPE_CHECKING:
    from core.aligner import VocalAnalyzer

_QWEN_MODEL = None
_QWEN_LOAD_FAILED = False

_LANG_MAP = {
    "en": "English",
    "english": "English",
    "zh": "Chinese",
    "chinese": "Chinese",
    "yue": "Cantonese",
    "cantonese": "Cantonese",
    "ja": "Japanese",
    "japanese": "Japanese",
    "ko": "Korean",
    "korean": "Korean",
}


def is_qwen3_available() -> bool:
    """Return True if qwen_asr is importable and weights are cached or loadable."""
    try:
        import qwen_asr  # noqa: F401
        return True
    except Exception:
        return False


def _resolve_local_snapshot() -> str | None:
    """Return local Hugging Face snapshot directory if already downloaded."""
    hub_dir = Path.home() / ".cache" / "huggingface" / "hub" / "models--Qwen--Qwen3-ForcedAligner-0.6B" / "snapshots"
    if hub_dir.exists():
        snaps = sorted([p for p in hub_dir.iterdir() if p.is_dir()])
        if snaps:
            return str(snaps[-1])
    return None


def get_qwen3_aligner(device: str = "cuda"):
    """Lazy-load Qwen3-ForcedAligner-0.6B once per process."""
    global _QWEN_MODEL, _QWEN_LOAD_FAILED
    if _QWEN_MODEL is not None:
        return _QWEN_MODEL
    if _QWEN_LOAD_FAILED:
        return None

    try:
        from qwen_asr import Qwen3ForcedAligner

        use_cuda = device.startswith("cuda") and torch.cuda.is_available()
        device_map = "cuda:0" if use_cuda else "cpu"
        dtype = torch.bfloat16 if use_cuda else torch.float32

        local_snap = _resolve_local_snapshot()
        model_id = local_snap if local_snap else "Qwen/Qwen3-ForcedAligner-0.6B"

        # Ensure Triton cache is writable if running in a restricted environment
        if "TRITON_CACHE_DIR" not in os.environ:
            default_triton = Path.home() / ".triton"
            try:
                default_triton.mkdir(parents=True, exist_ok=True)
            except OSError:
                os.environ["TRITON_CACHE_DIR"] = "/tmp/triton_cache"

        _QWEN_MODEL = Qwen3ForcedAligner.from_pretrained(
            model_id,
            dtype=dtype,
            device_map=device_map,
        )
        return _QWEN_MODEL
    except Exception as e:
        print(f"  [!] Failed to load Qwen3-ForcedAligner-0.6B: {e}")
        _QWEN_LOAD_FAILED = True
        return None


def detect_qwen_language(words: list[dict], lang: str | None = None, is_ja: bool = False) -> str:
    """Resolve Qwen3-ForcedAligner language name from explicit lang hint or token script."""
    if lang:
        mapped = _LANG_MAP.get(lang.lower().strip())
        if mapped:
            return mapped
    if is_ja:
        return "Japanese"
    text = "".join(w.get("text", "") for w in words)
    if any(has_kana(ch) for ch in text):
        return "Japanese"
    if any(is_hangul(ch) for ch in text):
        return "Korean"
    if any(is_han(ch) for ch in text):
        return "Chinese"
    return "English"


def _norm_piece(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s, flags=re.UNICODE).lower()


def _map_qwen_items_to_words(
    words: list[dict],
    items: list,
    buf_s_ms: int,
) -> list[tuple[int, int]] | None:
    """
    Map Qwen3 ForcedAlignItem list back to the exact Singalong `words` list.
    Returns list of (start_ms, end_ms) pairs aligned 1-to-1 with `words`, or None on mismatch.
    """
    if not words or not items:
        return None

    # Fast path: exact count match
    if len(items) == len(words):
        return [
            (buf_s_ms + int(round(it.start_time * 1000)), buf_s_ms + int(round(it.end_time * 1000)))
            for it in items
        ]

    # Character-stream mapping (handles contractions, punctuation splits, or CJK character grouping)
    item_chars: list[tuple[int, int]] = []
    for it in items:
        piece = _norm_piece(getattr(it, "text", "") or "")
        if not piece:
            continue
        ts = buf_s_ms + int(round(it.start_time * 1000))
        te = buf_s_ms + int(round(it.end_time * 1000))
        n = len(piece)
        for k in range(n):
            cs = int(round(ts + (te - ts) * k / n))
            ce = int(round(ts + (te - ts) * (k + 1) / n))
            item_chars.append((cs, ce))

    word_norms = [_norm_piece(w.get("text", "")) for w in words]
    total_word_chars = sum(max(1, len(wn)) for wn in word_norms)
    if not item_chars:
        return None

    # Walk proportionally across item_chars if total length matches closely
    out: list[tuple[int, int]] = []
    n_ic = len(item_chars)
    cursor = 0
    for wn in word_norms:
        w_len = max(1, len(wn))
        if abs(n_ic - total_word_chars) <= max(2, int(0.15 * total_word_chars)):
            s_idx = min(n_ic - 1, cursor)
            e_idx = min(n_ic - 1, max(s_idx, cursor + w_len - 1))
            cursor += w_len
        else:
            # Proportional fallback
            s_idx = min(n_ic - 1, int(round(cursor * n_ic / max(1, total_word_chars))))
            cursor += w_len
            e_idx = min(n_ic - 1, max(s_idx, int(round(cursor * n_ic / max(1, total_word_chars))) - 1))
        out.append((item_chars[s_idx][0], item_chars[e_idx][1]))
    return out


def forced_align_with_qwen3(
    words: list[dict],
    span_s: int,
    span_e: int,
    analyzer: "VocalAnalyzer",
    lang: str | None = None,
    is_ja: bool = False,
    device: str = "cuda",
) -> list[dict] | None:
    """
    Run Qwen3-ForcedAligner-0.6B on a vocal phrase slice [span_s, span_e] and return
    Singalong-formatted tokens with repaired 0ms point-tokens, onset snapping, and
    acoustic vowel-tail sustain extension.
    """
    if not words or analyzer.data_16k is None or span_e <= span_s:
        return None

    model = get_qwen3_aligner(device=device)
    if model is None:
        return None

    audio_dur_ms = len(analyzer.data_16k) * 1000 // 16000
    buf_s_ms = max(0, span_s - 250)
    buf_e_ms = min(audio_dur_ms, span_e + 350)
    # Cap per-line slice at 25s so Qwen3 stays in its sweet spot
    buf_e_ms = min(buf_e_ms, buf_s_ms + 25000)

    sample_s = int(buf_s_ms * 16)
    sample_e = int(buf_e_ms * 16)
    audio_slice = analyzer.data_16k[sample_s:sample_e]
    if len(audio_slice) < 3200:
        return None

    qwen_lang = detect_qwen_language(words, lang=lang, is_ja=is_ja)
    if qwen_lang in ("Chinese", "Cantonese", "Japanese"):
        ref_text = "".join(w.get("text", "") for w in words)
    else:
        ref_text = " ".join(w.get("text", "") for w in words)

    try:
        results = model.align(
            audio=(audio_slice, 16000),
            text=ref_text,
            language=qwen_lang,
        )
        items = results[0] if results else None
    except Exception as e:
        print(f"  [!] Qwen3-ForcedAligner inference error: {e}")
        return None

    if not items:
        return None

    raw_bounds = _map_qwen_items_to_words(words, items, buf_s_ms)
    if not raw_bounds or len(raw_bounds) != len(words):
        return None

    # Count collapsed 0ms tokens before repair (diagnostic quality signal)
    n_zero = sum(1 for ts, te in raw_bounds if te <= ts)
    m = len(words)

    # Repair 0ms point-tokens cleanly by borrowing space up to the next non-zero token
    repaired: list[list[int]] = [[int(ts), int(te)] for ts, te in raw_bounds]
    i = 0
    while i < m:
        if repaired[i][1] <= repaired[i][0]:
            j = i
            while j < m and repaired[j][1] <= repaired[j][0]:
                j += 1
            # Available span from previous end (or current start) to next token start (or span_e)
            anchor_s = repaired[i][0]
            if i > 0 and anchor_s < repaired[i - 1][1]:
                anchor_s = repaired[i - 1][1]
            next_s = repaired[j][0] if j < m else max(anchor_s + (j - i) * 140, span_e)
            avail = next_s - anchor_s
            count = j - i
            if avail >= count * 70:
                step = avail / count
                for k in range(count):
                    ts = int(round(anchor_s + k * step))
                    te = int(round(anchor_s + (k + 1) * step))
                    repaired[i + k] = [ts, max(ts + 65, te - 15)]
            else:
                # Borrow slightly from previous token if it is long, otherwise give minimum 75ms
                if i > 0 and repaired[i - 1][1] - repaired[i - 1][0] > count * 90 + 120:
                    borrow = min(count * 90, (repaired[i - 1][1] - repaired[i - 1][0]) // 2)
                    repaired[i - 1][1] -= borrow
                    anchor_s = repaired[i - 1][1] + 10
                    step = max(75.0, (next_s - anchor_s) / count)
                    for k in range(count):
                        ts = int(round(anchor_s + k * step))
                        repaired[i + k] = [ts, int(round(ts + max(70.0, step - 10)))]
                else:
                    cur = anchor_s
                    for k in range(count):
                        repaired[i + k] = [cur, cur + 80]
                        cur += 85
            i = j
        else:
            i += 1

    # Build final tokens with onset snapping and acoustic vowel-tail sustain extension
    out_tokens: list[dict] = []
    for idx, w in enumerate(words):
        w_start_ms, w_end_ms = repaired[idx]
        was_zero = raw_bounds[idx][1] <= raw_bounds[idx][0]

        if analyzer.onset is not None and not was_zero:
            f_ms = analyzer._idx(w_start_ms)
            w_lo = max(0, f_ms - 3)
            w_hi = min(len(analyzer.onset), f_ms + 4)
            if w_hi > w_lo:
                best_peak_idx = w_lo + int(np.argmax(analyzer.onset[w_lo:w_hi]))
                if analyzer.onset[best_peak_idx] > 0.40:
                    w_start_ms = best_peak_idx * analyzer.frame_ms

        if out_tokens and w_start_ms < out_tokens[-1]["end"] + 10:
            w_start_ms = out_tokens[-1]["end"] + 10
        if w_end_ms <= w_start_ms + 60:
            w_end_ms = w_start_ms + 75

        next_s = repaired[idx + 1][0] if idx + 1 < m else min(span_e + 400, w_start_ms + 5000)
        ceiling_ms = min(next_s - 20, w_start_ms + 5000)

        orig_end = w_end_ms
        sustain_ext = 0
        if w_end_ms < ceiling_ms:
            curr_e = w_end_ms
            while curr_e + 40 <= ceiling_ms:
                if analyzer.mean_db(curr_e, curr_e + 40) >= -34.0:
                    curr_e += 40
                else:
                    break
            if curr_e > orig_end:
                sustain_ext = curr_e - orig_end
                w_end_ms = curr_e

        tok_rms = analyzer.mean_db(w_start_ms, w_end_ms)
        ons_val = float(analyzer.onset[analyzer._idx(w_start_ms)]) if analyzer.onset is not None else 1.0
        # Confidence heuristic: 0.85 for direct Qwen3 span, 0.55 for repaired 0ms point-token
        conf = 0.55 if was_zero else 0.85

        flags = []
        if tok_rms < -42.0:
            flags.append({
                "code": "LOW_RMS_SILENCE",
                "severity": "warning",
                "detail": f"Token sits in low vocal energy ({tok_rms:.1f} dBFS)",
            })

        out_tokens.append({
            "text": w.get("text", ""),
            "start": int(w_start_ms),
            "end": int(w_end_ms),
            "space": w.get("space", False),
            "comment": w.get("comment", ""),
            "diag": {
                "source": "qwen3_fa",
                "ctc_score": round(conf, 3),
                "rms_db": round(tok_rms, 1),
                "onset_strength": round(ons_val, 2),
                "sustain_extended_ms": sustain_ext,
                "flags": flags,
            },
        })

    # Reject if Qwen3 collapsed more than half of a multi-word line to 0ms
    if m >= 5 and (n_zero / m) > 0.55:
        return None

    return out_tokens
