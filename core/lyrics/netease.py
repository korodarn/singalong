"""
core.lyrics.netease - NetEase Cloud Music lyrics search and verification.
"""

import re
from pathlib import Path
import requests

from core.lyrics.parser import parse_lrc, compute_text_similarity
from core.phonetics.zh import is_han
from core.phonetics.ko import is_hangul


def query_netease_api(query_str: str) -> list[dict]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://music.163.com/",
        "Cookie": "os=pc",
    }
    try:
        r = requests.post(
            "https://music.163.com/api/cloudsearch/pc",
            data={"s": query_str, "type": 1, "limit": 8},
            headers=headers,
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("result", {}).get("songs", [])
    except Exception as e:
        print(f"  ! NetEase API request failed for {query_str!r}: {e}")
        return []


def fetch_netease_lyrics(query: str, workdir: Path, vid: str, asr_text: str | None = None, force_refresh: bool = False):
    """
    Search NetEase Cloud Music with automated ASR cross-verification.
    Validates candidates against ASR text to prevent grabbing wrong-song homophones or unrelated foreign tracks.
    """
    lrc_path = workdir / f"{vid}.netease.lrc"
    if lrc_path.exists() and not force_refresh:
        raw_cached = lrc_path.read_text(encoding="utf-8")
        if asr_text:
            sim = compute_text_similarity(asr_text, raw_cached)
            asr_has_han = any(is_han(c) for c in asr_text[:200])
            lrc_has_han = any(is_han(c) for c in raw_cached[:500])
            asr_has_hangul = any(is_hangul(c) for c in asr_text[:200])
            lrc_has_hangul = any(is_hangul(c) for c in raw_cached[:500])
            script_mismatch = (asr_has_han and not lrc_has_han and lrc_has_hangul) or (asr_has_hangul and not lrc_has_hangul and lrc_has_han)

            if sim < 0.03 or script_mismatch:
                print(f"  [!] Cached LRC {lrc_path.name} failed ASR verification (overlap: {sim:.3f}, script_mismatch: {script_mismatch}). Re-fetching...")
            else:
                print(f"[*] Reusing verified NetEase LRC: {lrc_path} (ASR match score: {sim:.3f})")
                return parse_lrc(raw_cached)
        else:
            print(f"[*] Reusing cached NetEase LRC: {lrc_path}")
            return parse_lrc(raw_cached)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://music.163.com/",
        "Cookie": "os=pc",
    }

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
        songs = query_netease_api(q)
        if not songs:
            continue

        for s in songs:
            sid = s["id"]
            sname = s["name"]
            artists = ", ".join(a["name"] for a in s.get("artists", []))

            l_url = f"https://music.163.com/api/song/lyric?os=pc&id={sid}&lv=-1&kv=-1&tv=-1"
            try:
                l_res = requests.get(l_url, headers=headers, timeout=10)
                if l_res.status_code != 200:
                    continue
                lrc_raw = l_res.json().get("lrc", {}).get("lyric", "")
                if not lrc_raw or "纯音乐" in lrc_raw:
                    continue

                parsed = parse_lrc(lrc_raw)
                if not parsed:
                    continue

                sim = compute_text_similarity(asr_text, lrc_raw) if asr_text else 1.0
                print(f"  -> Candidate: [{sid}] {sname} - {artists} (lines: {len(parsed)}, ASR match: {sim:.3f})")

                if sim > best_sim:
                    best_sim = sim
                    best_candidate = (sid, sname, artists)
                    best_parsed = parsed
                    best_raw = lrc_raw

                if asr_text and sim >= 0.12:
                    break
            except Exception:
                continue

        if asr_text and best_sim >= 0.12:
            break

    if best_parsed and (not asr_text or best_sim > 0.015):
        sid, sname, artists = best_candidate
        print(f"  [+] Selected NetEase lyrics: [{sid}] {sname} - {artists} (ASR match score: {best_sim:.3f})")
        lrc_path.write_text(best_raw, encoding="utf-8")
        print(f"  -> Saved verified NetEase LRC: {lrc_path}")
        return best_parsed

    print("  ! No verified NetEase lyrics found matching audio transcript.")
    return None
