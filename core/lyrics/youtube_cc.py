"""
core.lyrics.youtube_cc - YouTube closed captions and description lyrics extractor.
"""

import re
from typing import Any
import yt_dlp

from core.lyrics.parser import is_credit_line


def extract_lyrics_from_description(description: str) -> str | None:
    """
    Search description text for a lyrics section (e.g. 'Lyrics:', '歌词:', '가사:', '歌詞:').
    Filters out production/studio credit blocks (where 'Lyrics: Name' means 'Lyricist: Name').
    """
    if not description:
        return None

    marker_pattern = re.compile(
        r"(?:(?:^|\n)\s*(?:lyrics|lyric|歌词|歌詞|가사)\s*(?:\n|[:：]\s*\n)(.*?))"
        r"(?=(?:\n\s*(?:credits|credit|composed by|written by|arranger|produced by|performed by|connect with|follow|links|social|stream|subscribe|℗|©|all rights)|$))",
        re.IGNORECASE | re.DOTALL,
    )
    match = marker_pattern.search(description)
    if not match:
        return None

    extracted = match.group(1).strip()
    raw_lines = [line.strip() for line in extracted.splitlines() if line.strip()]
    if len(raw_lines) < 6:
        return None

    # Filter out credit lines and reject if a high proportion of lines are credits
    credit_count = sum(1 for l in raw_lines if is_credit_line(l))
    if credit_count / max(1, len(raw_lines)) > 0.25:
        return None

    clean_lines = [l for l in raw_lines if not is_credit_line(l)]
    if len(clean_lines) < 6:
        return None

    return "\n".join(clean_lines)


def fetch_youtube_lyrics(url_or_vid: str) -> list[dict[str, Any]]:
    """
    Fetch lyrics candidates directly from YouTube video metadata (CC subtitles and description).
    """
    if not url_or_vid:
        return []

    url = url_or_vid if "http" in url_or_vid else f"https://www.youtube.com/watch?v={url_or_vid}"

    ydl_opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
    }

    results = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return []

            title = info.get("title", "YouTube Video")
            uploader = info.get("uploader", "YouTube Channel")
            duration = float(info.get("duration", 0))

            # 1. Check description
            desc = info.get("description", "")
            desc_lyrics = extract_lyrics_from_description(desc)
            if desc_lyrics:
                lines = desc_lyrics.splitlines()
                results.append({
                    "id": f"yt_desc_{info.get('id')}",
                    "source": "youtube_desc",
                    "title": title,
                    "artist": uploader,
                    "album": "YouTube Description",
                    "duration": duration,
                    "is_synced": False,
                    "synced_lyrics": "",
                    "plain_lyrics": desc_lyrics,
                    "raw_lyrics": desc_lyrics,
                    "parsed": [],
                    "line_count": len(lines),
                })

            # 2. Check manual subtitles
            subtitles = info.get("subtitles") or {}
            for lang_code in ("en", "zh-Hans", "zh-Hant", "zh", "ja", "ko"):
                if lang_code in subtitles:
                    sub_entries = subtitles[lang_code]
                    vtt_url = next((e.get("url") for e in sub_entries if e.get("ext") in ("vtt", "ttml")), None)
                    if vtt_url:
                        results.append({
                            "id": f"yt_sub_{info.get('id')}_{lang_code}",
                            "source": "youtube_cc",
                            "title": f"{title} (CC {lang_code})",
                            "artist": uploader,
                            "album": f"YouTube CC ({lang_code})",
                            "duration": duration,
                            "is_synced": True,
                            "synced_lyrics": "",
                            "plain_lyrics": "",
                            "raw_lyrics": f"[CC Subtitle Track: {lang_code}]",
                            "parsed": [],
                            "sub_url": vtt_url,
                            "sub_lang": lang_code,
                            "line_count": 0,
                        })
                        break

    except Exception as e:
        print(f"  [!] YouTube CC / Description inspection failed: {e}")

    return results
