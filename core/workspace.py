"""
core.workspace - Workspace management and artifact lifecycle utilities.
"""

from pathlib import Path
import shutil


def purge_workdir(
    workdir: Path,
    vid: str | None = None,
    keep_media: bool = False,
    legacy_only: bool = False,
) -> list[str]:
    """
    Purge stale, legacy, or complete project artifacts from workdir.

    Args:
        workdir: Base project workspace directory (typically 'work').
        vid: Optional video ID to target a specific project. If None, targets workdir.
        keep_media: If True, preserves original mix (.wav, .mp4) and demucs stems.
        legacy_only: If True, only cleans legacy naming artifacts (.asr.json, .netease.lrc, .tmp_*).

    Returns:
        List of deleted file/directory paths (strings).
    """
    deleted: list[str] = []
    if not workdir.exists():
        return deleted

    # 1. Clean temporary and preview files
    for tmp in workdir.glob(".tmp_*"):
        try:
            if tmp.is_dir():
                shutil.rmtree(tmp)
            else:
                tmp.unlink()
            deleted.append(str(tmp))
        except Exception:
            pass

    for preview in workdir.glob(".preview_*.json"):
        if vid is None or preview.name == f".preview_{vid}.json":
            try:
                preview.unlink()
                deleted.append(str(preview))
            except Exception:
                pass

    # 2. Legacy artifacts
    legacy_patterns = [
        "*.asr.json",
        "*.netease.lrc",
    ]
    for pat in legacy_patterns:
        for f in workdir.glob(pat):
            if vid is None or f.name.startswith(f"{vid}."):
                try:
                    f.unlink()
                    deleted.append(str(f))
                except Exception:
                    pass

    if legacy_only:
        return deleted

    # 3. Target project or entire workdir
    if vid is not None:
        # Target specific vid
        for item in workdir.glob(f"{vid}*"):
            if item.name == f"{vid}.wav" and keep_media:
                continue
            if item.suffix in (".mp4", ".mkv", ".webm") and keep_media:
                continue
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                deleted.append(str(item))
            except Exception:
                pass

        if not keep_media:
            demucs_stem = workdir / "htdemucs" / vid
            if demucs_stem.exists():
                try:
                    shutil.rmtree(demucs_stem)
                    deleted.append(str(demucs_stem))
                except Exception:
                    pass
    else:
        # Full workspace purge
        for item in workdir.iterdir():
            if item.name == ".gitkeep":
                continue
            if keep_media:
                if item.name == "htdemucs":
                    continue
                if item.suffix in (".wav", ".mp4", ".mkv", ".webm"):
                    continue
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                deleted.append(str(item))
            except Exception:
                pass

    return deleted
