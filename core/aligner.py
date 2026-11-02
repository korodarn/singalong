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
import torch
import torchaudio
import torchaudio.functional as taf

from core.models import LineDiagnostics, TokenDiagnostics, DiagnosticFlag
from core.phonetics import (
    add_phonetics,
    apply_korean_phonology,
    decompose_hangul,
    has_kana,
    is_hangul,
    is_han,
    is_kana,
    tokenize_tokens,
    tokenize_text,
)
from core.phonetics.ja import SUNG_KANJI_OVERRIDES, _get_kakasi, respell_hira_to_romaji_and_en, split_morae


class VocalAnalyzer:
    """10ms-resolution vocal RMS (dBFS), spectral flatness (breath vs voice), and onset curve."""

    def __init__(self, wav_path: Path | None, frame_ms: int = 10):
        self.frame_ms = frame_ms
        self.wav_path: Path | None = wav_path
        self.rms_db: np.ndarray | None = None
        self.onset: np.ndarray | None = None
        self.spectral_flatness: np.ndarray | None = None
        self.data_16k: np.ndarray | None = None

        if wav_path and wav_path.exists():
            data, sr = sf.read(str(wav_path), dtype="float32")
            if data.ndim > 1:
                data = np.mean(data, axis=1)

            if sr != 16000:
                self.data_16k = librosa.resample(data, orig_sr=sr, target_sr=16000)
            else:
                self.data_16k = data

            hop = max(1, int(sr * frame_ms / 1000))
            win = hop * 2
            rms = librosa.feature.rms(y=data, frame_length=win, hop_length=hop, center=True)[0]
            self.rms_db = 20.0 * np.log10(np.maximum(rms, 1e-6))

            flatness_raw = librosa.feature.spectral_flatness(y=data, hop_length=hop, n_fft=win)[0]
            if len(flatness_raw) < len(self.rms_db):
                flatness_raw = np.pad(flatness_raw, (0, len(self.rms_db) - len(flatness_raw)))
            else:
                flatness_raw = flatness_raw[:len(self.rms_db)]
            self.spectral_flatness = flatness_raw

            onset_raw = librosa.onset.onset_strength(y=data, sr=sr, hop_length=hop, center=True)
            if len(onset_raw) < len(self.rms_db):
                onset_raw = np.pad(onset_raw, (0, len(self.rms_db) - len(onset_raw)))
            else:
                onset_raw = onset_raw[:len(self.rms_db)]
            p95 = float(np.percentile(onset_raw, 95)) if len(onset_raw) else 1.0
            self.onset = np.clip(onset_raw / max(p95, 1e-4), 0.0, 2.0)

    def is_breath(self, start_ms: int, end_ms: int) -> bool:
        """Return True if slice [start_ms, end_ms] represents an unvoiced breath intake rather than voiced singing."""
        if self.rms_db is None or self.spectral_flatness is None or end_ms <= start_ms:
            return False
        s = self._idx(start_ms)
        e = max(s + 1, self._idx(end_ms))
        sub_rms = self.rms_db[s:e]
        sub_flat = self.spectral_flatness[s:e]
        mean_db = float(np.mean(sub_rms))
        mean_flat = float(np.mean(sub_flat))
        return -40.0 <= mean_db <= -22.0 and mean_flat > 0.38

    def find_voiced_attack(self, start_ms: int, search_window_ms: int = 700) -> tuple[int, int]:
        """
        Search forward from nominal start_ms for the first voiced (non-breath) attack,
        skipping leading breath intakes. Returns (adjusted_start_ms, trimmed_breath_ms).
        """
        if self.rms_db is None or self.spectral_flatness is None:
            return start_ms, 0
        s = self._idx(start_ms)
        max_idx = min(len(self.rms_db), s + int(search_window_ms / self.frame_ms))
        for idx in range(s, max_idx):
            db = self.rms_db[idx]
            flat = self.spectral_flatness[idx]
            if db >= -34.0 and flat < 0.35:
                w_s = max(0, idx - 2)
                w_e = min(len(self.onset), idx + 5) if self.onset is not None else idx + 1
                peak_offset = int(np.argmax(self.onset[w_s:w_e])) if self.onset is not None and w_e > w_s else 0
                best_idx = w_s + peak_offset
                adj_ms = int(best_idx * self.frame_ms)
                trimmed = max(0, adj_ms - start_ms)
                return adj_ms, trimmed
        return start_ms, 0

    def find_elastic_inter_line_boundary(
        self,
        nom_boundary_ms: int,
        search_back_ms: int = 1200,
        search_fwd_ms: int = 2800,
    ) -> tuple[int, dict]:
        """
        Negotiate an elastic boundary between adjacent lines, sliding past held sustained vowels
        and snapping into the acoustic breath/silence trough right before the next phrase onset.
        """
        if self.rms_db is None:
            return nom_boundary_ms, {"shift_ms": 0, "reason": "no_audio"}
        s_ms = max(0, nom_boundary_ms - search_back_ms)
        e_ms = min(len(self.rms_db) * self.frame_ms, nom_boundary_ms + search_fwd_ms)
        s_idx = self._idx(s_ms)
        e_idx = max(s_idx + 1, self._idx(e_ms))

        best_cost = 1e9
        best_t = nom_boundary_ms
        best_idx = self._idx(nom_boundary_ms)

        for idx in range(s_idx, e_idx):
            t_ms = idx * self.frame_ms
            rms = self.rms_db[idx]
            flat = self.spectral_flatness[idx] if self.spectral_flatness is not None else 0.2
            next_ons = float(np.max(self.onset[idx:min(len(self.onset), idx + 4)])) if self.onset is not None else 0.0

            # Energy cost: high RMS means singing vowel, should not cut here
            w_energy = 1.0 * (rms + 25.0) / 10.0
            # Flatness bonus: high spectral flatness means breath intake / unvoiced trough
            w_flat = -0.8 * flat
            # Onset bonus: right before next consonant attack
            w_onset = -1.2 * next_ons
            # Prior penalty keeps boundary reasonable
            w_prior = 0.5 * ((t_ms - nom_boundary_ms) / 2000.0) ** 2

            cost = w_energy + w_flat + w_onset + w_prior
            if cost < best_cost:
                best_cost = cost
                best_t = t_ms
                best_idx = idx

        shift = best_t - nom_boundary_ms
        return best_t, {
            "shift_ms": shift,
            "cost": float(best_cost),
            "rms_at_boundary": float(self.rms_db[best_idx]),
        }

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
    """Estimate spoken/sung syllable count for CJK characters, Japanese morae, or English words."""
    if not txt:
        return 1
    if has_kana(txt):
        kks = _get_kakasi()
        hira = "".join(it.get("hira", "") for it in kks.convert(txt))
        return max(1, len(split_morae(hira)))
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
    if has_kana(txt):
        ns = count_syllables(txt)
        w = 0.45 + 0.55 * ns
    elif len(txt) == 1 and is_hangul(txt):
        w = 1.0
        _, v_idx, f_idx = decompose_hangul(txt)
        if f_idx != 0:
            w += 0.18
        if v_idx in (2, 3, 6, 7, 9, 10, 11, 12, 14, 15, 16, 17, 19):
            w += 0.12
    elif len(txt) == 1 and is_han(txt):
        w = 1.0 + (0.08 * ((ord(txt) % 3) - 1))
    elif any(is_han(ch) for ch in txt):
        # Multi-character Kanji compound in Japanese
        w = 0.45 + 0.65 * len(txt)
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


def _token_phonetic_str(txt: str, is_ja: bool = False) -> str:
    if not txt:
        return ""
    if is_ja or has_kana(txt):
        if txt in SUNG_KANJI_OVERRIDES:
            return re.sub(r"[^a-z0-9]", "", SUNG_KANJI_OVERRIDES[txt][0].lower())
        kks = _get_kakasi()
        hira = "".join(it.get("hira", "") for it in kks.convert(txt))
        rom, _ = respell_hira_to_romaji_and_en(hira)
        return re.sub(r"[^a-z0-9]", "", rom.lower())
    if any(is_hangul(ch) for ch in txt):
        ko_toks = [{"text": ch} for ch in txt if is_hangul(ch)]
        if ko_toks:
            apply_korean_phonology(ko_toks)
            return re.sub(r"[^a-z0-9]", "", "".join(t.get("pinyin", "") for t in ko_toks).lower())
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
    is_ja = any(has_kana(c.get("text", "")) for c in chars) or any(has_kana(t.get("text", "")) for t in toks)
    c_py = "".join(_token_phonetic_str(c["text"], is_ja=is_ja) for c in chars)
    t_py = "".join(_token_phonetic_str(t["text"], is_ja=is_ja) for t in toks)
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
            cp = _token_phonetic_str(c["text"], is_ja=is_ja)
            tp = _token_phonetic_str(t["text"], is_ja=is_ja)
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
        is_ja = any(has_kana(c.get("text", "")) for c in sub_chars) or any(has_kana(t.get("text", "")) for t in sub_toks)
        for c in sub_chars[1:]:
            cp = _token_phonetic_str(c["text"], is_ja=is_ja)
            if not cp:
                continue
            for t in sub_toks:
                tp = _token_phonetic_str(t["text"], is_ja=is_ja)
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
                            analyzer: VocalAnalyzer,
                            drop_silent: bool = True) -> list[dict]:
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

    if drop_silent and not voiced_toks and analyzer.rms_db is not None:
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
                if "diag" in cur_ln:
                    orig_s = cur_ln["diag"].get("lrc_nominal_start")
                    orig_e = cur_ln["diag"].get("lrc_nominal_end")
                    if orig_s is not None:
                        cur_ln["diag"]["shift_from_lrc_start_ms"] = cur_ln["start"] - orig_s
                    if orig_e is not None:
                        cur_ln["diag"]["shift_from_lrc_end_ms"] = cur_ln["end"] - orig_e


_MMS_MODEL = None
_MMS_DICT = None


def get_mms_aligner(device: str = "cpu"):
    global _MMS_MODEL, _MMS_DICT
    if _MMS_MODEL is None:
        try:
            bundle = torchaudio.pipelines.MMS_FA
            _MMS_DICT = bundle.get_dict()
            model = bundle.get_model()
            model.eval()
            _MMS_MODEL = model.to(device)
        except Exception as e:
            print(f"  [!] Failed to load MMS_FA model: {e}")
            return None, None
    return _MMS_MODEL, _MMS_DICT


def forced_align_with_mms(
    words: list[dict],
    span_s: int,
    span_e: int,
    analyzer: VocalAnalyzer,
    is_ja: bool = False,
    device: str = "cpu",
) -> list[dict] | None:
    if not words or analyzer.data_16k is None or span_e <= span_s:
        return None

    model, dictionary = get_mms_aligner(device=device)
    if model is None or dictionary is None:
        return None

    word_tokens: list[list[int]] = []
    for w in words:
        txt = w.get("text", "")
        phon = _token_phonetic_str(txt, is_ja=is_ja).lower()
        t_ids = [dictionary[ch] for ch in phon if ch in dictionary]
        if not t_ids:
            clean_ascii = re.sub(r"[^a-z']", "", txt.lower())
            t_ids = [dictionary[ch] for ch in clean_ascii if ch in dictionary]
        if not t_ids:
            t_ids = [dictionary.get("a", 1)]
        word_tokens.append(t_ids)

    flat_tokens: list[int] = []
    word_ranges: list[tuple[int, int]] = []
    curr = 0
    for w_ids in word_tokens:
        flat_tokens.extend(w_ids)
        word_ranges.append((curr, curr + len(w_ids)))
        curr += len(w_ids)

    if not flat_tokens:
        return None

    buf_s_ms = max(0, span_s - 150)
    buf_e_ms = min(len(analyzer.data_16k) * 1000 // 16000, span_e + 200)
    sample_s = int(buf_s_ms * 16)
    sample_e = int(buf_e_ms * 16)
    audio_slice = analyzer.data_16k[sample_s:sample_e]

    min_samples = max(len(flat_tokens) * 2 * 320, 1600)
    if len(audio_slice) < min_samples:
        return None

    wav_t = torch.from_numpy(audio_slice).unsqueeze(0).to(device)

    try:
        with torch.inference_mode():
            emission, _ = model(wav_t)
            targets = torch.tensor([flat_tokens], dtype=torch.int32, device=device)
            input_lengths = torch.tensor([emission.size(1)], dtype=torch.int32, device=device)
            target_lengths = torch.tensor([len(flat_tokens)], dtype=torch.int32, device=device)

            aligned_tokens, scores = taf.forced_align(
                emission, targets, input_lengths, target_lengths
            )
            spans = taf.merge_tokens(aligned_tokens[0], scores[0])
    except Exception:
        return None

    if len(spans) != len(flat_tokens):
        return None

    num_frames = emission.size(1)
    num_samples = len(audio_slice)
    samples_per_frame = num_samples / num_frames

    def frame_to_ms(f: int) -> int:
        return buf_s_ms + int(round(f * samples_per_frame / 16.0))

    out_tokens: list[dict] = []
    m = len(words)

    for idx, w in enumerate(words):
        w_s_char, w_e_char = word_ranges[idx]
        w_spans = spans[w_s_char:w_e_char]

        w_start_ms = frame_to_ms(w_spans[0].start)
        w_end_ms = frame_to_ms(w_spans[-1].end)
        mean_ctc_log = float(np.mean([s.score for s in w_spans]))
        ctc_prob = float(np.exp(mean_ctc_log))

        if analyzer.onset is not None:
            f_ms = analyzer._idx(w_start_ms)
            w_lo = max(0, f_ms - 2)
            w_hi = min(len(analyzer.onset), f_ms + 4)
            if w_hi > w_lo:
                best_peak_idx = w_lo + int(np.argmax(analyzer.onset[w_lo:w_hi]))
                if analyzer.onset[best_peak_idx] > 0.35:
                    w_start_ms = best_peak_idx * analyzer.frame_ms

        if out_tokens and w_start_ms < out_tokens[-1]["end"] + 10:
            w_start_ms = out_tokens[-1]["end"] + 10

        if w_end_ms <= w_start_ms + 60:
            w_end_ms = w_start_ms + 75

        # Blank-Frame Acoustic Vowel Tail Extension
        next_nominal_s = frame_to_ms(spans[word_ranges[idx + 1][0]].start) if idx + 1 < m else span_e + 100
        ceiling_ms = min(next_nominal_s - 25, w_start_ms + 5000)

        orig_end = w_end_ms
        sustain_ext = 0
        if w_end_ms < ceiling_ms:
            curr_e = w_end_ms
            while curr_e + 40 <= ceiling_ms:
                slice_db = analyzer.mean_db(curr_e, curr_e + 40)
                if slice_db >= -34.0:
                    curr_e += 40
                else:
                    break
            if curr_e > orig_end:
                sustain_ext = curr_e - orig_end
                w_end_ms = curr_e

        tok_rms = analyzer.mean_db(w_start_ms, w_end_ms)
        ons_val = float(analyzer.onset[analyzer._idx(w_start_ms)]) if analyzer.onset is not None else 1.0

        flags = []
        if ctc_prob < 0.25:
            flags.append({
                "code": "LOW_CTC_SCORE",
                "severity": "warning",
                "detail": f"Low CTC forced-alignment confidence ({ctc_prob:.2f})",
            })
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
                "source": "mms_fa",
                "ctc_score": round(ctc_prob, 3),
                "rms_db": round(tok_rms, 1),
                "onset_strength": round(ons_val, 2),
                "sustain_extended_ms": sustain_ext,
                "flags": flags,
            },
        })

    return out_tokens


def estimate_global_lrc_drift(
    lrc_lines: list[dict],
    tokens: list[dict],
    is_ja: bool = False,
    max_search_dist_ms: int = 15000,
) -> tuple[float, float, list[dict]]:
    """
    Fit a robust offset model Δt(t) = slope * t + intercept using RANSAC
    across high-confidence phonetic matches between LRC lines and ASR tokens.
    Returns (slope, intercept, matched_anchors).
    """
    if not lrc_lines or not tokens:
        return 0.0, 0.0, []

    anchors = []
    for ln in lrc_lines:
        nom_s = ln.get("start", 0)
        txt = ln.get("text", "")
        if not txt.strip():
            continue
        c_chars = tokenize_text(txt, lang="ja" if is_ja else None)
        c_py = "".join(_token_phonetic_str(ch, is_ja=is_ja) for ch in c_chars)
        if not c_py:
            continue

        cands = [t for t in tokens if nom_s - max_search_dist_ms <= t["start"] <= nom_s + max_search_dist_ms]
        if not cands:
            continue

        best_sub = None
        best_r = -1.0
        for span_len in range(max(1, len(c_chars) - 2), min(len(cands) + 1, len(c_chars) + 3)):
            for k in range(len(cands) - span_len + 1):
                sub = cands[k:k + span_len]
                sub_py = "".join(_token_phonetic_str(t["text"], is_ja=is_ja) for t in sub)
                if not sub_py:
                    continue
                r = fuzz.ratio(c_py, sub_py)
                if r > best_r:
                    best_r = r
                    best_sub = sub

        if best_sub and best_r >= 72.0:
            anchors.append({
                "lrc_start": nom_s,
                "asr_start": best_sub[0]["start"],
                "offset": best_sub[0]["start"] - nom_s,
                "score": best_r,
            })

    if len(anchors) < 3:
        if len(anchors) >= 1:
            med_off = float(np.median([a["offset"] for a in anchors]))
            if abs(med_off) >= 120:
                return 0.0, med_off, anchors
        return 0.0, 0.0, anchors

    offsets = np.array([a["offset"] for a in anchors], dtype=np.float64)
    lrc_times = np.array([a["lrc_start"] for a in anchors], dtype=np.float64)

    best_inliers = []
    best_intercept = float(np.median(offsets))
    best_slope = 0.0

    med_off = float(np.median(offsets))
    const_inliers = [a for a in anchors if abs(a["offset"] - med_off) <= 350]
    if len(const_inliers) > len(best_inliers):
        best_inliers = const_inliers
        best_intercept = med_off
        best_slope = 0.0

    rng = np.random.RandomState(42)
    n_pts = len(anchors)
    for _ in range(min(50, n_pts * (n_pts - 1) // 2)):
        i, j = rng.choice(n_pts, 2, replace=False)
        dt = lrc_times[j] - lrc_times[i]
        if abs(dt) < 15000:
            continue
        slope = (offsets[j] - offsets[i]) / dt
        if abs(slope) > 0.02:
            continue
        intercept = offsets[i] - slope * lrc_times[i]
        pred_offsets = slope * lrc_times + intercept
        inliers = [a for idx, a in enumerate(anchors) if abs(offsets[idx] - pred_offsets[idx]) <= 320]
        if len(inliers) > len(best_inliers):
            best_inliers = inliers
            best_slope = float(slope)
            best_intercept = float(intercept)

    if len(best_inliers) >= max(2, int(0.4 * len(anchors))) and abs(best_intercept) >= 120:
        return best_slope, best_intercept, best_inliers
    return 0.0, 0.0, anchors


def align_lrc_with_tokens(lrc_lines, tokens, analyzer: VocalAnalyzer | None = None):
    """
    Marry synchronized lines with FunASR/Whisper acoustic tokens, Meta MMS-FA forced alignment,
    and vocal energy telemetry. Uses elastic inter-line boundary negotiation and RANSAC drift correction.
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

    is_ja_song = any(has_kana(it.get("text", "")) for it in lrc_lines) or any(
        has_kana(t.get("text", "")) for t in valid_tokens
    )

    # 1. Global & Piecewise Linear Drift Correction (RANSAC)
    slope, intercept, drift_inliers = estimate_global_lrc_drift(lrc_lines, valid_tokens, is_ja=is_ja_song)
    if slope != 0.0 or abs(intercept) >= 120:
        print(f"  [*] Global LRC Drift corrected (slope={slope:.4f}, offset={intercept:+.0f}ms, inliers={len(drift_inliers)}/{len(lrc_lines)})")

    processed_lrc = []
    for it in lrc_lines:
        orig_s = it["start"]
        shifted_s = max(0, int(round(orig_s + slope * orig_s + intercept))) if (slope != 0.0 or abs(intercept) >= 120) else orig_s
        item_copy = dict(it)
        item_copy["_orig_start"] = orig_s
        item_copy["start"] = shifted_s
        processed_lrc.append(item_copy)

    # 2. Group into simultaneous / overlapping windows
    windows = []
    i = 0
    while i < len(processed_lrc):
        cur_start = processed_lrc[i]["start"]
        j = i + 1
        while j < len(processed_lrc) and processed_lrc[j]["start"] == cur_start:
            j += 1
        windows.append(processed_lrc[i:j])
        i = j

    anchors = []
    for w_idx, win in enumerate(windows):
        combined_text = " ".join(it["text"] for it in win)
        c_chars = tokenize_text(combined_text, lang="ja" if is_ja_song else None)
        c_py = "".join(_token_phonetic_str(ch, is_ja=is_ja_song) for ch in c_chars)
        nom_s = win[0]["start"]

        compat_tokens = _filter_script_compatible_tokens(combined_text, valid_tokens)
        cands = [t for t in compat_tokens if nom_s - 2500 <= t["start"] <= nom_s + 3500]
        best_sub = None
        best_r = -1.0
        if c_chars and cands:
            for span_len in range(max(1, len(c_chars) - 2), min(len(cands) + 1, len(c_chars) + 3)):
                for k in range(len(cands) - span_len + 1):
                    sub = cands[k:k + span_len]
                    sub_py = "".join(_token_phonetic_str(t["text"], is_ja=is_ja_song) for t in sub)
                    if not sub_py:
                        continue
                    r = fuzz.ratio(c_py, sub_py)
                    if r > best_r:
                        best_r = r
                        best_sub = sub

        first_ok = (
            best_sub is not None
            and len(c_chars) > 0
            and fuzz.ratio(
                _token_phonetic_str(c_chars[0], is_ja=is_ja_song),
                _token_phonetic_str(best_sub[0]["text"], is_ja=is_ja_song),
            )
            >= 60.0
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
    line_idx_counter = 0

    for w_idx, win in enumerate(windows):
        toks = win_tokens[w_idx]
        anc_s = anchors[w_idx]["start"]
        next_nominal = windows[w_idx + 1][0]["start"] if w_idx + 1 < len(windows) else None

        # Negotiate elastic inter-line boundary
        if next_nominal is not None:
            elastic_end, b_diag = analyzer.find_elastic_inter_line_boundary(next_nominal)
        else:
            elastic_end = anc_s + 6000
            b_diag = {"shift_ms": 0}

        for item_idx, item in enumerate(win):
            line_text = item["text"]
            orig_lrc_s = item.get("_orig_start", item["start"])
            norm_chars = tokenize_tokens(line_text)
            if not norm_chars:
                continue

            # Leading breath rejection
            adj_s, lead_breath_trimmed = analyzer.find_voiced_attack(anc_s)

            # Attempt MMS-FA Forced Alignment
            mms_toks = forced_align_with_mms(
                norm_chars,
                adj_s,
                elastic_end,
                analyzer,
                is_ja=is_ja_song,
                device="cuda" if torch.cuda.is_available() else "cpu",
            )

            tok_list = None
            align_method = "uniform_fallback"

            if mms_toks and len(mms_toks) == len(norm_chars):
                mean_ctc = float(np.mean([t["diag"]["ctc_score"] for t in mms_toks]))
                if mean_ctc >= 0.28:
                    tok_list = mms_toks
                    align_method = "mms_fa_ctc"

            if not tok_list:
                # Fallback to ASR-anchored island alignment
                tok_list = align_line_with_clauses(line_text, toks, adj_s, elastic_end, analyzer)
                if tok_list:
                    c_py = "".join(_token_phonetic_str(c["text"], is_ja=is_ja_song) for c in norm_chars)
                    t_py = "".join(_token_phonetic_str(t["text"], is_ja=is_ja_song) for t in toks)
                    asr_matched = bool(c_py and t_py and fuzz.ratio(c_py, t_py) >= 65.0)
                    align_method = "asr_anchored" if asr_matched else "island_weighted"
                    # Populate token diag for fallback tokens
                    for t in tok_list:
                        if "diag" not in t:
                            t_rms = analyzer.mean_db(t["start"], t["end"])
                            t_ons = float(analyzer.onset[analyzer._idx(t["start"])]) if analyzer.onset is not None else 1.0
                            t["diag"] = {
                                "source": "asr" if asr_matched else "dp_boundary",
                                "ctc_score": 0.55 if asr_matched else 0.35,
                                "rms_db": round(float(t_rms), 1),
                                "onset_strength": round(float(t_ons), 2),
                                "sustain_extended_ms": 0,
                                "flags": [],
                            }

            if not tok_list:
                continue

            # Populate Line Diagnostics
            flags = []
            if lead_breath_trimmed >= 150:
                flags.append({
                    "code": "LEADING_BREATH_SKIPPED",
                    "severity": "info",
                    "detail": f"Skipped {lead_breath_trimmed}ms of low-energy breath/silence [{anc_s}..{adj_s}ms]",
                })

            tail_sustain_ext = max(0, tok_list[-1]["end"] - (next_nominal or tok_list[-1]["end"]))
            if next_nominal is not None and tok_list[-1]["end"] > next_nominal + 200:
                flags.append({
                    "code": "TAIL_EXTENDED_PAST_NEXT_LRC",
                    "severity": "info",
                    "detail": f"Sustained vocal energy held {tail_sustain_ext}ms past next line's nominal LRC timestamp ({next_nominal}ms)",
                })

            shift_start = tok_list[0]["start"] - orig_lrc_s
            if abs(shift_start) >= 1200:
                flags.append({
                    "code": "LARGE_LRC_SHIFT",
                    "severity": "warning",
                    "detail": f"Acoustic alignment shifted start by {shift_start:+d}ms from nominal LRC",
                })

            c_py = "".join(_token_phonetic_str(c["text"], is_ja=is_ja_song) for c in norm_chars)
            t_py = "".join(_token_phonetic_str(t["text"], is_ja=is_ja_song) for t in toks)
            phon_match = float(fuzz.ratio(c_py, t_py)) if (c_py and t_py) else 0.0

            islands = analyzer.active_islands(tok_list[0]["start"], tok_list[-1]["end"])
            line_mean_ctc = float(np.mean([t.get("diag", {}).get("ctc_score", 0.70) for t in tok_list]))
            line_mean_rms = analyzer.mean_db(tok_list[0]["start"], tok_list[-1]["end"])

            line_diag: LineDiagnostics = {
                "line_idx": line_idx_counter,
                "lrc_nominal_start": orig_lrc_s,
                "lrc_nominal_end": next_nominal,
                "shift_from_lrc_start_ms": shift_start,
                "shift_from_lrc_end_ms": tok_list[-1]["end"] - (next_nominal or tok_list[-1]["end"]),
                "alignment_method": align_method,
                "phonetic_match_score": round(phon_match, 1),
                "mean_ctc_score": round(line_mean_ctc, 2),
                "mean_rms_db": round(float(line_mean_rms), 1),
                "lead_in_breath_trimmed_ms": lead_breath_trimmed,
                "tail_sustain_extended_ms": tail_sustain_ext,
                "voiced_islands": [[int(a), int(b)] for a, b in islands],
                "asr_heard_text": " ".join(t.get("text", "") for t in toks),
                "asr_raw_tokens": [{"text": t.get("text", ""), "start": t.get("start", 0), "end": t.get("end", 0)} for t in toks],
                "flags": flags,
            }

            add_phonetics(tok_list)
            out_lines.append({
                "start": tok_list[0]["start"],
                "end": tok_list[-1]["end"],
                "is_backing": item.get("is_backing", False),
                "tokens": tok_list,
                "diag": line_diag,
            })
            line_idx_counter += 1

    enforce_non_overlapping_lines(out_lines)
    return out_lines


_MORA_ROMAN_CACHE: dict[str, str] = {}
_VOWELS = "aiueo"


def _mora_roman(mora: str) -> str:
    r = _MORA_ROMAN_CACHE.get(mora)
    if r is None:
        rom, _ = respell_hira_to_romaji_and_en(mora)
        r = re.sub(r"[^a-z]", "", rom.lower())
        _MORA_ROMAN_CACHE[mora] = r
    return r


def _ja_units(text: str, char_src: list[int]) -> list[tuple[str, int]]:
    """
    Convert a Japanese string to romanized mora units using *contextual* kakasi readings
    (so e.g. 幾千 -> i-ku-se-n regardless of how the characters were tokenized).
    Each unit keeps the index of the source token/line its first character came from.
    """
    from core.phonetics.ja import kata_to_hira

    kks = _get_kakasi()
    items = kks.convert(text)
    if "".join(it["orig"] for it in items) != text:
        items = [{"orig": ch, "hira": ch} for ch in text]
    units: list[tuple[str, int]] = []
    pos = 0
    for it in items:
        orig = it["orig"]
        n = len(orig)
        hira = kata_to_hira(it.get("hira") or orig)
        morae = split_morae(hira)
        for k, m in enumerate(morae):
            rom = _mora_roman(m)
            if rom:
                ci = pos + min(n - 1, int(k * n / max(1, len(morae))))
                units.append((rom, char_src[ci]))
        pos += n
    return units


def _spread_over_islands(weights: list[float], lo: int, hi: int,
                         analyzer: VocalAnalyzer) -> list[tuple[int, int]]:
    """Distribute consecutive lines over only the *voiced* time inside [lo, hi]."""
    n = len(weights)
    if n == 0:
        return []
    hi = max(hi, lo + 400 * n)
    islands = [(a, b) for a, b in analyzer.active_islands(lo, hi, min_island_ms=160) if b > a]
    if not islands:
        islands = [(lo, hi)]
    total = float(sum(b - a for a, b in islands))
    wsum = float(sum(weights)) or 1.0

    def to_real(v: float) -> int:
        acc = 0.0
        for a, b in islands:
            if v <= acc + (b - a):
                return int(a + (v - acc))
            acc += b - a
        return int(islands[-1][1])

    out, cum = [], 0.0
    for w in weights:
        out.append((to_real(cum / wsum * total), to_real((cum + w) / wsum * total)))
        cum += w
    return out


def _dp_anchor_plain_lines(clean_lines: list[str], tokens: list[dict], is_ja: bool,
                           analyzer: VocalAnalyzer, first_vocal_ms: int) -> list[dict]:
    """
    Globally align every pasted lyric line to the ASR stream with a monotonic
    (Needleman-Wunsch style) phonetic alignment, so repeated choruses, garbled ASR
    stretches and skipped lines cannot derail later lines the way greedy matching does.
    Returns one anchor dict per lyric line: {text, start, end, tokens, conf}.
    """
    # ---- ASR units (drop whisper "iiiii…" style repetition loops) ----
    atoks: list[dict] = []
    i = 0
    while i < len(tokens):
        j = i
        while j < len(tokens) and tokens[j].get("text") == tokens[i].get("text"):
            j += 1
        if j - i < 5:
            atoks.extend(tokens[i:j])
        i = j

    a_units: list[dict] = []
    if is_ja:
        a_text = "".join(t["text"] for t in atoks)
        a_src = [k for k, t in enumerate(atoks) for _ in t["text"]]
        raw = _ja_units(a_text, a_src)
    else:
        raw = [(_token_phonetic_str(t["text"]), k) for k, t in enumerate(atoks)]
        raw = [(r, k) for r, k in raw if r]
    k = 0
    while k < len(raw):
        e = k
        while e < len(raw) and raw[e][1] == raw[k][1]:
            e += 1
        tok = atoks[raw[k][1]]
        span = max(40, tok["end"] - tok["start"])
        cnt = e - k
        for q in range(cnt):
            a_units.append({
                "rom": raw[k + q][0],
                "s": int(tok["start"] + span * q / cnt),
                "e": int(tok["start"] + span * (q + 1) / cnt),
            })
        k = e

    # ---- Lyric units ----
    l_units: list[tuple[str, int]] = []
    for li, line in enumerate(clean_lines):
        if is_ja:
            l_units.extend(_ja_units(line, [li] * len(line)))
        else:
            for ch in tokenize_text(line):
                r = _token_phonetic_str(ch)
                if r:
                    l_units.append((r, li))

    n, m = len(l_units), len(a_units)
    total_ms = len(analyzer.rms_db) * analyzer.frame_ms if analyzer.rms_db is not None else (
        (tokens[-1]["end"] + 2000) if tokens else 60000)

    hits_by_line: dict[int, list[tuple[int, int]]] = {}
    matched_units = 0
    if n and m:
        EQ, GAP_L, SKIP_A = 1.3, -0.8, -0.10
        if is_ja:
            ids: dict[str, int] = {}
            lid = np.array([ids.setdefault(r, len(ids)) for r, _ in l_units])
            aid = np.array([ids.setdefault(u["rom"], len(ids)) for u in a_units])
            names = [""] * len(ids)
            for r, v in ids.items():
                names[v] = r
            vow = np.array([ord(r[-1]) if r[-1] in _VOWELS else 0 for r in names])
            con = np.array([hash(r[:-1] if r[-1] in _VOWELS else r) for r in names])
            con_empty = np.array([(r[:-1] if r[-1] in _VOWELS else r) == "" for r in names])
            eq = lid[:, None] == aid[None, :]
            same_c = (con[lid][:, None] == con[aid][None, :]) & ~con_empty[lid][:, None]
            same_v = (vow[lid][:, None] == vow[aid][None, :]) & (vow[lid][:, None] != 0)
            S = np.where(eq, EQ, np.where(same_c, -0.2, np.where(same_v, -0.4, -0.9)))
        else:
            S = np.full((n, m), -0.9)
            cache: dict[tuple[str, str], float] = {}
            for x, (lr, _) in enumerate(l_units):
                for y, au in enumerate(a_units):
                    key = (lr, au["rom"])
                    v = cache.get(key)
                    if v is None:
                        r = fuzz.ratio(lr, au["rom"])
                        v = EQ if r >= 85 else (2.0 * r / 100.0 - 1.1 if r >= 50 else -0.9)
                        cache[key] = v
                    S[x, y] = v

        dp = np.zeros((n + 1, m + 1), dtype=np.float64)
        dp[:, 0] = np.arange(n + 1) * GAP_L
        jj = np.arange(m + 1, dtype=np.float64)
        for r in range(1, n + 1):
            cand = np.full(m + 1, -1e18)
            cand[1:] = np.maximum(dp[r - 1, :-1] + S[r - 1], dp[r - 1, 1:] + GAP_L)
            cand[0] = dp[r - 1, 0] + GAP_L
            # left moves (skip ASR unit) via prefix-max trick
            dp[r] = np.maximum.accumulate(cand - SKIP_A * jj) + SKIP_A * jj
            dp[r, 0] = dp[r - 1, 0] + GAP_L

        r, c = n, int(np.argmax(dp[n]))
        pairs: list[tuple[int, int]] = []
        while r > 0:
            if c > 0 and abs(dp[r, c] - (dp[r - 1, c - 1] + S[r - 1, c - 1])) < 1e-6:
                if S[r - 1, c - 1] >= EQ - 1e-9:
                    pairs.append((r - 1, c - 1))
                r -= 1
                c -= 1
            elif abs(dp[r, c] - (dp[r - 1, c] + GAP_L)) < 1e-6 or c == 0:
                r -= 1
            else:
                c -= 1
        pairs.reverse()
        matched_units = len(pairs)
        for ui, aj in pairs:
            hits_by_line.setdefault(l_units[ui][1], []).append((ui, aj))

    # ---- Per-line anchors from hits ----
    line_units: dict[int, list[int]] = {}
    for ui, (_, li) in enumerate(l_units):
        line_units.setdefault(li, []).append(ui)

    anchors: list[dict | None] = []
    prev_hit_end = first_vocal_ms
    for li, line in enumerate(clean_lines):
        hits = hits_by_line.get(li, [])
        need = max(2, int(np.ceil(0.25 * len(line_units.get(li, [])))))
        if hits:
            clusters, cur = [], [hits[0]]
            for h in hits[1:]:
                if a_units[h[1]]["s"] - a_units[cur[-1][1]]["e"] > 3500:
                    clusters.append(cur)
                    cur = [h]
                else:
                    cur.append(h)
            clusters.append(cur)
            hits = max(clusters, key=len)
        if len(hits) >= need:
            fs, le = a_units[hits[0][1]]["s"], a_units[hits[-1][1]]["e"]
            first_ui, last_ui = hits[0][0], hits[-1][0]
            lu = line_units[li]
            avg = min(450.0, max(120.0, (le - fs) / max(1, last_ui - first_ui + 1)))
            lead = (first_ui - lu[0]) * avg
            trail = (lu[-1] - last_ui) * avg
            start = int(max(fs - lead, min(fs, prev_hit_end + 20)))
            end = int(max(start + 250, le + trail))
            anchors.append({"text": line, "start": start, "end": end, "conf": True})
            prev_hit_end = max(prev_hit_end + 20, le)
        else:
            anchors.append(None)

    # Clamp confident anchor ends so a trailing estimate never overlaps the next confident anchor
    conf_indices = [idx for idx, a in enumerate(anchors) if a is not None]
    for c_pos in range(len(conf_indices) - 1):
        i_cur = conf_indices[c_pos]
        i_nxt = conf_indices[c_pos + 1]
        gap_lines = i_nxt - i_cur - 1
        min_gap_ms = 600 * gap_lines + 20
        if anchors[i_cur]["end"] > anchors[i_nxt]["start"] - min_gap_ms:
            mid = (anchors[i_cur]["end"] + anchors[i_nxt]["start"]) // 2
            half = min_gap_ms // 2
            anchors[i_cur]["end"] = max(anchors[i_cur]["start"] + 300, mid - half)
            if gap_lines > 0 and anchors[i_nxt]["start"] - anchors[i_cur]["end"] < min_gap_ms:
                anchors[i_nxt]["start"] = min(
                    anchors[i_nxt]["end"] - 300,
                    anchors[i_cur]["end"] + min_gap_ms,
                )

    # ---- Interpolate unmatched lines between matched neighbours over voiced time ----
    li = 0
    while li < len(anchors):
        if anchors[li] is not None:
            li += 1
            continue
        k = li
        while k < len(anchors) and anchors[k] is None:
            k += 1
        lo = anchors[li - 1]["end"] if li > 0 else first_vocal_ms
        hi = anchors[k]["start"] if k < len(anchors) else total_ms
        lo = int(lo + (80 if li > 0 else 0))
        hi = int(hi - (80 if k < len(anchors) else 0))
        span_needed = max(320 * (k - li), 400)
        if hi < lo + span_needed and k < len(anchors):
            hi = max(lo + span_needed, anchors[k]["start"])
            anchors[k]["start"] = max(anchors[k]["start"], hi + 20)
            anchors[k]["end"] = max(anchors[k]["end"], anchors[k]["start"] + 300)
        weights = [float(max(1, sum(count_syllables(c) for c in tokenize_text(clean_lines[x])))) for x in range(li, k)]
        for x, (s_, e_) in zip(range(li, k), _spread_over_islands(weights, lo, max(hi, lo + span_needed), analyzer)):
            anchors[x] = {"text": clean_lines[x], "start": int(s_), "end": int(max(e_, s_ + 260)), "conf": False}
        li = k

    out = []
    for a in anchors:
        a["tokens"] = [t for t in tokens if a["conf"] and t["start"] >= a["start"] - 150 and t["end"] <= a["end"] + 150]
        out.append(a)
    ratio = matched_units / max(1, n)
    conf_lines = sum(1 for a in out if a["conf"])
    print(f"  [plain-align] DP phonetic match: {conf_lines}/{len(out)} lines anchored, "
          f"{matched_units}/{n} sounds ({ratio:.0%}) matched ASR", flush=True)
    return out


def align_plain_lyrics_with_tokens(plain_lines: list[str], tokens: list[dict], analyzer: VocalAnalyzer | None = None) -> list[dict]:
    """
    Align plain unsynchronized lyrics text lines against ASR tokens and vocal acoustic energy.
    Matches lines sequentially across tokens using character and phonetic (Romaji/Pinyin/RR) similarity,
    with acoustic vocal-island fallback when ASR tokens are missing or unmatched.
    """
    clean_lines = [ln.strip() for ln in plain_lines if ln.strip()]
    if not clean_lines:
        return []
    if analyzer is None:
        analyzer = VocalAnalyzer(None)

    is_ja = any(has_kana(ln) for ln in clean_lines) or any(has_kana(t.get("text", "")) for t in tokens)
    tok_pys = [_token_phonetic_str(t.get("text", ""), is_ja=is_ja) for t in tokens] if tokens else []

    # Determine the earliest voiced singing onset in the audio so an unmatched first line
    # starts at the actual vocal entry instead of jumping to a late ASR token.
    first_vocal_ms = 1000
    if analyzer.rms_db is not None and len(analyzer.rms_db) > 0:
        total_ms = len(analyzer.rms_db) * analyzer.frame_ms
        voiced_isl = analyzer.active_islands(0, total_ms, min_island_ms=250)
        if voiced_isl:
            first_vocal_ms = voiced_isl[0][0]
    elif tokens:
        first_vocal_ms = tokens[0]["start"]

    cur_tok_idx = 0
    anchors = []
    use_dp = bool(tokens)
    if use_dp:
        try:
            anchors = _dp_anchor_plain_lines(clean_lines, tokens, is_ja, analyzer, first_vocal_ms)
        except Exception as e:  # never let the global matcher break alignment
            print(f"  [plain-align] DP anchoring failed ({e}); using greedy matcher", flush=True)
            anchors = []
            use_dp = False
    for line in ([] if use_dp else clean_lines):
        norm_chars = tokenize_text(line, lang="ja" if is_ja else None)
        m_chars = len(norm_chars)
        if m_chars == 0:
            continue
        line_str = "".join(norm_chars).lower()
        line_py = "".join(_token_phonetic_str(c, is_ja=is_ja) for c in norm_chars)

        best_score = -1e9
        best_r = -1.0
        best_span = (cur_tok_idx, cur_tok_idx)
        win_limit = 55 if cur_tok_idx == 0 else 42
        window = tokens[cur_tok_idx : cur_tok_idx + win_limit] if tokens else []
        win_pys = tok_pys[cur_tok_idx : cur_tok_idx + win_limit] if tokens else []
        prev_end = anchors[-1]["end"] if anchors else first_vocal_ms

        for span_len in range(max(1, m_chars - 3), min(len(window) + 1, m_chars + 6)):
            for k in range(len(window) - span_len + 1):
                sub = window[k : k + span_len]
                sub_str = "".join(t["text"] for t in sub).lower()
                sub_py = "".join(win_pys[k : k + span_len])
                r_raw = fuzz.ratio(line_str, sub_str)
                r_py = fuzz.ratio(line_py, sub_py) if (line_py and sub_py) else 0.0
                r = max(r_raw, r_py * 0.96)

                # Penalize skipping ASR tokens or jumping across large time gaps when tie-breaking
                skip_pen = k * 0.65
                if anchors:
                    gap_s = max(0.0, (sub[0]["start"] - prev_end) / 1000.0)
                    if gap_s > 6.0:
                        skip_pen += (gap_s - 6.0) * 1.2
                score = r - skip_pen
                if score > best_score:
                    best_score = score
                    best_r = r
                    best_span = (cur_tok_idx + k, cur_tok_idx + k + span_len)

        if best_r >= 48.0 and tokens:
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
            last_end = anchors[-1]["end"] if anchors else first_vocal_ms
            total_syls = sum(count_syllables(c) for c in norm_chars)
            est_dur = max(1200, total_syls * 240)
            cand_s = last_end + (220 if anchors else 0)
            # Snap to next voiced island if available nearby
            if analyzer.rms_db is not None and len(analyzer.rms_db) > 0:
                total_ms = len(analyzer.rms_db) * analyzer.frame_ms
                isl = analyzer.active_islands(cand_s, min(total_ms, cand_s + 8000), min_island_ms=160)
                if isl:
                    cand_s = isl[0][0]
            anchors.append({
                "text": line,
                "start": cand_s,
                "end": cand_s + est_dur,
                "tokens": [],
                "conf": False,
            })

    out_lines = []
    for idx, anc in enumerate(anchors):
        next_s = anchors[idx + 1]["start"] if idx + 1 < len(anchors) else anc["end"] + 1500
        if anc.get("conf") and next_s - anc["end"] > 2500:
            next_s = anc["end"] + 1200
        if next_s <= anc["start"] + 200:
            next_s = max(anc["end"], anc["start"] + 1200)
        tok_list = align_line_with_clauses(
            anc["text"],
            anc["tokens"],
            anc["start"],
            next_s,
            analyzer,
            drop_silent=False,
        )
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
