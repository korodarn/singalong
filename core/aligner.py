"""
core.aligner - Acoustic Energy & Onset Analyzer and hybrid LRC + ASR token alignment engine.
"""

import json
import re
from pathlib import Path

import librosa
import numpy as np
from pypinyin import lazy_pinyin
from rapidfuzz import fuzz
import soundfile as sf

from core.phonetics import (
    add_phonetics,
    decompose_hangul,
    is_hangul,
    is_han,
    tokenize_tokens,
    tokenize_text,
)


class VocalAnalyzer:
    """10ms-resolution vocal RMS (dBFS) and spectral onset strength curve."""

    def __init__(self, wav_path: Path | None, frame_ms: int = 10):
        self.frame_ms = frame_ms
        self.rms_db: np.ndarray | None = None
        self.onset: np.ndarray | None = None
        if wav_path and wav_path.exists():
            data, sr = sf.read(str(wav_path), dtype="float32")
            if data.ndim > 1:
                data = np.mean(data, axis=1)
            hop = max(1, int(sr * frame_ms / 1000))
            win = hop * 2
            rms = librosa.feature.rms(y=data, frame_length=win, hop_length=hop, center=True)[0]
            self.rms_db = 20.0 * np.log10(np.maximum(rms, 1e-6))

            onset_raw = librosa.onset.onset_strength(y=data, sr=sr, hop_length=hop, center=True)
            if len(onset_raw) < len(self.rms_db):
                onset_raw = np.pad(onset_raw, (0, len(self.rms_db) - len(onset_raw)))
            else:
                onset_raw = onset_raw[:len(self.rms_db)]
            p95 = float(np.percentile(onset_raw, 95)) if len(onset_raw) else 1.0
            self.onset = np.clip(onset_raw / max(p95, 1e-4), 0.0, 2.0)

    def _idx(self, ms: int) -> int:
        if self.rms_db is None:
            return 0
        return int(np.clip(ms // self.frame_ms, 0, len(self.rms_db) - 1))

    def mean_db(self, start_ms: int, end_ms: int) -> float:
        if self.rms_db is None or end_ms <= start_ms:
            return 0.0
        s = self._idx(start_ms)
        e = max(s + 1, self._idx(end_ms))
        lin = np.power(10.0, self.rms_db[s:e] / 10.0)
        return float(10.0 * np.log10(np.mean(lin) + 1e-12))

    def active_islands(self, start_ms: int, end_ms: int,
                       thresh_db: float = -41.5,
                       min_gap_ms: int = 240,
                       min_island_ms: int = 90) -> list[tuple[int, int]]:
        """Return voiced [start_ms, end_ms] intervals inside [start_ms, end_ms]."""
        if self.rms_db is None or end_ms <= start_ms:
            return [(start_ms, end_ms)]
        s = self._idx(start_ms)
        e = max(s + 1, self._idx(end_ms))
        sub = self.rms_db[s:e]
        p90 = float(np.percentile(sub, 90)) if len(sub) else thresh_db
        eff_thresh = max(thresh_db, min(-27.5, p90 - 16.5))
        mask = sub > eff_thresh
        if not np.any(mask):
            mask = sub > thresh_db
        if not np.any(mask):
            mask = sub > (thresh_db - 4.0)
            if not np.any(mask):
                return [(start_ms, min(end_ms, start_ms + 1500))]

        islands = []
        in_run = False
        run_s = 0
        for i, val in enumerate(mask):
            if val and not in_run:
                in_run = True
                run_s = i
            elif not val and in_run:
                in_run = False
                islands.append([start_ms + run_s * self.frame_ms, start_ms + i * self.frame_ms])
        if in_run:
            islands.append([start_ms + run_s * self.frame_ms, start_ms + len(mask) * self.frame_ms])

        merged = []
        for isl in islands:
            if merged and (isl[0] - merged[-1][1]) < min_gap_ms:
                merged[-1][1] = isl[1]
            else:
                merged.append(isl)

        valid = []
        for a, b in merged:
            if (b - a) >= min_island_ms:
                pa = max(start_ms, int(a) - 20)
                pb = min(end_ms, int(b) + 30)
                if valid and pa <= valid[-1][1] + 20:
                    valid[-1] = (valid[-1][0], max(valid[-1][1], pb))
                else:
                    valid.append((pa, pb))
        return valid if valid else [(int(merged[0][0]), int(merged[-1][1]))]

    def clamp_token_silence(self, start_ms: int, end_ms: int, min_dur: int = 80) -> tuple[int, int]:
        """Trim leading/trailing silence and cap runaway silent tails."""
        if self.rms_db is None or end_ms <= start_ms:
            return start_ms, end_ms
        s = self._idx(start_ms)
        e = max(s + 1, self._idx(end_ms))
        sub = self.rms_db[s:e]
        p90 = float(np.percentile(sub, 90)) if len(sub) else -41.5
        eff_thresh = max(-41.5, min(-28.5, p90 - 16.0))
        voiced = np.where(sub > eff_thresh)[0]
        if len(voiced) == 0:
            voiced = np.where(sub > -41.5)[0]
        if len(voiced) == 0:
            voiced = np.where(sub > -44.5)[0]
        if len(voiced) > 0:
            new_s = start_ms + max(0, int(voiced[0]) - 2) * self.frame_ms
            new_e = start_ms + min(len(sub), int(voiced[-1]) + 3) * self.frame_ms
            if new_e - new_s >= min_dur:
                return int(new_s), int(new_e)
        return start_ms, min(end_ms, start_ms + 1800)

    def boundary_curve(self, start_ms: int, end_ms: int) -> np.ndarray:
        """Compute 10ms acoustic syllable-boundary score combining spectral onset and consonant RMS dip."""
        if self.rms_db is None or self.onset is None or end_ms <= start_ms:
            n = max(2, (end_ms - start_ms) // self.frame_ms)
            return np.zeros(n, dtype=np.float64)
        s = self._idx(start_ms)
        e = max(s + 2, self._idx(end_ms))
        rms = self.rms_db[s:e]
        ons = self.onset[s:e]
        n = len(rms)
        b = np.zeros(n, dtype=np.float64)
        p90 = float(np.percentile(rms, 90)) if n else -20.0
        for idx in range(1, n - 1):
            w0 = max(0, idx - 12)
            w1 = min(n, idx + 12)
            loc_max = float(np.max(rms[w0:w1]))
            dip_val = float(np.min(rms[max(0, idx - 3):idx + 1]))
            dip = max(0.0, loc_max - dip_val)
            rise = max(0.0, float(np.max(rms[idx:min(n, idx + 6)])) - dip_val)
            dip_score = min(1.6, (dip / 10.0) * min(1.3, rise / 4.5))
            ons_score = float(np.max(ons[max(0, idx - 1):min(n, idx + 2)]))
            target_db = float(np.max(rms[idx:min(n, idx + 4)]))
            voice_gate = np.clip((target_db - (p90 - 18.0)) / 10.0, 0.15, 1.0)
            b[idx] = (0.65 * ons_score + 0.65 * dip_score) * voice_gate
        return b

    def char_weights(self, chars: list[dict], span_start: int, span_end: int, toks: list[dict]) -> list[float]:
        m = len(chars)
        if m == 1:
            return [1.0]

        ling_w = [word_weight(c, idx, m) for idx, c in enumerate(chars)]

        ac_w = np.ones(m, dtype=np.float64)
        if toks and len(toks) >= 2 and _tokens_match_chars(chars, toks, min_ratio=65.0):
            tok_durs = np.array([max(60, t["end"] - t["start"]) for t in toks], dtype=np.float64)
            x_src = np.linspace(0.0, 1.0, len(tok_durs))
            x_dst = np.linspace(0.0, 1.0, m)
            ac_w = np.interp(x_dst, x_src, tok_durs)
            ac_w = ac_w / max(np.mean(ac_w), 1.0)

        if self.rms_db is not None and self.onset is not None and span_end > span_start:
            s = self._idx(span_start)
            e = max(s + m, self._idx(span_end))
            rms_slice = np.clip((self.rms_db[s:e] + 45.0) / 25.0, 0.15, 1.8)
            ons_slice = self.onset[s:e]
            bins = np.array_split(rms_slice + 0.35 * ons_slice, m)
            env_w = np.array([float(np.mean(b)) if len(b) else 1.0 for b in bins], dtype=np.float64)
            env_w = env_w / max(np.mean(env_w), 1e-4)
            ac_w = 0.55 * ac_w + 0.45 * env_w

        combined = np.array(ling_w, dtype=np.float64) * np.clip(ac_w, 0.55, 1.75)
        return (combined / np.sum(combined)).tolist()


SHORT_FUNC_WORDS = {
    "i", "a", "an", "the", "to", "in", "on", "of", "it", "is", "and", "but",
    "or", "with", "for", "at", "by", "as", "could", "would", "should", "will",
    "can", "may", "my", "no", "so", "be", "we", "me", "he", "she",
}


def count_syllables(txt: str) -> int:
    """Estimate spoken/sung syllable count for CJK characters or English words."""
    if not txt:
        return 1
    if is_han(txt[0]) or is_hangul(txt[0]):
        return len(txt)
    w = txt.lower().strip("'")
    w = re.sub(r"'(s|d|ll|t|re|ve|m)$", "", w)
    w = w.replace("every", "evry")
    if len(w) <= 3:
        return 1
    if w.endswith("ed") and len(w) > 4 and w[-3] not in "td":
        w = w[:-2]
    elif w.endswith("es") and len(w) > 4 and not w.endswith(("ses", "zes", "xes", "ches", "shes", "ges", "ces")):
        w = w[:-2]
    elif w.endswith("e") and not w.endswith(("le", "ee", "oe", "ye")):
        w = w[:-1]
    clusters = re.findall(r"[aeiouy]+", w)
    return max(1, len(clusters))


def word_weight(c: dict, idx: int, m: int) -> float:
    txt = c["text"]
    if is_hangul(txt):
        w = 1.0
        _, v_idx, f_idx = decompose_hangul(txt)
        if f_idx != 0:
            w += 0.18
        if v_idx in (2, 3, 6, 7, 9, 10, 11, 12, 14, 15, 16, 17, 19):
            w += 0.12
    elif is_han(txt):
        w = 1.0 + (0.08 * ((ord(txt) % 3) - 1))
    else:
        ns = count_syllables(txt)
        w = 0.35 + 0.65 * ns + 0.02 * min(len(txt), 8)
        if ns == 1 and txt.lower() in SHORT_FUNC_WORDS and idx < m - 1:
            w *= 0.72

    if c.get("space") and idx < m - 1:
        w *= 1.12
    if idx == m - 1:
        w *= 1.40
    return max(0.4, w)


def _token_phonetic_str(txt: str) -> str:
    if not txt:
        return ""
    if any(is_han(ch) for ch in txt):
        return "".join(lazy_pinyin(txt)).lower()
    return re.sub(r"[^a-z0-9]", "", txt.lower())


def _filter_script_compatible_tokens(line_text: str, toks: list[dict]) -> list[dict]:
    """Filter out cross-script ASR hallucinations (e.g. Mandarin Hanzi tokens on pure Latin lyrics)."""
    if not toks:
        return []
    line_has_han = any(is_han(ch) for ch in line_text)
    if not line_has_han:
        return [t for t in toks if not any(is_han(ch) for ch in t.get("text", ""))]
    return toks


def _tokens_match_chars(chars: list[dict], toks: list[dict], min_ratio: float = 65.0) -> bool:
    """Verify whether ASR tokens phonetically match the target lyric tokens."""
    if not chars or not toks:
        return False
    line_has_han = any(is_han(c["text"][0]) for c in chars if c.get("text"))
    if not line_has_han and any(is_han(t["text"][0]) for t in toks if t.get("text")):
        return False
    c_py = "".join(_token_phonetic_str(c["text"]) for c in chars)
    t_py = "".join(_token_phonetic_str(t["text"]) for t in toks)
    if not c_py or not t_py:
        return False
    overall = fuzz.ratio(c_py, t_py)
    if overall < min_ratio:
        return False
    if len(chars) == len(toks):
        if overall < max(min_ratio, 72.0):
            return False
        pair_matches = 0
        for idx, (c, t) in enumerate(zip(chars, toks)):
            cp = _token_phonetic_str(c["text"])
            tp = _token_phonetic_str(t["text"])
            pr = fuzz.ratio(cp, tp) if (cp and tp) else 0.0
            if pr >= 55.0 or (len(cp) <= 2 and cp == tp):
                pair_matches += 1
            elif idx == len(chars) - 1 and pr < 50.0:
                return False
        if pair_matches < max(1, int(np.ceil(0.75 * len(chars)))):
            return False
    return True


def align_words_in_island(sub_chars: list[dict], span_s: int, span_e: int,
                          sub_toks: list[dict], analyzer: VocalAnalyzer) -> list[dict]:
    """Align words inside a voiced island using syllable-level DP over acoustic onsets and consonant dips."""
    m = len(sub_chars)
    if m == 0:
        return []
    if m == 1:
        ts, te = analyzer.clamp_token_silence(span_s, span_e)
        if te - ts < 70:
            te = min(span_e, ts + 120)
        return [{
            "text": sub_chars[0]["text"],
            "start": int(ts),
            "end": int(te),
            "space": sub_chars[0].get("space", False),
        }]

    syl_weights = []
    word_syl_ranges = []
    cur_s_idx = 0
    for idx, c in enumerate(sub_chars):
        txt = c["text"]
        ns = count_syllables(txt)
        w_base = 0.72 if (ns == 1 and txt.lower() in SHORT_FUNC_WORDS and idx < m - 1) else 1.0
        for s_k in range(ns):
            sw = w_base
            if idx == m - 1 and s_k == ns - 1:
                sw *= 1.55
            elif s_k == ns - 1 and c.get("space"):
                sw *= 1.12
            syl_weights.append(sw)
        word_syl_ranges.append((cur_s_idx, cur_s_idx + ns))
        cur_s_idx += ns

    n_syl = len(syl_weights)
    syl_weights_arr = np.array(syl_weights, dtype=np.float64)

    b_curve = analyzer.boundary_curve(span_s, span_e)
    n_frames = len(b_curve)
    if analyzer.rms_db is None or n_frames < n_syl * 6:
        total_span = max(span_e - span_s, m * 110)
        weights = analyzer.char_weights(sub_chars, span_s, span_s + total_span, sub_toks)
        raw_durs = [max(75.0, total_span * w) for w in weights]
        scale = total_span / sum(raw_durs)
        durs = [int(round(d * scale)) for d in raw_durs]
        cur_t = span_s
        out = []
        for c, d in zip(sub_chars, durs):
            out.append({
                "text": c["text"],
                "start": int(cur_t),
                "end": int(cur_t + max(70, d)),
                "space": c.get("space", False),
            })
            cur_t += max(70, d)
        return out

    # Reinforce boundary curve where individual ASR tokens phonetically match lyric words
    if sub_toks:
        for c in sub_chars[1:]:
            cp = _token_phonetic_str(c["text"])
            if not cp:
                continue
            for t in sub_toks:
                tp = _token_phonetic_str(t["text"])
                if tp and fuzz.ratio(cp, tp) >= 75.0:
                    f_idx = (t["start"] - span_s) // analyzer.frame_ms
                    if 1 <= f_idx < n_frames - 1:
                        for d_f in (-1, 0, 1):
                            if 1 <= f_idx + d_f < n_frames - 1:
                                b_curve[f_idx + d_f] += 0.45

    non_final_w = syl_weights_arr[:-1]
    non_final_sum = float(np.sum(non_final_w))
    min_step = max(6, min(13, n_frames // (n_syl + 1)))

    s_idx = analyzer._idx(span_s)
    rms_slice = analyzer.rms_db[s_idx:s_idx + n_frames]
    p90_db = float(np.percentile(rms_slice, 90)) if len(rms_slice) else -20.0
    min_last_f = max((n_syl - 1) * 18, int(0.36 * n_frames))
    strong_peaks = [
        idx for idx in range(min_last_f, n_frames - min_step)
        if b_curve[idx] >= 1.45 and b_curve[idx] >= b_curve[idx - 1] and b_curve[idx] >= b_curve[idx + 1]
    ]
    if strong_peaks and strong_peaks[-1] < 0.68 * n_frames:
        cand_f = strong_peaks[-1]
        tail_rms = rms_slice[min(len(rms_slice) - 1, cand_f + 25):]
        if len(tail_rms) > 15 and float(np.median(tail_rms)) < p90_db - 5.0:
            art_end = float(cand_f)
            exp_durs = (non_final_w / non_final_sum) * art_end
            exp_last_dur = float(n_frames - art_end)
        else:
            norm_w = syl_weights_arr / np.sum(syl_weights_arr)
            exp_durs = norm_w[:-1] * n_frames
            exp_last_dur = norm_w[-1] * n_frames
    else:
        norm_w = syl_weights_arr / np.sum(syl_weights_arr)
        exp_durs = norm_w[:-1] * n_frames
        exp_last_dur = norm_w[-1] * n_frames

    exp_cum = np.cumsum(exp_durs)

    dp = np.full((n_syl, n_frames), -1e9, dtype=np.float64)
    back = np.zeros((n_syl, n_frames), dtype=np.int32)

    for k in range(1, n_syl):
        exp_f = exp_cum[k - 1]
        exp_dur = exp_durs[k - 1]
        lo = k * min_step
        hi = n_frames - (n_syl - k) * min_step
        for f in range(lo, hi):
            pos_pen = ((f - exp_f) / max(12.0, 0.32 * n_frames)) ** 2
            b_val = b_curve[f]
            if k == 1:
                dur = f
                dur_pen = ((dur - exp_dur) / max(8.0, 0.65 * exp_dur)) ** 2
                dp[k, f] = 1.45 * b_val - 0.85 * dur_pen - 0.55 * pos_pen
            else:
                prev_lo = (k - 1) * min_step
                prev_hi = f - min_step + 1
                if prev_hi <= prev_lo:
                    continue
                prev_f = np.arange(prev_lo, prev_hi)
                durs = f - prev_f
                dur_pen = ((durs - exp_dur) / max(8.0, 0.65 * exp_dur)) ** 2
                scores = dp[k - 1, prev_lo:prev_hi] + 1.45 * b_val - 0.85 * dur_pen - 0.50 * pos_pen
                best_rel = int(np.argmax(scores))
                dp[k, f] = scores[best_rel]
                back[k, f] = prev_lo + best_rel

    last_lo = (n_syl - 1) * min_step
    last_hi = max(last_lo + 1, n_frames - min_step)
    best_last_f = last_lo
    best_total = -1e9
    for f in range(last_lo, last_hi):
        rem_dur = n_frames - f
        if rem_dur < exp_last_dur:
            tail_pen = ((exp_last_dur - rem_dur) / max(10.0, 0.55 * exp_last_dur)) ** 2
        else:
            tail_pen = 0.25 * ((rem_dur - exp_last_dur) / max(18.0, 1.1 * exp_last_dur)) ** 2
        sc = dp[n_syl - 1, f] - 0.55 * tail_pen
        if sc > best_total:
            best_total = sc
            best_last_f = f

    syl_starts = [0] * n_syl
    cur_f = best_last_f
    for k in range(n_syl - 1, 0, -1):
        syl_starts[k] = cur_f
        cur_f = int(back[k, cur_f])
    syl_starts.append(n_frames)

    out = []
    for idx, c in enumerate(sub_chars):
        s0, s1 = word_syl_ranges[idx]
        ts = span_s + syl_starts[s0] * analyzer.frame_ms
        te = span_s + syl_starts[s1] * analyzer.frame_ms
        if idx > 0 and ts < out[-1]["end"]:
            ts = out[-1]["end"]
        if te - ts < 65:
            te = ts + 70
        # Trim trailing silence/breath before the next word if the token is long
        if idx < m - 1 and te - ts > 380:
            _, clamped_e = analyzer.clamp_token_silence(ts, te, min_dur=140)
            if ts + 140 <= clamped_e < te - 40:
                te = clamped_e
        elif idx == m - 1 and te - ts > 1400:
            _, clamped_e = analyzer.clamp_token_silence(ts, te, min_dur=220)
            te = max(ts + 220, min(te, clamped_e))
        out.append({
            "text": c["text"],
            "start": int(ts),
            "end": int(te),
            "space": c.get("space", False),
        })
    return out


def allocate_chars_to_islands(norm_chars: list[dict], islands: list[tuple[int, int]],
                              toks: list[dict], analyzer: VocalAnalyzer) -> list[dict]:
    m = len(norm_chars)
    if m == 0:
        return []

    while len(islands) > m:
        best_idx = min(range(len(islands) - 1), key=lambda k: islands[k + 1][0] - islands[k][1])
        islands[best_idx] = (islands[best_idx][0], islands[best_idx + 1][1])
        islands.pop(best_idx + 1)

    if len(islands) == 1:
        char_groups = [(norm_chars, islands[0])]
    else:
        w_weights = [word_weight(c, idx, m) for idx, c in enumerate(norm_chars)]
        cum_w = np.cumsum(w_weights) / sum(w_weights)
        isl_durs = [max(80, b - a) for a, b in islands]
        cum_d = np.cumsum(isl_durs) / sum(isl_durs)
        splits = [0]
        for k_isl in range(len(islands) - 1):
            target = cum_d[k_isl]
            lo_idx = splits[-1] + 1
            hi_idx = m - (len(islands) - 1 - k_isl)
            best_split = min(range(lo_idx, hi_idx + 1), key=lambda idx: abs(cum_w[idx - 1] - target))
            splits.append(best_split)
        splits.append(m)
        char_groups = [
            (norm_chars[splits[k]:splits[k + 1]], islands[k])
            for k in range(len(islands))
        ]

    out = []
    for sub_chars, (span_s, span_e) in char_groups:
        sub_m = len(sub_chars)
        if sub_m == 0:
            continue

        sub_syls = sum(count_syllables(c["text"]) for c in sub_chars)
        # Only cap span_e when the window includes a multi-second instrumental/vocalize interlude after the lyrics
        if span_e - span_s > sub_syls * 850 + 900:
            span_e = span_s + sub_syls * 540 + 500
            _, clamped_e = analyzer.clamp_token_silence(span_s, span_e)
            if clamped_e > span_s + sub_syls * 180:
                span_e = clamped_e

        sub_toks = [t for t in toks if t["end"] > span_s and t["start"] < span_e]

        if len(sub_toks) == sub_m and _tokens_match_chars(sub_chars, sub_toks, min_ratio=72.0):
            valid_1to1 = True
            cand_out = []
            for idx, (c, t) in enumerate(zip(sub_chars, sub_toks)):
                ts, te = analyzer.clamp_token_silence(t["start"], t["end"])
                if idx > 0 and ts < cand_out[-1]["end"]:
                    ts = cand_out[-1]["end"]
                if te - ts < 65:
                    te = ts + 75
                if te - ts > 2400:
                    te = ts + 1800
                if analyzer.mean_db(ts, te) < -44.5:
                    valid_1to1 = False
                    break
                cand_out.append({
                    "text": c["text"],
                    "start": int(ts),
                    "end": int(te),
                    "space": c.get("space", False),
                })
            if valid_1to1 and len({t["end"] - t["start"] for t in cand_out}) > 1:
                out.extend(cand_out)
                continue

        cur_s = max(span_s, out[-1]["end"] + 10 if out else span_s)
        cur_e = max(cur_s + sub_m * 80, span_e)
        aligned_sub = align_words_in_island(sub_chars, cur_s, cur_e, sub_toks, analyzer)
        out.extend(aligned_sub)

    return out


def align_line_with_clauses(line_text: str, toks: list[dict],
                            fallback_start: int, fallback_end: int,
                            analyzer: VocalAnalyzer) -> list[dict]:
    norm_chars = tokenize_tokens(line_text)
    if not norm_chars:
        return []

    compat_toks = _filter_script_compatible_tokens(line_text, toks)
    voiced_toks = [
        t for t in compat_toks
        if fallback_start - 350 <= t["start"] < fallback_end
        and analyzer.mean_db(t["start"], t["end"]) >= -44.0
    ]
    asr_matches_line = _tokens_match_chars(norm_chars, voiced_toks, min_ratio=65.0)

    win_s = min(fallback_start, voiced_toks[0]["start"]) if (voiced_toks and asr_matches_line) else fallback_start
    win_e = fallback_end

    if not voiced_toks and analyzer.rms_db is not None:
        if analyzer.mean_db(fallback_start, fallback_end) < -44.0:
            s_idx = analyzer._idx(fallback_start)
            e_idx = max(s_idx + 1, analyzer._idx(fallback_end))
            if float(np.percentile(analyzer.rms_db[s_idx:e_idx], 90)) < -42.0:
                print(f"  [!] Dropping phantom LRC line in vocal silence [{fmt_time(fallback_start)}]: {line_text!r}")
                return []

    islands = analyzer.active_islands(win_s, win_e)

    # Drop tiny trailing pickup islands at the very end of a long window (which belong to the next line's vocal pickup)
    total_syls = sum(count_syllables(c["text"]) for c in norm_chars)
    while len(islands) > 1:
        prev_dur = sum(b - a for a, b in islands[:-1])
        last_a, last_b = islands[-1]
        gap = last_a - islands[-2][1]
        if prev_dur >= total_syls * 500 and (last_b - last_a < 380 or gap > 450):
            islands.pop()
        elif win_e - last_a < 320 and last_b - last_a < 260 and gap >= 180:
            islands.pop()
        else:
            break

    if asr_matches_line and len(voiced_toks) >= max(2, len(norm_chars) // 2):
        asr_s = voiced_toks[0]["start"]
        asr_e = voiced_toks[-1]["end"]
        _, clamped_asr_e = analyzer.clamp_token_silence(voiced_toks[-1]["start"], asr_e)
        max_e = max(clamped_asr_e + 350, fallback_start + total_syls * 320)
        filtered_isl = [
            (max(a, min(fallback_start, asr_s - 80)), min(b, max_e))
            for a, b in islands
            if b > min(fallback_start, asr_s) and a < max_e
        ]
        valid_isl = [(a, b) for a, b in filtered_isl if b - a >= 80 and analyzer.mean_db(a, b) >= -43.5]
        if valid_isl:
            islands = valid_isl

    line_toks = allocate_chars_to_islands(
        norm_chars,
        islands,
        voiced_toks if asr_matches_line else [],
        analyzer,
    )
    if line_toks:
        add_phonetics(line_toks)
    return line_toks


def enforce_non_overlapping_lines(lines: list[dict]):
    """Ensure lines on the same vocal track (lead vs backing) never bleed into the next line."""
    for track_is_backing in (False, True):
        track_lines = [ln for ln in lines if bool(ln.get("is_backing", False)) == track_is_backing]
        for idx in range(len(track_lines) - 1):
            cur_ln = track_lines[idx]
            nxt_ln = track_lines[idx + 1]
            nxt_start = nxt_ln["tokens"][0]["start"]
            cur_end = cur_ln["tokens"][-1]["end"]
            if cur_end > nxt_start - 20:
                target_end = max(cur_ln["tokens"][0]["start"] + len(cur_ln["tokens"]) * 65, nxt_start - 30)
                span_s = cur_ln["tokens"][0]["start"]
                old_dur = max(1, cur_end - span_s)
                new_dur = max(len(cur_ln["tokens"]) * 65, target_end - span_s)
                scale = new_dur / old_dur
                for t in cur_ln["tokens"]:
                    t["start"] = int(round(span_s + (t["start"] - span_s) * scale))
                    t["end"] = int(round(span_s + (t["end"] - span_s) * scale))
                    if t["end"] - t["start"] < 62:
                        t["end"] = t["start"] + 65
                cur_ln["start"] = cur_ln["tokens"][0]["start"]
                cur_ln["end"] = cur_ln["tokens"][-1]["end"]


def align_lrc_with_tokens(lrc_lines, tokens, analyzer: VocalAnalyzer | None = None):
    """
    Marry synchronized lines with FunASR acoustic tokens and vocal energy.
    Uses phonetic sliding-window anchor matching to resolve drifted timestamps.
    """
    if not lrc_lines:
        return []
    if analyzer is None:
        analyzer = VocalAnalyzer(None)

    first_line_start = lrc_lines[0]["start"]
    valid_tokens = [t for t in tokens if t["start"] >= first_line_start - 600]

    for t in valid_tokens:
        if "pinyin" not in t:
            t["pinyin"] = lazy_pinyin(t["text"])[0] if t["text"].strip() else ""

    windows = []
    i = 0
    while i < len(lrc_lines):
        cur_start = lrc_lines[i]["start"]
        j = i + 1
        while j < len(lrc_lines) and lrc_lines[j]["start"] == cur_start:
            j += 1
        windows.append(lrc_lines[i:j])
        i = j

    anchors = []
    for w_idx, win in enumerate(windows):
        combined_text = " ".join(it["text"] for it in win)
        c_chars = tokenize_text(combined_text)
        c_py = "".join(_token_phonetic_str(ch) for ch in c_chars)
        nom_s = win[0]["start"]

        compat_tokens = _filter_script_compatible_tokens(combined_text, valid_tokens)
        cands = [t for t in compat_tokens if nom_s - 1500 <= t["start"] <= nom_s + 2200]
        best_sub = None
        best_r = -1.0
        if c_chars and cands:
            for span_len in range(max(1, len(c_chars) - 2), min(len(cands) + 1, len(c_chars) + 3)):
                for k in range(len(cands) - span_len + 1):
                    sub = cands[k:k + span_len]
                    sub_py = "".join(_token_phonetic_str(t["text"]) for t in sub)
                    if not sub_py:
                        continue
                    r = fuzz.ratio(c_py, sub_py)
                    if r > best_r:
                        best_r = r
                        best_sub = sub

        first_ok = (
            best_sub is not None
            and len(c_chars) > 0
            and fuzz.ratio(_token_phonetic_str(c_chars[0]), _token_phonetic_str(best_sub[0]["text"])) >= 60.0
        )
        if best_sub and best_r >= 72.0 and first_ok:
            anchors.append({"start": best_sub[0]["start"], "end": best_sub[-1]["end"], "conf": True})
        else:
            anchors.append({"start": nom_s, "end": None, "conf": False})

    prev_s = 0
    for idx in range(len(anchors)):
        if anchors[idx]["start"] < prev_s:
            anchors[idx]["start"] = prev_s + 500
            anchors[idx]["conf"] = False
        prev_s = anchors[idx]["start"]

    win_tokens = [[] for _ in windows]
    for tok in valid_tokens:
        t_start = tok["start"]
        best_w = 0
        for w_idx in range(len(windows)):
            w_start = anchors[w_idx]["start"]
            if t_start >= w_start - 250:
                best_w = w_idx
            else:
                break
        win_tokens[best_w].append(tok)

    out_lines = []
    for w_idx, win in enumerate(windows):
        toks = win_tokens[w_idx]
        anc_s = anchors[w_idx]["start"]
        next_start = anchors[w_idx + 1]["start"] if w_idx + 1 < len(windows) else anc_s + 6000

        if len(win) == 1:
            item = win[0]
            tok_list = align_line_with_clauses(item["text"], toks, anc_s, next_start, analyzer)
            if tok_list:
                out_lines.append({
                    "start": tok_list[0]["start"],
                    "end": tok_list[-1]["end"],
                    "is_backing": item.get("is_backing", False),
                    "tokens": tok_list,
                })
        else:
            char_counts = [len(tokenize_text(it["text"])) for it in win]
            total_chars = max(1, sum(char_counts))

            tok_offset = 0
            time_offset = anc_s
            window_duration = next_start - time_offset

            for it_idx, item in enumerate(win):
                c_cnt = char_counts[it_idx]
                frac = c_cnt / total_chars

                if it_idx == len(win) - 1:
                    sub_toks = toks[tok_offset:]
                else:
                    take_count = round(len(toks) * frac)
                    sub_toks = toks[tok_offset:tok_offset + take_count]
                    tok_offset += take_count

                sub_start = int(time_offset)
                sub_end = int(time_offset + window_duration * frac)
                time_offset = sub_end

                tok_list = align_line_with_clauses(item["text"], sub_toks, sub_start, sub_end, analyzer)
                if tok_list:
                    out_lines.append({
                        "start": tok_list[0]["start"],
                        "end": tok_list[-1]["end"],
                        "is_backing": item.get("is_backing", False),
                        "tokens": tok_list,
                    })

    enforce_non_overlapping_lines(out_lines)
    return out_lines


def align_plain_lyrics_with_tokens(plain_lines: list[str], tokens: list[dict], analyzer: VocalAnalyzer | None = None) -> list[dict]:
    """
    Align plain unsynchronized lyrics text lines against ASR tokens and vocal acoustic energy.
    Matches lines sequentially across tokens using character/phonetic string similarity.
    """
    clean_lines = [ln.strip() for ln in plain_lines if ln.strip()]
    if not clean_lines:
        return []
    if analyzer is None:
        analyzer = VocalAnalyzer(None)

    cur_tok_idx = 0
    anchors = []
    for line in clean_lines:
        norm_chars = tokenize_text(line)
        m_chars = len(norm_chars)
        if m_chars == 0:
            continue
        line_str = "".join(norm_chars).lower()

        best_r = -1
        best_span = (cur_tok_idx, cur_tok_idx)
        window = tokens[cur_tok_idx : cur_tok_idx + 45] if tokens else []
        for span_len in range(max(1, m_chars - 3), min(len(window) + 1, m_chars + 6)):
            for k in range(len(window) - span_len + 1):
                sub = window[k : k + span_len]
                sub_str = "".join(t["text"] for t in sub).lower()
                r = fuzz.ratio(line_str, sub_str)
                if r > best_r:
                    best_r = r
                    best_span = (cur_tok_idx + k, cur_tok_idx + k + span_len)

        if best_r >= 45 and tokens:
            s_idx, e_idx = best_span
            anchors.append({
                "text": line,
                "start": tokens[s_idx]["start"],
                "end": tokens[e_idx - 1]["end"],
                "tokens": tokens[s_idx:e_idx],
                "conf": True,
            })
            cur_tok_idx = e_idx
        else:
            last_end = anchors[-1]["end"] if anchors else (tokens[0]["start"] if tokens else 1000)
            est_dur = max(1200, m_chars * 260)
            anchors.append({
                "text": line,
                "start": last_end + 300,
                "end": last_end + 300 + est_dur,
                "tokens": [],
                "conf": False,
            })

    out_lines = []
    for idx, anc in enumerate(anchors):
        next_s = anchors[idx + 1]["start"] if idx + 1 < len(anchors) else anc["end"] + 1500
        tok_list = align_line_with_clauses(anc["text"], anc["tokens"], anc["start"], next_s, analyzer)
        if tok_list:
            out_lines.append({
                "start": tok_list[0]["start"],
                "end": tok_list[-1]["end"],
                "is_backing": False,
                "tokens": tok_list,
            })

    enforce_non_overlapping_lines(out_lines)
    return out_lines


def group_lines(tokens, gap_ms=800, max_len=16):
    lines, cur = [], []
    for t in tokens:
        if cur and (t["start"] - cur[-1]["end"] > gap_ms or len(cur) >= max_len):
            lines.append(cur)
            cur = []
        cur.append(t)
    if cur:
        lines.append(cur)
    return [{"start": l[0]["start"], "end": l[-1]["end"], "is_backing": False, "tokens": l} for l in lines]


def fmt_time(ms: int) -> str:
    s = ms / 1000
    return f"{int(s // 60):02d}:{s % 60:05.2f}"


def write_outputs(vid: str, lines: list[dict], workdir: Path):
    jpath = workdir / f"{vid}.json"
    jpath.write_text(json.dumps(lines, ensure_ascii=False, indent=2), encoding="utf-8")

    out = []
    for ln in lines:
        prefix = "[Backing] " if ln.get("is_backing") else ""
        en = "-".join(t.get("en", t["text"]) for t in ln["tokens"])
        py = " ".join(t.get("pinyin", t["text"]) for t in ln["tokens"])
        zh = "".join(t["text"] + (" " if t.get("space") else "") for t in ln["tokens"]).strip()
        out.append(f"[{fmt_time(ln['start'])}] {prefix}{en}\n          {py}\n          {zh}\n")
    tpath = workdir / f"{vid}.txt"
    tpath.write_text("\n".join(out), encoding="utf-8")
    return jpath, tpath
