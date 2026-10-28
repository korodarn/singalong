"""
core.phonetics - Unified phonetic tokenizer, romanizer, and dictionary manager.
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


def tokenize_text(text: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fff]|[\uac00-\ud7af]|[A-Za-z0-9']+", text)


def tokenize_tokens(text: str) -> list[dict]:
    """Extract tokens preserving original trailing whitespace or punctuation breaks."""
    matches = list(re.finditer(r"[\u4e00-\u9fff]|[\uac00-\ud7af]|[A-Za-z0-9']+", text))
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


def add_phonetics(tokens: list[dict], custom_dict: dict | None = None):
    if custom_dict is None:
        custom_dict = load_custom_dict()

    i = 0
    while i < len(tokens):
        t_text = tokens[i]["text"]
        if is_han(t_text):
            j = i
            while j < len(tokens) and is_han(tokens[j]["text"]):
                j += 1
            process_chinese_tokens(tokens[i:j], custom_dict)
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
