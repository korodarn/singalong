"""
core.lyrics.parser - LRC and plain-text lyrics parsing, credit stripping, and similarity scoring.
"""

import re
from pypinyin import lazy_pinyin


CREDIT_LINE_RE = re.compile(
    r"^\s*(?:lyrics|lyricist|music|composer|composed by|arranger|arrangement|arranged by|"
    r"vocal|vocals|backing vocals|vocal producer|vocal recording|instrumental|guitar|bass|drums|"
    r"guzheng|guqin|pipa|dizi|violin|cello|choir|orchestra|conductor|studio|mixing|mastering|"
    r"planning|coordination|business|supervisor|producer|producers|produced by|special thanks|"
    r"music production|project execution|"
    r"作词|作曲|编曲|演唱|制作人|制作|配唱|和声|吉他|贝斯|鼓|古筝|古琴|琵琶|笛子|弦乐|乐团|指挥|"
    r"录音|混音|母带|统筹|监制|出品|鸣谢|发行|词|曲)\s*[:：\-—/]",
    re.IGNORECASE,
)


def is_credit_line(text: str) -> bool:
    """Return True if a line is a production/studio credit rather than sung lyrics."""
    s = text.strip()
    if not s:
        return True
    if ":" in s or "：" in s:
        return True
    if CREDIT_LINE_RE.match(s):
        return True
    return False


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
        if not text or is_credit_line(text):
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


def clean_lyrics_text(raw_text: str) -> str:
    """
    Extract only the actual sung lyric lines from LRC or plain text,
    stripping timestamps and studio credit lines.
    """
    if not raw_text:
        return ""
    parsed = parse_lrc(raw_text)
    if parsed:
        return "\n".join(it["text"] for it in parsed)

    out = []
    for line in raw_text.splitlines():
        s = re.sub(r"^\[\d+:\d+(?:\.\d+)?\]", "", line).strip()
        if not s or is_credit_line(s):
            continue
        out.append(s)
    return "\n".join(out)


def compute_text_similarity(text_a: str, text_b: str) -> float:
    """
    Compute character/word + phonetic (pinyin/romaji) Jaccard similarity between two texts.
    Strips LRC timestamps and studio credits before comparison.
    """
    clean_a = clean_lyrics_text(text_a) if "[" in text_a or ":" in text_a or "：" in text_a else text_a
    clean_b = clean_lyrics_text(text_b) if "[" in text_b or ":" in text_b or "：" in text_b else text_b

    chars_a = re.findall(r"[\u3040-\u309f\u30a0-\u30ff]|[\u4e00-\u9fff]|[\uac00-\ud7af]|[A-Za-z0-9']+", clean_a.lower())
    chars_b = re.findall(r"[\u3040-\u309f\u30a0-\u30ff]|[\u4e00-\u9fff]|[\uac00-\ud7af]|[A-Za-z0-9']+", clean_b.lower())
    if not chars_a or not chars_b:
        return 0.0

    set_a = set(chars_a)
    set_b = set(chars_b)
    unigram_sim = len(set_a & set_b) / max(1, len(set_a | set_b))

    bg_a = set(zip(chars_a[:-1], chars_a[1:]))
    bg_b = set(zip(chars_b[:-1], chars_b[1:]))
    bigram_sim = (len(bg_a & bg_b) / max(1, len(bg_a | bg_b))) if (bg_a and bg_b) else unigram_sim
    raw_sim = max(unigram_sim * 0.5, bigram_sim)

    # Also compute phonetic (pinyin) similarity so Chinese homophones from ASR match official lyrics
    han_a = [c for c in chars_a if "\u4e00" <= c <= "\u9fff"]
    han_b = [c for c in chars_b if "\u4e00" <= c <= "\u9fff"]
    if len(han_a) >= 8 and len(han_b) >= 8:
        py_a = lazy_pinyin("".join(han_a))
        py_b = lazy_pinyin("".join(han_b))
        py_bg_a = set(zip(py_a[:-1], py_a[1:]))
        py_bg_b = set(zip(py_b[:-1], py_b[1:]))
        if py_bg_a and py_bg_b:
            py_sim = len(py_bg_a & py_bg_b) / max(1, len(py_bg_a | py_bg_b))
            return max(raw_sim, py_sim * 0.85)

    return raw_sim
