"""
core.phonetics.zh - Mandarin Pinyin and US-English phonetic respelling.
"""

import re
from pypinyin import Style, lazy_pinyin

INITIALS = [
    "zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l",
    "g", "k", "h", "j", "q", "x", "r", "z", "c", "s"
]

INITIAL_EN = {
    "": "", "b": "b", "p": "p", "m": "m", "f": "f", "d": "d", "t": "t",
    "n": "n", "l": "l", "g": "g", "k": "k", "h": "h",
    "j": "j", "q": "ch", "x": "sh",
    "zh": "j", "ch": "ch", "sh": "sh", "r": "r",
    "z": "dz", "c": "ts", "s": "s",
}

Y_MAP = {
    "yi": "i", "ya": "ia", "ye": "ie", "yao": "iao", "you": "iu",
    "yan": "ian", "yin": "in", "yang": "iang", "ying": "ing",
    "yong": "iong", "yu": "v", "yue": "ve", "yuan": "van", "yun": "vn"
}
W_MAP = {
    "wu": "u", "wa": "ua", "wo": "uo", "wai": "uai", "wei": "ui",
    "wan": "uan", "wen": "un", "wang": "uang", "weng": "ueng"
}

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


def is_han(s: str) -> bool:
    return bool(re.fullmatch(r"[\u4e00-\u9fff]", s))


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


def process_chinese_tokens(token_slice: list[dict], custom_dict: dict | None = None):
    run_str = "".join(t["text"] for t in token_slice)
    marks = lazy_pinyin(run_str, style=Style.TONE)
    nums = lazy_pinyin(run_str, style=Style.TONE3)
    for k, t in enumerate(token_slice):
        t["pinyin"] = marks[k]
        t["en"] = respell(nums[k])

    if custom_dict:
        zh_dict = custom_dict.get("zh", {})
        for word, ov in zh_dict.items():
            pos = run_str.find(word)
            while pos != -1:
                py_parts = ov.get("pinyin", "").split()
                en_parts = ov.get("en", "").split("-")
                for offset in range(len(word)):
                    if offset < len(py_parts):
                        token_slice[pos + offset]["pinyin"] = py_parts[offset]
                    if offset < len(en_parts):
                        token_slice[pos + offset]["en"] = en_parts[offset]
                pos = run_str.find(word, pos + 1)
