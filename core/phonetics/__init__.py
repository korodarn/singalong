"""
core.phonetics - Unified phonetic tokenizer, romanizer, and dictionary manager.
Supports Mandarin (zh), Japanese (ja), Korean (ko), and English/Latin (en).
"""

import json
import re
from pathlib import Path

from core.phonetics.zh import is_han, process_chinese_tokens, respell
from core.phonetics.ko import (
    is_hangul,
    apply_korean_phonology,
    decompose_hangul,
    HANGUL_CHOSEONG,
    HANGUL_JUNGSEONG,
    HANGUL_JONGSEONG,
)
from core.phonetics.ja import (
    is_kana,
    is_japanese_char,
    has_kana,
    segment_japanese_line,
    process_japanese_tokens,
)
from core.phonetics.en import process_english_tokens, is_latin_word

CUSTOM_DICT_PATH = Path("custom_dict.json")


def load_custom_dict(dict_path: Path = CUSTOM_DICT_PATH) -> dict:
    if dict_path.exists():
        try:
            return json.loads(dict_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_custom_dict(d: dict, dict_path: Path = CUSTOM_DICT_PATH):
    dict_path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def tokenize_text(text: str, lang: str | None = None) -> list[str]:
    text = text.replace("’", "'").replace("‘", "'")
    if lang == "ja" or has_kana(text):
        return [t["text"] for t in segment_japanese_line(text)]
    return re.findall(r"[\u4e00-\u9fff]|[\uac00-\ud7af]|[\u3040-\u30ff]+|[A-Za-z0-9']+", text)


def tokenize_tokens(text: str, lang: str | None = None) -> list[dict]:
    """Extract tokens preserving original trailing whitespace or punctuation breaks."""
    text = text.replace("’", "'").replace("‘", "'")
    if lang == "ja" or has_kana(text):
        return segment_japanese_line(text)

    matches = list(re.finditer(r"[\u4e00-\u9fff]|[\uac00-\ud7af]|[\u3040-\u30ff]+|[A-Za-z0-9']+", text))
    items = []
    for idx, m in enumerate(matches):
        tok = m.group()
        has_space = bool(m.end() < len(text) and re.match(r"^[\s,，.;；!！?？、]", text[m.end():]))
        if not has_space and idx + 1 < len(matches):
            next_tok = matches[idx + 1].group()
            is_cur_latin = bool(re.match(r"^[A-Za-z0-9']+$", tok))
            is_nxt_latin = bool(re.match(r"^[A-Za-z0-9']+$", next_tok))
            if is_cur_latin != is_nxt_latin:
                has_space = True
        items.append({"text": tok, "space": has_space})
    return items


def add_phonetics(tokens: list[dict], custom_dict: dict | None = None, lang: str | None = None):
    if custom_dict is None:
        custom_dict = load_custom_dict()

    # If the token sequence contains Hiragana/Katakana or lang == 'ja', treat Kanji+Kana spans as Japanese
    is_ja_context = (lang == "ja") or any(has_kana(t.get("text", "")) for t in tokens)
    ko_dict = custom_dict.get("ko", {})

    i = 0
    n = len(tokens)
    while i < n:
        t_text = tokens[i].get("text", "")
        if not t_text:
            i += 1
            continue

        if is_ja_context and any(is_japanese_char(ch) for ch in t_text):
            j = i
            while j < n and any(is_japanese_char(ch) for ch in tokens[j].get("text", "")):
                j += 1
            process_japanese_tokens(tokens[i:j], custom_dict)
            i = j
        elif not is_ja_context and len(t_text) == 1 and is_han(t_text):
            j = i
            while j < n and len(tokens[j].get("text", "")) == 1 and is_han(tokens[j]["text"]):
                j += 1
            process_chinese_tokens(tokens[i:j], custom_dict)
            i = j
        elif is_hangul(t_text[0]):
            j = i
            while j < n and tokens[j].get("text") and is_hangul(tokens[j]["text"][0]):
                j += 1
            apply_korean_phonology(tokens[i:j])
            for tok in tokens[i:j]:
                if tok["text"] in ko_dict:
                    tok["pinyin"] = ko_dict[tok["text"]]["pinyin"]
                    tok["en"] = ko_dict[tok["text"]]["en"]
            i = j
        else:
            process_english_tokens([tokens[i]], custom_dict)
            i += 1
