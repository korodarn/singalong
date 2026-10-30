"""
core.phonetics.en - English and Latin word handling.
"""

import re


def is_latin_word(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9']+", s))


def process_english_tokens(token_slice: list[dict], custom_dict: dict | None = None):
    en_dict = (custom_dict or {}).get("en", {})
    for t in token_slice:
        txt = t.get("text", "")
        if txt in en_dict:
            t["pinyin"] = en_dict[txt].get("pinyin", txt)
            t["en"] = en_dict[txt].get("en", txt)
        else:
            t["pinyin"] = txt
            t["en"] = txt
