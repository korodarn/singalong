"""
core.phonetics.ko - Hangul phonology, Revised Romanization (RR), and US-English respelling.
"""

def is_hangul(s: str) -> bool:
    return len(s) == 1 and 0xAC00 <= ord(s) <= 0xD7A3


# Choseong indices (0..18):
HANGUL_CHOSEONG = [
    "g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss",
    "", "j", "jj", "ch", "k", "t", "p", "h"
]
HANGUL_JUNGSEONG = [
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa",
    "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i"
]
# Jongseong indices (0..27):
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

JONG_TO_CHO = {
    1: 0, 2: 1, 4: 2, 7: 3, 8: 5, 16: 6, 17: 7, 19: 9,
    20: 10, 22: 12, 23: 14, 24: 15, 25: 16, 26: 17,
}

DOUBLE_JONG_SPLIT = {
    3: (1, 10), 5: (4, 12), 6: (4, 2), 9: (8, 0), 10: (8, 6),
    11: (8, 7), 12: (8, 10), 13: (8, 16), 14: (8, 17), 15: (8, 5), 18: (17, 10),
}

CODA_NEUTRAL = {
    0: 0,
    1: 1, 2: 1, 3: 1, 9: 1, 24: 1,
    4: 4, 5: 4, 6: 4,
    7: 7, 19: 7, 20: 7, 22: 7, 23: 7, 25: 7, 27: 7,
    8: 8, 11: 8, 12: 8, 13: 8, 15: 8,
    10: 16, 16: 16,
    14: 17, 17: 17, 18: 17, 26: 17,
    21: 21,
}

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

        # 1. Liaison & Palatalization
        if same_word and f1 != 0 and c2 == 11:
            if v2 == 20 and f1 in (7, 25, 13):
                if f1 == 7:
                    jamos[i][2], jamos[i + 1][0] = 0, 12
                elif f1 == 25:
                    jamos[i][2], jamos[i + 1][0] = 0, 14
                elif f1 == 13:
                    jamos[i][2], jamos[i + 1][0] = 8, 14
                continue
            if f1 == 27:
                jamos[i][2] = 0
                continue
            if f1 in DOUBLE_JONG_SPLIT:
                rem_f, next_c = DOUBLE_JONG_SPLIT[f1]
                jamos[i][2] = rem_f if rem_f not in (4, 8) or f1 not in (6, 15) else 0
                jamos[i + 1][0] = next_c
                continue
            if f1 in JONG_TO_CHO:
                jamos[i][2] = 0
                jamos[i + 1][0] = JONG_TO_CHO[f1]
                continue

        # 2. Aspiration involving ㅎ
        if f1 in (27, 6, 15) and c2 in ASPIRATE_CHO:
            jamos[i][2] = 0 if f1 == 27 else (4 if f1 == 6 else 8)
            jamos[i + 1][0] = ASPIRATE_CHO[c2]
            continue
        if c2 == 18:
            nf1 = CODA_NEUTRAL.get(f1, f1)
            if nf1 in (1, 7, 17) or f1 in (22, 23):
                if f1 in (22, 23):
                    jamos[i][2], jamos[i + 1][0] = 0, 14
                elif nf1 == 1:
                    jamos[i][2], jamos[i + 1][0] = 0, 15
                elif nf1 == 7:
                    jamos[i][2], jamos[i + 1][0] = 0, 16
                elif nf1 == 17:
                    jamos[i][2], jamos[i + 1][0] = 0, 17
                continue

        # 3. Neutralize remaining coda
        nf1 = CODA_NEUTRAL.get(f1, f1)
        jamos[i][2] = nf1

        # 4. Nasalization
        if c2 in (2, 6):
            if nf1 == 1:
                jamos[i][2] = 21
            elif nf1 == 7:
                jamos[i][2] = 4
            elif nf1 == 17:
                jamos[i][2] = 16

        # 5. Liquidization
        if same_word and ((jamos[i][2] == 4 and c2 == 5) or (jamos[i][2] == 8 and c2 == 2)):
            jamos[i][2] = 8
            jamos[i + 1][0] = 5

    jamos[-1][2] = CODA_NEUTRAL.get(jamos[-1][2], jamos[-1][2])

    for i, (c_idx, j_idx, f_idx) in enumerate(jamos):
        init_rr = HANGUL_CHOSEONG[c_idx]
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
        if vowel_rr == "eu":
            v_en = "oo" if init_rr in ("m", "b", "pp", "p", "n", "r", "l", "s", "ss", "t", "d", "g", "k", "") else "uh"
        token_slice[i]["en"] = init_en + v_en + final_rr
