"""
core.lyrics - Unified lyrics discovery, validation, and multi-source provider aggregation.
"""

import json
import re
from pathlib import Path
from typing import Any

from core.lyrics.parser import (
    parse_lrc,
    clean_lyrics_text,
    compute_text_similarity,
    is_credit_line,
)
from core.lyrics.validator import (
    clean_search_query,
    detect_script_distribution,
    validate_lyrics_candidate,
)
from core.lyrics.lrclib import query_lrclib
from core.lyrics.netease import (
    fetch_netease_lyrics,
    query_netease_api,
    search_netease_candidates,
)
from core.lyrics.youtube_cc import fetch_youtube_lyrics
from core.media import extract_youtube_id


def format_clean_lrc_for_preview(raw_lyrics: str) -> str:
    """
    Return LRC with studio credit lines stripped out so the user immediately sees
    the actual timestamped sung lyrics in the preview editor.
    """
    if not raw_lyrics:
        return ""
    out = []
    for line in raw_lyrics.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^\[(\d+:\d+(?:\.\d+)?)\](.*)$", s)
        if m:
            stamp, txt = m.group(1), m.group(2).strip()
            if not txt or is_credit_line(txt):
                continue
            out.append(f"[{stamp}] {txt}")
        else:
            if not is_credit_line(s):
                out.append(s)
    return "\n".join(out) if out else raw_lyrics


def search_all_lyrics(
    query: str = "",
    url_or_vid: str | None = None,
    target_lang: str | None = None,
    asr_text: str | None = None,
    audio_duration_s: float | None = None,
    workdir: Path = Path("work"),
    limit: int = 12,
) -> list[dict[str, Any]]:
    """
    Query all supported providers (Cached LRC, NetEase, LRCLIB, YouTube CC/Description)
    and return sense-checked, scored candidates with credit lines stripped from previews.
    """
    vid = extract_youtube_id(url_or_vid) if url_or_vid else None
    resolved_title = query.strip() if query else ""

    # Auto-load cached metadata & ASR transcript from workdir if available
    if vid and workdir.exists():
        meta_path = workdir / f"{vid}.meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if not resolved_title:
                    resolved_title = meta.get("title", "")
                if not audio_duration_s:
                    audio_duration_s = float(meta.get("duration", 0)) or None
            except Exception:
                pass

        if not asr_text:
            asr_path = workdir / f"{vid}.asr.json"
            if asr_path.exists():
                try:
                    asr_data = json.loads(asr_path.read_text(encoding="utf-8"))
                    asr_text = asr_data.get("text", "")
                except Exception:
                    pass

    clean_q = clean_search_query(resolved_title) if resolved_title else ""
    queries_to_try = []
    if clean_q:
        queries_to_try.append(clean_q)
        # If query has " - " (e.g. "Eisodus - Huang Xiaoyun"), also try just the song title part
        if " - " in clean_q:
            first_part = clean_q.split(" - ", 1)[0].strip()
            if len(first_part) >= 3 and first_part not in queries_to_try:
                queries_to_try.append(first_part)
    if resolved_title and resolved_title not in queries_to_try:
        queries_to_try.append(resolved_title)

    candidates: list[dict[str, Any]] = []
    seen_keys = set()

    def add_cand(c: dict[str, Any]):
        key = (
            c.get("source"),
            c.get("title", "").strip().lower(),
            c.get("artist", "").strip().lower(),
            c.get("line_count", 0),
        )
        if key in seen_keys:
            return
        seen_keys.add(key)

        lyrics_text = c.get("raw_lyrics", "")
        clean_preview = format_clean_lrc_for_preview(lyrics_text)
        c["clean_lyrics"] = clean_preview

        script_info = detect_script_distribution(lyrics_text)
        sim = compute_text_similarity(asr_text, lyrics_text) if asr_text else None
        is_valid, reason, score = validate_lyrics_candidate(
            lyrics_text,
            target_lang=target_lang,
            asr_text=asr_text,
            similarity_score=sim,
            query_title=clean_q or resolved_title,
            cand_title=c.get("title"),
            audio_duration_s=audio_duration_s,
            cand_duration_s=c.get("duration"),
        )

        c["script"] = script_info
        c["validation"] = {
            "is_valid": is_valid,
            "reason": reason,
            "score": score,
        }
        candidates.append(c)

    # 0. Check existing local cached LRC in workdir
    if vid and workdir.exists():
        cached_lrc = workdir / f"{vid}.netease.lrc"
        if cached_lrc.exists():
            try:
                raw_cached = cached_lrc.read_text(encoding="utf-8")
                parsed_cached = parse_lrc(raw_cached)
                if parsed_cached:
                    add_cand({
                        "id": f"cached_{vid}",
                        "source": "cached_lrc",
                        "title": clean_q or resolved_title or vid,
                        "artist": "Local Verified Cache",
                        "album": f"{vid}.netease.lrc",
                        "duration": audio_duration_s or 0,
                        "is_synced": True,
                        "synced_lyrics": raw_cached,
                        "plain_lyrics": "\n".join(it["text"] for it in parsed_cached),
                        "raw_lyrics": raw_cached,
                        "parsed": parsed_cached,
                        "line_count": len(parsed_cached),
                    })
            except Exception:
                pass

    # 1. Query LRCLIB
    for q in queries_to_try[:2]:
        for c in query_lrclib(q, limit=4):
            add_cand(c)

    # 2. Query NetEase by title
    for q in queries_to_try[:2]:
        for c in search_netease_candidates(q, limit=5):
            add_cand(c)

    # 3. Query NetEase by ASR sung Chinese phrases ONLY when audio is predominantly Chinese
    if asr_text and (not target_lang or target_lang.lower() in ("auto", "zh", "mandarin", "chinese")):
        asr_script = detect_script_distribution(asr_text)
        if asr_script["han_ratio"] >= 0.65:
            asr_han = re.findall(r"[\u4e00-\u9fff]", asr_text)
            if len(asr_han) >= 12:
                for offset in (4, 10):
                    if offset + 10 <= len(asr_han):
                        p1 = "".join(asr_han[offset : offset + 5])
                        p2 = "".join(asr_han[offset + 5 : offset + 10])
                        for c in search_netease_candidates(f"{p1} {p2}", limit=3):
                            add_cand(c)

    # 4. Check YouTube metadata if URL / vid provided
    if url_or_vid:
        for c in fetch_youtube_lyrics(url_or_vid):
            add_cand(c)

    # Sort: valid first, highest score first, synced preferred on ties
    def sort_key(c):
        v = c["validation"]
        is_valid_val = 1 if v["is_valid"] else 0
        score_val = round(v.get("score", 0.0), 2)
        is_synced_val = 1 if c.get("is_synced") else 0
        return (is_valid_val, score_val, is_synced_val)

    candidates.sort(key=sort_key, reverse=True)
    return candidates[:limit]
