#!/usr/bin/env python3
"""
singalong.py - YouTube URL / Video ID -> timed multi-language lyrics + romanization + US-English phonetics.
"""

import argparse
import json
from pathlib import Path

from core.media import download_audio, isolate_vocals
from core.asr import transcribe, build_tokens
from core.lyrics import (
    search_all_lyrics,
    parse_lrc,
    validate_lyrics_candidate,
    detect_script_distribution,
)
from core.aligner import (
    VocalAnalyzer,
    align_lrc_with_tokens,
    align_plain_lyrics_with_tokens,
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
                    help="custom query/title for lyrics lookup")
    ap.add_argument("--target-lang", default=None,
                    help="expected language: en, zh, ja, ko, or auto")
    ap.add_argument("--asr-engine", default="auto",
                    choices=["auto", "paraformer", "whisper", "sensevoice"],
                    help="ASR engine: auto, paraformer (Mandarin), whisper (EN/JA/KO), or sensevoice")
    ap.add_argument("--force-asr", action="store_true",
                    help="force re-running ASR even if cached timestamps exist")
    ap.add_argument("--lyrics-file", default=None, type=Path,
                    help="path to explicit user-provided or cached lyrics file (LRC or TXT)")
    ap.add_argument("--preview-only", action="store_true",
                    help="run audio/ASR + lyrics discovery and save preview candidates without aligning JSON")
    ap.add_argument("--skip-lyrics", action="store_true",
                    help="skip lyrics search and use unconstrained ASR only")
    ap.add_argument("--skip-netease", action="store_true",
                    help="alias for backward compatibility: skip lyrics lookup")
    ap.add_argument("--force-lrc", action="store_true",
                    help="force re-fetching lyrics even if cached")
    args = ap.parse_args()

    if args.device is None:
        import torch
        args.device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(f"Device: {args.device}", flush=True)

    args.workdir.mkdir(parents=True, exist_ok=True)
    vid, title, wav = download_audio(args.url, args.workdir)
    print(f"[RESOLVED_VID] {vid}", flush=True)

    src = wav if args.skip_demucs else isolate_vocals(wav, args.workdir, args.device)
    text, stamps = transcribe(
        src,
        args.workdir,
        vid,
        args.device,
        engine=args.asr_engine,
        lang=args.target_lang,
        force_asr=args.force_asr,
    )
    raw_tokens = build_tokens(text, stamps, lang=args.target_lang)

    # Pre-JSON Preview Mode: discover & sense-check lyrics against ASR and stop before alignment
    if args.preview_only:
        query = args.query or title
        print(f"[*] Searching & sense-checking lyrics across providers (query: {query!r})...", flush=True)
        candidates = search_all_lyrics(
            query=query,
            url_or_vid=args.url,
            target_lang=args.target_lang,
            asr_text=text,
            workdir=args.workdir,
            limit=10,
        )
        if args.force_lrc:
            candidates = [c for c in candidates if c.get("source") != "cached_lrc"] + [
                c for c in candidates if c.get("source") == "cached_lrc"
            ]

        # Build raw ASR transcript preview as fallback option
        asr_grouped = group_lines(raw_tokens)
        asr_lrc_lines = []
        for ln in asr_grouped:
            txt = "".join(t["text"] + (" " if t.get("space") else "") for t in ln["tokens"]).strip()
            if txt:
                asr_lrc_lines.append(f"[{fmt_time(ln['start'])}] {txt}")
        asr_lrc_text = "\n".join(asr_lrc_lines)
        asr_script = detect_script_distribution(text)
        candidates.append({
            "id": f"asr_raw_{vid}",
            "source": "asr_transcript",
            "title": f"{title} (Raw Acoustic ASR Transcript)",
            "artist": "FunASR Model",
            "album": "Unconstrained ASR",
            "duration": 0,
            "is_synced": True,
            "synced_lyrics": asr_lrc_text,
            "plain_lyrics": "\n".join(l.split("] ", 1)[-1] for l in asr_lrc_lines),
            "raw_lyrics": asr_lrc_text,
            "clean_lyrics": asr_lrc_text,
            "line_count": len(asr_lrc_lines),
            "script": asr_script,
            "validation": {
                "is_valid": True,
                "reason": f"Raw ASR fallback ({len(asr_lrc_lines)} phrases)",
                "score": 0.35,
            },
        })

        for c in candidates:
            c.pop("parsed", None)
            v = c["validation"]
            status_str = "VALID" if v["is_valid"] else "MISMATCH"
            print(f"  -> [{c['source'].upper()}] {c['title']} - {c['artist']} | {status_str} ({v['reason']})", flush=True)

        preview_path = args.workdir / f".preview_{vid}.json"
        preview_path.write_text(
            json.dumps(
                {
                    "video_id": vid,
                    "title": title,
                    "query": query,
                    "candidates": candidates,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[PREVIEW_READY] {vid}", flush=True)
        return

    analyzer = VocalAnalyzer(src)
    chosen_synced = None
    chosen_plain = None

    # 1. Explicit lyrics file passed (e.g. from editor confirmed preview / drop-in)
    if args.lyrics_file and args.lyrics_file.exists():
        raw_text = args.lyrics_file.read_text(encoding="utf-8")
        parsed = parse_lrc(raw_text)
        if parsed:
            print(f"[*] Using confirmed synchronized LRC: {args.lyrics_file} ({len(parsed)} lines)")
            chosen_synced = parsed
            # Only update local verified cache if this is not just the raw ASR transcript fallback
            asr_grouped_texts = {
                "".join(t["text"] + (" " if t.get("space") else "") for t in ln["tokens"]).strip()
                for ln in group_lines(raw_tokens)
            }
            parsed_texts = [p["text"].strip() for p in parsed if p.get("text", "").strip()]
            asr_overlap = (
                sum(1 for pt in parsed_texts if pt in asr_grouped_texts) / len(parsed_texts)
                if parsed_texts else 1.0
            )
            if asr_overlap < 0.6:
                (args.workdir / f"{vid}.netease.lrc").write_text(raw_text, encoding="utf-8")
        else:
            lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
            print(f"[*] Using confirmed plain lyrics: {args.lyrics_file} ({len(lines)} lines)")
            chosen_plain = lines
        if args.lyrics_file.name.startswith(".tmp_"):
            try:
                args.lyrics_file.unlink()
            except Exception:
                pass

    # 2. Multi-source search if not skipped
    elif not args.skip_lyrics and not args.skip_netease:
        query = args.query or title
        cached_lrc = args.workdir / f"{vid}.netease.lrc"
        if cached_lrc.exists() and not args.force_lrc:
            cached_raw = cached_lrc.read_text(encoding="utf-8")
            is_valid, reason, _ = validate_lyrics_candidate(
                cached_raw,
                target_lang=args.target_lang,
                asr_text=text,
                query_title=query,
                cand_title=title,
            )
            if is_valid:
                print(f"[*] Reusing verified cached lyrics: {cached_lrc}")
                chosen_synced = parse_lrc(cached_raw)
            else:
                print(f"  [!] Cached lyrics failed validation ({reason}). Searching online providers...")

        if not chosen_synced:
            print(f"[*] Searching lyrics across NetEase, LRCLIB, and YouTube (query: {query!r})...")
            candidates = search_all_lyrics(
                query=query,
                url_or_vid=args.url,
                target_lang=args.target_lang,
                asr_text=text,
                workdir=args.workdir,
                limit=8,
            )
            if args.force_lrc:
                candidates = [c for c in candidates if c.get("source") != "cached_lrc"]
            valid_cands = [c for c in candidates if c["validation"]["is_valid"]]
            if valid_cands:
                top = valid_cands[0]
                print(f"  [+] Selected [{top['source'].upper()}] {top['title']} - {top['artist']} (score: {top['validation']['score']:.3f})")
                if top.get("is_synced") and top.get("parsed"):
                    chosen_synced = top["parsed"]
                    cached_lrc.write_text(top.get("synced_lyrics") or top.get("raw_lyrics"), encoding="utf-8")
                elif top.get("plain_lyrics"):
                    chosen_plain = [l.strip() for l in top["plain_lyrics"].splitlines() if l.strip()]
            else:
                print("  [!] No online lyrics passed validation sense-check. Falling back to pure ASR.")

    # 3. Alignment
    if chosen_synced:
        print("[4/4] Aligning synchronized lyrics to acoustic timings + vocal energy...")
        lines = align_lrc_with_tokens(chosen_synced, raw_tokens, analyzer)
    elif chosen_plain:
        print("[4/4] Aligning plain lyrics to acoustic tokens + vocal energy...")
        lines = align_plain_lyrics_with_tokens(chosen_plain, raw_tokens, analyzer)
    else:
        print("[4/4] Building phonetics (unconstrained ASR fallback)...")
        add_phonetics(raw_tokens, lang=args.target_lang)
        lines = group_lines(raw_tokens)

    jpath, tpath = write_outputs(vid, lines, args.workdir)
    print(f"\nDone.\n  {tpath}\n  {jpath}")


if __name__ == "__main__":
    main()
