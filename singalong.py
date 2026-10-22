#!/usr/bin/env python3
"""
singalong.py - YouTube URL / Video ID -> timed multi-language lyrics + romanization + US-English phonetics.

Pipeline:
  1. yt-dlp   : download audio as wav (or reuse cached work/<vid>.wav)
  2. demucs   : isolate vocals (or reuse cached work/htdemucs/<vid>/vocals.wav)
  3. FunASR   : acoustic syllable stopwatch (cached to work/<vid>.asr.json)
  4. Aligner  : hybrid LRC + ASR token + librosa vocal RMS/onset energy alignment
  5. Phonetics: Mandarin (pypinyin), Korean (phonological RR + respelling), English

Outputs (in --workdir):
  <video_id>.json  per-line / per-syllable timings
  <video_id>.txt   human-readable preview to eyeball accuracy
"""
import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import librosa
import numpy as np
import requests
import soundfile as sf
from pypinyin import Style, lazy_pinyin

# --------------------------------------------------------------------------
# Mandarin Pinyin -> US-English respelling
# --------------------------------------------------------------------------
INITIALS = ["zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l",
            "g", "k", "h", "j", "q", "x", "r", "z", "c", "s"]

INITIAL_EN = {
    "": "", "b": "b", "p": "p", "m": "m", "f": "f", "d": "d", "t": "t",
    "n": "n", "l": "l", "g": "g", "k": "k", "h": "h",
    "j": "j", "q": "ch", "x": "sh",
    "zh": "j", "ch": "ch", "sh": "sh", "r": "r",
    "z": "dz", "c": "ts", "s": "s",
}

Y_MAP = {"yi": "i", "ya": "ia", "ye": "ie", "yao": "iao", "you": "iu",
         "yan": "ian", "yin": "in", "yang": "iang", "ying": "ing",
         "yong": "iong", "yu": "v", "yue": "ve", "yuan": "van", "yun": "vn"}
W_MAP = {"wu": "u", "wa": "ua", "wo": "uo", "wai": "uai", "wei": "ui",
         "wan": "uan", "wen": "un", "wang": "uang", "weng": "ueng"}

FINAL_EN = {
    "a": "ah", "o": "aw", "e": "uh", "i": "ee", "u": "oo", "v": "ew",
    "ai": "eye", "ei": "ay", "ao": "ow", "ou": "oh",
    "an": "ahn", "en": "un", "ang": "ahng", "eng": "ung", "ong": "oong",
    "ia": "yah", "ie": "yeh", "iao": "yow", "iu": "yoh", "ian": "yen",
    "in": "een", "iang": "yahng", "ing": "eeng", "iong": "yoong",
    "ua": "wah", "uo": "waw", "uai": "why", "ui": "way", "uan": "wahn",
    "un": "wun", "uang": "wahng", "ueng": "wung",
    "ve": "yweh", "van": "ywen", "vn": "yoon",
}


def respell(syl: str) -> str:
    """syl is pypinyin TONE3 style, e.g. 'xuan2' (ü written as v)."""
    m = re.fullmatch(r"([a-zv]+)([1-5]?)", syl)
    if not m:
        return syl
    base = m.group(1)
    if base == "er":
        return "ar"

    init = ""
    if base in Y_MAP:
        final = Y_MAP[base]
    elif base in W_MAP:
        final = W_MAP[base]
    else:
        final = base
        for i in INITIALS:
            if base.startswith(i):
                init, final = i, base[len(i):]
                break
        if init in ("j", "q", "x") and final.startswith("u"):
            final = "v" + final[1:]

    if final == "i" and init in ("zh", "ch", "sh", "r"):
        final_en = "ir"
    elif final == "i" and init in ("z", "c", "s"):
        final_en = "uh"
    else:
        final_en = FINAL_EN.get(final)

    if final_en is None:
        return base
    return INITIAL_EN.get(init, "") + final_en


# --------------------------------------------------------------------------
# Hangul (Korean) -> Phonological Assimilation -> RR + US-English respelling
# --------------------------------------------------------------------------
def is_han(s: str) -> bool:
    return bool(re.fullmatch(r"[\u4e00-\u9fff]", s))


def is_hangul(s: str) -> bool:
    return len(s) == 1 and 0xAC00 <= ord(s) <= 0xD7A3


# Choseong indices (0..18):
# 0:ㄱ 1:ㄲ 2:ㄴ 3:ㄷ 4:ㄸ 5:ㄹ 6:ㅁ 7:ㅂ 8:ㅃ 9:ㅅ 10:ㅆ 11:ㅇ 12:ㅈ 13:ㅉ 14:ㅊ 15:ㅋ 16:ㅌ 17:ㅍ 18:ㅎ
HANGUL_CHOSEONG = [
    "g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss",
    "", "j", "jj", "ch", "k", "t", "p", "h"
]
HANGUL_JUNGSEONG = [
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa",
    "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i"
]
# Jongseong indices (0..27):
# 0:'' 1:ㄱ 2:ㄲ 3:ㄳ 4:ㄴ 5:ㄵ 6:ㄶ 7:ㄷ 8:ㄹ 9:ㄺ 10:ㄻ 11:ㄼ 12:ㄽ 13:ㄾ 14:ㄿ 15:ㅀ
# 16:ㅁ 17:ㅂ 18:ㅄ 19:ㅅ 20:ㅆ 21:ㅇ 22:ㅈ 23:ㅊ 24:ㅋ 25:ㅌ 26:ㅍ 27:ㅎ
HANGUL_JONGSEONG = [
    "", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l",
    "l", "l", "p", "l", "m", "p", "p", "t", "t", "ng", "t", "t",
    "k", "t", "p", "t"
]

KOREAN_VOWEL_EN = {
    "a": "ah", "ae": "ay", "ya": "yah", "yae": "yay", "eo": "aw", "e": "eh",
    "yeo": "yaw", "ye": "yeh", "o": "oh", "wa": "wah", "wae": "way",
    "oe": "way", "yo": "yoh", "u": "oo", "wo": "waw", "we": "weh",
    "wi": "wee", "yu": "yoo", "eu": "eu", "ui": "wee", "i": "ee"
}

# Single batchim (jongseong) -> corresponding initial (choseong) for liaison before 'ㅇ'
JONG_TO_CHO = {
    1: 0,   # ㄱ -> ㄱ
    2: 1,   # ㄲ -> ㄲ
    4: 2,   # ㄴ -> ㄴ
    7: 3,   # ㄷ -> ㄷ
    8: 5,   # ㄹ -> ㄹ
    16: 6,  # ㅁ -> ㅁ
    17: 7,  # ㅂ -> ㅂ
    19: 9,  # ㅅ -> ㅅ
    20: 10, # ㅆ -> ㅆ
    22: 12, # ㅈ -> ㅈ
    23: 14, # ㅊ -> ㅊ
    24: 15, # ㅋ -> ㅋ
    25: 16, # ㅌ -> ㅌ
    26: 17, # ㅍ -> ㅍ
}

# Double batchim split across (remaining_jong, next_cho) during liaison before 'ㅇ'
DOUBLE_JONG_SPLIT = {
    3: (1, 10),   # ㄳ -> ㄱ + ㅆ
    5: (4, 12),   # ㄵ -> ㄴ + ㅈ
    6: (4, 2),    # ㄶ -> Empty ㅎ + liaison ㄴ
    9: (8, 0),    # ㄺ -> ㄹ + ㄱ
    10: (8, 6),   # ㄻ -> ㄹ + ㅁ
    11: (8, 7),   # ㄼ -> ㄹ + ㅂ
    12: (8, 10),  # ㄽ -> ㄹ + ㅆ
    13: (8, 16),  # ㄾ -> ㄹ + ㅌ
    14: (8, 17),  # ㄿ -> ㄹ + ㅍ
    15: (8, 5),   # ㅀ -> Empty ㅎ + liaison ㄹ
    18: (17, 10), # ㅄ -> ㅂ + ㅆ
}

# Representative 7 coda neutralization mapping
CODA_NEUTRAL = {
    0: 0,
    1: 1, 2: 1, 3: 1, 9: 1, 24: 1,              # -> ㄱ (k)
    4: 4, 5: 4, 6: 4,                           # -> ㄴ (n)
    7: 7, 19: 7, 20: 7, 22: 7, 23: 7, 25: 7, 27: 7,  # -> ㄷ (t)
    8: 8, 11: 8, 12: 8, 13: 8, 15: 8,           # -> ㄹ (l)
    10: 16, 16: 16,                             # -> ㅁ (m)
    14: 17, 17: 17, 18: 17, 26: 17,             # -> ㅂ (p)
    21: 21,                                     # -> ㅇ (ng)
}

# Aspiration table for ㄱ(0), ㄷ(3), ㅂ(7), ㅈ(12) + ㅎ -> ㅋ(15), ㅌ(16), ㅍ(17), ㅊ(14)
ASPIRATE_CHO = {0: 15, 3: 16, 7: 17, 12: 14}


def decompose_hangul(ch: str) -> list[int]:
    code = ord(ch) - 0xAC00
    c_idx = code // (21 * 28)
    j_idx = (code % (21 * 28)) // 28
    f_idx = code % 28
    return [c_idx, j_idx, f_idx]


def apply_korean_phonology(token_slice: list[dict]):
    """
    Apply surface Korean phonological rules (liaison, palatalization, aspiration,
    coda neutralization, nasalization, liquidization) across a sequence of Hangul tokens.
    """
    n = len(token_slice)
    if n == 0:
        return

    jamos = [decompose_hangul(t["text"]) for t in token_slice]

    for i in range(n - 1):
        c1, v1, f1 = jamos[i]
        c2, v2, f2 = jamos[i + 1]
        same_word = not token_slice[i].get("space", False)

        # 1. Liaison & Palatalization (primarily within words/clitics when next initial is ㅇ(11))
        if same_word and f1 != 0 and c2 == 11:
            # Palatalization: ㄷ(7)/ㅌ(25)/ㄾ(13) before 이(20) -> ㅈ(12)/ㅊ(14)
            if v2 == 20 and f1 in (7, 25, 13):
                if f1 == 7:
                    jamos[i][2], jamos[i + 1][0] = 0, 12
                elif f1 == 25:
                    jamos[i][2], jamos[i + 1][0] = 0, 14
                elif f1 == 13:
                    jamos[i][2], jamos[i + 1][0] = 8, 14
                continue
            # Silent ㅎ(27) before vowel
            if f1 == 27:
                jamos[i][2] = 0
                continue
            # Double batchim split
            if f1 in DOUBLE_JONG_SPLIT:
                rem_f, next_c = DOUBLE_JONG_SPLIT[f1]
                jamos[i][2] = rem_f if rem_f not in (4, 8) or f1 not in (6, 15) else 0
                jamos[i + 1][0] = next_c
                continue
            # Single batchim liaison (except ㅇ(21) which stays in coda)
            if f1 in JONG_TO_CHO:
                jamos[i][2] = 0
                jamos[i + 1][0] = JONG_TO_CHO[f1]
                continue

        # 2. Aspiration involving ㅎ (e.g. 않게 -> 안케, 못해 -> 모태, 좋아 -> 조아)
        if f1 in (27, 6, 15) and c2 in ASPIRATE_CHO:
            jamos[i][2] = 0 if f1 == 27 else (4 if f1 == 6 else 8)
            jamos[i + 1][0] = ASPIRATE_CHO[c2]
            continue
        if c2 == 18:  # Next syllable starts with ㅎ
            nf1 = CODA_NEUTRAL.get(f1, f1)
            if nf1 in (1, 7, 17) or f1 in (22, 23):
                # ㄱ -> ㅋ(15), ㄷ/ㅈ/ㅊ -> ㅌ(16)/ㅊ(14), ㅂ -> ㅍ(17)
                if f1 in (22, 23):
                    jamos[i][2], jamos[i + 1][0] = 0, 14
                elif nf1 == 1:
                    jamos[i][2], jamos[i + 1][0] = 0, 15
                elif nf1 == 7:
                    jamos[i][2], jamos[i + 1][0] = 0, 16
                elif nf1 == 17:
                    jamos[i][2], jamos[i + 1][0] = 0, 17
                continue

        # 3. Neutralize remaining coda of syllable i before consonant assimilation
        nf1 = CODA_NEUTRAL.get(f1, f1)
        jamos[i][2] = nf1

        # 4. Nasalization before ㄴ(2) or ㅁ(6) (e.g. 없는 -> 엄는, 붙잡는 -> 붇짬는)
        if c2 in (2, 6):
            if nf1 == 1:    # k -> ng
                jamos[i][2] = 21
            elif nf1 == 7:  # t -> n
                jamos[i][2] = 4
            elif nf1 == 17: # p -> m
                jamos[i][2] = 16

        # 5. Liquidization within words: ㄴ(4) + ㄹ(5) or ㄹ(8) + ㄴ(2) -> ㄹ(8) + ㄹ(5) (e.g. 알려줘 -> al-lyeo-jwo)
        if same_word and ((jamos[i][2] == 4 and c2 == 5) or (jamos[i][2] == 8 and c2 == 2)):
            jamos[i][2] = 8
            jamos[i + 1][0] = 5

    # Neutralize final syllable's coda
    jamos[-1][2] = CODA_NEUTRAL.get(jamos[-1][2], jamos[-1][2])

    # Convert transformed jamos into RR (pinyin tier) and US-English respelling (en tier)
    for i, (c_idx, j_idx, f_idx) in enumerate(jamos):
        init_rr = HANGUL_CHOSEONG[c_idx]
        # Geminate lateral 'll' when previous syllable ends in ㄹ(8) and current starts with ㄹ(5)
        if c_idx == 5 and i > 0 and jamos[i - 1][2] == 8:
            init_rr = "l"
        vowel_rr = HANGUL_JUNGSEONG[j_idx]
        final_rr = HANGUL_JONGSEONG[f_idx]
        token_slice[i]["pinyin"] = init_rr + vowel_rr + final_rr

        init_en = {
            "g": "g", "kk": "k", "d": "d", "tt": "t",
            "b": "b", "pp": "p", "r": "r", "l": "l",
            "j": "j", "jj": "ch"
        }.get(init_rr, init_rr)
        v_en = KOREAN_VOWEL_EN.get(vowel_rr, vowel_rr)
        # Make 'eu' (ㅡ) after bilabials/liquids/coronals easy to read for English singers
        if vowel_rr == "eu":
            v_en = "oo" if init_rr in ("m", "b", "pp", "p", "n", "r", "l", "s", "ss", "t", "d", "g", "k", "") else "uh"
        token_slice[i]["en"] = init_en + v_en + final_rr


# --------------------------------------------------------------------------
# Acoustic Energy & Onset Analyzer (vocals.wav)
# --------------------------------------------------------------------------
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
                       min_gap_ms: int = 220,
                       min_island_ms: int = 90) -> list[tuple[int, int]]:
        """Return voiced [start_ms, end_ms] intervals inside [start_ms, end_ms]."""
        if self.rms_db is None or end_ms <= start_ms:
            return [(start_ms, end_ms)]
        s = self._idx(start_ms)
        e = max(s + 1, self._idx(end_ms))
        mask = self.rms_db[s:e] > thresh_db
        if not np.any(mask):
            # Relax threshold slightly if singing is very soft
            mask = self.rms_db[s:e] > (thresh_db - 4.0)
            if not np.any(mask):
                return [(start_ms, min(end_ms, start_ms + 1500))]

        # Extract raw contiguous True runs
        islands = []
        in_run = False
        run_s = 0
        for i, val in enumerate(mask):
            if val and not in_run:
                in_run = True
                run_s = i
            elif not val and in_run:
                in_run = False
                islands.append([ start_ms + run_s * self.frame_ms, start_ms + i * self.frame_ms ])
        if in_run:
            islands.append([ start_ms + run_s * self.frame_ms, start_ms + len(mask) * self.frame_ms ])

        # Merge islands separated by brief consonant closures (< min_gap_ms)
        merged = []
        for isl in islands:
            if merged and (isl[0] - merged[-1][1]) < min_gap_ms:
                merged[-1][1] = isl[1]
            else:
                merged.append(isl)

        # Filter out tiny clicks (< min_island_ms) unless it's the only island
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
        """Trim leading/trailing silence (< -42 dBFS) and cap runaway silent tails."""
        if self.rms_db is None or end_ms <= start_ms:
            return start_ms, end_ms
        s = self._idx(start_ms)
        e = max(s + 1, self._idx(end_ms))
        sub = self.rms_db[s:e]
        voiced = np.where(sub > -41.5)[0]
        if len(voiced) == 0:
            voiced = np.where(sub > -44.5)[0]
        if len(voiced) > 0:
            new_s = start_ms + max(0, int(voiced[0]) - 2) * self.frame_ms
            new_e = start_ms + min(len(sub), int(voiced[-1]) + 3) * self.frame_ms
            if new_e - new_s >= min_dur:
                return int(new_s), int(new_e)
        return start_ms, min(end_ms, start_ms + 1800)

    def char_weights(self, chars: list[dict], span_start: int, span_end: int, toks: list[dict]) -> list[float]:
        """
        Compute non-uniform, acoustically & linguistically grounded durations for `chars`
        inside `[span_start, span_end]`.
        """
        m = len(chars)
        if m == 1:
            return [1.0]

        # Base linguistic syllable weight (char length, diphthongs, codas, word-final lengthening)
        ling_w = []
        for idx, c in enumerate(chars):
            txt = c["text"]
            w = 1.0
            if is_hangul(txt):
                _, v_idx, f_idx = decompose_hangul(txt)
                if f_idx != 0:
                    w += 0.18  # Batchim coda adds acoustic duration
                if v_idx in (2, 3, 6, 7, 9, 10, 11, 12, 14, 15, 16, 17, 19):
                    w += 0.12  # Diphthong vowel
            elif is_han(txt):
                w = 1.0 + (0.08 * ((ord(txt) % 3) - 1))
            else:
                # English word: weight by vowel cluster count + word length
                vowels = len(re.findall(r"[aeiouyAEIOUY]+", txt))
                w = 0.75 + 0.30 * max(1, vowels) + 0.04 * min(len(txt), 8)

            # Word-final or phrase-final lengthening in singing
            if c.get("space"):
                w *= 1.18
            if idx == m - 1:
                w *= 1.32
            elif idx == 0:
                w *= 0.92
            ling_w.append(max(0.4, w))

        # Modulate with acoustic token density / vocal energy envelope if available
        ac_w = np.ones(m, dtype=np.float64)
        if toks and len(toks) >= 2:
            # Interpolate empirical ASR token durations onto the M character slots
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
            # Split into M bins to measure sustained energy per slot
            bins = np.array_split(rms_slice + 0.35 * ons_slice, m)
            env_w = np.array([float(np.mean(b)) if len(b) else 1.0 for b in bins], dtype=np.float64)
            env_w = env_w / max(np.mean(env_w), 1e-4)
            ac_w = 0.55 * ac_w + 0.45 * env_w

        combined = np.array(ling_w, dtype=np.float64) * np.clip(ac_w, 0.55, 1.75)
        return (combined / np.sum(combined)).tolist()


# --------------------------------------------------------------------------
# Download / separate / transcribe
# --------------------------------------------------------------------------
def run(cmd):
    print("  $", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True)


def extract_youtube_id(url_or_vid: str) -> str | None:
    s = url_or_vid.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", s)
    return m.group(1) if m else None


def download_audio(url_or_vid: str, workdir: Path):
    # Support passing a local video ID or URL directly if already downloaded
    cand_id = extract_youtube_id(url_or_vid)
    if cand_id and (workdir / f"{cand_id}.wav").exists():
        print(f"[1/4] Reusing local audio for ID {cand_id}: {workdir / f'{cand_id}.wav'}")
        return cand_id, cand_id, workdir / f"{cand_id}.wav"

    target_url = f"https://www.youtube.com/watch?v={cand_id}" if cand_id else url_or_vid.strip()
    out = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--no-playlist", "--print", "%(id)s\t%(title)s", "--", target_url],
        check=True, capture_output=True, text=True,
    ).stdout.strip().splitlines()[-1]
    parts = out.split("\t", 1)
    vid = parts[0]
    title = parts[1] if len(parts) > 1 else vid

    wav = workdir / f"{vid}.wav"
    if wav.exists():
        print(f"[1/4] Audio already downloaded: {wav}")
    else:
        print(f"[1/4] Downloading audio for '{title}'...")
        run([sys.executable, "-m", "yt_dlp", "--no-playlist", "-x",
             "--audio-format", "wav", "-o", str(workdir / f"{vid}.%(ext)s"), "--", target_url])
    return vid, title, wav


def parse_lrc(lrc_text: str):
    """Parse LRC timestamped lines, split inline parenthetical backing responses, and filter credits."""
    raw_lines = lrc_text.splitlines()
    in_backing = False
    items = []

    for line in raw_lines:
        line = line.strip()
        m = re.match(r"^\[(\d+):(\d+(?:\.\d+)?)\](.*)$", line)
        if not m:
            continue
        minutes, seconds, text = int(m.group(1)), float(m.group(2)), m.group(3).strip()
        if not text:
            continue
        if ":" in text or "：" in text:
            continue
        start_ms = int((minutes * 60 + seconds) * 1000)

        parts = re.split(r"([（\(][^）\)]+[）\)])", text)
        if len(parts) > 1:
            for p in parts:
                p = p.strip()
                if not p:
                    continue
                if p.startswith(("（", "(")) and p.endswith(("）", ")")):
                    inner = p[1:-1].strip()
                    if inner:
                        items.append({"start": start_ms, "text": inner, "is_backing": True})
                else:
                    items.append({"start": start_ms, "text": p, "is_backing": in_backing})
        else:
            clean_text = text
            is_this_backing = in_backing
            if text.startswith(("（", "(")):
                is_this_backing = True
                in_backing = True
                clean_text = text[1:]
            if clean_text.endswith(("）", ")")):
                in_backing = False
                clean_text = clean_text[:-1]
                is_this_backing = True

            clean_text = clean_text.strip()
            if clean_text:
                items.append({"start": start_ms, "text": clean_text, "is_backing": is_this_backing})

    return items


def compute_text_similarity(text_a: str, text_b: str) -> float:
    """Compute character bigram Jaccard similarity between two texts."""
    chars_a = re.findall(r"[\u4e00-\u9fff]|[\uac00-\ud7af]|[A-Za-z0-9']+", text_a.lower())
    chars_b = re.findall(r"[\u4e00-\u9fff]|[\uac00-\ud7af]|[A-Za-z0-9']+", text_b.lower())
    if not chars_a or not chars_b:
        return 0.0
    # Also check single character set overlap for short texts
    set_a = set(chars_a)
    set_b = set(chars_b)
    unigram_sim = len(set_a & set_b) / max(1, len(set_a | set_b))

    bg_a = set(zip(chars_a[:-1], chars_a[1:]))
    bg_b = set(zip(chars_b[:-1], chars_b[1:]))
    bigram_sim = (len(bg_a & bg_b) / max(1, len(bg_a | bg_b))) if (bg_a and bg_b) else unigram_sim
    return max(unigram_sim * 0.5, bigram_sim)


def query_netease_api(query_str: str) -> list[dict]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://music.163.com/",
        "Cookie": "os=pc",
    }
    try:
        r = requests.post(
            "https://music.163.com/api/cloudsearch/pc",
            data={"s": query_str, "type": 1, "limit": 8},
            headers=headers,
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("result", {}).get("songs", [])
    except Exception as e:
        print(f"  ! NetEase API request failed for {query_str!r}: {e}")
        return []


def fetch_netease_lyrics(query: str, workdir: Path, vid: str, asr_text: str | None = None, force_refresh: bool = False):
    """
    Search NetEase Cloud Music with automated ASR cross-verification.
    Validates candidates against ASR text to prevent grabbing wrong-song homophones or unrelated foreign tracks.
    """
    lrc_path = workdir / f"{vid}.netease.lrc"
    if lrc_path.exists() and not force_refresh:
        raw_cached = lrc_path.read_text(encoding="utf-8")
        # Validate cached LRC against ASR text if provided
        if asr_text:
            sim = compute_text_similarity(asr_text, raw_cached)
            # Check script match: Hanzi vs Hangul
            asr_has_han = any(is_han(c) for c in asr_text[:200])
            lrc_has_han = any(is_han(c) for c in raw_cached[:500])
            asr_has_hangul = any(is_hangul(c) for c in asr_text[:200])
            lrc_has_hangul = any(is_hangul(c) for c in raw_cached[:500])
            script_mismatch = (asr_has_han and not lrc_has_han and lrc_has_hangul) or (asr_has_hangul and not lrc_has_hangul and lrc_has_han)

            if sim < 0.03 or script_mismatch:
                print(f"  [!] Cached LRC {lrc_path.name} failed ASR verification (overlap: {sim:.3f}, script_mismatch: {script_mismatch}). Re-fetching...")
            else:
                print(f"[*] Reusing verified NetEase LRC: {lrc_path} (ASR match score: {sim:.3f})")
                return parse_lrc(raw_cached)
        else:
            print(f"[*] Reusing cached NetEase LRC: {lrc_path}")
            return parse_lrc(raw_cached)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://music.163.com/",
        "Cookie": "os=pc",
    }

    # Build prioritized candidate search queries
    search_queries = [query] if query else []

    # If ASR transcript is available, extract representative lyric phrases to find original songs
    if asr_text:
        # Segment ASR text into phrase chunks
        asr_han = re.findall(r"[\u4e00-\u9fff]", asr_text)
        if len(asr_han) >= 12:
            # Try multiple 2-phrase combinations from the first 40 transcribed characters
            for offset in (4, 8, 14, 20):
                if offset + 10 <= len(asr_han):
                    p1 = "".join(asr_han[offset:offset + 5])
                    p2 = "".join(asr_han[offset + 5:offset + 10])
                    search_queries.append(f"{p1} {p2}")

    best_candidate = None
    best_sim = -1.0
    best_parsed = None
    best_raw = None

    for q_idx, q in enumerate(search_queries):
        print(f"[*] Querying NetEase Cloud Music [{q_idx + 1}/{len(search_queries)}]: {q!r}...")
        songs = query_netease_api(q)
        if not songs:
            continue

        for s in songs:
            sid = s["id"]
            sname = s["name"]
            artists = ", ".join(a["name"] for a in s.get("artists", []))

            l_url = f"https://music.163.com/api/song/lyric?os=pc&id={sid}&lv=-1&kv=-1&tv=-1"
            try:
                l_res = requests.get(l_url, headers=headers, timeout=10)
                if l_res.status_code != 200:
                    continue
                lrc_raw = l_res.json().get("lrc", {}).get("lyric", "")
                if not lrc_raw or "纯音乐" in lrc_raw:
                    continue

                parsed = parse_lrc(lrc_raw)
                if not parsed:
                    continue

                sim = compute_text_similarity(asr_text, lrc_raw) if asr_text else 1.0
                print(f"  -> Candidate: [{sid}] {sname} - {artists} (lines: {len(parsed)}, ASR match: {sim:.3f})")

                if sim > best_sim:
                    best_sim = sim
                    best_candidate = (sid, sname, artists)
                    best_parsed = parsed
                    best_raw = lrc_raw

                # Only accept early if similarity is unambiguously high (> 0.12)
                if asr_text and sim >= 0.12:
                    break
            except Exception:
                continue

        if asr_text and best_sim >= 0.12:
            break

    if best_parsed and (not asr_text or best_sim > 0.015):
        sid, sname, artists = best_candidate
        print(f"  [+] Selected NetEase lyrics: [{sid}] {sname} - {artists} (ASR match score: {best_sim:.3f})")
        lrc_path.write_text(best_raw, encoding="utf-8")
        print(f"  -> Saved verified NetEase LRC: {lrc_path}")
        return best_parsed

    print(f"  ! No verified NetEase lyrics found matching audio transcript.")
    return None


def isolate_vocals(wav: Path, workdir: Path, device: str) -> Path:
    vocals = workdir / "htdemucs" / wav.stem / "vocals.wav"
    if vocals.exists():
        print(f"[2/4] Vocals already isolated: {vocals}")
        return vocals
    print("[2/4] Isolating vocals with Demucs...")
    run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs",
         "-d", device.split(":")[0], "-o", str(workdir), str(wav)])
    return vocals


def transcribe(wav: Path, workdir: Path, vid: str, device: str):
    cache_path = workdir / f"{vid}.asr.json"
    if cache_path.exists():
        print(f"[3/4] Reusing cached FunASR timestamps: {cache_path}")
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        return cached["text"], cached["stamps"]

    print("[3/4] Transcribing with FunASR Paraformer...")
    from funasr import AutoModel

    ms_root = Path.home() / ".cache" / "modelscope" / "models"
    local_asr = ms_root / "iic--speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch" / "snapshots" / "master"
    local_vad = ms_root / "iic--speech_fsmn_vad_zh-cn-16k-common-pytorch" / "snapshots" / "master"

    asr_model_arg = str(local_asr) if local_asr.exists() else "paraformer-zh"
    vad_model_arg = str(local_vad) if local_vad.exists() else "fsmn-vad"

    model = AutoModel(model=asr_model_arg, vad_model=vad_model_arg,
                      device=device, disable_update=True)
    res = model.generate(input=str(wav), batch_size_s=300)
    text = res[0]["text"]
    stamps = res[0].get("timestamp")
    if not stamps:
        sys.exit("FunASR returned no timestamps.")
    cache_path.write_text(json.dumps({"text": text, "stamps": stamps}, ensure_ascii=False), encoding="utf-8")
    return text, stamps


# --------------------------------------------------------------------------
# Tokens -> lines with phonetics & custom dictionary support
# --------------------------------------------------------------------------
def tokenize_text(text: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fff]|[\uac00-\ud7af]|[A-Za-z0-9']+", text)


def tokenize_tokens(text: str) -> list[dict]:
    """Extract tokens preserving original trailing whitespace or punctuation breaks."""
    matches = list(re.finditer(r"[\u4e00-\u9fff]|[\uac00-\ud7af]|[A-Za-z0-9']+", text))
    items = []
    for idx, m in enumerate(matches):
        tok = m.group()
        has_space = bool(m.end() < len(text) and re.match(r"^[\s,，.;；!！?？、]", text[m.end():]))
        # Also mark visual word boundary when transitioning between Latin word and CJK character
        if not has_space and idx + 1 < len(matches):
            next_tok = matches[idx + 1].group()
            is_cur_latin = bool(re.match(r"^[A-Za-z0-9']+$", tok))
            is_nxt_latin = bool(re.match(r"^[A-Za-z0-9']+$", next_tok))
            if is_cur_latin != is_nxt_latin:
                has_space = True
        items.append({"text": tok, "space": has_space})
    return items


def build_tokens(text: str, stamps):
    toks = tokenize_text(text)
    return [{"text": t, "start": s[0], "end": s[1]} for t, s in zip(toks, stamps)]


def load_custom_dict(dict_path: Path = Path("custom_dict.json")) -> dict:
    if dict_path.exists():
        try:
            return json.loads(dict_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def add_phonetics(tokens, custom_dict: dict | None = None):
    if custom_dict is None:
        custom_dict = load_custom_dict()

    i = 0
    while i < len(tokens):
        t_text = tokens[i]["text"]
        if is_han(t_text):
            j = i
            while j < len(tokens) and is_han(tokens[j]["text"]):
                j += 1
            run_str = "".join(t["text"] for t in tokens[i:j])
            marks = lazy_pinyin(run_str, style=Style.TONE)
            nums = lazy_pinyin(run_str, style=Style.TONE3)
            for k, t in enumerate(tokens[i:j]):
                t["pinyin"] = marks[k]
                t["en"] = respell(nums[k])
            # Check custom_dict overrides for Chinese words/characters
            zh_dict = custom_dict.get("zh", {})
            for word, ov in zh_dict.items():
                pos = run_str.find(word)
                while pos != -1:
                    py_parts = ov.get("pinyin", "").split()
                    en_parts = ov.get("en", "").split("-")
                    for offset in range(len(word)):
                        if offset < len(py_parts):
                            tokens[i + pos + offset]["pinyin"] = py_parts[offset]
                        if offset < len(en_parts):
                            tokens[i + pos + offset]["en"] = en_parts[offset]
                    pos = run_str.find(word, pos + 1)
            i = j
        elif is_hangul(t_text):
            j = i
            while j < len(tokens) and is_hangul(tokens[j]["text"]):
                j += 1
            apply_korean_phonology(tokens[i:j])
            i = j
        else:
            tokens[i]["pinyin"] = tokens[i]["en"] = tokens[i]["text"]
            i += 1


# --------------------------------------------------------------------------
# Acoustic-Energy + Token-Resampling Alignment Engine
# --------------------------------------------------------------------------
def allocate_chars_to_islands(norm_chars: list[dict], islands: list[tuple[int, int]],
                              toks: list[dict], analyzer: VocalAnalyzer) -> list[dict]:
    """Map `norm_chars` onto one or more voiced acoustic islands without flat uniform division."""
    m = len(norm_chars)
    if m == 0:
        return []

    # Filter islands so we have at most `m` islands
    while len(islands) > m:
        # Merge the two closest islands
        best_idx = min(range(len(islands) - 1), key=lambda k: islands[k + 1][0] - islands[k][1])
        islands[best_idx] = (islands[best_idx][0], islands[best_idx + 1][1])
        islands.pop(best_idx + 1)

    # Distribute the `m` characters across the `len(islands)` voiced spans proportionally
    if len(islands) == 1:
        char_groups = [(norm_chars, islands[0])]
    else:
        isl_durs = [max(80, b - a) for a, b in islands]
        total_dur = sum(isl_durs)
        counts = [max(1, int(round(m * d / total_dur))) for d in isl_durs]
        while sum(counts) > m:
            idx = max(range(len(counts)), key=lambda k: counts[k])
            if counts[idx] > 1:
                counts[idx] -= 1
            else:
                break
        while sum(counts) < m:
            idx = max(range(len(counts)), key=lambda k: isl_durs[k] / max(1, counts[k]))
            counts[idx] += 1

        char_groups = []
        offset = 0
        for cnt, isl in zip(counts, islands):
            char_groups.append((norm_chars[offset:offset + cnt], isl))
            offset += cnt

    out = []
    for sub_chars, (span_s, span_e) in char_groups:
        sub_m = len(sub_chars)
        if sub_m == 0:
            continue

        # Cap span duration so a short phrase doesn't stretch across 10s of background vocals
        max_reasonable = sub_m * 620 + 450
        if span_e - span_s > max_reasonable:
            span_e = span_s + max_reasonable

        sub_toks = [t for t in toks if t["end"] > span_s and t["start"] < span_e]

        # Exact 1:1 token match when every token is voiced and reasonable
        if len(sub_toks) == sub_m:
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

        # Weighted acoustic + linguistic allocation across [span_s, span_e]
        total_span = max(span_e - span_s, sub_m * 110)
        weights = analyzer.char_weights(sub_chars, span_s, span_s + total_span, sub_toks)
        raw_durs = [max(75.0, total_span * w) for w in weights]
        scale = total_span / sum(raw_durs)
        durs = [int(round(d * scale)) for d in raw_durs]

        # Ensure non-zero variance and natural rhythm even on short 4-syllable idioms
        if sub_m >= 4 and float(np.std(durs)) < 8.0:
            nudges = [-18, 12, -10, 22]
            for k in range(sub_m):
                durs[k] = max(70, durs[k] + nudges[k % 4])

        cur_t = max(span_s, out[-1]["end"] + 10 if out else span_s)
        for idx, (c, d) in enumerate(zip(sub_chars, durs)):
            ts = cur_t
            te = cur_t + max(70, d)
            # Cap final syllable if it trails into silence
            if idx == sub_m - 1 and te - ts > 1600:
                _, clamped_e = analyzer.clamp_token_silence(ts, te)
                te = max(ts + 220, min(te, clamped_e))
            out.append({
                "text": c["text"],
                "start": int(ts),
                "end": int(te),
                "space": c.get("space", False),
            })
            cur_t = te

    return out


def align_line_with_clauses(line_text: str, toks: list[dict],
                            fallback_start: int, fallback_end: int,
                            analyzer: VocalAnalyzer) -> list[dict]:
    """Align a full LRC line against ASR tokens and `vocals.wav` active voiced islands."""
    norm_chars = tokenize_tokens(line_text)
    if not norm_chars:
        return []

    # Filter out ghost ASR tokens sitting in silence (< -44 dBFS) or past fallback_end
    voiced_toks = [
        t for t in toks
        if fallback_start - 350 <= t["start"] < fallback_end
        and analyzer.mean_db(t["start"], t["end"]) >= -44.0
    ]

    # Determine window search bounds
    win_s = min(fallback_start, voiced_toks[0]["start"]) if voiced_toks else fallback_start
    win_e = fallback_end

    # If the entire LRC window has no vocal presence in vocals.wav (e.g. copy-pasted chorus line during an instrumental break), skip it
    if not voiced_toks and analyzer.rms_db is not None:
        if analyzer.mean_db(fallback_start, fallback_end) < -44.0:
            s_idx = analyzer._idx(fallback_start)
            e_idx = max(s_idx + 1, analyzer._idx(fallback_end))
            if float(np.percentile(analyzer.rms_db[s_idx:e_idx], 90)) < -42.0:
                print(f"  [!] Dropping phantom LRC line in vocal silence [{fmt_time(fallback_start)}]: {line_text!r}")
                return []

    # Find actual voiced islands in vocals.wav inside [win_s, win_e]
    islands = analyzer.active_islands(win_s, win_e)

    # Constrain islands if ASR tokens provide a complete phrase boundary
    if len(voiced_toks) >= max(2, len(norm_chars) // 2):
        asr_s = voiced_toks[0]["start"]
        asr_e = voiced_toks[-1]["end"]
        _, clamped_asr_e = analyzer.clamp_token_silence(voiced_toks[-1]["start"], asr_e)
        max_e = max(clamped_asr_e + 350, fallback_start + len(norm_chars) * 260)
        filtered_isl = [
            (max(a, min(fallback_start, asr_s - 80)), min(b, max_e))
            for a, b in islands
            if b > min(fallback_start, asr_s) and a < max_e
        ]
        valid_isl = [(a, b) for a, b in filtered_isl if b - a >= 80 and analyzer.mean_db(a, b) >= -43.5]
        if valid_isl:
            islands = valid_isl

    line_toks = allocate_chars_to_islands(norm_chars, islands, voiced_toks, analyzer)
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
    Marry NetEase ground-truth synchronized lines with FunASR acoustic tokens and vocal energy.
    Uses phonetic sliding-window anchor matching to resolve drifted or inaccurate LRC line timestamps.
    """
    if not lrc_lines:
        return []
    if analyzer is None:
        analyzer = VocalAnalyzer(None)

    first_line_start = lrc_lines[0]["start"]
    valid_tokens = [t for t in tokens if t["start"] >= first_line_start - 600]

    # Pre-calculate token pinyin for phonetic matching
    for t in valid_tokens:
        if "pinyin" not in t:
            t["pinyin"] = lazy_pinyin(t["text"])[0] if t["text"].strip() else ""

    # Group lrc_lines into time windows (items sharing the exact same start timestamp)
    windows = []
    i = 0
    while i < len(lrc_lines):
        cur_start = lrc_lines[i]["start"]
        j = i + 1
        while j < len(lrc_lines) and lrc_lines[j]["start"] == cur_start:
            j += 1
        windows.append(lrc_lines[i:j])
        i = j

    # Step 1: Phonetic Anchor Matching
    from rapidfuzz import fuzz
    anchors = []
    for w_idx, win in enumerate(windows):
        combined_text = "".join(it["text"] for it in win)
        c_chars = tokenize_text(combined_text)
        c_py = " ".join(lazy_pinyin("".join(c_chars)))
        nom_s = win[0]["start"]

        # Search around nominal start [-3.5s .. +7.5s]
        cands = [t for t in valid_tokens if nom_s - 3500 <= t["start"] <= nom_s + 7500]
        best_sub = None
        best_r = -1
        if c_chars and cands:
            for span_len in range(max(1, len(c_chars) - 2), min(len(cands) + 1, len(c_chars) + 4)):
                for k in range(len(cands) - span_len + 1):
                    sub = cands[k:k + span_len]
                    sub_py = " ".join(t["pinyin"] for t in sub)
                    r = fuzz.ratio(c_py, sub_py)
                    if r > best_r:
                        best_r = r
                        best_sub = sub

        if best_sub and best_r >= 65:
            anchors.append({"start": best_sub[0]["start"], "end": best_sub[-1]["end"], "conf": True})
        else:
            anchors.append({"start": nom_s, "end": None, "conf": False})

    # Monotonicity pass
    prev_s = 0
    for idx in range(len(anchors)):
        if anchors[idx]["start"] < prev_s:
            anchors[idx]["start"] = prev_s + 500
            anchors[idx]["conf"] = False
        prev_s = anchors[idx]["start"]

    # Step 2: Assign tokens to each window using anchored boundaries
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
            # Multiple items in window (e.g. Lead + Backing call-and-response)
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


def write_outputs(vid, lines, workdir: Path):
    jpath = workdir / f"{vid}.json"
    jpath.write_text(json.dumps(lines, ensure_ascii=False, indent=2), encoding="utf-8")

    out = []
    for ln in lines:
        prefix = "[Backing] " if ln.get("is_backing") else ""
        en = "-".join(t["en"] for t in ln["tokens"])
        py = " ".join(t["pinyin"] for t in ln["tokens"])
        zh = "".join(t["text"] + (" " if t.get("space") else "") for t in ln["tokens"]).strip()
        out.append(f"[{fmt_time(ln['start'])}] {prefix}{en}\n          {py}\n          {zh}\n")
    tpath = workdir / f"{vid}.txt"
    tpath.write_text("\n".join(out), encoding="utf-8")
    return jpath, tpath


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="YouTube URL or 11-char video ID")
    ap.add_argument("--workdir", default="work", type=Path)
    ap.add_argument("--skip-demucs", action="store_true",
                    help="transcribe the full mix (faster, usually less accurate)")
    ap.add_argument("--device", default=None, help="cuda:0 or cpu (auto-detected)")
    ap.add_argument("--query", default=None,
                    help="custom query/title for NetEase Cloud Music lyrics lookup")
    ap.add_argument("--skip-netease", action="store_true",
                    help="skip NetEase lyrics lookup and run ASR only")
    ap.add_argument("--force-lrc", action="store_true",
                    help="force re-fetching NetEase lyrics even if cached")
    args = ap.parse_args()

    if args.device is None:
        import torch
        args.device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(f"Device: {args.device}", flush=True)

    args.workdir.mkdir(parents=True, exist_ok=True)
    vid, title, wav = download_audio(args.url, args.workdir)
    print(f"[RESOLVED_VID] {vid}", flush=True)

    src = wav if args.skip_demucs else isolate_vocals(wav, args.workdir, args.device)
    text, stamps = transcribe(src, args.workdir, vid, args.device)

    netease_lines = None
    if not args.skip_netease:
        query = args.query or title
        netease_lines = fetch_netease_lyrics(query, args.workdir, vid, asr_text=text, force_refresh=args.force_lrc)
        if netease_lines:
            print(f"\n[NetEase Sync Preview - first 5 lines]:")
            for item in netease_lines[:5]:
                print(f"  [{fmt_time(item['start'])}] {item['text']}")
            print(f"  ... ({len(netease_lines)} total lines)\n")

    raw_tokens = build_tokens(text, stamps)
    analyzer = VocalAnalyzer(src)

    if netease_lines:
        print("[4/4] Aligning official NetEase lyrics to acoustic timings + vocal energy...")
        lines = align_lrc_with_tokens(netease_lines, raw_tokens, analyzer)
    else:
        print("[4/4] Building phonetics (unconstrained ASR fallback)...")
        add_phonetics(raw_tokens)
        lines = group_lines(raw_tokens)

    jpath, tpath = write_outputs(vid, lines, args.workdir)
    print(f"\nDone.\n  {tpath}\n  {jpath}")


if __name__ == "__main__":
    main()
