"""
core.models - Common data types and structures used across Singalong modules.
"""

from dataclasses import dataclass, field
from typing import Any, Optional, TypedDict


class TokenDict(TypedDict, total=False):
    text: str
    start: int
    end: int
    space: bool
    is_deleted: bool
    pinyin: str
    en: str


class LineDict(TypedDict, total=False):
    start: int
    end: int
    is_backing: bool
    tokens: list[TokenDict]


@dataclass
class ProjectSummary:
    id: str
    filename: str
    num_lines: int
    has_audio: bool
    has_vocals: bool
    has_video: bool


@dataclass
class ProjectMeta:
    bg_video_id: Optional[str] = None
    bg_video_path: Optional[str] = None
    bg_media_path: Optional[str] = None
    bg_media_type: Optional[str] = None  # "video" | "image"
    bg_offset_ms: int = 0
    bg_dim_pct: int = 35
    tier_mode: str = "multi_tier"
    asr_engine: str = "auto"
    target_lang: str = "auto"

