"""
core.lyrics.validator - Script detection, query cleaning, and lyrics candidate validation.
"""

import re
from typing import TypedDict
from core.lyrics.parser import clean_lyrics_text, compute_text_similarity, is_credit_line


class ScriptDistribution(TypedDict):
    total_chars: int
    latin_ratio: float
    han_ratio: float
    hangul_ratio: float
    kana_ratio: float
    dominant: str


def clean_search_query(title_or_query: str) -> str:
    """
    Clean YouTube video titles or raw queries to extract the song and artist names.
    Strips noise like '[Wuthering Waves]', '【Official MV】', 'OST', '(CN ver.)', 'Official ... Lyrics', etc.
    """
    if not title_or_query:
        return ""

    s = title_or_query.strip()

    # Remove bracketed/parenthetical metadata tags
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"【[^】]*】", " ", s)
    s = re.sub(
        r"\((?:official|mv|audio|lyric|ost|full|hd|4k|original|cn\s*ver|en\s*ver|jp\s*ver|kr\s*ver|prod\.?|feat\.?|ft\.?)[^\)]*\)",
        " ",
        s,
        flags=re.IGNORECASE,
    )

    # Remove common YouTube / fan-channel descriptive suffixes
    noise_phrases = [
        r"official\s+(?:chinese\s+)?(?:pinyin\s*/?\s*)?(?:english\s+)?(?:romaji\s+)?(?:music\s+video|mv|audio|lyric\s+video|lyrics|video)",
        r"(?:chinese|english|japanese|korean)?\s*pinyin\s*/?\s*english\s*lyrics",
        r"wuthering\s+waves\s*(?:ost|theme\s+song|ep)?",
        r"original\s+(?:game\s+)?soundtrack",
        r"-\s*topic\b",
    ]
    for pat in noise_phrases:
        s = re.sub(pat, " ", s, flags=re.IGNORECASE)

    s = re.sub(r"[|_•]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^[\s\-_–—]+|[\s\-_–—]+$", "", s).strip()
    return s


def detect_script_distribution(text: str) -> ScriptDistribution:
    """
    Analyze the distribution of scripts in the actual sung lyrics (excluding LRC timestamps and credits):
    Latin, Han (Hanzi/Kanji), Hangul, and Kana.
    """
    cleaned = clean_lyrics_text(text) if text else ""
    if not cleaned:
        return {
            "total_chars": 0,
            "latin_ratio": 0.0,
            "han_ratio": 0.0,
            "hangul_ratio": 0.0,
            "kana_ratio": 0.0,
            "dominant": "none",
        }

    # Count Latin words as ~3 chars weight or character count so CJK and Latin are balanced
    latin_chars = len(re.findall(r"[A-Za-z]", cleaned))
    han_chars = len(re.findall(r"[\u4e00-\u9fff]", cleaned))
    hangul_chars = len(re.findall(r"[\uac00-\ud7af]", cleaned))
    kana_chars = len(re.findall(r"[\u3040-\u309f\u30a0-\u30ff]", cleaned))

    total = latin_chars + han_chars + hangul_chars + kana_chars
    if total == 0:
        return {
            "total_chars": 0,
            "latin_ratio": 0.0,
            "han_ratio": 0.0,
            "hangul_ratio": 0.0,
            "kana_ratio": 0.0,
            "dominant": "none",
        }

    ratios = {
        "latin": latin_chars / total,
        "han": han_chars / total,
        "hangul": hangul_chars / total,
        "kana": kana_chars / total,
    }

    if ratios["hangul"] > 0.25:
        dominant = "ko"
    elif ratios["kana"] > 0.12 or (ratios["han"] > 0.25 and ratios["kana"] > 0.04):
        dominant = "ja"
    elif ratios["han"] > 0.35 and ratios["kana"] == 0:
        dominant = "zh"
    elif ratios["latin"] > 0.65:
        dominant = "en"
    else:
        dominant = "mixed"

    return {
        "total_chars": total,
        "latin_ratio": round(ratios["latin"], 3),
        "han_ratio": round(ratios["han"], 3),
        "hangul_ratio": round(ratios["hangul"], 3),
        "kana_ratio": round(ratios["kana"], 3),
        "dominant": dominant,
    }


def _titles_match(query_title: str | None, cand_title: str | None) -> bool:
    if not query_title or not cand_title:
        return False
    q_clean = re.sub(r"[^a-z0-9\u4e00-\u9fff\uac00-\ud7af]", "", clean_search_query(query_title).lower())
    c_clean = re.sub(r"[^a-z0-9\u4e00-\u9fff\uac00-\ud7af]", "", clean_search_query(cand_title).lower())
    if not q_clean or not c_clean:
        return False
    return (q_clean in c_clean) or (c_clean in q_clean)


def validate_lyrics_candidate(
    candidate_text: str,
    target_lang: str | None = None,
    asr_text: str | None = None,
    similarity_score: float | None = None,
    query_title: str | None = None,
    cand_title: str | None = None,
    audio_duration_s: float | None = None,
    cand_duration_s: float | None = None,
) -> tuple[bool, str, float]:
    """
    Sense-check a lyrics candidate against expected language, title/duration, and ASR transcript.
    Returns: (is_valid, reason, confidence)
    """
    if not candidate_text or not candidate_text.strip():
        return False, "Empty lyrics", 0.0

    cleaned = clean_lyrics_text(candidate_text)
    clean_lines = [l for l in cleaned.splitlines() if l.strip() and not is_credit_line(l)]
    if len(clean_lines) < 4:
        return False, f"Too few non-credit lyric lines ({len(clean_lines)} lines)", 0.05

    script = detect_script_distribution(cleaned)
    if script["total_chars"] < 15:
        return False, "Lyrics too short (<15 characters)", 0.05

    # Check duration mismatch if both known
    if audio_duration_s and cand_duration_s and audio_duration_s > 30 and cand_duration_s > 30:
        if abs(audio_duration_s - cand_duration_s) > 35:
            return (
                False,
                f"Duration mismatch: song is {int(audio_duration_s)}s, candidate is {int(cand_duration_s)}s",
                0.1,
            )

    # Check explicit target_lang constraints
    if target_lang and target_lang.lower() not in ("auto", ""):
        t = target_lang.lower().strip()
        if t in ("en", "english"):
            if script["han_ratio"] > 0.15 and script["latin_ratio"] < 0.60:
                return (
                    False,
                    f"Script mismatch: expected English but found {script['han_ratio']*100:.0f}% Hanzi",
                    0.1,
                )
        elif t in ("zh", "mandarin", "chinese"):
            if script["latin_ratio"] > 0.70 and script["han_ratio"] < 0.20:
                return (
                    False,
                    f"Script mismatch: expected Chinese but found {script['latin_ratio']*100:.0f}% Latin",
                    0.1,
                )
        elif t in ("ko", "korean"):
            if script["hangul_ratio"] < 0.15:
                return (
                    False,
                    f"Script mismatch: expected Korean Hangul but found {script['hangul_ratio']*100:.0f}%",
                    0.1,
                )
        elif t in ("ja", "japanese"):
            if script["kana_ratio"] < 0.05 and script["han_ratio"] < 0.15:
                return (
                    False,
                    "Script mismatch: expected Japanese Kana/Kanji",
                    0.1,
                )

    title_matched = _titles_match(query_title, cand_title)
    dur_matched = bool(
        audio_duration_s and cand_duration_s and abs(audio_duration_s - cand_duration_s) <= 12
    )

    # Check ASR compatibility if ASR text is available
    if asr_text:
        asr_script = detect_script_distribution(asr_text)
        # 1. Audio is heavily English (>60% Latin) but candidate is heavily Hanzi (>35% Hanzi)
        if asr_script["latin_ratio"] > 0.60 and script["han_ratio"] > 0.35:
            return (
                False,
                f"Audio is English ({asr_script['latin_ratio']*100:.0f}% Latin), but candidate is {script['han_ratio']*100:.0f}% Hanzi",
                0.05,
            )
        # 2. Audio is heavily Chinese (>75% Hanzi) but candidate has almost no Hanzi (<20% Hanzi)
        if asr_script["han_ratio"] > 0.75 and script["han_ratio"] < 0.20:
            return (
                False,
                f"Audio is Chinese ({asr_script['han_ratio']*100:.0f}% Hanzi), but candidate is {script['dominant'].upper()}",
                0.08,
            )

        if similarity_score is None:
            similarity_score = compute_text_similarity(asr_text, candidate_text)
        sim = similarity_score

        # If audio contains English (>30% Latin) AND candidate is English AND title matches (e.g. "Thawing Light"),
        # paraformer-zh ASR may have transcribed some English singing into Chinese homophones ("每一段那"),
        # lowering literal overlap. Trust Title + English script match!
        if asr_script["latin_ratio"] >= 0.30 and script["dominant"] == "en" and title_matched and (dur_matched or not cand_duration_s):
            score = max(sim, 0.85 if dur_matched else 0.75)
            return True, f"Verified English title match ({len(clean_lines)} lines)", round(score, 3)

        if sim < 0.06:
            return (
                False,
                f"Low audio transcript match ({sim*100:.1f}%)",
                round(sim, 3),
            )

        boost = 0.25 if (title_matched and dur_matched) else (0.10 if title_matched else 0.0)
        score = min(0.99, sim + boost)
        return True, f"Verified ({script['dominant'].upper()}, {sim*100:.0f}% ASR overlap)", round(score, 3)

    # Without ASR text: score by title, duration, and target_lang match
    base_score = 0.55
    if title_matched:
        base_score += 0.25
    if dur_matched:
        base_score += 0.15
    if target_lang and script["dominant"] == target_lang:
        base_score += 0.10

    return (
        True,
        f"Valid candidate ({script['dominant'].upper()}, {len(clean_lines)} lines)",
        round(min(0.98, base_score), 3),
    )
