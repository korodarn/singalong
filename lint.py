#!/usr/bin/env python3
"""
lint.py - Pre-Render QA / Linter for Singalong JSON timings.
"""

import argparse
import json
import sys
from pathlib import Path

from core.linter import lint_project, fmt_time, load_audio_mono, rms_dbfs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("json_file", type=Path, help="Path to work/<vid>.json")
    ap.add_argument("--vocals", type=Path, default=None, help="Path to vocals.wav")
    ap.add_argument("--json-out", type=Path, default=None, help="Optional path to save lint report JSON")
    args = ap.parse_args()

    res = lint_project(args.json_file, args.vocals)
    print(f"=== Singalong QA Lint Report: {args.json_file.name} ===")
    print(f"Audio reference : {res['vocals_path']}")
    print(f"Total lines     : {res['total_lines']}")
    print(f"Flagged lines   : {res['flagged_lines']} / {res['total_lines']}")
    print(f"Total issues    : {res['total_issues']}")
    print(f"Mean confidence : {res['mean_confidence']} / 100.0\n")

    for r in res["lines"]:
        if r["issues"]:
            tag = "[Backing] " if r["is_backing"] else ""
            print(f"Line {r['line_index']:02d} [{fmt_time(r['start'])} -> {fmt_time(r['end'])}] "
                  f"(Score: {r['score']:3d}) {tag}{r['text']}")
            for iss in r["issues"]:
                print(f"   - {iss['rule']}: {iss['detail']}")

    if args.json_out:
        args.json_out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
