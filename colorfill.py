#!/usr/bin/env python3
r"""
colorfill.py - Render karaoke / color-fill animated sing-along videos.

Features:
  - Per-syllable Ruby Column Grid layout (`\an2\pos(x,y)`) for multi-tier lines so
    English Phonetics, Pinyin/Romanization, and Hanzi/Hangul wipe heads never drift horizontally.
  - Automatic single-tier collapse for pure-English lines.
  - Automatic Korean font switching (`Source Han Sans KR`) when Hangul is detected.
  - Separate Lead (`bottom-center` Gold) and Backing (`bottom-right` Cyan) vocal tracks.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


DEFAULT_CONFIG = {
    "video": {
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "background_color": "black",
    },
    "subtitles": {
        "font_name": "Source Han Sans CN",
        "ruby_columns": True,
        "position": {
            "alignment": "bottom-center",
            "margin_bottom": 100,
            "line_spacing": 20,
        },
        "hanzi": {
            "enabled": True,
            "font_size": 64,
            "color_unfilled": "#FFFFFF",
            "color_filled": "#FFD700",
            "color_outline": "#000000",
            "outline_width": 3,
            "shadow": 2,
            "bold": True,
        },
        "pinyin": {
            "enabled": True,
            "font_size": 32,
            "color_unfilled": "#E0E0E0",
            "color_filled": "#FFD700",
            "color_outline": "#000000",
            "outline_width": 2.5,
            "shadow": 1,
            "bold": False,
        },
        "english": {
            "enabled": True,
            "font_size": 28,
            "color_unfilled": "#B0B0B0",
            "color_filled": "#FFD700",
            "color_outline": "#000000",
            "outline_width": 2.5,
            "shadow": 1,
            "bold": False,
        },
        "backing": {
            "placement": "bottom-right",
            "margin_right": 80,
            "margin_bottom": 120,
            "margin_left": 40,
            "font_scale": 0.85,
            "italic": True,
            "color_filled": "#70D6FF",
            "color_unfilled": "#D0E8FF",
            "color_outline": "#000000",
            "prefix": "( ",
            "suffix": " )",
        },
    },
}


def hex_to_ass(hex_color: str, alpha="00") -> str:
    """Convert #RRGGBB or #RRGGBBAA to ASS &HAABBGGRR format."""
    s = hex_color.strip().lstrip("#")
    if len(s) == 6:
        r, g, b = s[0:2], s[2:4], s[4:6]
        a = alpha
    elif len(s) == 8:
        r, g, b, a = s[0:2], s[2:4], s[4:6], s[6:8]
    else:
        return "&H00FFFFFF"
    return f"&H{a.upper()}{b.upper()}{g.upper()}{r.upper()}"


def parse_alignment(align_str: str) -> int:
    """Map alignment strings to ASS alignment numbers (numpad layout)."""
    mapping = {
        "bottom-left": 1, "bottom-center": 2, "bottom-right": 3,
        "middle-left": 4, "center": 5, "middle-right": 6,
        "top-left": 7, "top-center": 8, "top-right": 9,
    }
    return mapping.get(align_str.lower().strip(), 2)


def fmt_ass_time(ms: int) -> str:
    """Format milliseconds to ASS timestamp H:MM:SS.cs (centiseconds)."""
    s = ms // 1000
    cs = (ms % 1000) // 10
    m = s // 60
    h = m // 60
    return f"{h}:{m % 60:02d}:{s % 60:02d}.{cs:02d}"


def estimate_text_width(text: str, font_size: float, bold: bool = False) -> float:
    """Estimate rendered pixel width of a string in Source Han Sans at `font_size`."""
    if not text:
        return 0.0
    w = 0.0
    for ch in text:
        cp = ord(ch)
        if 0x3000 <= cp <= 0x9FFF or 0xAC00 <= cp <= 0xD7A3 or 0xFF00 <= cp <= 0xFFEF:
            w += 1.0 * font_size
        elif ch in "iljtfI1'.,:;!|()":
            w += 0.36 * font_size
        elif ch in "mwMW":
            w += 0.84 * font_size
        elif ch.isupper():
            w += 0.68 * font_size
        elif ch == " ":
            w += 0.33 * font_size
        else:
            w += 0.56 * font_size
    if bold:
        w *= 1.05
    # Account for ASS Spacing=1 and outline padding
    w += len(text) * 1.2 + 4.0
    return w


def load_config(config_path: Path | None) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if config_path and config_path.exists():
        try:
            user_cfg = json.loads(config_path.read_text(encoding="utf-8"))
            if "video" in user_cfg:
                cfg["video"].update(user_cfg["video"])
            if "subtitles" in user_cfg:
                sub = user_cfg["subtitles"]
                for k in ["font_name", "ruby_columns", "position", "hanzi", "pinyin", "english", "backing"]:
                    if k in sub:
                        if isinstance(sub[k], dict) and isinstance(cfg["subtitles"].get(k), dict):
                            cfg["subtitles"][k].update(sub[k])
                        else:
                            cfg["subtitles"][k] = sub[k]
        except Exception as e:
            print(f"Warning: Failed to parse config file {config_path}: {e}. Using defaults.")
    return cfg


def generate_ass(lines: list, cfg: dict, ass_path: Path):
    """Generate Advanced SubStation Alpha (.ass) subtitle file with Ruby Column alignment."""
    v_cfg = cfg["video"]
    s_cfg = cfg["subtitles"]
    pos = s_cfg["position"]
    font = s_cfg["font_name"]
    use_ruby = s_cfg.get("ruby_columns", True)

    has_korean = any(
        any(0xAC00 <= ord(c) <= 0xD7A3 for c in t.get("text", ""))
        for ln in lines for t in ln.get("tokens", [])
    )
    if has_korean and font == "Source Han Sans CN":
        font = "Source Han Sans KR"

    width = v_cfg["width"]
    height = v_cfg["height"]
    align = parse_alignment(pos.get("alignment", "bottom-center"))

    margin_bottom = pos.get("margin_bottom", 100)
    spacing = pos.get("line_spacing", 20)

    hanzi_cfg = s_cfg["hanzi"]
    pinyin_cfg = s_cfg["pinyin"]
    en_cfg = s_cfg["english"]

    # Calculate MarginV for each enabled layer (bottom up)
    current_margin = margin_bottom
    hz_margin = current_margin
    if hanzi_cfg["enabled"]:
        current_margin += hanzi_cfg["font_size"] + spacing

    py_margin = current_margin
    if pinyin_cfg["enabled"]:
        current_margin += pinyin_cfg["font_size"] + spacing

    en_margin = current_margin

    # Backing vocal parameters
    b_cfg = s_cfg.get("backing", DEFAULT_CONFIG["subtitles"]["backing"])
    b_align = parse_alignment(b_cfg.get("placement", "bottom-right"))
    b_margin_b = b_cfg.get("margin_bottom", 120)
    b_margin_r = b_cfg.get("margin_right", 80)
    b_margin_l = b_cfg.get("margin_left", 40)
    b_scale = b_cfg.get("font_scale", 0.85)
    b_italic = -1 if b_cfg.get("italic", True) else 0

    b_hz_size = int(round(hanzi_cfg["font_size"] * b_scale))
    b_py_size = int(round(pinyin_cfg["font_size"] * b_scale))
    b_en_size = int(round(en_cfg["font_size"] * b_scale))

    b_current = b_margin_b
    b_hz_margin = b_current
    if hanzi_cfg["enabled"]:
        b_current += b_hz_size + spacing
    b_py_margin = b_current
    if pinyin_cfg["enabled"]:
        b_current += b_py_size + spacing
    b_en_margin = b_current

    styles_block = [
        "[Script Info]",
        "Title: Singalong Karaoke Subtitles",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
    ]

    def build_style_line(name, c, margin_v):
        pri = hex_to_ass(c["color_filled"])
        sec = hex_to_ass(c["color_unfilled"])
        out = hex_to_ass(c["color_outline"])
        back = "&H80000000"
        bold = -1 if c.get("bold") else 0
        return (f"Style: {name},{font},{c['font_size']},{pri},{sec},{out},{back},"
                f"{bold},0,0,0,100,100,1,0,1,{c['outline_width']},{c['shadow']},"
                f"{align},40,40,{margin_v},1")

    def build_backing_style(name, base_c, font_size, margin_v):
        pri = hex_to_ass(b_cfg.get("color_filled", base_c["color_filled"]))
        sec = hex_to_ass(b_cfg.get("color_unfilled", base_c["color_unfilled"]))
        out = hex_to_ass(b_cfg.get("color_outline", base_c["color_outline"]))
        back = "&H80000000"
        bold = -1 if base_c.get("bold") else 0
        return (f"Style: {name},{font},{font_size},{pri},{sec},{out},{back},"
                f"{bold},{b_italic},0,0,100,100,1,0,1,{base_c['outline_width']},{base_c['shadow']},"
                f"{b_align},{b_margin_l},{b_margin_r},{margin_v},1")

    if hanzi_cfg["enabled"]:
        styles_block.append(build_style_line("Hanzi", hanzi_cfg, hz_margin))
    if pinyin_cfg["enabled"]:
        styles_block.append(build_style_line("Pinyin", pinyin_cfg, py_margin))
    if en_cfg["enabled"]:
        styles_block.append(build_style_line("English", en_cfg, en_margin))

    if hanzi_cfg["enabled"]:
        styles_block.append(build_backing_style("Hanzi_Backing", hanzi_cfg, b_hz_size, b_hz_margin))
    if pinyin_cfg["enabled"]:
        styles_block.append(build_backing_style("Pinyin_Backing", pinyin_cfg, b_py_size, b_py_margin))
    if en_cfg["enabled"]:
        styles_block.append(build_backing_style("English_Backing", en_cfg, b_en_size, b_en_margin))

    events_block = [
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for ln in lines:
        tokens = [t for t in ln.get("tokens", []) if not t.get("is_deleted")]
        if not tokens:
            continue

        first_start = tokens[0]["start"]
        last_end = tokens[-1]["end"]

        disp_start = max(0, first_start - 400)
        disp_end = last_end + 400
        st = fmt_ass_time(disp_start)
        et = fmt_ass_time(disp_end)

        is_backing = ln.get("is_backing", False)
        prefix = b_cfg.get("prefix", "( ") if is_backing else ""
        suffix = b_cfg.get("suffix", " )") if is_backing else ""

        hz_style = "Hanzi_Backing" if is_backing else "Hanzi"
        py_style = "Pinyin_Backing" if is_backing else "Pinyin"
        en_style = "English_Backing" if is_backing else "English"

        is_foreign = any(
            any(0x4E00 <= ord(c) <= 0x9FFF or 0xAC00 <= ord(c) <= 0xD7A3 for c in t.get("text", ""))
            for t in tokens
        )

        # Pure English line: render single centered karaoke line with natural word spacing
        if not is_foreign or not use_ruby:
            lead_in = first_start - disp_start
            lead_tag = f"{{\\k{round(lead_in / 10)}}}" if lead_in > 20 else ""
            hz_parts = [lead_tag]
            py_parts = [lead_tag]
            en_parts = [lead_tag]

            for idx, t in enumerate(tokens):
                dur_cs = max(1, round((t["end"] - t["start"]) / 10))
                is_latin = bool(re.search(r"[A-Za-z0-9]", t["text"]))
                sp = " " if (idx + 1 < len(tokens) and (t.get("space") or is_latin)) else ""
                hz_parts.append(f"{{\\kf{dur_cs}}}{t['text']}{sp}")
                py_parts.append(f"{{\\kf{dur_cs}}}{t.get('pinyin', t['text'])} ")
                en_parts.append(f"{{\\kf{dur_cs}}}{t.get('en', t['text'])} ")

                if idx + 1 < len(tokens):
                    gap = tokens[idx + 1]["start"] - t["end"]
                    if gap > 80:
                        gap_tag = f"{{\\k{round(gap / 10)}}}"
                        hz_parts.append(gap_tag)
                        py_parts.append(gap_tag)
                        en_parts.append(gap_tag)

            if not is_foreign:
                events_block.append(f"Dialogue: 0,{st},{et},{hz_style},,0,0,0,,{prefix}{''.join(hz_parts).strip()}{suffix}")
            else:
                if en_cfg["enabled"]:
                    events_block.append(f"Dialogue: 0,{st},{et},{en_style},,0,0,0,,{prefix}{''.join(en_parts).strip()}{suffix}")
                if pinyin_cfg["enabled"]:
                    events_block.append(f"Dialogue: 0,{st},{et},{py_style},,0,0,0,,{prefix}{''.join(py_parts).strip()}{suffix}")
                if hanzi_cfg["enabled"]:
                    events_block.append(f"Dialogue: 0,{st},{et},{hz_style},,0,0,0,,{prefix}{''.join(hz_parts).strip()}{suffix}")
            continue

        # Multi-tier Ruby Column Layout: lock each syllable vertically across all 3 tiers
        hz_fsize = b_hz_size if is_backing else hanzi_cfg["font_size"]
        py_fsize = b_py_size if is_backing else pinyin_cfg["font_size"]
        en_fsize = b_en_size if is_backing else en_cfg["font_size"]

        y_hz = height - (b_hz_margin if is_backing else hz_margin)
        y_py = height - (b_py_margin if is_backing else py_margin)
        y_en = height - (b_en_margin if is_backing else en_margin)

        col_widths = []
        col_gaps = []
        for idx, t in enumerate(tokens):
            hz_txt = t["text"]
            py_txt = t.get("pinyin", hz_txt)
            en_txt = t.get("en", hz_txt)
            if idx == 0 and prefix:
                hz_txt = prefix + hz_txt
            if idx == len(tokens) - 1 and suffix:
                hz_txt = hz_txt + suffix

            w_h = estimate_text_width(hz_txt, hz_fsize, bold=hanzi_cfg.get("bold", True)) if hanzi_cfg["enabled"] else 0.0
            w_p = estimate_text_width(py_txt, py_fsize, bold=pinyin_cfg.get("bold", False)) if pinyin_cfg["enabled"] else 0.0
            w_e = estimate_text_width(en_txt, en_fsize, bold=en_cfg.get("bold", False)) if en_cfg["enabled"] else 0.0

            col_widths.append(max(w_h, w_p, w_e, hz_fsize * 0.85))
            if idx + 1 < len(tokens):
                col_gaps.append(26.0 if t.get("space") else 12.0)
            else:
                col_gaps.append(0.0)

        total_w = sum(col_widths) + sum(col_gaps)
        max_avail_w = (width - b_margin_l - b_margin_r - 80) if is_backing else (width - 140)
        scale_ratio = min(1.0, max_avail_w / max(1.0, total_w))
        scale_pct = int(round(scale_ratio * 100))
        scale_tag = f"\\fscx{scale_pct}\\fscy{scale_pct}" if scale_pct < 100 else ""

        scaled_widths = [w * scale_ratio for w in col_widths]
        scaled_gaps = [g * scale_ratio for g in col_gaps]
        scaled_total_w = sum(scaled_widths) + sum(scaled_gaps)

        active_align = b_align if is_backing else align
        if active_align in (1, 4, 7):  # Left aligned
            start_x = float(b_margin_l if is_backing else 70)
        elif active_align in (3, 6, 9):  # Right aligned
            start_x = float(width - (b_margin_r if is_backing else 70) - scaled_total_w)
        else:  # Center aligned
            start_x = (width - scaled_total_w) / 2.0

        cur_x = start_x
        for idx, t in enumerate(tokens):
            cw = scaled_widths[idx]
            center_x = cur_x + cw / 2.0
            cur_x += cw + scaled_gaps[idx]

            wait_cs = max(0, round((t["start"] - disp_start) / 10))
            dur_cs = max(1, round((t["end"] - t["start"]) / 10))
            wait_tag = f"\\k{wait_cs}" if wait_cs > 0 else ""

            hz_txt = t["text"]
            py_txt = t.get("pinyin", hz_txt)
            en_txt = t.get("en", hz_txt)
            if idx == 0 and prefix:
                hz_txt = prefix + hz_txt
            if idx == len(tokens) - 1 and suffix:
                hz_txt = hz_txt + suffix

            if en_cfg["enabled"]:
                tag = f"{{\\an2\\pos({center_x:.1f},{y_en}){scale_tag}{wait_tag}\\kf{dur_cs}}}"
                events_block.append(f"Dialogue: 0,{st},{et},{en_style},,0,0,0,,{tag}{en_txt}")
            if pinyin_cfg["enabled"]:
                tag = f"{{\\an2\\pos({center_x:.1f},{y_py}){scale_tag}{wait_tag}\\kf{dur_cs}}}"
                events_block.append(f"Dialogue: 0,{st},{et},{py_style},,0,0,0,,{tag}{py_txt}")
            if hanzi_cfg["enabled"]:
                tag = f"{{\\an2\\pos({center_x:.1f},{y_hz}){scale_tag}{wait_tag}\\kf{dur_cs}}}"
                events_block.append(f"Dialogue: 0,{st},{et},{hz_style},,0,0,0,,{tag}{hz_txt}")

    full_ass = "\n".join(styles_block + events_block)
    ass_path.write_text(full_ass, encoding="utf-8")
    return ass_path


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


def render_video(ass_path: Path, output_path: Path, audio_path: Path | None,
                 video_bg_path: Path | None, cfg: dict, preview_sec: float | None = None):
    """Render final video with burned-in animated karaoke subtitles using FFmpeg."""
    v_cfg = cfg["video"]
    width = v_cfg["width"]
    height = v_cfg["height"]
    fps = v_cfg["fps"]
    bg_color = v_cfg.get("background_color", "black")

    cmd = ["ffmpeg", "-y"]

    if preview_sec:
        cmd.extend(["-t", str(preview_sec)])

    if video_bg_path and video_bg_path.exists():
        print(f"[*] Using video background: {video_bg_path}")
        cmd.extend(["-i", str(video_bg_path)])
    else:
        duration = None
        if audio_path and audio_path.exists():
            duration = get_audio_duration(audio_path)
        if preview_sec and (duration is None or preview_sec < duration):
            duration = preview_sec
        duration_arg = f":d={duration:.2f}" if duration else ""

        print(f"[*] Generating synthetic background: {bg_color} {width}x{height} @ {fps}fps")
        cmd.extend(["-f", "lavfi", "-i", f"color=c={bg_color}:s={width}x{height}:r={fps}{duration_arg}"])

    has_audio = False
    if audio_path and audio_path.exists():
        print(f"[*] Using audio source: {audio_path}")
        cmd.extend(["-i", str(audio_path)])
        has_audio = True

    escaped_ass = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    cmd.extend(["-vf", f"ass='{escaped_ass}'"])

    ext = output_path.suffix.lower()
    if ext == ".webm":
        cmd.extend(["-c:v", "libvpx-vp9", "-b:v", "2M", "-cpu-used", "4", "-row-mt", "1", "-pix_fmt", "yuv420p"])
        if has_audio:
            cmd.extend(["-c:a", "libopus", "-b:a", "128k"])
    else:
        cmd.extend(["-c:v", "h264_nvenc", "-preset", "p4", "-b:v", "4M", "-pix_fmt", "yuv420p"])
        if has_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])

    cmd.append("-shortest")
    cmd.append(str(output_path))

    print(f"[*] Running FFmpeg render -> {output_path}...")
    try:
        subprocess.run(cmd, check=True)
        print(f"\n[+] Render complete: {output_path}")
    except subprocess.CalledProcessError as e:
        if ext != ".webm" and "h264_nvenc" in cmd:
            print("  ! NVENC encoding failed, falling back to libx264 software encoder...")
            idx = cmd.index("h264_nvenc")
            cmd[idx] = "libx264"
            cmd[cmd.index("-preset") + 1] = "fast"
            b_idx = cmd.index("-b:v")
            cmd[b_idx] = "-crf"
            cmd[b_idx + 1] = "20"
            subprocess.run(cmd, check=True)
            print(f"\n[+] Render complete: {output_path}")
        else:
            raise e


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("json_file", type=Path, help="Path to character timings JSON (e.g. work/vid.json)")
    parser.add_argument("--config", type=Path, default=None, help="Path to custom config.json")
    parser.add_argument("--output", type=Path, default=None, help="Output video path (e.g. work/output.webm)")
    parser.add_argument("--audio", type=Path, default=None, help="Path to audio file (defaults to matching .wav in work dir)")
    parser.add_argument("--video", type=Path, default=None, help="Path to optional background video to layer text onto")
    parser.add_argument("--preview", type=float, default=None, help="Render only the first N seconds for a quick test")
    parser.add_argument("--ass-only", action="store_true", help="Generate only the .ass subtitle file and exit")
    args = parser.parse_args()

    if not args.json_file.exists():
        sys.exit(f"Error: JSON file not found: {args.json_file}")

    cfg = load_config(args.config)
    lines = json.loads(args.json_file.read_text(encoding="utf-8"))

    stem = args.json_file.stem
    workdir = args.json_file.parent

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
    generate_ass(lines, cfg, ass_path)

    if args.ass_only:
        print("[+] ASS file generated successfully. Skipping video render (--ass-only).")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    render_video(
        ass_path=ass_path,
        output_path=output_path,
        audio_path=audio_path,
        video_bg_path=args.video,
        cfg=cfg,
        preview_sec=args.preview,
    )


if __name__ == "__main__":
    main()
