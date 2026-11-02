"""
core.models - Common data types and structures used across Singalong modules.
"""

from dataclasses import dataclass, field
from typing import Any, Optional, TypedDict


class DiagnosticFlag(TypedDict, total=False):
    code: str
    severity: str  # "info" | "warning" | "error"
    detail: str


class TokenDiagnostics(TypedDict, total=False):
    source: str  # "mms_fa" | "asr" | "dp_boundary" | "char_weight" | "interpolated"
    ctc_score: float
    rms_db: float
    onset_strength: float
    sustain_extended_ms: int
    flags: list[DiagnosticFlag]


class LineDiagnostics(TypedDict, total=False):
    line_idx: int
    lrc_nominal_start: Optional[int]
    lrc_nominal_end: Optional[int]
    shift_from_lrc_start_ms: Optional[int]
    shift_from_lrc_end_ms: Optional[int]
    alignment_method: str  # "mms_fa_ctc" | "asr_anchored" | "island_weighted" | "uniform_fallback"
    phonetic_match_score: float
    mean_ctc_score: float
    mean_rms_db: float
    lead_in_breath_trimmed_ms: int
    tail_sustain_extended_ms: int
    voiced_islands: list[list[int]]
    asr_heard_text: str
    asr_raw_tokens: list[dict[str, Any]]
    flags: list[DiagnosticFlag]


class TokenDict(TypedDict, total=False):
    text: str
    start: int
    end: int
    space: bool
    is_deleted: bool
    pinyin: str
    en: str
    comment: str
    diag: TokenDiagnostics


class LineDict(TypedDict, total=False):
    start: int
    end: int
    is_backing: bool
    tokens: list[TokenDict]
    diag: LineDiagnostics


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
    visualizer: Optional[dict] = None

