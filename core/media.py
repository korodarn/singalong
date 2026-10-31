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
    meta_path = workdir / f"{cand_id}.meta.json" if cand_id else None

    if cand_id and (workdir / f"{cand_id}.wav").exists():
        title = cand_id
        if meta_path and meta_path.exists():
            try:
                import json
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                title = meta.get("title", cand_id)
            except Exception:
                pass
        print(f"[1/4] Reusing local audio for {cand_id}: '{title}'")
        return cand_id, title, workdir / f"{cand_id}.wav"

    target_url = f"https://www.youtube.com/watch?v={cand_id}" if cand_id else url_or_vid.strip()
    out = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--no-playlist", "--print", "%(id)s\t%(title)s\t%(uploader)s\t%(duration)s", "--", target_url],
        check=True, capture_output=True, text=True,
    ).stdout.strip().splitlines()[-1]
    parts = out.split("\t")
    vid = parts[0]
    title = parts[1] if len(parts) > 1 and parts[1] else vid
    artist = parts[2] if len(parts) > 2 and parts[2] else ""
    try:
        duration = float(parts[3]) if len(parts) > 3 and parts[3] else 0
    except ValueError:
        duration = 0

    # Save metadata cache
    try:
        import json
        m_file = workdir / f"{vid}.meta.json"
        m_file.write_text(json.dumps({
            "video_id": vid,
            "title": title,
            "artist": artist,
            "duration": duration,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

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


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".flv", ".ts"}


def detect_media_type(path_or_url: str | Path) -> str:
    path_str = str(path_or_url).split("?")[0].lower()
    ext = Path(path_str).suffix
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return "video"


def download_bg_video(video_url_or_id: str, workdir: Path) -> Path:
    """Downloads or retrieves background video into work/bg/ and returns the file path."""
    _, file_path, _ = download_bg_media(video_url_or_id, workdir)
    return file_path


def download_bg_media(url_or_id: str, workdir: Path) -> tuple[str, Path, str]:
    """
    Downloads or retrieves background media (video or static image) into work/bg/.
    Returns (bg_id, file_path, media_type).
    """
    bg_dir = workdir / "bg"
    bg_dir.mkdir(parents=True, exist_ok=True)

    cand_id = extract_youtube_id(url_or_id)
    if cand_id:
        for ext in [".mp4", ".webm", ".mkv"]:
            cand_file = bg_dir / f"{cand_id}{ext}"
            if cand_file.exists() and cand_file.stat().st_size > 0:
                print(f"[*] Reusing cached background video: {cand_file}")
                return cand_id, cand_file, "video"

        target_url = f"https://www.youtube.com/watch?v={cand_id}"
        print(f"[*] Downloading background video for '{cand_id}'...")
        run([
            sys.executable, "-m", "yt_dlp", "--no-playlist",
            "-f", "bestvideo[height<=1080][vcodec^=avc1][ext=mp4]/bestvideo[height<=1080][ext=mp4]/best[height<=1080][ext=mp4]/bestvideo[height<=1080]/best[height<=1080]/best",
            "-o", str(bg_dir / f"{cand_id}.%(ext)s"), "--", target_url
        ])

        for cand_file in bg_dir.glob(f"{cand_id}.*"):
            if cand_file.suffix.lower() in VIDEO_EXTENSIONS and cand_file.stat().st_size > 0:
                return cand_id, cand_file, "video"
        raise RuntimeError(f"Failed to find downloaded background video for {cand_id}")

    s = url_or_id.strip()
    if s.startswith("http://") or s.startswith("https://"):
        m_type = detect_media_type(s)
        clean_name = re.sub(r"[^A-Za-z0-9_-]", "_", Path(s.split("?")[0]).stem)[:32] or "bg"
        url_hash = abs(hash(s)) % 1000000
        bg_id = f"{clean_name}_{url_hash}"

        if m_type == "image":
            import urllib.request
            ext = Path(s.split("?")[0]).suffix.lower() or ".jpg"
            out_file = bg_dir / f"{bg_id}{ext}"
            if not out_file.exists() or out_file.stat().st_size == 0:
                print(f"[*] Downloading background image from {s} -> {out_file}...")
                req = urllib.request.Request(s, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req) as resp, open(out_file, "wb") as f:
                    f.write(resp.read())
            return bg_id, out_file, "image"
        else:
            cand_matches = [f for f in bg_dir.glob(f"{bg_id}.*") if f.suffix.lower() in VIDEO_EXTENSIONS]
            if cand_matches and cand_matches[0].stat().st_size > 0:
                return bg_id, cand_matches[0], "video"

            print(f"[*] Downloading background video from {s}...")
            try:
                run([
                    sys.executable, "-m", "yt_dlp", "--no-playlist",
                    "-f", "bestvideo[height<=1080][vcodec^=avc1][ext=mp4]/bestvideo[height<=1080][ext=mp4]/best[height<=1080][ext=mp4]/bestvideo[height<=1080]/best[height<=1080]/best",
                    "-o", str(bg_dir / f"{bg_id}.%(ext)s"), "--", s
                ])
                for cand_file in bg_dir.glob(f"{bg_id}.*"):
                    if cand_file.suffix.lower() in VIDEO_EXTENSIONS and cand_file.stat().st_size > 0:
                        return bg_id, cand_file, "video"
            except Exception:
                import urllib.request
                ext = Path(s.split("?")[0]).suffix.lower() or ".mp4"
                out_file = bg_dir / f"{bg_id}{ext}"
                req = urllib.request.Request(s, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req) as resp, open(out_file, "wb") as f:
                    f.write(resp.read())
                return bg_id, out_file, detect_media_type(out_file)

    return resolve_local_bg_file(s, workdir)


def resolve_local_bg_file(local_path: str | Path, workdir: Path, copy_to_bg_dir: bool = True) -> tuple[str, Path, str]:
    """
    Validates a local file path and optionally copies/symlinks it to work/bg/.
    Returns (bg_id, resolved_path, media_type).
    """
    p = Path(local_path).expanduser()
    if not p.is_absolute():
        if (workdir / p).exists():
            p = (workdir / p).resolve()
        else:
            p = p.resolve()

    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"Background media file not found: {local_path}")

    m_type = detect_media_type(p)
    bg_dir = workdir / "bg"
    bg_dir.mkdir(parents=True, exist_ok=True)

    clean_stem = re.sub(r"[^A-Za-z0-9_-]", "_", p.stem)[:32]
    ext = p.suffix.lower()

    if copy_to_bg_dir and not str(p).startswith(str(bg_dir.resolve())):
        file_hash = abs(hash(str(p))) % 1000000
        target = bg_dir / f"{clean_stem}_{file_hash}{ext}"
        if not target.exists() or target.stat().st_size != p.stat().st_size:
            import shutil
            shutil.copyfile(p, target)
        return target.stem, target, m_type

    return clean_stem, p, m_type

