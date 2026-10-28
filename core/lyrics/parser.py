"""
core.lyrics.parser - LRC and plain-text lyrics parsing and similarity scoring.
"""

import re


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
    set_a = set(chars_a)
    set_b = set(chars_b)
    unigram_sim = len(set_a & set_b) / max(1, len(set_a | set_b))

    bg_a = set(zip(chars_a[:-1], chars_a[1:]))
    bg_b = set(zip(chars_b[:-1], chars_b[1:]))
    bigram_sim = (len(bg_a & bg_b) / max(1, len(bg_a | bg_b))) if (bg_a and bg_b) else unigram_sim
    return max(unigram_sim * 0.5, bigram_sim)
