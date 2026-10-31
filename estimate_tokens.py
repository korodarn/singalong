#!/usr/bin/env python3
"""
Estimate token counts for files and directories across the project.
Generates `tokens.json` structured hierarchically:
- Root project folder node with aggregated total
- Folder nodes with sub-totals
- Individual file entries with token counts and metadata
Includes text/code/json/markdown/lyrics/config and media (audio/video/image) estimates.
"""

import os
import sys
import json
import subprocess
from pathlib import Path

try:
    import tiktoken
    ENC = tiktoken.get_encoding("cl100k_base")
except Exception:
    ENC = None

# Directories to ignore
IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "dist",
    ".vite",
    ".idea",
    ".vscode",
    ".cache",
    ".gemini",
    ".agents",
}

# Recognized file types
CODE_TEXT_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".txt",
    ".md", ".lrc", ".ass", ".html", ".css", ".sh", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".sql", ".rst"
}

AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"}


def count_text_tokens(file_path: Path) -> tuple[int, str]:
    """Return (token_count, details_str)."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return 0, f"error reading: {e}"

    if ENC is not None:
        try:
            tokens = len(ENC.encode(content, disallowed_special=()))
            return tokens, "tiktoken (cl100k_base)"
        except Exception:
            pass

    # Heuristic fallback: ~4 characters per token
    tokens = max(1, len(content) // 4)
    return tokens, "heuristic (~4 chars/token)"


def get_media_duration_seconds(file_path: Path) -> float:
    """Use ffprobe to get media duration in seconds."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path)
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(res.stdout.strip())
    except Exception:
        return 0.0


def count_media_tokens(file_path: Path, ext: str) -> tuple[int, str]:
    """Estimate multimodal inference tokens for media."""
    if ext in AUDIO_EXTS:
        dur = get_media_duration_seconds(file_path)
        # Multimodal models (Gemini / Whisper) typically consume ~25 - 32 tokens per audio second (Gemini is 32 tokens/sec)
        tokens = int(dur * 32)
        return tokens, f"audio estimate (32 tokens/sec, {dur:.1f}s)"

    if ext in VIDEO_EXTS:
        dur = get_media_duration_seconds(file_path)
        # Video at standard multimodal sampling (~1 frame/sec, ~258 tokens/frame + 32 tokens/sec audio)
        tokens = int(dur * (258 + 32))
        return tokens, f"video estimate (1 fps @ 258 tok + 32 tok audio/sec, {dur:.1f}s)"

    if ext in IMAGE_EXTS:
        # Standard multimodal image patch token estimate
        return 258, "image estimate (~258 tokens per standard image)"

    return 0, "unknown media"


def scan_directory(dir_path: Path, project_root: Path) -> dict:
    """Recursively scan directory and calculate tokens."""
    rel_path = str(dir_path.relative_to(project_root))
    if rel_path == ".":
        rel_path = dir_path.resolve().name

    node = {
        "name": dir_path.name if dir_path != project_root else "project_root",
        "path": str(dir_path.relative_to(project_root)),
        "type": "directory",
        "tokens": 0,
        "files_count": 0,
        "children": []
    }

    try:
        entries = sorted(list(dir_path.iterdir()), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return node

    total_tokens = 0
    total_files = 0

    for entry in entries:
        if entry.is_dir():
            if entry.name in IGNORE_DIRS or entry.name.startswith("."):
                continue
            child_node = scan_directory(entry, project_root)
            # Only include directories that have files or non-empty content
            node["children"].append(child_node)
            total_tokens += child_node["tokens"]
            total_files += child_node["files_count"]
        elif entry.is_file():
            # Skip hidden files except essential configs or self
            if entry.name.startswith(".") and entry.name not in {".gitignore", ".env.example"}:
                continue
            if entry.name == "tokens.json":
                continue

            ext = entry.suffix.lower()
            tokens = 0
            category = "other"
            detail = ""

            if ext in CODE_TEXT_EXTS:
                tokens, detail = count_text_tokens(entry)
                category = "text/code"
            elif ext in AUDIO_EXTS or ext in VIDEO_EXTS or ext in IMAGE_EXTS:
                tokens, detail = count_media_tokens(entry, ext)
                category = "media"
            else:
                # Check if it might be a text file without extension or special format
                try:
                    with open(entry, "rb") as f:
                        chunk = f.read(1024)
                        if b"\0" not in chunk:
                            tokens, detail = count_text_tokens(entry)
                            category = "text/code"
                except Exception:
                    pass

            file_node = {
                "name": entry.name,
                "path": str(entry.relative_to(project_root)),
                "type": "file",
                "category": category,
                "size_bytes": entry.stat().st_size,
                "tokens": tokens,
                "estimation_method": detail or "skipped"
            }
            node["children"].append(file_node)
            total_tokens += tokens
            total_files += 1

    node["tokens"] = total_tokens
    node["files_count"] = total_files
    return node


def main():
    root = Path(".").resolve()
    print(f"Scanning project at: {root}")
    tree = scan_directory(root, root)

    # Structure top-level with summary and folder breakdown
    output = {
        "project_total_tokens": tree["tokens"],
        "project_total_files": tree["files_count"],
        "folder_summary": {
            child["name"]: {
                "tokens": child["tokens"],
                "type": child["type"],
                "files_count": child.get("files_count", 1)
            }
            for child in tree["children"]
        },
        "tree": tree
    }

    out_file = root / "tokens.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"Token scan complete! Total project tokens: {tree['tokens']:,}")
    print(f"Results written to: {out_file}")


if __name__ == "__main__":
    main()
