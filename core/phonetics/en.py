"""
core.phonetics.en - English and Latin word handling.
"""

import re


def is_latin_word(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9']+", s))


def process_english_tokens(token_slice: list[dict]):
    for t in token_slice:
        t["pinyin"] = t["text"]
        t["en"] = t["text"]
