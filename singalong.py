#!/usr/bin/env python3
"""
singalong.py - YouTube URL / Video ID -> timed multi-language lyrics + romanization + US-English phonetics.
"""

import argparse
from pathlib import Path
import sys

from core.media import download_audio, isolate_vocals
from core.asr import transcribe, build_tokens
from core.lyrics.netease import fetch_netease_lyrics
from core.aligner import (
    VocalAnalyzer,
    align_lrc_with_tokens,
    group_lines,
    write_outputs,
    fmt_time,
)
from core.phonetics import add_phonetics


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="YouTube URL or 11-char video ID")
    ap.add_argument("--workdir", default="work", type=Path)
    ap.add_argument("--skip-demucs", action="store_true",
                    help="transcribe the full mix (faster, usually less accurate)")
    ap.add_argument("--device", default=None, help="cuda:0 or cpu (auto-detected)")
    ap.add_argument("--query", default=None,
                    help="custom query/title for NetEase Cloud Music lyrics lookup")
    ap.add_argument("--skip-netease", action="store_true",
                    help="skip NetEase lyrics lookup and run ASR only")
    ap.add_argument("--force-lrc", action="store_true",
                    help="force re-fetching NetEase lyrics even if cached")
    args = ap.parse_args()

    if args.device is None:
        import torch
        args.device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(f"Device: {args.device}", flush=True)

    args.workdir.mkdir(parents=True, exist_ok=True)
    vid, title, wav = download_audio(args.url, args.workdir)
    print(f"[RESOLVED_VID] {vid}", flush=True)

    src = wav if args.skip_demucs else isolate_vocals(wav, args.workdir, args.device)
    text, stamps = transcribe(src, args.workdir, vid, args.device)

    netease_lines = None
    if not args.skip_netease:
        query = args.query or title
        netease_lines = fetch_netease_lyrics(query, args.workdir, vid, asr_text=text, force_refresh=args.force_lrc)
        if netease_lines:
            print(f"\n[NetEase Sync Preview - first 5 lines]:")
            for item in netease_lines[:5]:
                print(f"  [{fmt_time(item['start'])}] {item['text']}")
            print(f"  ... ({len(netease_lines)} total lines)\n")

    raw_tokens = build_tokens(text, stamps)
    analyzer = VocalAnalyzer(src)

    if netease_lines:
        print("[4/4] Aligning official NetEase lyrics to acoustic timings + vocal energy...")
        lines = align_lrc_with_tokens(netease_lines, raw_tokens, analyzer)
    else:
        print("[4/4] Building phonetics (unconstrained ASR fallback)...")
        add_phonetics(raw_tokens)
        lines = group_lines(raw_tokens)

    jpath, tpath = write_outputs(vid, lines, args.workdir)
    print(f"\nDone.\n  {tpath}\n  {jpath}")


if __name__ == "__main__":
    main()
