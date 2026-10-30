#!/usr/bin/env python3
r"""
colorfill.py - Render karaoke / color-fill animated sing-along videos.
"""

import argparse
import json
import sys
from pathlib import Path

from core.render import (
    DEFAULT_CONFIG,
    estimate_text_width,
    fmt_ass_time,
    generate_ass,
    hex_to_ass,
    load_config,
    parse_alignment,
    render_video,
)
from core.media import get_audio_duration


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("json_file", type=Path, help="Path to character timings JSON (e.g. work/vid.json)")
    parser.add_argument("--config", type=Path, default=None, help="Path to custom config.json")
    parser.add_argument("--output", type=Path, default=None, help="Output video path (e.g. work/output.webm)")
    parser.add_argument("--audio", type=Path, default=None, help="Path to audio file (defaults to matching .wav in work dir)")
    parser.add_argument("--video", "--bg", dest="video", type=Path, default=None, help="Path to optional background video or image (mp4/webm/jpg/png)")
    parser.add_argument("--bg-offset", type=int, default=None, help="Background video start offset in milliseconds")
    parser.add_argument("--bg-dim", type=int, default=None, help="Background dimming percentage (0-100, default 35)")
    parser.add_argument("--preview", type=float, default=None, help="Render only the first N seconds for a quick test")
    parser.add_argument("--ass-only", action="store_true", help="Generate only the .ass subtitle file and exit")
    parser.add_argument("--tiers", default=None, help="Display tier mode: 1 (Native only), 2 (Romaji/Pinyin + Native), 3 (Full 3-tier)")
    args = parser.parse_args()

    if not args.json_file.exists():
        sys.exit(f"Error: JSON file not found: {args.json_file}")

    cfg = load_config(args.config)
    lines = json.loads(args.json_file.read_text(encoding="utf-8"))

    stem = args.json_file.stem
    workdir = args.json_file.parent

    meta_tier_mode = args.tiers
    bg_video_path = args.video
    bg_offset_ms = args.bg_offset
    bg_dim_pct = args.bg_dim

    meta_path = workdir / f"{stem}.meta.json"
    if meta_path.exists():
        try:
            meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta_tier_mode is None:
                meta_tier_mode = meta_data.get("tier_mode")
            if bg_video_path is None:
                cand_bg = meta_data.get("bg_media_path") or meta_data.get("bg_video_path")
                if cand_bg:
                    p = Path(cand_bg)
                    if not p.is_absolute():
                        p = workdir / p if (workdir / p).exists() else p.resolve()
                    if p.exists():
                        bg_video_path = p
            if bg_offset_ms is None:
                bg_offset_ms = meta_data.get("bg_offset_ms", 0)
            if bg_dim_pct is None:
                bg_dim_pct = meta_data.get("bg_dim_pct", 35)
        except Exception:
            pass

    if bg_offset_ms is None:
        bg_offset_ms = 0
    if bg_dim_pct is None:
        bg_dim_pct = 35

    audio_path = args.audio
    if audio_path is None:
        candidate_wav = workdir / f"{stem}.wav"
        if candidate_wav.exists():
            audio_path = candidate_wav

    output_path = args.output
    if output_path is None:
        output_path = workdir / f"{stem}.webm"

    ass_path = workdir / f"{stem}.ass"
    print(f"[*] Generating karaoke ASS subtitle: {ass_path}")
    generate_ass(lines, cfg, ass_path, tier_mode=meta_tier_mode)

    if args.ass_only:
        print("[+] ASS file generated successfully. Skipping video render (--ass-only).")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    render_video(
        ass_path=ass_path,
        output_path=output_path,
        audio_path=audio_path,
        video_bg_path=bg_video_path,
        cfg=cfg,
        preview_sec=args.preview,
        bg_dim_pct=bg_dim_pct,
        bg_offset_ms=bg_offset_ms,
    )



if __name__ == "__main__":
    main()
