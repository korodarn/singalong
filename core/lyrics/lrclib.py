"""
core.lyrics.lrclib - LRCLIB API lyrics provider (synced LRC and plain lyrics).
"""

import requests
from typing import Any
from core.lyrics.parser import parse_lrc


LRCLIB_API_BASE = "https://lrclib.net/api"
USER_AGENT = "Singalong/1.0 (https://github.com/korodarn/singalong)"


def query_lrclib(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """
    Search LRCLIB for synchronized or plain lyrics.
    Returns standardized candidate dictionaries.
    """
    if not query or not query.strip():
        return []

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }

    try:
        resp = requests.get(
            f"{LRCLIB_API_BASE}/search",
            params={"q": query.strip()},
            headers=headers,
            timeout=8,
        )
        if resp.status_code != 200:
            return []

        raw_items = resp.json()
        if not isinstance(raw_items, list):
            return []

        results = []
        for item in raw_items[:limit]:
            if item.get("instrumental"):
                continue

            synced = item.get("syncedLyrics") or ""
            plain = item.get("plainLyrics") or ""
            if not synced and not plain:
                continue

            parsed = parse_lrc(synced) if synced else []

            results.append({
                "id": str(item.get("id", "")),
                "source": "lrclib",
                "title": item.get("trackName") or item.get("name") or "Unknown",
                "artist": item.get("artistName") or "Unknown",
                "album": item.get("albumName") or "",
                "duration": float(item.get("duration", 0)),
                "is_synced": bool(synced and len(parsed) > 0),
                "synced_lyrics": synced,
                "plain_lyrics": plain,
                "raw_lyrics": synced if synced else plain,
                "parsed": parsed,
                "line_count": len(parsed) if parsed else len(plain.splitlines()),
            })

        return results
    except Exception as e:
        print(f"  [!] LRCLIB search failed for {query!r}: {e}")
        return []
