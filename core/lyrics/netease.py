"""
core.lyrics.netease - NetEase Cloud Music lyrics search and verification.
"""

import re
from pathlib import Path
from typing import Any
import requests

from core.lyrics.parser import parse_lrc, compute_text_similarity
from core.lyrics.validator import validate_lyrics_candidate, detect_script_distribution
from core.phonetics.zh import is_han
from core.phonetics.ko import is_hangul


def query_netease_api(query_str: str, limit: int = 8) -> list[dict]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://music.163.com/",
        "Cookie": "os=pc",
    }
    try:
        r = requests.post(
            "https://music.163.com/api/cloudsearch/pc",
            data={"s": query_str, "type": 1, "limit": limit},
            headers=headers,
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("result", {}).get("songs", [])
    except Exception as e:
        print(f"  ! NetEase API request failed for {query_str!r}: {e}")
        return []


def search_netease_candidates(query_str: str, limit: int = 5) -> list[dict[str, Any]]:
    """
    Search NetEase and fetch lyrics for candidates into standardized dict format.
    """
    songs = query_netease_api(query_str, limit=limit)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://music.163.com/",
        "Cookie": "os=pc",
    }
    results = []
    for s in songs:
        sid = s["id"]
        sname = s.get("name", "Unknown")
        if "伴奏" in sname or "instrumental" in sname.lower():
            continue
        artists = ", ".join(a.get("name", "") for a in s.get("artists", []))
        duration_s = float(s.get("dt", 0)) / 1000.0

        l_url = f"https://music.163.com/api/song/lyric?os=pc&id={sid}&lv=-1&kv=-1&tv=-1"
        try:
            l_res = requests.get(l_url, headers=headers, timeout=8)
            if l_res.status_code != 200:
                continue
            lrc_raw = l_res.json().get("lrc", {}).get("lyric", "")
            if not lrc_raw or "纯音乐" in lrc_raw:
                continue

            parsed = parse_lrc(lrc_raw)
            if not parsed:
                continue

            results.append({
                "id": str(sid),
                "source": "netease",
                "title": sname,
                "artist": artists,
                "album": s.get("al", {}).get("name", ""),
                "duration": duration_s,
                "is_synced": True,
                "synced_lyrics": lrc_raw,
                "plain_lyrics": "\n".join(it["text"] for it in parsed),
                "raw_lyrics": lrc_raw,
                "parsed": parsed,
                "line_count": len(parsed),
            })
        except Exception:
            continue

    return results


def fetch_netease_lyrics(
    query: str,
    workdir: Path,
    vid: str,
    asr_text: str | None = None,
    target_lang: str | None = None,
    force_refresh: bool = False,
):
    """
    Search NetEase Cloud Music with automated ASR and script cross-verification.
    Validates candidates against ASR text and target language to prevent grabbing wrong-song homophones or unrelated foreign tracks.
    """
    lrc_path = workdir / f"{vid}.netease.lrc"
    if lrc_path.exists() and not force_refresh:
        raw_cached = lrc_path.read_text(encoding="utf-8")
        if asr_text:
            sim = compute_text_similarity(asr_text, raw_cached)
            is_valid, reason, _ = validate_lyrics_candidate(raw_cached, target_lang=target_lang, asr_text=asr_text, similarity_score=sim)
            if not is_valid or sim < 0.08:
                print(f"  [!] Cached LRC {lrc_path.name} failed verification ({reason}). Re-fetching...")
            else:
                print(f"[*] Reusing verified NetEase LRC: {lrc_path} (ASR match score: {sim:.3f})")
                return parse_lrc(raw_cached)
        else:
            print(f"[*] Reusing cached NetEase LRC: {lrc_path}")
            return parse_lrc(raw_cached)

    search_queries = [query] if query else []

    if asr_text:
        asr_han = re.findall(r"[\u4e00-\u9fff]", asr_text)
        if len(asr_han) >= 12:
            for offset in (4, 8, 14, 20):
                if offset + 10 <= len(asr_han):
                    p1 = "".join(asr_han[offset:offset + 5])
                    p2 = "".join(asr_han[offset + 5:offset + 10])
                    search_queries.append(f"{p1} {p2}")

    best_candidate = None
    best_sim = -1.0
    best_parsed = None
    best_raw = None

    for q_idx, q in enumerate(search_queries):
        print(f"[*] Querying NetEase Cloud Music [{q_idx + 1}/{len(search_queries)}]: {q!r}...")
        candidates = search_netease_candidates(q, limit=6)
        if not candidates:
            continue

        for cand in candidates:
            lrc_raw = cand["raw_lyrics"]
            parsed = cand["parsed"]
            sim = compute_text_similarity(asr_text, lrc_raw) if asr_text else 1.0

            is_valid, reason, _ = validate_lyrics_candidate(
                lrc_raw,
                target_lang=target_lang,
                asr_text=asr_text,
                similarity_score=sim,
            )
            print(f"  -> Candidate: [{cand['id']}] {cand['title']} - {cand['artist']} (lines: {len(parsed)}, match: {sim:.3f}, valid: {is_valid} [{reason}])")

            if not is_valid:
                continue

            if sim > best_sim:
                best_sim = sim
                best_candidate = (cand["id"], cand["title"], cand["artist"])
                best_parsed = parsed
                best_raw = lrc_raw

            if asr_text and sim >= 0.12:
                break

        if asr_text and best_sim >= 0.12:
            break

    # Raised similarity threshold from 0.015 to 0.08 to prevent false homophone matches
    min_required_sim = 0.08 if asr_text else 0.0
    if best_parsed and (not asr_text or best_sim >= min_required_sim):
        sid, sname, artists = best_candidate
        print(f"  [+] Selected NetEase lyrics: [{sid}] {sname} - {artists} (ASR match score: {best_sim:.3f})")
        lrc_path.write_text(best_raw, encoding="utf-8")
        print(f"  -> Saved verified NetEase LRC: {lrc_path}")
        return best_parsed

    print("  ! No verified NetEase lyrics found matching audio transcript.")
    return None
