"""
core.media - Audio/video download, Demucs vocal isolation, and probing helpers.
"""

import re
import subprocess
import sys
from pathlib import Path


def run(cmd):
    print("  $", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True)


def extract_youtube_id(url_or_vid: str) -> str | None:
    s = url_or_vid.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", s)
    return m.group(1) if m else None


def download_audio(url_or_vid: str, workdir: Path):
    cand_id = extract_youtube_id(url_or_vid)
    if cand_id and (workdir / f"{cand_id}.wav").exists():
        print(f"[1/4] Reusing local audio for ID {cand_id}: {workdir / f'{cand_id}.wav'}")
        return cand_id, cand_id, workdir / f"{cand_id}.wav"

    target_url = f"https://www.youtube.com/watch?v={cand_id}" if cand_id else url_or_vid.strip()
    out = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--no-playlist", "--print", "%(id)s\t%(title)s", "--", target_url],
        check=True, capture_output=True, text=True,
    ).stdout.strip().splitlines()[-1]
    parts = out.split("\t", 1)
    vid = parts[0]
    title = parts[1] if len(parts) > 1 else vid

    wav = workdir / f"{vid}.wav"
    if wav.exists():
        print(f"[1/4] Audio already downloaded: {wav}")
    else:
        print(f"[1/4] Downloading audio for '{title}'...")
        run([sys.executable, "-m", "yt_dlp", "--no-playlist", "-x",
             "--audio-format", "wav", "-o", str(workdir / f"{vid}.%(ext)s"), "--", target_url])
    return vid, title, wav


def isolate_vocals(wav: Path, workdir: Path, device: str) -> Path:
    vocals = workdir / "htdemucs" / wav.stem / "vocals.wav"
    if vocals.exists():
        print(f"[2/4] Vocals already isolated: {vocals}")
        return vocals
    print("[2/4] Isolating vocals with Demucs...")
    run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs",
         "-d", device.split(":")[0], "-o", str(workdir), str(wav)])
    return vocals


def get_audio_duration(audio_path: Path) -> float | None:
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            capture_output=True, text=True, check=True,
        )
        return float(res.stdout.strip())
    except Exception:
        return None
