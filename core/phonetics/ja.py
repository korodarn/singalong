"""
core.phonetics.ja - Japanese Kanji/Kana tokenization, Hepburn Romaji, and US-English phonetic respelling.
"""

import re
from functools import lru_cache

# Hiragana (\u3040-\u309f), Katakana (\u30a0-\u30ff), Prolonged sound mark (\u30fc), Iteration mark (\u3005)
KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff\u30fc]")
JA_CHAR_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff\u30fc\u3005\u4e00-\u9fff]")

# Common standalone Kanji in Japanese songs where kun-yomi is overwhelmingly sung instead of pykakasi's default on-yomi
SUNG_KANJI_OVERRIDES: dict[str, tuple[str, str]] = {
    "君": ("kimi", "kee-mee"),
    "僕": ("boku", "boh-koo"),
    "私": ("watashi", "wah-tah-shee"),
    "俺": ("ore", "oh-reh"),
    "名": ("na", "nah"),
    "心": ("kokoro", "koh-koh-roh"),
    "空": ("sora", "soh-rah"),
    "風": ("kaze", "kah-zeh"),
    "夢": ("yume", "yoo-meh"),
    "光": ("hikari", "hee-kah-ree"),
    "声": ("koe", "koh-eh"),
    "涙": ("namida", "nah-mee-dah"),
    "愛": ("ai", "eye"),
    "夜": ("yoru", "yoh-roo"),
    "時": ("toki", "toh-kee"),
    "歌": ("uta", "oo-tah"),
    "花": ("hana", "hah-nah"),
    "星": ("hoshi", "hoh-shee"),
    "海": ("umi", "oo-mee"),
    "雨": ("ame", "ah-meh"),
    "雪": ("yuki", "yoo-kee"),
    "月": ("tsuki", "tsoo-kee"),
    "陽": ("hi", "hee"),
    "日": ("hi", "hee"),
    "手": ("te", "teh"),
    "目": ("me", "meh"),
    "瞳": ("hitomi", "hee-toh-mee"),
    "翼": ("tsubasa", "tsoo-bah-sah"),
    "闇": ("yami", "yah-mee"),
    "明日": ("ashita", "ah-shee-tah"),
    "今日": ("kyou", "kyoh"),
    "昨日": ("kinou", "kee-noh"),
    "一人": ("hitori", "hee-toh-ree"),
    "二人": ("futari", "foo-tah-ree"),
    "最": ("sai", "sye"),
    "攫": ("sara", "sah-rah"),
    "視": ("mi", "mee"),
    "此の": ("kono", "koh-noh"),
    "見凝": ("mitsu", "mee-tsoo"),
    "対に": ("tsuini", "tsoo-ee-nee"),
    "還": ("kae", "kah-eh"),
}

# Mora-to-(Romaji, US-English Respelling) lookup table (Hiragana)
MORA_MAP: dict[str, tuple[str, str]] = {
    # Vowels
    "あ": ("a", "ah"), "い": ("i", "ee"), "う": ("u", "oo"), "え": ("e", "eh"), "お": ("o", "oh"),
    "ぁ": ("a", "ah"), "ぃ": ("i", "ee"), "ぅ": ("u", "oo"), "ぇ": ("e", "eh"), "ぉ": ("o", "oh"),
    # K
    "か": ("ka", "kah"), "き": ("ki", "kee"), "く": ("ku", "koo"), "け": ("ke", "keh"), "こ": ("ko", "koh"),
    "きゃ": ("kya", "kyah"), "きゅ": ("kyu", "kyoo"), "きょ": ("kyo", "kyoh"),
    # G
    "が": ("ga", "gah"), "ぎ": ("gi", "gee"), "ぐ": ("gu", "goo"), "げ": ("ge", "geh"), "ご": ("go", "goh"),
    "ぎゃ": ("gya", "gyah"), "ぎゅ": ("gyu", "gyoo"), "ぎょ": ("gyo", "gyoh"),
    # S
    "さ": ("sa", "sah"), "し": ("shi", "shee"), "す": ("su", "soo"), "せ": ("se", "seh"), "そ": ("so", "soh"),
    "しゃ": ("sha", "shah"), "しゅ": ("shu", "shoo"), "しょ": ("sho", "shoh"), "しぇ": ("she", "sheh"),
    # Z / J
    "ざ": ("za", "zah"), "じ": ("ji", "jee"), "ず": ("zu", "zoo"), "ぜ": ("ze", "zeh"), "ぞ": ("zo", "zoh"),
    "じゃ": ("ja", "jah"), "じゅ": ("ju", "joo"), "じょ": ("jo", "joh"), "じぇ": ("je", "jeh"),
    # T
    "た": ("ta", "tah"), "ち": ("chi", "chee"), "つ": ("tsu", "tsoo"), "て": ("te", "teh"), "と": ("to", "toh"),
    "ちゃ": ("cha", "chah"), "ちゅ": ("chu", "choo"), "ちょ": ("cho", "choh"), "ちぇ": ("che", "cheh"),
    "てぃ": ("ti", "tee"), "とぅ": ("tu", "too"),
    # D
    "だ": ("da", "dah"), "ぢ": ("ji", "jee"), "づ": ("zu", "zoo"), "で": ("de", "deh"), "ど": ("do", "doh"),
    "でぃ": ("di", "dee"), "どぅ": ("du", "doo"), "ぢゃ": ("ja", "jah"), "ぢゅ": ("ju", "joo"), "ぢょ": ("jo", "joh"),
    # N
    "な": ("na", "nah"), "に": ("ni", "nee"), "ぬ": ("nu", "noo"), "ね": ("ne", "neh"), "の": ("no", "noh"),
    "にゃ": ("nya", "nyah"), "にゅ": ("nyu", "nyoo"), "にょ": ("nyo", "nyoh"),
    # H / F
    "は": ("ha", "hah"), "ひ": ("hi", "hee"), "ふ": ("fu", "foo"), "へ": ("he", "heh"), "ほ": ("ho", "hoh"),
    "ひゃ": ("hya", "hyah"), "ひゅ": ("hyu", "hyoo"), "ひょ": ("hyo", "hyoh"),
    "ふぁ": ("fa", "fah"), "ふぃ": ("fi", "fee"), "ふぇ": ("fe", "feh"), "ふぉ": ("fo", "foh"),
    # B
    "ば": ("ba", "bah"), "び": ("bi", "bee"), "ぶ": ("bu", "boo"), "べ": ("be", "beh"), "ぼ": ("bo", "boh"),
    "びゃ": ("bya", "byah"), "びゅ": ("byu", "byoo"), "びょ": ("byo", "byoh"),
    # P
    "ぱ": ("pa", "pah"), "ぴ": ("pi", "pee"), "ぷ": ("pu", "poo"), "ぺ": ("pe", "peh"), "ぽ": ("po", "poh"),
    "ぴゃ": ("pya", "pyah"), "ぴゅ": ("pyu", "pyoo"), "ぴょ": ("pyo", "pyoh"),
    # M
    "ま": ("ma", "mah"), "み": ("mi", "mee"), "む": ("mu", "moo"), "め": ("me", "meh"), "も": ("mo", "moh"),
    "みゃ": ("mya", "myah"), "みゅ": ("myu", "myoo"), "みょ": ("myo", "myoh"),
    # Y
    "や": ("ya", "yah"), "ゆ": ("yu", "yoo"), "よ": ("yo", "yoh"),
    # R
    "ら": ("ra", "rah"), "り": ("ri", "ree"), "る": ("ru", "roo"), "れ": ("re", "reh"), "ろ": ("ro", "roh"),
    "りゃ": ("rya", "ryah"), "りゅ": ("ryu", "ryoo"), "りょ": ("ryo", "ryoh"),
    # W
    "わ": ("wa", "wah"), "ゐ": ("i", "ee"), "ゑ": ("e", "eh"), "を": ("o", "oh"),
    "うぃ": ("wi", "wee"), "うぇ": ("we", "weh"), "うぉ": ("wo", "woh"),
    # V
    "ゔ": ("vu", "voo"), "ゔぁ": ("va", "vah"), "ゔぃ": ("vi", "vee"), "ゔぇ": ("ve", "veh"), "ゔぉ": ("vo", "voh"),
    # Syllabic N
    "ん": ("n", "n"),
}


@lru_cache(maxsize=1)
def _get_kakasi():
    import pykakasi
    return pykakasi.kakasi()


def is_kana(ch: str) -> bool:
    if not ch:
        return False
    cp = ord(ch[0])
    return (0x3040 <= cp <= 0x309F) or (0x30A0 <= cp <= 0x30FF) or cp == 0x30FC


def is_japanese_char(ch: str) -> bool:
    if not ch:
        return False
    cp = ord(ch[0])
    return (
        (0x3040 <= cp <= 0x309F)
        or (0x30A0 <= cp <= 0x30FF)
        or cp in (0x30FC, 0x3005)
        or (0x4E00 <= cp <= 0x9FFF)
    )


def has_kana(text: str) -> bool:
    return bool(KANA_RE.search(text))


def kata_to_hira(text: str) -> str:
    """Convert Katakana characters to Hiragana for uniform mora parsing."""
    out = []
    for ch in text:
        cp = ord(ch)
        if 0x30A1 <= cp <= 0x30F6:
            out.append(chr(cp - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def split_morae(hira: str) -> list[str]:
    """Split a Hiragana string into phonetic morae (handling small yoon/vowels)."""
    hira = kata_to_hira(hira)
    morae: list[str] = []
    i = 0
    n = len(hira)
    small_combiners = set("ゃゅょぁぃぅぇぉ")
    while i < n:
        if i + 1 < n and hira[i + 1] in small_combiners and hira[i] not in ("っ", "ん", "ー"):
            morae.append(hira[i : i + 2])
            i += 2
        else:
            morae.append(hira[i])
            i += 1
    return morae


def respell_hira_to_romaji_and_en(hira: str) -> tuple[str, str]:
    """
    Convert a Hiragana reading into (Hepburn Romaji, US-English phonetic respelling).
    Handles diphthongs (ai -> eye/kye), long vowels (ou -> oh, ei -> ay), and sokuon (っ).
    """
    hira = kata_to_hira(hira.strip())
    if not hira:
        return "", ""

    morae = split_morae(hira)
    romaji_parts: list[str] = []
    en_parts: list[str] = []

    i = 0
    m = len(morae)
    geminate = False

    while i < m:
        cur = morae[i]

        # Sokuon (っ) -> doubles following consonant
        if cur == "っ":
            geminate = True
            i += 1
            continue

        # Prolonged sound mark (ー) -> extends previous syllable
        if cur == "ー":
            i += 1
            continue

        rom, en = MORA_MAP.get(cur, (cur, cur))

        # Check next mora for natural diphthong / long-vowel fusion within the same word
        if i + 1 < m:
            nxt = morae[i + 1]
            # Syllabic 'n' attached to current syllable (e.g. かん -> kan / kahn)
            if nxt == "ん":
                rom = rom + "n"
                if en.endswith("h") and len(en) >= 2 and en[-2] in "aeo":
                    en = en[:-1] + "n"
                else:
                    en = en + "n"
                i += 1
            # 'a' + 'i' diphthong (e.g. かい -> kai / kye, あい -> ai / eye)
            elif rom.endswith("a") and nxt == "い":
                onset = rom[:-1]
                rom = onset + "ai"
                if onset == "":
                    en = "eye"
                elif onset in ("k", "s", "t", "n", "m", "r", "b", "p", "g", "z", "d", "w"):
                    en = onset + "ye"
                else:
                    en = onset + "eye"
                i += 1
            # 'e' + 'i' long vowel (e.g. めい -> mei / may, せかい -> sekai)
            elif rom.endswith("e") and nxt == "い":
                onset = rom[:-1]
                rom = onset + "ei"
                en = (onset + "ay") if onset else "ay"
                i += 1
            # 'o' + 'u' or 'o' + 'o' long vowel (e.g. とう -> tou / toh)
            elif rom.endswith("o") and nxt in ("う", "お"):
                onset = rom[:-1]
                rom = onset + ("ou" if nxt == "う" else "oo")
                en = (onset + "oh") if onset else "oh"
                i += 1

        if geminate and rom:
            if rom.startswith("ch"):
                rom = "t" + rom
            else:
                rom = rom[0] + rom
            geminate = False

        romaji_parts.append(rom)
        en_parts.append(en)
        i += 1

    romaji_str = "".join(romaji_parts)
    en_str = "-".join(p for p in en_parts if p)
    return romaji_str, en_str


def segment_japanese_line(text: str) -> list[dict]:
    """
    Segment a Japanese line into natural morphological/reading units using pykakasi,
    preserving trailing whitespace breaks.
    """
    kks = _get_kakasi()
    # Normalize curly apostrophes so English contractions inside Japanese songs stay single words
    text = text.replace("’", "'").replace("‘", "'")
    # Split on whitespace/punctuation while preserving tokens
    raw_chunks = list(re.finditer(r"[\u3040-\u309f\u30a0-\u30ff\u30fc\u3005\u4e00-\u9fff]+|[A-Za-z0-9']+", text))
    out: list[dict] = []

    for idx, match in enumerate(raw_chunks):
        chunk = match.group()
        chunk_has_space = bool(
            match.end() < len(text) and re.match(r"^[\s,，.;；!！?？、・]", text[match.end() :])
        )
        if re.match(r"^[A-Za-z0-9']+$", chunk):
            out.append({"text": chunk, "space": chunk_has_space})
            continue

        # Convert Japanese chunk via pykakasi into reading units
        conv = kks.convert(chunk)
        for c_idx, item in enumerate(conv):
            orig = item.get("orig", "").strip()
            if not orig:
                continue
            is_last_in_chunk = c_idx == len(conv) - 1
            out.append({
                "text": orig,
                "space": chunk_has_space if is_last_in_chunk else False,
            })
    return out


def process_japanese_tokens(tokens: list[dict], custom_dict: dict | None = None):
    """
    Populate 'pinyin' (Hepburn Romaji) and 'en' (US-English phonetic respelling)
    for a contiguous list of Japanese tokens.
    """
    if not tokens:
        return
    ja_dict = (custom_dict or {}).get("ja", {})
    kks = _get_kakasi()

    for idx, tok in enumerate(tokens):
        txt = tok.get("text", "")
        if not txt:
            continue

        # 1. User custom dictionary override
        if txt in ja_dict:
            tok["pinyin"] = ja_dict[txt]["pinyin"]
            tok["en"] = ja_dict[txt]["en"]
            continue

        # 2. Sung lyric standalone Kanji override
        if txt in SUNG_KANJI_OVERRIDES:
            rom, en = SUNG_KANJI_OVERRIDES[txt]
            tok["pinyin"] = rom
            tok["en"] = en
            continue

        # 3. Standalone grammatical particles
        if txt == "を":
            tok["pinyin"] = "o"
            tok["en"] = "oh"
            continue
        if txt == "へ" and idx > 0:
            tok["pinyin"] = "e"
            tok["en"] = "eh"
            continue
        if txt == "は" and idx > 0:
            tok["pinyin"] = "wa"
            tok["en"] = "wah"
            continue

        # 4. Convert via pykakasi -> Hiragana -> Moraic Romaji & US-English respelling
        conv = kks.convert(txt)
        hira = "".join(item.get("hira", "") for item in conv)
        if hira:
            rom, en = respell_hira_to_romaji_and_en(hira)
            tok["pinyin"] = rom or txt
            tok["en"] = en or txt
        else:
            tok["pinyin"] = txt
            tok["en"] = txt
