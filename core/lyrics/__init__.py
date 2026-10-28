"""
core.lyrics - Unified lyrics discovery and parser exports.
"""

from core.lyrics.parser import parse_lrc, compute_text_similarity
from core.lyrics.netease import fetch_netease_lyrics, query_netease_api
