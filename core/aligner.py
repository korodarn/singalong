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
                       min_gap_ms: int = 220,
                       min_island_ms: int = 90) -> list[tuple[int, int]]:
        """Return voiced [start_ms, end_ms] intervals inside [start_ms, end_ms]."""
        if self.rms_db is None or end_ms <= start_ms:
            return [(start_ms, end_ms)]
        s = self._idx(start_ms)
        e = max(s + 1, self._idx(end_ms))
        mask = self.rms_db[s:e] > thresh_db
        if not np.any(mask):
            mask = self.rms_db[s:e] > (thresh_db - 4.0)
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
        m = len(chars)
        if m == 1:
            return [1.0]

        ling_w = []
        for idx, c in enumerate(chars):
            txt = c["text"]
            w = 1.0
            if is_hangul(txt):
                _, v_idx, f_idx = decompose_hangul(txt)
                if f_idx != 0:
                    w += 0.18
                if v_idx in (2, 3, 6, 7, 9, 10, 11, 12, 14, 15, 16, 17, 19):
                    w += 0.12
            elif is_han(txt):
                w = 1.0 + (0.08 * ((ord(txt) % 3) - 1))
            else:
                vowels = len(re.findall(r"[aeiouyAEIOUY]+", txt))
                w = 0.75 + 0.30 * max(1, vowels) + 0.04 * min(len(txt), 8)

            if c.get("space"):
                w *= 1.18
            if idx == m - 1:
                w *= 1.32
            elif idx == 0:
                w *= 0.92
            ling_w.append(max(0.4, w))

        ac_w = np.ones(m, dtype=np.float64)
        if toks and len(toks) >= 2:
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

        max_reasonable = sub_m * 620 + 450
        if span_e - span_s > max_reasonable:
            span_e = span_s + max_reasonable

        sub_toks = [t for t in toks if t["end"] > span_s and t["start"] < span_e]

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

        total_span = max(span_e - span_s, sub_m * 110)
        weights = analyzer.char_weights(sub_chars, span_s, span_s + total_span, sub_toks)
        raw_durs = [max(75.0, total_span * w) for w in weights]
        scale = total_span / sum(raw_durs)
        durs = [int(round(d * scale)) for d in raw_durs]

        if sub_m >= 4 and float(np.std(durs)) < 8.0:
            nudges = [-18, 12, -10, 22]
            for k in range(sub_m):
                durs[k] = max(70, durs[k] + nudges[k % 4])

        cur_t = max(span_s, out[-1]["end"] + 10 if out else span_s)
        for idx, (c, d) in enumerate(zip(sub_chars, durs)):
            ts = cur_t
            te = cur_t + max(70, d)
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
    norm_chars = tokenize_tokens(line_text)
    if not norm_chars:
        return []

    voiced_toks = [
        t for t in toks
        if fallback_start - 350 <= t["start"] < fallback_end
        and analyzer.mean_db(t["start"], t["end"]) >= -44.0
    ]

    win_s = min(fallback_start, voiced_toks[0]["start"]) if voiced_toks else fallback_start
    win_e = fallback_end

    if not voiced_toks and analyzer.rms_db is not None:
        if analyzer.mean_db(fallback_start, fallback_end) < -44.0:
            s_idx = analyzer._idx(fallback_start)
            e_idx = max(s_idx + 1, analyzer._idx(fallback_end))
            if float(np.percentile(analyzer.rms_db[s_idx:e_idx], 90)) < -42.0:
                print(f"  [!] Dropping phantom LRC line in vocal silence [{fmt_time(fallback_start)}]: {line_text!r}")
                return []

    islands = analyzer.active_islands(win_s, win_e)

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
        combined_text = "".join(it["text"] for it in win)
        c_chars = tokenize_text(combined_text)
        c_py = " ".join(lazy_pinyin("".join(c_chars)))
        nom_s = win[0]["start"]

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
