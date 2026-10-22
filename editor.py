#!/usr/bin/env python3
"""
editor.py - End-to-End Interactive Web Editor, YouTube Pipeline Runner & QA Workbench for Singalong AI.

Features:
  - End-to-End YouTube Ingestion: Enter any YouTube Video ID (e.g. XEGWxPmWTYs) or URL in the UI
    to run Pipeline 1 (singalong.py: yt-dlp -> demucs -> NetEase LRC -> FunASR -> alignment)
    with live Server-Sent Events (SSE) progress and automatic loading of the generated JSON.
  - Dual-stem audio streaming with HTTP 206 Partial Content (Vocals vs Full Mix, toggled via 'V').
  - Live 60fps CSS gradient karaoke wipe preview with strict Ruby column alignment.
  - WaveSurfer v7 waveform editor with properly colored, draggable/resizable syllable regions.
  - Spacebar 'Tap-Along' rhythm retiming mode ('T' key) at 0.75x speed.
  - Alt+Click syllable deletion (strikethrough; excluded from ASS output) on stage, waveform, or table.
  - 'B' key call-and-response backing vocal toggling (Gold Lead vs Cyan Backing) + 1-click Line Split.
  - Double-click phonetic override with dual-tier persistence (JSON + custom_dict.json).
  - Pre-render QA Linter queue ('N'/'P' jump to issue lines, cached fast audio checks).
  - One-click Save (Ctrl+S) and Video Render (colorfill.py) with live SSE build logs.

Usage:
  ./editor.py
  ./editor.py XEGWxPmWTYs
  ./editor.py work/jgWBRVy3uIg.json --port 8000
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as _StdThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Auto re-exec with workspace .venv/bin/python if available and soundfile/numpy are missing
VENV_PYTHON = Path(__file__).parent / ".venv" / "bin" / "python"
if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    if "--no-reexec" not in sys.argv:
        try:
            import soundfile  # noqa: F401
        except ImportError:
            args = [str(VENV_PYTHON), str(Path(__file__).resolve()), "--no-reexec"] + [
                a for a in sys.argv[1:] if a != "--no-reexec"
            ]
            os.execv(str(VENV_PYTHON), args)

try:
    from lint import lint_project
except ImportError:
    lint_project = None


CUSTOM_DICT_PATH = Path("custom_dict.json")
CONFIG_PATH = Path("config.json")
DEFAULT_WORKDIR = Path("work")


def load_custom_dict() -> dict:
    if CUSTOM_DICT_PATH.exists():
        try:
            return json.loads(CUSTOM_DICT_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_custom_dict(d: dict):
    CUSTOM_DICT_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "video": {"width": 1920, "height": 1080, "fps": 30},
        "subtitles": {
            "hanzi": {"color_filled": "#FFD700", "color_unfilled": "#FFFFFF"},
            "backing": {"color_filled": "#70D6FF", "color_unfilled": "#D0E8FF"},
        },
    }


def find_projects(workdir: Path = DEFAULT_WORKDIR) -> list[dict]:
    if not workdir.exists():
        return []
    projects = []
    for p in sorted(workdir.glob("*.json")):
        if p.name.endswith(".asr.json") or p.name.endswith(".bak") or p.name.endswith(".tmp"):
            continue
        vid = p.stem
        wav_path = workdir / f"{vid}.wav"
        vocals_path = workdir / "htdemucs" / vid / "vocals.wav"
        webm_path = workdir / f"{vid}.webm"
        mp4_path = workdir / f"{vid}.mp4"

        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            num_lines = len(data) if isinstance(data, list) else 0
        except Exception:
            num_lines = 0

        projects.append({
            "id": vid,
            "filename": p.name,
            "num_lines": num_lines,
            "has_audio": wav_path.exists(),
            "has_vocals": vocals_path.exists(),
            "has_video": webm_path.exists() or mp4_path.exists(),
        })
    return projects


class ThreadingHTTPServer(_StdThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class EditorRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        # Quiet noisy range requests while keeping API actions visible
        if len(args) >= 1 and isinstance(args[0], str) and args[0].startswith("GET /api/audio/"):
            return
        super().log_message(format, *args)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self.serve_editor_html()
        elif path == "/api/projects":
            self.send_json(find_projects(self.server.workdir))
        elif path == "/api/pipeline/run":
            url_or_vid = query.get("url", [""])[0].strip()
            custom_query = query.get("query", [""])[0].strip()
            skip_demucs = query.get("skip_demucs", ["0"])[0] in ("1", "true")
            skip_netease = query.get("skip_netease", ["0"])[0] in ("1", "true")
            force_lrc = query.get("force_lrc", ["0"])[0] in ("1", "true")
            self.serve_pipeline_sse(url_or_vid, custom_query, skip_demucs, skip_netease, force_lrc)
        elif path.startswith("/api/project/"):
            parts = path.strip("/").split("/")
            if len(parts) >= 3:
                vid = parts[2]
                if len(parts) == 3:
                    self.serve_project_data(vid)
                elif len(parts) == 4 and parts[3] == "render":
                    preview_sec = query.get("preview", [None])[0]
                    ass_only = query.get("ass_only", ["0"])[0] in ("1", "true")
                    self.serve_render_sse(vid, preview_sec, ass_only)
                else:
                    self.send_error(404, "Endpoint not found")
            else:
                self.send_error(400, "Missing video id")
        elif path.startswith("/api/audio/"):
            parts = path.strip("/").split("/")
            if len(parts) >= 4:
                vid = parts[2]
                stem = parts[3]
                self.serve_audio_file(vid, stem)
            else:
                self.send_error(400, "Missing parameters")
        elif path.startswith("/api/video/"):
            parts = path.strip("/").split("/")
            if len(parts) >= 3:
                vid = parts[2]
                self.serve_video_file(vid)
            else:
                self.send_error(400, "Missing video id")
        elif path == "/api/dict":
            self.send_json(load_custom_dict())
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/project/"):
            parts = path.strip("/").split("/")
            if len(parts) >= 4 and parts[3] == "save":
                vid = parts[2]
                self.handle_project_save(vid)
            elif len(parts) >= 4 and parts[3] == "dict":
                self.handle_dict_update()
            else:
                self.send_error(404, "Endpoint not found")
        elif path == "/api/dict":
            self.handle_dict_update()
        else:
            self.send_error(404, "Not Found")

    def send_json(self, data, status=200):
        try:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def serve_project_data(self, vid: str):
        json_path = self.server.workdir / f"{vid}.json"
        if not json_path.exists():
            self.send_json({
                "exists": False,
                "video_id": vid,
                "error": f"Project JSON for '{vid}' has not been generated yet.",
            }, status=404)
            return

        try:
            lines = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.send_json({"exists": False, "error": f"Error reading project JSON: {e}"}, status=500)
            return

        lint_report = None
        if lint_project:
            try:
                vocals_cand = self.server.workdir / "htdemucs" / vid / "vocals.wav"
                if not vocals_cand.exists():
                    vocals_cand = self.server.workdir / f"{vid}.wav"
                lint_report = lint_project(json_path, vocals_cand if vocals_cand.exists() else None)
            except Exception as e:
                lint_report = {"error": str(e), "mean_confidence": 100.0, "total_issues": 0, "lines": []}

        cfg = load_config()
        custom_dict = load_custom_dict()

        response = {
            "exists": True,
            "video_id": vid,
            "lines": lines,
            "config": cfg,
            "lint": lint_report,
            "custom_dict": custom_dict,
            "has_vocals": (self.server.workdir / "htdemucs" / vid / "vocals.wav").exists(),
            "has_mix": (self.server.workdir / f"{vid}.wav").exists(),
            "has_video": (self.server.workdir / f"{vid}.webm").exists() or (self.server.workdir / f"{vid}.mp4").exists(),
        }
        self.send_json(response)

    def handle_project_save(self, vid: str):
        json_path = self.server.workdir / f"{vid}.json"
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            payload = json.loads(body.decode("utf-8"))
            lines = payload.get("lines")
            dict_updates = payload.get("custom_dict_updates")

            if lines is None or not isinstance(lines, list):
                self.send_json({"ok": False, "error": "Invalid lines array"}, status=400)
                return

            # Recalculate line start/end from active tokens
            for ln in lines:
                active_toks = [t for t in ln.get("tokens", []) if not t.get("is_deleted")]
                if active_toks:
                    ln["start"] = active_toks[0]["start"]
                    ln["end"] = active_toks[-1]["end"]

            # Atomic save with .bak snapshot
            bak_path = self.server.workdir / f"{vid}.json.bak"
            if json_path.exists():
                shutil.copyfile(json_path, bak_path)

            temp_path = self.server.workdir / f"{vid}.json.tmp"
            temp_path.write_text(json.dumps(lines, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(json_path)

            # Persist custom dict entries if provided
            if dict_updates and isinstance(dict_updates, dict):
                current_dict = load_custom_dict()
                for lang, words in dict_updates.items():
                    if lang not in current_dict:
                        current_dict[lang] = {}
                    current_dict[lang].update(words)
                save_custom_dict(current_dict)

            # Re-lint
            lint_report = None
            if lint_project:
                try:
                    vocals_cand = self.server.workdir / "htdemucs" / vid / "vocals.wav"
                    if not vocals_cand.exists():
                        vocals_cand = self.server.workdir / f"{vid}.wav"
                    lint_report = lint_project(json_path, vocals_cand if vocals_cand.exists() else None)
                except Exception as e:
                    lint_report = {"error": str(e), "mean_confidence": 100.0, "total_issues": 0, "lines": []}

            self.send_json({"ok": True, "lint": lint_report, "message": "Saved successfully"})
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, status=500)

    def handle_dict_update(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            payload = json.loads(body.decode("utf-8"))
            current = load_custom_dict()
            for lang, entries in payload.items():
                if lang not in current:
                    current[lang] = {}
                current[lang].update(entries)
            save_custom_dict(current)
            self.send_json({"ok": True, "custom_dict": current})
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, status=400)

    def serve_audio_file(self, vid: str, stem: str):
        if stem == "vocals":
            cand = self.server.workdir / "htdemucs" / vid / "vocals.wav"
            if not cand.exists():
                cand = self.server.workdir / f"{vid}.wav"
        elif stem == "inst":
            cand = self.server.workdir / "htdemucs" / vid / "no_vocals.wav"
        else:
            cand = self.server.workdir / f"{vid}.wav"

        if not cand.exists():
            self.send_error(404, f"Audio file {cand.name} not found")
            return

        self.stream_file_with_range(cand, "audio/wav")

    def serve_video_file(self, vid: str):
        cand = self.server.workdir / f"{vid}.webm"
        mime = "video/webm"
        if not cand.exists():
            cand = self.server.workdir / f"{vid}.mp4"
            mime = "video/mp4"

        if not cand.exists():
            self.send_error(404, f"Video file for {vid} not found")
            return

        self.stream_file_with_range(cand, mime)

    def stream_file_with_range(self, file_path: Path, content_type: str):
        try:
            file_size = file_path.stat().st_size
        except OSError:
            self.send_error(404, "File not accessible")
            return

        range_header = self.headers.get("Range")
        try:
            if not range_header or not range_header.startswith("bytes="):
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(file_size))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                with open(file_path, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)
                return

            range_val = range_header.replace("bytes=", "").strip()
            parts = range_val.split("-")
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
            if start >= file_size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.end_headers()
                return
            end = min(end, file_size - 1)
            chunk_length = end - start + 1

            self.send_response(206)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Content-Length", str(chunk_length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            with open(file_path, "rb") as f:
                f.seek(start)
                bytes_left = chunk_length
                while bytes_left > 0:
                    read_len = min(65536, bytes_left)
                    data = f.read(read_len)
                    if not data:
                        break
                    self.wfile.write(data)
                    bytes_left -= len(data)
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception:
            pass

    def serve_pipeline_sse(self, url_or_vid: str, custom_query: str, skip_demucs: bool, skip_netease: bool, force_lrc: bool):
        """Run Pipeline 1 (singalong.py) on a YouTube video ID or URL and stream progress via SSE."""
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        def send_event(event_type: str, data: dict):
            try:
                payload = json.dumps({"type": event_type, **data})
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass

        if not url_or_vid:
            send_event("error", {"message": "Please enter a YouTube Video ID (e.g. XEGWxPmWTYs) or URL."})
            return

        cmd = [sys.executable, "-u", "singalong.py", url_or_vid, "--workdir", str(self.server.workdir)]
        if custom_query:
            cmd.extend(["--query", custom_query])
        if skip_demucs:
            cmd.append("--skip-demucs")
        if skip_netease:
            cmd.append("--skip-netease")
        if force_lrc:
            cmd.append("--force-lrc")

        send_event("start", {"command": " ".join(cmd)})

        resolved_vid = None
        # Pre-guess 11-char ID if input is one
        m_id = re.search(r"(?:v=|youtu\.be/|shorts/|embed/|^)([A-Za-z0-9_-]{11})(?:$|[^A-Za-z0-9_-])", url_or_vid)
        if m_id:
            resolved_vid = m_id.group(1)

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
            for line in proc.stdout:
                line_clean = line.rstrip()
                if not line_clean:
                    continue
                if line_clean.startswith("[RESOLVED_VID]"):
                    resolved_vid = line_clean.split("[RESOLVED_VID]", 1)[1].strip()
                send_event("log", {"text": line_clean})

            proc.wait()
            ret = proc.returncode
            send_event("complete", {
                "returncode": ret,
                "video_id": resolved_vid,
            })
        except Exception as e:
            send_event("error", {"message": str(e)})

    def serve_render_sse(self, vid: str, preview_sec: str | None, ass_only: bool):
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        def send_event(event_type: str, data: dict):
            try:
                payload = json.dumps({"type": event_type, **data})
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass

        json_path = self.server.workdir / f"{vid}.json"
        if not json_path.exists():
            send_event("error", {"message": f"Project file {json_path} does not exist"})
            return

        cmd = [sys.executable, "-u", "colorfill.py", str(json_path)]
        if ass_only:
            cmd.append("--ass-only")
        elif preview_sec:
            cmd.extend(["--preview", str(preview_sec)])

        send_event("start", {"command": " ".join(cmd)})

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
            for line in proc.stdout:
                line_clean = line.rstrip()
                if line_clean:
                    send_event("log", {"text": line_clean})
            proc.wait()
            ret = proc.returncode
            video_url = f"/api/video/{vid}?t={int(os.times().elapsed)}" if not ass_only and ret == 0 else None
            send_event("complete", {
                "returncode": ret,
                "video_url": video_url,
                "ass_only": ass_only,
            })
        except Exception as e:
            send_event("error", {"message": str(e)})

    def serve_editor_html(self):
        try:
            body = EDITOR_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


EDITOR_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Singalong AI — Studio Editor & YouTube Pipeline Workbench</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600&family=Inter:wght@400;500;600;700;800&family=Noto+Sans+SC:wght@500;700;900&family=Noto+Sans+KR:wght@500;700;900&display=swap" rel="stylesheet">

  <!-- WaveSurfer.js v7 CDN with Regions Plugin -->
  <script src="https://unpkg.com/wavesurfer.js@7/dist/wavesurfer.min.js"></script>
  <script src="https://unpkg.com/wavesurfer.js@7/dist/plugins/regions.min.js"></script>

  <style>
    :root {
      --bg-darkest: #0b0e14;
      --bg-darker: #121620;
      --bg-panel: #1a2030;
      --bg-card: #22293e;
      --border-subtle: #2b334b;
      --border-bright: #3d4868;
      
      --accent-lead: #FFD700;
      --accent-backing: #70D6FF;
      --accent-danger: #ff5c77;
      --accent-success: #38ef7d;
      --accent-warn: #ffb703;
      --accent-primary: #6366f1;
      --accent-primary-hover: #4f46e5;
      
      --text-white: #ffffff;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
      
      --font-ui: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      --font-mono: 'Fira Code', monospace;
      --font-cjk: 'Source Han Sans CN', 'Source Han Sans KR', 'Noto Sans SC', 'Noto Sans KR', sans-serif;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg-darkest);
      color: var(--text-white);
      font-family: var(--font-ui);
      font-size: 13px;
      overflow: hidden;
      height: 100vh;
      display: flex;
      flex-direction: column;
    }

    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: var(--bg-darkest); }
    ::-webkit-scrollbar-thumb { background: var(--border-bright); border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

    /* Top Header Bar */
    header {
      background: var(--bg-darker);
      border-bottom: 1px solid var(--border-subtle);
      padding: 8px 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-shrink: 0;
      z-index: 20;
      flex-wrap: wrap;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 8px;
      font-weight: 800;
      font-size: 15px;
      letter-spacing: -0.4px;
      white-space: nowrap;
    }
    .brand-badge {
      background: linear-gradient(135deg, var(--accent-lead), #f59e0b);
      color: #000;
      font-size: 10px;
      font-weight: 800;
      padding: 2px 6px;
      border-radius: 4px;
      text-transform: uppercase;
    }

    /* YouTube Ingestion Bar + Project Switcher */
    .ingest-bar {
      display: flex;
      align-items: center;
      gap: 6px;
      background: var(--bg-panel);
      border: 1px solid var(--border-bright);
      padding: 3px 6px;
      border-radius: 7px;
    }
    .yt-input {
      background: var(--bg-darkest);
      color: var(--text-white);
      border: 1px solid var(--border-subtle);
      padding: 5px 10px;
      border-radius: 5px;
      font-family: var(--font-mono);
      font-size: 12px;
      width: 210px;
      outline: none;
    }
    .yt-input:focus {
      border-color: var(--accent-lead);
    }

    select.project-select {
      background: var(--bg-panel);
      color: var(--text-white);
      border: 1px solid var(--border-bright);
      padding: 5px 10px;
      border-radius: 6px;
      font-weight: 600;
      font-family: var(--font-mono);
      font-size: 12px;
      outline: none;
      cursor: pointer;
    }
    select.project-select:hover { border-color: var(--accent-primary); }

    .btn-group {
      display: inline-flex;
      background: var(--bg-panel);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 2px;
    }
    .btn-toggle {
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 4px 9px;
      font-size: 12px;
      font-weight: 600;
      border-radius: 4px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 4px;
      transition: all 0.12s ease;
    }
    .btn-toggle.active {
      background: var(--border-bright);
      color: var(--text-white);
    }
    .btn-toggle:hover:not(.active) { color: var(--text-white); }

    /* QA Score & Issue Nav */
    .qa-nav {
      display: flex;
      align-items: center;
      gap: 6px;
      background: var(--bg-panel);
      border: 1px solid var(--border-subtle);
      padding: 3px 7px;
      border-radius: 6px;
    }
    .score-chip {
      font-weight: 700;
      font-family: var(--font-mono);
      font-size: 11px;
      padding: 3px 7px;
      border-radius: 4px;
    }
    .score-green { background: rgba(56, 239, 125, 0.15); color: var(--accent-success); border: 1px solid rgba(56, 239, 125, 0.3); }
    .score-yellow { background: rgba(255, 183, 3, 0.15); color: var(--accent-warn); border: 1px solid rgba(255, 183, 3, 0.3); }
    .score-red { background: rgba(255, 92, 119, 0.15); color: var(--accent-danger); border: 1px solid rgba(255, 92, 119, 0.3); }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 6px;
    }

    button.btn {
      background: var(--bg-panel);
      color: var(--text-white);
      border: 1px solid var(--border-bright);
      padding: 5px 11px;
      font-size: 12px;
      font-weight: 600;
      border-radius: 6px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      transition: all 0.12s;
      white-space: nowrap;
    }
    button.btn:hover { background: var(--border-bright); }
    button.btn:active { transform: scale(0.98); }
    button.btn-primary {
      background: var(--accent-primary);
      border-color: var(--accent-primary);
      color: #fff;
    }
    button.btn-primary:hover { background: var(--accent-primary-hover); }
    button.btn-success {
      background: linear-gradient(135deg, #10b981, #059669);
      border-color: #10b981;
      color: #fff;
    }
    button.btn-lead {
      background: linear-gradient(135deg, var(--accent-lead), #f59e0b);
      color: #000;
      border-color: var(--accent-lead);
      font-weight: 700;
    }

    /* Main Workbench Layout */
    .workbench {
      flex: 1;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      position: relative;
    }

    /* Stage 1: Live Karaoke Preview Screen */
    .preview-stage {
      background: #000000;
      background-image: radial-gradient(circle at 50% 30%, #151a2d 0%, #000000 85%);
      min-height: 175px;
      max-height: 230px;
      padding: 14px 24px 22px 24px;
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      position: relative;
      border-bottom: 2px solid var(--border-subtle);
      overflow: hidden;
      flex-shrink: 0;
    }

    .stage-top-meta {
      position: absolute;
      top: 10px;
      left: 16px;
      right: 16px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      pointer-events: none;
    }
    .stage-hint {
      font-size: 11px;
      color: var(--text-dim);
      background: rgba(18, 22, 32, 0.75);
      padding: 3px 8px;
      border-radius: 4px;
      border: 1px solid var(--border-subtle);
    }

    .lane-badge {
      font-size: 11px;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: 4px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .lane-badge.lead { background: rgba(255, 215, 0, 0.15); color: var(--accent-lead); border: 1px solid rgba(255, 215, 0, 0.35); }
    .lane-badge.backing { background: rgba(112, 214, 255, 0.15); color: var(--accent-backing); border: 1px solid rgba(112, 214, 255, 0.35); }

    .video-viewport-container {
      position: absolute;
      top: 10px;
      right: 16px;
      width: 240px;
      height: 135px;
      background: #000;
      border: 1px solid var(--border-bright);
      border-radius: 6px;
      overflow: hidden;
      box-shadow: 0 4px 12px rgba(0,0,0,0.6);
      z-index: 15;
      display: none;
    }
    .video-viewport-container.visible { display: block; }
    .video-viewport-container video { width: 100%; height: 100%; object-fit: contain; }

    /* Ruby Column Grid for 3-tier Karaoke Subtitles */
    .karaoke-display {
      display: flex;
      justify-content: center;
      align-items: flex-end;
      flex-wrap: wrap;
      gap: 2px;
      user-select: none;
      max-width: 96%;
      margin-top: 12px;
    }

    .karaoke-col {
      display: inline-flex;
      flex-direction: column;
      align-items: center;
      justify-content: flex-end;
      padding: 4px 6px;
      border-radius: 6px;
      cursor: pointer;
      transition: background 0.12s, transform 0.1s;
      border: 1px solid transparent;
    }
    .karaoke-col:hover {
      background: rgba(255, 255, 255, 0.07);
      border-color: rgba(255, 255, 255, 0.18);
    }
    .karaoke-col.active-now {
      background: rgba(255, 215, 0, 0.1);
      border-color: rgba(255, 215, 0, 0.35);
    }
    .karaoke-col.has-space { margin-right: 18px; }
    .karaoke-col.deleted {
      opacity: 0.3;
      text-decoration: line-through;
      filter: grayscale(1);
    }

    .tier-en {
      font-size: 14px;
      font-weight: 500;
      letter-spacing: 0.4px;
      color: #b0b0b0;
      white-space: nowrap;
      margin-bottom: 3px;
    }
    .tier-py {
      font-size: 16px;
      font-weight: 600;
      color: #e0e0e0;
      white-space: nowrap;
      margin-bottom: 4px;
      font-family: var(--font-cjk);
    }
    .tier-hz {
      font-size: 36px;
      font-weight: 800;
      font-family: var(--font-cjk);
      white-space: nowrap;
      line-height: 1.1;
    }

    /* Progressive Colorfill Wipe via CSS background-clip */
    .wipe-text {
      display: inline-block;
      --wipe: 0%;
      background: linear-gradient(90deg, var(--fill-color, var(--accent-lead)) var(--wipe), #ffffff var(--wipe));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      filter: drop-shadow(0 2px 3px rgba(0,0,0,0.95));
    }
    .wipe-text.backing {
      --fill-color: var(--accent-backing);
      font-style: italic;
    }
    .wipe-text.lead {
      --fill-color: var(--accent-lead);
    }
    .tier-en .wipe-text {
      background: linear-gradient(90deg, var(--fill-color, var(--accent-lead)) var(--wipe), #b0b0b0 var(--wipe));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    .tier-py .wipe-text {
      background: linear-gradient(90deg, var(--fill-color, var(--accent-lead)) var(--wipe), #e0e0e0 var(--wipe));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }

    /* Pure English single tier line */
    .karaoke-display.pure-english .tier-en,
    .karaoke-display.pure-english .tier-py {
      display: none;
    }
    .karaoke-display.pure-english .tier-hz {
      font-size: 38px;
      letter-spacing: 0.5px;
    }

    /* Tap-Along Mode Banner */
    .tap-along-banner {
      position: absolute;
      bottom: 8px;
      left: 50%;
      transform: translateX(-50%);
      background: linear-gradient(135deg, #dc2626, #991b1b);
      border: 2px solid #f87171;
      padding: 5px 16px;
      border-radius: 20px;
      color: #fff;
      font-weight: 700;
      font-size: 12px;
      display: none;
      align-items: center;
      gap: 10px;
      box-shadow: 0 4px 16px rgba(220, 38, 38, 0.5);
      z-index: 15;
    }
    .tap-along-banner.active { display: flex; }

    /* Stage 2: Waveform & Region Editor */
    .waveform-stage {
      background: var(--bg-darker);
      border-bottom: 1px solid var(--border-subtle);
      padding: 8px 14px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      flex-shrink: 0;
    }
    .waveform-toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }
    .waveform-controls {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }
    .time-readout {
      font-family: var(--font-mono);
      font-size: 12px;
      font-weight: 600;
      color: var(--text-muted);
      background: var(--bg-panel);
      padding: 4px 10px;
      border-radius: 6px;
      border: 1px solid var(--border-subtle);
    }
    .time-readout span.current { color: var(--accent-lead); font-weight: 700; }

    #waveform-container {
      position: relative;
      background: var(--bg-darkest);
      border: 1px solid var(--border-bright);
      border-radius: 6px;
      overflow: hidden;
      height: 102px;
    }
    #waveform { width: 100%; height: 100px; }

    /* Stage 3: Split Line List and Syllable Table */
    .editor-body {
      flex: 1;
      display: flex;
      overflow: hidden;
      background: var(--bg-darkest);
    }

    .lines-sidebar {
      width: 410px;
      border-right: 1px solid var(--border-subtle);
      background: var(--bg-darker);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      flex-shrink: 0;
    }
    .sidebar-header {
      padding: 9px 14px;
      background: var(--bg-panel);
      border-bottom: 1px solid var(--border-subtle);
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-weight: 700;
      font-size: 12px;
    }
    .lines-list {
      flex: 1;
      overflow-y: auto;
      padding: 8px;
      display: flex;
      flex-direction: column;
      gap: 5px;
    }

    .line-card {
      background: var(--bg-panel);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 8px 10px;
      cursor: pointer;
      transition: all 0.12s ease;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .line-card:hover {
      background: var(--bg-card);
      border-color: var(--border-bright);
    }
    .line-card.active {
      border-color: var(--accent-primary);
      background: rgba(99, 102, 241, 0.14);
      box-shadow: 0 0 0 1px var(--accent-primary);
    }
    .line-card.is-backing { border-left: 3px solid var(--accent-backing); }
    .line-card.is-lead { border-left: 3px solid var(--accent-lead); }

    .line-card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .line-idx {
      font-family: var(--font-mono);
      font-size: 11px;
      font-weight: 600;
      color: var(--text-dim);
    }
    .line-time {
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--text-muted);
    }
    .line-preview-text {
      font-family: var(--font-cjk);
      font-size: 14px;
      font-weight: 600;
      color: var(--text-white);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .issue-chips-row {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }
    .issue-pill {
      font-size: 10px;
      font-weight: 700;
      padding: 1px 5px;
      border-radius: 3px;
      font-family: var(--font-mono);
    }
    .issue-ERR_LINE_BOUNDARY_BLEED { background: rgba(255, 92, 119, 0.2); color: var(--accent-danger); border: 1px solid rgba(255, 92, 119, 0.4); }
    .issue-WARN_MICRO_SYLLABLE { background: rgba(255, 183, 3, 0.2); color: var(--accent-warn); border: 1px solid rgba(255, 183, 3, 0.4); }
    .issue-WARN_SILENCE_GHOST { background: rgba(168, 85, 247, 0.2); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.4); }
    .issue-WARN_RUNAWAY_HOLD { background: rgba(249, 115, 22, 0.2); color: #fb923c; border: 1px solid rgba(249, 115, 22, 0.4); }
    .issue-WARN_UNIFORM_FALLBACK { background: rgba(59, 130, 246, 0.2); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.4); }

    /* Syllable Detail Inspector (Right Pane) */
    .syllable-inspector {
      flex: 1;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      background: var(--bg-darkest);
    }
    .inspector-header {
      padding: 8px 16px;
      background: var(--bg-darker);
      border-bottom: 1px solid var(--border-subtle);
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
    }
    .inspector-actions {
      display: flex;
      gap: 6px;
      align-items: center;
    }

    .table-container {
      flex: 1;
      overflow: auto;
      padding: 12px 16px;
    }

    table.syllable-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }
    table.syllable-table th {
      background: var(--bg-panel);
      color: var(--text-muted);
      text-align: left;
      padding: 8px 10px;
      font-weight: 600;
      border-bottom: 1px solid var(--border-bright);
      position: sticky;
      top: 0;
      z-index: 2;
    }
    table.syllable-table td {
      padding: 6px 10px;
      border-bottom: 1px solid var(--border-subtle);
      vertical-align: middle;
    }
    table.syllable-table tr:hover td { background: var(--bg-card); }
    table.syllable-table tr.active td { background: rgba(99, 102, 241, 0.18); }
    table.syllable-table tr.deleted td {
      opacity: 0.38;
      text-decoration: line-through;
    }

    .token-text-cell {
      font-family: var(--font-cjk);
      font-size: 17px;
      font-weight: 700;
      color: var(--text-white);
      cursor: pointer;
    }
    .token-pinyin-cell, .token-en-cell {
      font-family: var(--font-mono);
      font-size: 12px;
      color: var(--accent-lead);
      cursor: pointer;
      padding: 3px 6px;
      border-radius: 4px;
      border: 1px dashed rgba(255, 215, 0, 0.25);
      display: inline-block;
    }
    .token-pinyin-cell:hover, .token-en-cell:hover {
      background: var(--border-bright);
      border-color: var(--accent-lead);
    }
    input.editable-time {
      background: var(--bg-panel);
      border: 1px solid var(--border-bright);
      color: var(--text-white);
      font-family: var(--font-mono);
      font-size: 12px;
      padding: 3px 6px;
      border-radius: 4px;
      width: 78px;
    }
    input.editable-time:focus {
      border-color: var(--accent-primary);
      outline: none;
    }

    /* Modals */
    .modal-overlay {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.78);
      backdrop-filter: blur(4px);
      display: none;
      justify-content: center;
      align-items: center;
      z-index: 100;
    }
    .modal-overlay.open { display: flex; }
    .modal-box {
      background: var(--bg-panel);
      border: 1px solid var(--border-bright);
      border-radius: 10px;
      padding: 20px;
      width: 560px;
      max-width: 92vw;
      box-shadow: 0 12px 36px rgba(0,0,0,0.7);
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .modal-title {
      font-size: 15px;
      font-weight: 700;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .modal-body {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .modal-footer {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      margin-top: 4px;
    }
    .form-group {
      display: flex;
      flex-direction: column;
      gap: 5px;
    }
    .form-group label {
      font-size: 12px;
      font-weight: 600;
      color: var(--text-muted);
    }
    .form-group input[type="text"], .form-group select {
      background: var(--bg-darker);
      border: 1px solid var(--border-bright);
      color: var(--text-white);
      padding: 8px 10px;
      border-radius: 6px;
      font-size: 13px;
      font-family: var(--font-mono);
    }
    .form-group input:focus {
      outline: none;
      border-color: var(--accent-primary);
    }

    .terminal-console {
      background: #05070b;
      border-radius: 6px;
      padding: 12px;
      font-family: var(--font-mono);
      font-size: 12px;
      color: #38ef7d;
      height: 250px;
      overflow-y: auto;
      white-space: pre-wrap;
      border: 1px solid var(--border-subtle);
      line-height: 1.45;
    }

    /* Pipeline Steps Indicator */
    .pipeline-steps {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 8px;
    }
    .step-box {
      background: var(--bg-darker);
      border: 1px solid var(--border-subtle);
      padding: 8px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 600;
      color: var(--text-dim);
      text-align: center;
    }
    .step-box.active {
      border-color: var(--accent-lead);
      color: var(--accent-lead);
      background: rgba(255, 215, 0, 0.08);
    }
    .step-box.done {
      border-color: var(--accent-success);
      color: var(--accent-success);
      background: rgba(56, 239, 125, 0.08);
    }

    .kb-hints {
      font-size: 11px;
      color: var(--text-dim);
      display: flex;
      gap: 10px;
      align-items: center;
    }
  </style>
</head>
<body>

  <!-- Top Navigation Header -->
  <header>
    <div style="display: flex; align-items: center; gap: 12px;">
      <div class="brand">
        <span>🎤 SINGALONG STUDIO</span>
        <span class="brand-badge">Workbench</span>
      </div>

      <!-- YouTube Video ID / URL Ingestion Bar -->
      <div class="ingest-bar" title="Enter a YouTube Video ID (like XEGWxPmWTYs) or URL to run Pipeline 1 and create its JSON">
        <input id="header-yt-input" type="text" class="yt-input" placeholder="YouTube ID / URL (e.g. XEGWxPmWTYs)" onkeydown="if(event.key==='Enter') openPipelineModalWithInput()">
        <button class="btn btn-lead" style="padding: 4px 10px;" onclick="openPipelineModalWithInput()">
          🚀 Import / Create JSON
        </button>
      </div>

      <!-- Existing Project Switcher -->
      <select id="project-select" class="project-select" onchange="loadProject(this.value)" title="Switch between generated projects in work/">
        <option value="">Loading projects...</option>
      </select>
    </div>

    <div style="display: flex; align-items: center; gap: 10px;">
      <div class="btn-group">
        <button id="btn-stem-vocals" class="btn-toggle active" onclick="setStem('vocals')" title="Listen to isolated vocal stem (V)">
          🎤 Vocals (V)
        </button>
        <button id="btn-stem-mix" class="btn-toggle" onclick="setStem('mix')" title="Listen to full original mix (V)">
          🎵 Full Mix
        </button>
      </div>

      <div class="qa-nav">
        <div id="qa-score-badge" class="score-chip score-green">QA: 100%</div>
        <button class="btn" style="padding: 3px 7px; font-size: 11px;" onclick="jumpIssue(-1)" title="Previous Flagged Issue (P or [)">◀</button>
        <span id="qa-issues-count" style="font-size: 11px; color: var(--text-muted); font-family: var(--font-mono);">0 issues</span>
        <button class="btn" style="padding: 3px 7px; font-size: 11px;" onclick="jumpIssue(1)" title="Next Flagged Issue (N or ])">▶</button>
      </div>
    </div>

    <div class="header-actions">
      <button class="btn" onclick="openPipelineModalForCurrent()" title="Re-run singalong.py alignment or change NetEase lyrics query">
        🔄 Re-Align
      </button>
      <button id="btn-toggle-video" class="btn" onclick="toggleVideoViewport()" style="display: none;" title="Toggle Video HUD Preview">
        📹 Video HUD
      </button>
      <button id="btn-save" class="btn btn-primary" onclick="saveProject()" title="Save changes to JSON & dictionary (Ctrl+S)">
        💾 Save (Ctrl+S)
      </button>
      <button class="btn btn-success" onclick="openRenderModal()" title="Burn Karaoke Video with colorfill.py">
        🎬 Render Video
      </button>
    </div>
  </header>

  <!-- Main Workbench Body -->
  <main class="workbench">

    <!-- Stage 1: Live Karaoke Preview Stage -->
    <section class="preview-stage">
      <div class="stage-top-meta">
        <div id="lane-badge" class="lane-badge lead">LEAD VOCALS (GOLD)</div>
        <div class="stage-hint">Click syllable: Seek • Alt+Click: Delete/Restore • Double-Click: Edit Pinyin/Phonetics</div>
      </div>

      <div id="video-container" class="video-viewport-container">
        <video id="synced-video" playsinline muted></video>
      </div>

      <div id="karaoke-display" class="karaoke-display">
        <!-- Ruby column tokens dynamically rendered here -->
      </div>

      <div id="tap-banner" class="tap-along-banner">
        <span>🥁 TAP-ALONG (0.75x):</span>
        <span id="tap-token-status">Syllable 1/6: [玄]</span>
        <span style="font-size: 11px; opacity: 0.9;">Tap [SPACE] on each syllable onset • [ESC] to cancel</span>
      </div>
    </section>

    <!-- Stage 2: Waveform & Region Editor -->
    <section class="waveform-stage">
      <div class="waveform-toolbar">
        <div class="waveform-controls">
          <button id="btn-play" class="btn btn-primary" onclick="togglePlay()" title="Play / Pause continuous audio (Spacebar)">
            ▶ Play (Space)
          </button>
          <button class="btn" onclick="playActiveLine()" title="Play only the active line and stop at its end (R)">
            🎯 Play Line (R)
          </button>
          <button class="btn" onclick="seekRelative(-3)" title="Rewind 3s (J)">⏪ -3s (J)</button>
          <button class="btn" onclick="seekRelative(3)" title="Forward 3s (L)">+3s ⏩ (L)</button>
          <button id="btn-tap" class="btn" style="border-color: #f87171; color: #f87171;" onclick="startTapAlong()" title="Rhythm Retiming Mode (T)">
            🥁 Tap-Along (T)
          </button>
          <button id="btn-autofollow" class="btn active" style="border-color: var(--accent-primary);" onclick="toggleAutoFollow()" title="Automatically switch active line as song plays">
            📍 Follow Playhead: ON
          </button>
        </div>

        <div class="time-readout">
          <span id="cur-time" class="current">00:00.00</span> / <span id="total-time">00:00.00</span>
        </div>

        <div class="kb-hints">
          <span>Speed:</span>
          <select id="play-speed" style="background: var(--bg-panel); color: var(--text-white); border: 1px solid var(--border-bright); border-radius: 4px; padding: 3px 6px; font-size: 11px;" onchange="setPlaybackRate(this.value)">
            <option value="0.5">0.5x</option>
            <option value="0.75">0.75x</option>
            <option value="1.0" selected>1.0x (Normal)</option>
            <option value="1.25">1.25x</option>
          </select>
          <span>Zoom:</span>
          <input type="range" id="zoom-slider" min="15" max="300" value="70" oninput="setWaveformZoom(this.value)" style="width: 90px;">
        </div>
      </div>

      <div id="waveform-container">
        <div id="waveform"></div>
      </div>
    </section>

    <!-- Stage 3: Split Line Sidebar and Syllable Table -->
    <section class="editor-body">
      <!-- Left: Line Index List -->
      <aside class="lines-sidebar">
        <div class="sidebar-header">
          <span>Lyrics Clauses (<span id="line-count-label">0</span>)</span>
          <span style="font-size: 11px; color: var(--text-dim); font-family: var(--font-mono);">↑/↓ Nav • [B] Backing</span>
        </div>
        <div id="lines-list" class="lines-list">
          <!-- Line cards rendered here -->
        </div>
      </aside>

      <!-- Right: Syllable Detail Inspector -->
      <section class="syllable-inspector">
        <div class="inspector-header">
          <div>
            <span style="font-weight: 700; font-size: 13px;">Active Line Syllable Inspector</span>
            <span id="active-line-meta" style="margin-left: 10px; font-family: var(--font-mono); font-size: 12px; color: var(--text-muted);">Line #1</span>
          </div>
          <div class="inspector-actions">
            <button class="btn" onclick="nudgeActiveLine(-50)" title="Shift entire line earlier by 50ms">◀ -50ms</button>
            <button class="btn" onclick="nudgeActiveLine(50)" title="Shift entire line later by 50ms">+50ms ▶</button>
            <button class="btn" onclick="toggleBackingActiveLine()" title="Toggle Lead / Backing Vocals (B)">
              👥 Toggle Backing (B)
            </button>
            <button class="btn" onclick="realignLineUniform()" title="Evenly distribute syllable boundaries across line window">
              ⚖ Even Syllables
            </button>
          </div>
        </div>

        <div class="table-container">
          <table class="syllable-table">
            <thead>
              <tr>
                <th style="width: 36px;">#</th>
                <th style="width: 75px;">Syllable</th>
                <th style="width: 135px;">Pinyin / RR (Edit)</th>
                <th style="width: 155px;">US Phonetic (Edit)</th>
                <th style="width: 105px;">Start (ms)</th>
                <th style="width: 105px;">End (ms)</th>
                <th style="width: 75px;">Dur</th>
                <th style="width: 145px;">Actions</th>
              </tr>
            </thead>
            <tbody id="syllables-tbody">
              <!-- Syllable rows rendered here -->
            </tbody>
          </table>
        </div>
      </section>
    </section>
  </main>

  <!-- Modal 1: YouTube Pipeline 1 Runner (singalong.py -> JSON creation) -->
  <div id="modal-pipeline" class="modal-overlay">
    <div class="modal-box" style="width: 680px;">
      <div class="modal-title">
        <span>🚀 YouTube Import & Alignment Pipeline (singalong.py)</span>
        <button class="btn" style="padding: 2px 8px;" onclick="closePipelineModal()">✕</button>
      </div>
      <div class="modal-body">
        <div class="form-group">
          <label>YouTube Video Code (11-char ID) or Full YouTube URL:</label>
          <input id="pipe-url-input" type="text" placeholder="e.g. XEGWxPmWTYs or https://www.youtube.com/watch?v=XEGWxPmWTYs">
        </div>
        <div class="form-group">
          <label>Custom NetEase Lyrics Search Query (Optional — leave blank to use YouTube title):</label>
          <input id="pipe-query-input" type="text" placeholder="e.g. No One Told Me Wuthering Waves">
        </div>
        <div style="display: flex; gap: 18px; flex-wrap: wrap; padding: 4px 0;">
          <label style="display: flex; align-items: center; gap: 6px; font-size: 12px; cursor: pointer;">
            <input id="pipe-force-lrc" type="checkbox"> Force Re-fetch NetEase Lyrics
          </label>
          <label style="display: flex; align-items: center; gap: 6px; font-size: 12px; cursor: pointer;">
            <input id="pipe-skip-demucs" type="checkbox"> Skip Demucs Vocal Split (Faster)
          </label>
          <label style="display: flex; align-items: center; gap: 6px; font-size: 12px; cursor: pointer;">
            <input id="pipe-skip-netease" type="checkbox"> Unconstrained ASR Only (Skip LRC)
          </label>
        </div>

        <div class="pipeline-steps">
          <div id="pstep-1" class="step-box">1. Audio Download</div>
          <div id="pstep-2" class="step-box">2. Demucs Vocals</div>
          <div id="pstep-3" class="step-box">3. FunASR Stopwatch</div>
          <div id="pstep-4" class="step-box">4. Lyric Alignment</div>
        </div>

        <div id="pipeline-console" class="terminal-console">Enter a YouTube Video ID (like XEGWxPmWTYs) and click [Run Pipeline & Create JSON].</div>
      </div>
      <div class="modal-footer">
        <button id="btn-run-pipeline" class="btn btn-lead" onclick="executePipeline()">
          ⚡ Run Pipeline & Create JSON
        </button>
        <button class="btn" onclick="closePipelineModal()">Close</button>
      </div>
    </div>
  </div>

  <!-- Modal 2: Edit Phonetics & Dictionary Override -->
  <div id="modal-edit-phonetics" class="modal-overlay">
    <div class="modal-box">
      <div class="modal-title">
        <span>✏️ Edit Syllable Phonetics & Dictionary Override</span>
        <button class="btn" style="padding: 2px 8px;" onclick="closePhoneticsModal()">✕</button>
      </div>
      <div class="modal-body">
        <div class="form-group">
          <label>Character / Token Text:</label>
          <input id="edit-modal-text" type="text">
        </div>
        <div class="form-group">
          <label>Pinyin / Romanization (Tone Marks or RR):</label>
          <input id="edit-modal-pinyin" type="text">
        </div>
        <div class="form-group">
          <label>US-English Phonetic Respelling:</label>
          <input id="edit-modal-en" type="text">
        </div>
        <div class="form-group" style="flex-direction: row; align-items: center; gap: 8px;">
          <input id="edit-modal-persist" type="checkbox" checked style="width: 16px; height: 16px;">
          <label for="edit-modal-persist" style="cursor: pointer; color: var(--text-white);">
            Save to global <code style="color: var(--accent-lead);">custom_dict.json</code> (persists across future runs)
          </label>
        </div>
        <div class="form-group">
          <label>Dictionary Language:</label>
          <select id="edit-modal-lang">
            <option value="zh">Chinese (zh)</option>
            <option value="ko">Korean (ko)</option>
            <option value="ja">Japanese (ja)</option>
          </select>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn" onclick="closePhoneticsModal()">Cancel</button>
        <button class="btn btn-primary" onclick="applyPhoneticsEdit()">Apply Override</button>
      </div>
    </div>
  </div>

  <!-- Modal 3: Video Render & SSE Console -->
  <div id="modal-render" class="modal-overlay">
    <div class="modal-box" style="width: 650px;">
      <div class="modal-title">
        <span>🎬 Video Render Engine (colorfill.py)</span>
        <button class="btn" style="padding: 2px 8px;" onclick="closeRenderModal()">✕</button>
      </div>
      <div class="modal-body">
        <div style="display: flex; gap: 10px; align-items: center;">
          <label style="font-weight: 600; font-size: 12px;">Render Scope:</label>
          <select id="render-scope" style="background: var(--bg-darker); color: #fff; border: 1px solid var(--border-bright); padding: 5px 10px; border-radius: 5px;">
            <option value="full">Full Track Video (WebM)</option>
            <option value="preview15">Quick 15s Preview Video</option>
            <option value="ass_only">Generate .ASS Subtitle File Only (Instant)</option>
          </select>
          <button id="btn-start-render" class="btn btn-lead" onclick="executeRender()">
            ⚡ Start Render
          </button>
        </div>

        <div id="render-console" class="terminal-console">Ready to render. Hit [Start Render] above.</div>
      </div>
      <div class="modal-footer">
        <a id="render-watch-btn" href="#" target="_blank" class="btn btn-success" style="display: none;">
          ▶ Open Rendered Video
        </a>
        <button class="btn" onclick="closeRenderModal()">Close</button>
      </div>
    </div>
  </div>

  <script>
    // Application State
    let currentVid = "";
    let projectData = null;
    let activeLineIdx = 0;
    let currentStem = "vocals";
    let isTapAlongActive = false;
    let tapTokenIdx = 0;
    let tapBackupTokens = null;
    let wavesurfer = null;
    let wsRegions = null;
    let regionTokenMap = new Map();
    let isWaveReady = false;
    let currentZoom = 70;
    let autoFollow = true;
    let lineStopTimeSec = null;
    let customDictPending = {};
    let activeEditingToken = null;

    function fmtMs(ms) {
      if (ms < 0 || isNaN(ms)) ms = 0;
      const totalSec = ms / 1000.0;
      const m = Math.floor(totalSec / 60);
      const s = totalSec % 60;
      return `${String(m).padStart(2, '0')}:${s.toFixed(2).padStart(5, '0')}`;
    }

    window.addEventListener('DOMContentLoaded', async () => {
      initWaveSurfer();
      await fetchProjects(true);
      setupKeybindings();
      startKaraokeClockLoop();
    });

    function initWaveSurfer() {
      if (typeof WaveSurfer === 'undefined') {
        console.warn("WaveSurfer CDN unavailable.");
        return;
      }

      const RegionsPlugin = WaveSurfer.Regions || window.RegionsPlugin;
      wsRegions = RegionsPlugin ? RegionsPlugin.create() : null;

      wavesurfer = WaveSurfer.create({
        container: '#waveform',
        waveColor: '#4f46e5',
        progressColor: '#FFD700',
        cursorColor: '#ffffff',
        cursorWidth: 2,
        height: 100,
        normalize: true,
        minPxPerSec: currentZoom,
        autoScroll: true,
        autoCenter: true,
        plugins: wsRegions ? [wsRegions] : []
      });

      wavesurfer.on('play', () => {
        document.getElementById('btn-play').textContent = '⏸ Pause (Space)';
        const v = document.getElementById('synced-video');
        if (v && v.src && document.getElementById('video-container').classList.contains('visible')) {
          v.currentTime = wavesurfer.getCurrentTime();
          v.play().catch(() => {});
        }
      });

      wavesurfer.on('pause', () => {
        document.getElementById('btn-play').textContent = '▶ Play (Space)';
        lineStopTimeSec = null;
        const v = document.getElementById('synced-video');
        if (v) v.pause();
      });

      wavesurfer.on('timeupdate', (curTime) => {
        document.getElementById('cur-time').textContent = fmtMs(curTime * 1000);
        syncVideoPlayback(curTime);

        // Stop at end of line if triggered via 'Play Line (R)'
        if (lineStopTimeSec !== null && curTime >= lineStopTimeSec) {
          lineStopTimeSec = null;
          wavesurfer.pause();
          return;
        }

        // Auto-follow active lyric line during continuous playback
        if (autoFollow && !isTapAlongActive && wavesurfer.isPlaying() && projectData && projectData.lines) {
          const curMs = curTime * 1000;
          const curLn = projectData.lines[activeLineIdx];
          const inCur = curLn && curMs >= (curLn.start - 200) && curMs <= (curLn.end + 250);
          if (!inCur) {
            const matchIdx = projectData.lines.findIndex(ln => curMs >= ln.start && curMs <= ln.end);
            if (matchIdx !== -1 && matchIdx !== activeLineIdx) {
              selectLine(matchIdx, false);
            }
          }
        }
      });

      wavesurfer.on('ready', () => {
        isWaveReady = true;
        const total = wavesurfer.getDuration();
        document.getElementById('total-time').textContent = fmtMs(total * 1000);
        try {
          wavesurfer.zoom(currentZoom);
        } catch (e) {}
        updateWaveformRegions();
      });

      if (wsRegions) {
        wsRegions.on('region-updated', (region) => {
          const meta = regionTokenMap.get(region.id);
          if (meta && meta.token) {
            meta.token.start = Math.max(0, Math.round(region.start * 1000));
            meta.token.end = Math.max(meta.token.start + 20, Math.round(region.end * 1000));
            recalcLineBounds(projectData.lines[activeLineIdx]);
            renderSyllablesTable();
            renderLinesSidebar();
          }
        });

        wsRegions.on('region-clicked', (region, e) => {
          e.stopPropagation();
          const meta = regionTokenMap.get(region.id);
          if (e.altKey && meta) {
            toggleTokenDeleted(meta.tokIdx);
            return;
          }
          wavesurfer.setTime(region.start);
        });
      }
    }

    function recalcLineBounds(ln) {
      if (!ln || !ln.tokens) return;
      const active = ln.tokens.filter(t => !t.is_deleted);
      if (active.length > 0) {
        ln.start = active[0].start;
        ln.end = active[active.length - 1].end;
      }
    }

    async function fetchProjects(initialLoad = false, preferVid = null) {
      try {
        const res = await fetch('/api/projects');
        const list = await res.json();
        const sel = document.getElementById('project-select');
        sel.innerHTML = '';

        if (list.length === 0) {
          sel.innerHTML = '<option value="">(No projects yet — Import a YouTube ID)</option>';
        } else {
          list.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = `${p.id} (${p.num_lines} lines)${p.has_vocals ? ' 🎤' : ''}`;
            sel.appendChild(opt);
          });
        }

        if (preferVid) {
          sel.value = preferVid;
          await loadProject(preferVid);
          return;
        }

        if (initialLoad) {
          const urlParams = new URLSearchParams(window.location.search);
          const queryVid = urlParams.get('vid');
          if (queryVid) {
            const existsInList = list.some(p => p.id === queryVid);
            if (existsInList) {
              sel.value = queryVid;
              await loadProject(queryVid);
            } else {
              // Video ID was passed on CLI or URL but JSON is not built yet -> open pipeline modal!
              document.getElementById('header-yt-input').value = queryVid;
              openPipelineModalWithInput();
            }
          } else if (list.length > 0) {
            sel.value = list[0].id;
            await loadProject(list[0].id);
          } else {
            openPipelineModalWithInput();
          }
        }
      } catch (err) {
        console.error("Failed to fetch projects:", err);
      }
    }

    async function loadProject(vid) {
      if (!vid) return;
      currentVid = vid;
      document.getElementById('header-yt-input').value = vid;

      try {
        const res = await fetch(`/api/project/${vid}`);
        if (res.status === 404) {
          // Project JSON doesn't exist yet -> prompt pipeline runner
          openPipelineModalWithInput(vid);
          return;
        }
        if (!res.ok) throw new Error("Could not load project");
        projectData = await res.json();
        activeLineIdx = 0;

        // Default stem to vocals if available, else mix
        currentStem = projectData.has_vocals ? 'vocals' : 'mix';
        document.getElementById('btn-stem-vocals').classList.toggle('active', currentStem === 'vocals');
        document.getElementById('btn-stem-mix').classList.toggle('active', currentStem === 'mix');

        loadAudioSource();
        renderLinesSidebar();
        renderActiveLine();
        updateQAHealth();

        const vidBtn = document.getElementById('btn-toggle-video');
        if (projectData.has_video) {
          vidBtn.style.display = 'inline-flex';
          document.getElementById('synced-video').src = `/api/video/${vid}`;
        } else {
          vidBtn.style.display = 'none';
          document.getElementById('video-container').classList.remove('visible');
        }
      } catch (err) {
        console.error("Error loading project:", err);
      }
    }

    function loadAudioSource() {
      if (!wavesurfer || !currentVid) return;
      isWaveReady = false;
      const audioUrl = `/api/audio/${currentVid}/${currentStem}`;
      const prevTime = wavesurfer.getCurrentTime() || 0;
      const wasPlaying = wavesurfer.isPlaying();
      wavesurfer.load(audioUrl);
      wavesurfer.once('ready', () => {
        if (prevTime > 0 && prevTime < wavesurfer.getDuration()) {
          wavesurfer.setTime(prevTime);
          if (wasPlaying) wavesurfer.play();
        } else if (projectData && projectData.lines && projectData.lines[activeLineIdx]) {
          const firstStart = (projectData.lines[activeLineIdx].start || 0) / 1000.0;
          wavesurfer.setTime(Math.max(0, firstStart - 0.3));
        }
      });
    }

    function setStem(stem) {
      if (currentStem === stem) return;
      currentStem = stem;
      document.getElementById('btn-stem-vocals').classList.toggle('active', stem === 'vocals');
      document.getElementById('btn-stem-mix').classList.toggle('active', stem === 'mix');
      loadAudioSource();
    }

    function toggleStem() {
      setStem(currentStem === 'vocals' ? 'mix' : 'vocals');
    }

    function toggleAutoFollow() {
      autoFollow = !autoFollow;
      const btn = document.getElementById('btn-autofollow');
      btn.textContent = `📍 Follow Playhead: ${autoFollow ? 'ON' : 'OFF'}`;
      btn.classList.toggle('active', autoFollow);
    }

    function renderLinesSidebar() {
      if (!projectData || !projectData.lines) return;
      const container = document.getElementById('lines-list');
      container.innerHTML = '';
      document.getElementById('line-count-label').textContent = projectData.lines.length;

      const lintLines = (projectData.lint && projectData.lint.lines) ? projectData.lint.lines : [];

      projectData.lines.forEach((ln, idx) => {
        const toks = (ln.tokens || []).filter(t => !t.is_deleted);
        const isBacking = !!ln.is_backing;
        const start = toks.length ? toks[0].start : (ln.start || 0);
        const end = toks.length ? toks[toks.length - 1].end : (ln.end || 0);
        const text = (ln.tokens || []).map(t => (t.is_deleted ? '' : t.text + (t.space ? ' ' : ''))).join('').trim();

        const card = document.createElement('div');
        card.className = `line-card ${idx === activeLineIdx ? 'active' : ''} ${isBacking ? 'is-backing' : 'is-lead'}`;
        card.onclick = () => selectLine(idx, true);

        const lintInfo = lintLines[idx];
        let issuesHtml = '';
        if (lintInfo && lintInfo.issues && lintInfo.issues.length) {
          issuesHtml = '<div class="issue-chips-row">' +
            lintInfo.issues.map(iss => `<span class="issue-pill issue-${iss.rule}" title="${iss.detail}">${iss.rule.replace('WARN_', '').replace('ERR_', '')}</span>`).join('') +
            '</div>';
        }

        card.innerHTML = `
          <div class="line-card-header">
            <span class="line-idx">#${String(idx + 1).padStart(2, '0')} ${isBacking ? '👥 BACKING' : '🎤 LEAD'}</span>
            <span class="line-time">${fmtMs(start)} → ${fmtMs(end)}</span>
          </div>
          <div class="line-preview-text">${text || '(all tokens deleted)'}</div>
          ${issuesHtml}
        `;
        container.appendChild(card);
      });
    }

    function selectLine(idx, seekAudio = true) {
      if (!projectData || !projectData.lines[idx]) return;
      activeLineIdx = idx;
      renderLinesSidebar();
      renderActiveLine();
      updateWaveformRegions();

      const cards = document.querySelectorAll('.line-card');
      if (cards[idx]) {
        cards[idx].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }

      if (seekAudio && wavesurfer && isWaveReady) {
        const ln = projectData.lines[idx];
        const toks = (ln.tokens || []).filter(t => !t.is_deleted);
        if (toks.length) {
          lineStopTimeSec = null;
          const seekS = Math.max(0, (toks[0].start - 200) / 1000.0);
          wavesurfer.setTime(seekS);
        }
      }
    }

    function renderActiveLine() {
      if (!projectData || !projectData.lines[activeLineIdx]) return;
      const ln = projectData.lines[activeLineIdx];
      const toks = ln.tokens || [];
      const isBacking = !!ln.is_backing;

      const badge = document.getElementById('lane-badge');
      badge.textContent = isBacking ? '👥 BACKING VOCALS (CYAN)' : '🎤 LEAD VOCALS (GOLD)';
      badge.className = `lane-badge ${isBacking ? 'backing' : 'lead'}`;

      document.getElementById('active-line-meta').textContent =
        `Line #${activeLineIdx + 1} (${isBacking ? 'Backing' : 'Lead'}) • ${fmtMs(ln.start)} → ${fmtMs(ln.end)} (${toks.length} syllables)`;

      renderKaraokePreview(ln);
      renderSyllablesTable();
    }

    function renderKaraokePreview(ln) {
      const container = document.getElementById('karaoke-display');
      container.innerHTML = '';
      const toks = ln.tokens || [];

      const hasCjk = toks.some(t => /[\u4e00-\u9fff\uac00-\ud7af\u3040-\u30ff]/.test(t.text));
      container.classList.toggle('pure-english', !hasCjk);

      toks.forEach((tok, tokIdx) => {
        const col = document.createElement('div');
        col.className = `karaoke-col ${tok.space ? 'has-space' : ''} ${tok.is_deleted ? 'deleted' : ''}`;
        col.id = `col-tok-${tokIdx}`;
        col.title = `Click: Seek (${tok.start}ms) | Alt+Click: Delete/Restore | Double-Click: Edit Phonetics`;

        col.onclick = (e) => {
          if (e.altKey) {
            e.stopPropagation();
            toggleTokenDeleted(tokIdx);
            return;
          }
          if (wavesurfer && isWaveReady) {
            lineStopTimeSec = tok.end / 1000.0 + 0.08;
            wavesurfer.setTime(tok.start / 1000.0);
            wavesurfer.play();
          }
        };

        col.ondblclick = (e) => {
          e.stopPropagation();
          openPhoneticsModal(tokIdx);
        };

        const isBacking = !!ln.is_backing;
        const colorClass = isBacking ? 'backing' : 'lead';

        col.innerHTML = `
          <div class="tier-en"><span class="wipe-text ${colorClass}">${tok.en || tok.text}</span></div>
          <div class="tier-py"><span class="wipe-text ${colorClass}">${tok.pinyin || tok.text}</span></div>
          <div class="tier-hz"><span class="wipe-text ${colorClass}">${tok.text}</span></div>
        `;
        container.appendChild(col);
      });
    }

    function renderSyllablesTable() {
      if (!projectData || !projectData.lines[activeLineIdx]) return;
      const ln = projectData.lines[activeLineIdx];
      const toks = ln.tokens || [];
      const tbody = document.getElementById('syllables-tbody');
      tbody.innerHTML = '';

      toks.forEach((tok, idx) => {
        const tr = document.createElement('tr');
        tr.id = `tbl-tok-${idx}`;
        if (tok.is_deleted) tr.className = 'deleted';

        const dur = Math.max(0, tok.end - tok.start);

        tr.innerHTML = `
          <td style="font-family: var(--font-mono); color: var(--text-dim);">${idx + 1}</td>
          <td class="token-text-cell" onclick="seekAndPlayToken(${idx})" title="Click to play syllable">${tok.text}${tok.space ? ' <span style="font-size:11px;color:var(--text-dim);">␣</span>' : ''}</td>
          <td><span class="token-pinyin-cell" onclick="openPhoneticsModal(${idx})" title="Click to override Pinyin / Romanization">${tok.pinyin || '-'}</span></td>
          <td><span class="token-en-cell" onclick="openPhoneticsModal(${idx})" title="Click to override US-English phonetics">${tok.en || '-'}</span></td>
          <td><input type="number" class="editable-time" value="${tok.start}" onchange="updateTokenTime(${idx}, 'start', this.value)"></td>
          <td><input type="number" class="editable-time" value="${tok.end}" onchange="updateTokenTime(${idx}, 'end', this.value)"></td>
          <td style="font-family: var(--font-mono); color: ${dur < 60 ? 'var(--accent-warn)' : 'var(--text-muted)'};">${dur}ms</td>
          <td style="display: flex; gap: 4px;">
            <button class="btn" style="padding: 2px 6px; font-size: 11px;" onclick="toggleTokenDeleted(${idx})" title="Toggle Delete/Strikethrough (Alt+Click)">
              ${tok.is_deleted ? '↩ Restore' : '❌ Del'}
            </button>
            ${idx > 0 ? `<button class="btn" style="padding: 2px 6px; font-size: 11px;" onclick="splitLineAtToken(${idx})" title="Split into a new line starting at this syllable">✂ Split</button>` : ''}
          </td>
        `;
        tbody.appendChild(tr);
      });
    }

    function seekAndPlayToken(idx) {
      if (!projectData || !projectData.lines[activeLineIdx] || !wavesurfer || !isWaveReady) return;
      const tok = projectData.lines[activeLineIdx].tokens[idx];
      if (!tok) return;
      lineStopTimeSec = tok.end / 1000.0 + 0.08;
      wavesurfer.setTime(tok.start / 1000.0);
      wavesurfer.play();
    }

    function updateTokenTime(idx, field, val) {
      if (!projectData || !projectData.lines[activeLineIdx]) return;
      const ln = projectData.lines[activeLineIdx];
      const toks = ln.tokens;
      if (toks[idx]) {
        toks[idx][field] = parseInt(val, 10) || 0;
        recalcLineBounds(ln);
        updateWaveformRegions();
        renderActiveLine();
        renderLinesSidebar();
      }
    }

    function toggleTokenDeleted(idx) {
      if (!projectData || !projectData.lines[activeLineIdx]) return;
      const ln = projectData.lines[activeLineIdx];
      const tok = ln.tokens[idx];
      if (tok) {
        tok.is_deleted = !tok.is_deleted;
        recalcLineBounds(ln);
        renderActiveLine();
        renderLinesSidebar();
        updateWaveformRegions();
      }
    }

    function splitLineAtToken(tokIdx) {
      if (!projectData || !projectData.lines[activeLineIdx] || tokIdx <= 0) return;
      const ln = projectData.lines[activeLineIdx];
      const firstHalf = ln.tokens.slice(0, tokIdx);
      const secondHalf = ln.tokens.slice(tokIdx);
      if (!firstHalf.length || !secondHalf.length) return;

      ln.tokens = firstHalf;
      recalcLineBounds(ln);

      const newLine = {
        start: secondHalf[0].start,
        end: secondHalf[secondHalf.length - 1].end,
        is_backing: ln.is_backing,
        tokens: secondHalf
      };
      recalcLineBounds(newLine);
      projectData.lines.splice(activeLineIdx + 1, 0, newLine);
      selectLine(activeLineIdx + 1, false);
    }

    function nudgeActiveLine(deltaMs) {
      if (!projectData || !projectData.lines[activeLineIdx]) return;
      const ln = projectData.lines[activeLineIdx];
      (ln.tokens || []).forEach(t => {
        t.start = Math.max(0, t.start + deltaMs);
        t.end = Math.max(t.start + 20, t.end + deltaMs);
      });
      recalcLineBounds(ln);
      renderActiveLine();
      renderLinesSidebar();
      updateWaveformRegions();
    }

    function toggleBackingActiveLine() {
      if (!projectData || !projectData.lines[activeLineIdx]) return;
      const ln = projectData.lines[activeLineIdx];
      ln.is_backing = !ln.is_backing;
      renderLinesSidebar();
      renderActiveLine();
      updateWaveformRegions();
    }

    function realignLineUniform() {
      if (!projectData || !projectData.lines[activeLineIdx]) return;
      const ln = projectData.lines[activeLineIdx];
      const toks = (ln.tokens || []).filter(t => !t.is_deleted);
      if (!toks.length) return;
      const start = toks[0].start;
      const end = toks[toks.length - 1].end;
      const durPerTok = Math.max(60, Math.floor((end - start) / toks.length));
      toks.forEach((t, i) => {
        t.start = start + i * durPerTok;
        t.end = (i === toks.length - 1) ? Math.max(t.start + 60, end) : t.start + durPerTok;
      });
      recalcLineBounds(ln);
      renderActiveLine();
      renderLinesSidebar();
      updateWaveformRegions();
    }

    function updateWaveformRegions() {
      if (!wsRegions || !isWaveReady || !projectData || !projectData.lines[activeLineIdx]) return;
      wsRegions.clearRegions();
      regionTokenMap.clear();

      const ln = projectData.lines[activeLineIdx];
      const isBacking = !!ln.is_backing;

      (ln.tokens || []).forEach((tok, idx) => {
        const sSec = tok.start / 1000.0;
        const eSec = Math.max(sSec + 0.03, tok.end / 1000.0);

        let color = isBacking ? 'rgba(112, 214, 255, 0.28)' : 'rgba(255, 215, 0, 0.26)';
        if (tok.is_deleted) {
          color = 'rgba(148, 163, 184, 0.10)';
        }

        const regId = `tok-${activeLineIdx}-${idx}`;
        const reg = wsRegions.addRegion({
          id: regId,
          start: sSec,
          end: eSec,
          content: tok.is_deleted ? `✕${tok.text}` : tok.text,
          color: color,
          drag: true,
          resize: true
        });
        regionTokenMap.set(regId, { tokIdx: idx, token: tok });
      });
    }

    // 60fps Real-Time CSS Karaoke Wipe Loop
    function startKaraokeClockLoop() {
      function tick() {
        if (wavesurfer && isWaveReady && projectData && projectData.lines[activeLineIdx]) {
          const curMs = (wavesurfer.getCurrentTime() || 0) * 1000;
          const ln = projectData.lines[activeLineIdx];
          const toks = ln.tokens || [];

          toks.forEach((tok, idx) => {
            let progress = 0;
            const isNow = (curMs >= tok.start && curMs <= tok.end);
            if (curMs >= tok.end) {
              progress = 100;
            } else if (curMs <= tok.start) {
              progress = 0;
            } else {
              const dur = Math.max(1, tok.end - tok.start);
              progress = Math.min(100, Math.max(0, ((curMs - tok.start) / dur) * 100));
            }

            const col = document.getElementById(`col-tok-${idx}`);
            if (col) {
              col.classList.toggle('active-now', isNow);
              col.querySelectorAll('.wipe-text').forEach(el => {
                el.style.setProperty('--wipe', `${progress.toFixed(1)}%`);
              });
            }

            const tr = document.getElementById(`tbl-tok-${idx}`);
            if (tr) {
              tr.classList.toggle('active', isNow);
            }
          });
        }
        requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    }

    function syncVideoPlayback(curTime) {
      const v = document.getElementById('synced-video');
      if (v && v.src && document.getElementById('video-container').classList.contains('visible')) {
        if (Math.abs(v.currentTime - curTime) > 0.18) {
          v.currentTime = curTime;
        }
      }
    }

    // "Tap-Along" Rhythm Retiming Mode ('T' Key)
    function startTapAlong() {
      if (!projectData || !projectData.lines[activeLineIdx] || !wavesurfer || !isWaveReady) return;
      const ln = projectData.lines[activeLineIdx];
      const toks = (ln.tokens || []).filter(t => !t.is_deleted);
      if (!toks.length) return;

      // Snapshot original timings in case user hits Escape
      tapBackupTokens = JSON.parse(JSON.stringify(ln.tokens));
      isTapAlongActive = true;
      tapTokenIdx = 0;
      lineStopTimeSec = null;

      document.getElementById('tap-banner').classList.add('active');
      document.getElementById('btn-tap').textContent = '🔴 Tapping... (Esc)';
      updateTapStatus();

      const playStart = Math.max(0, (toks[0].start - 2000) / 1000.0);
      wavesurfer.setPlaybackRate(0.75);
      document.getElementById('play-speed').value = "0.75";
      wavesurfer.setTime(playStart);
      wavesurfer.play();
    }

    function handleTapSpace() {
      if (!isTapAlongActive || !projectData) return;
      const ln = projectData.lines[activeLineIdx];
      const toks = (ln.tokens || []).filter(t => !t.is_deleted);
      if (tapTokenIdx >= toks.length) return;

      const curMs = Math.round(wavesurfer.getCurrentTime() * 1000);
      toks[tapTokenIdx].start = curMs;

      if (tapTokenIdx > 0) {
        toks[tapTokenIdx - 1].end = Math.max(toks[tapTokenIdx - 1].start + 40, curMs);
      }

      tapTokenIdx++;
      if (tapTokenIdx >= toks.length) {
        toks[toks.length - 1].end = curMs + 600;
        recalcLineBounds(ln);
        finishTapAlong();
      } else {
        updateTapStatus();
        renderSyllablesTable();
      }
    }

    function updateTapStatus() {
      const ln = projectData.lines[activeLineIdx];
      const toks = (ln.tokens || []).filter(t => !t.is_deleted);
      const cur = toks[tapTokenIdx];
      if (cur) {
        document.getElementById('tap-token-status').textContent =
          `Syllable ${tapTokenIdx + 1}/${toks.length}: [${cur.text}] (${cur.pinyin || ''})`;
      }
    }

    function finishTapAlong() {
      isTapAlongActive = false;
      tapBackupTokens = null;
      document.getElementById('tap-banner').classList.remove('active');
      document.getElementById('btn-tap').textContent = '🥁 Tap-Along (T)';
      wavesurfer.setPlaybackRate(1.0);
      document.getElementById('play-speed').value = "1.0";
      wavesurfer.pause();
      renderActiveLine();
      renderLinesSidebar();
      updateWaveformRegions();
    }

    function cancelTapAlong() {
      if (!isTapAlongActive) return;
      if (tapBackupTokens && projectData && projectData.lines[activeLineIdx]) {
        projectData.lines[activeLineIdx].tokens = tapBackupTokens;
        recalcLineBounds(projectData.lines[activeLineIdx]);
      }
      isTapAlongActive = false;
      tapBackupTokens = null;
      document.getElementById('tap-banner').classList.remove('active');
      document.getElementById('btn-tap').textContent = '🥁 Tap-Along (T)';
      wavesurfer.setPlaybackRate(1.0);
      document.getElementById('play-speed').value = "1.0";
      wavesurfer.pause();
      renderActiveLine();
      renderLinesSidebar();
      updateWaveformRegions();
    }

    // QA Health & Issue Jumping
    function updateQAHealth() {
      if (!projectData || !projectData.lint) return;
      const lint = projectData.lint;
      const score = Math.round(lint.mean_confidence || 100);
      const badge = document.getElementById('qa-score-badge');
      badge.textContent = `QA: ${score}%`;
      badge.className = `score-chip ${score >= 85 ? 'score-green' : (score >= 70 ? 'score-yellow' : 'score-red')}`;

      const numIssues = lint.total_issues || 0;
      document.getElementById('qa-issues-count').textContent = `${numIssues} issue${numIssues === 1 ? '' : 's'}`;
    }

    function jumpIssue(dir) {
      if (!projectData || !projectData.lint || !projectData.lint.lines) return;
      const lintLines = projectData.lint.lines;
      const total = lintLines.length;
      if (!total) return;
      let nextIdx = activeLineIdx;

      for (let step = 1; step <= total; step++) {
        nextIdx = (nextIdx + dir + total) % total;
        if (lintLines[nextIdx] && lintLines[nextIdx].issues && lintLines[nextIdx].issues.length > 0) {
          selectLine(nextIdx, true);
          return;
        }
      }
    }

    // YouTube Pipeline 1 Modal & Runner
    function openPipelineModalWithInput(overrideVid = null) {
      const val = overrideVid || document.getElementById('header-yt-input').value.trim();
      document.getElementById('pipe-url-input').value = val;
      document.getElementById('modal-pipeline').classList.add('open');
      document.getElementById('pipe-url-input').focus();
    }

    function openPipelineModalForCurrent() {
      document.getElementById('pipe-url-input').value = currentVid || document.getElementById('header-yt-input').value.trim();
      document.getElementById('modal-pipeline').classList.add('open');
    }

    function closePipelineModal() {
      document.getElementById('modal-pipeline').classList.remove('open');
    }

    function setPipelineStep(activeStep) {
      for (let i = 1; i <= 4; i++) {
        const el = document.getElementById(`pstep-${i}`);
        el.classList.remove('active', 'done');
        if (i < activeStep) el.classList.add('done');
        else if (i === activeStep) el.classList.add('active');
      }
    }

    function executePipeline() {
      const urlOrVid = document.getElementById('pipe-url-input').value.trim();
      if (!urlOrVid) {
        alert("Please enter a YouTube Video ID (e.g. XEGWxPmWTYs) or URL.");
        return;
      }
      const query = document.getElementById('pipe-query-input').value.trim();
      const forceLrc = document.getElementById('pipe-force-lrc').checked ? '1' : '0';
      const skipDemucs = document.getElementById('pipe-skip-demucs').checked ? '1' : '0';
      const skipNetease = document.getElementById('pipe-skip-netease').checked ? '1' : '0';

      const consoleEl = document.getElementById('pipeline-console');
      consoleEl.textContent = `[*] Starting Singalong Pipeline for: ${urlOrVid}\n`;
      setPipelineStep(1);

      const runBtn = document.getElementById('btn-run-pipeline');
      runBtn.disabled = true;
      runBtn.textContent = '⏳ Running Pipeline...';

      const params = new URLSearchParams({
        url: urlOrVid,
        query: query,
        force_lrc: forceLrc,
        skip_demucs: skipDemucs,
        skip_netease: skipNetease
      });

      const sse = new EventSource(`/api/pipeline/run?${params.toString()}`);
      sse.onmessage = async (e) => {
        try {
          const msg = JSON.parse(e.data);
          if (msg.type === 'start') {
            consoleEl.textContent += `$ ${msg.command}\n\n`;
          } else if (msg.type === 'log') {
            const line = msg.text;
            consoleEl.textContent += line + '\n';
            consoleEl.scrollTop = consoleEl.scrollHeight;
            if (line.includes('[1/4]')) setPipelineStep(1);
            else if (line.includes('[2/4]')) setPipelineStep(2);
            else if (line.includes('[3/4]')) setPipelineStep(3);
            else if (line.includes('[4/4]')) setPipelineStep(4);
          } else if (msg.type === 'complete') {
            sse.close();
            runBtn.disabled = false;
            runBtn.textContent = '⚡ Run Pipeline & Create JSON';
            if (msg.returncode === 0) {
              setPipelineStep(5);
              consoleEl.textContent += `\n[✅] Pipeline finished! Loading ${msg.video_id}.json into Workbench...\n`;
              consoleEl.scrollTop = consoleEl.scrollHeight;
              await fetchProjects(false, msg.video_id);
              setTimeout(() => closePipelineModal(), 900);
            } else {
              consoleEl.textContent += `\n[!] Pipeline exited with code ${msg.returncode}.\n`;
            }
          } else if (msg.type === 'error') {
            sse.close();
            runBtn.disabled = false;
            runBtn.textContent = '⚡ Run Pipeline & Create JSON';
            consoleEl.textContent += `\n[!] Error: ${msg.message}\n`;
          }
        } catch (err) {
          consoleEl.textContent += e.data + '\n';
        }
      };
      sse.onerror = () => {
        sse.close();
        runBtn.disabled = false;
        runBtn.textContent = '⚡ Run Pipeline & Create JSON';
      };
    }

    // Phonetics Edit Modal
    function openPhoneticsModal(tokIdx) {
      if (!projectData || !projectData.lines[activeLineIdx]) return;
      activeEditingToken = projectData.lines[activeLineIdx].tokens[tokIdx];
      if (!activeEditingToken) return;

      document.getElementById('edit-modal-text').value = activeEditingToken.text;
      document.getElementById('edit-modal-pinyin').value = activeEditingToken.pinyin || '';
      document.getElementById('edit-modal-en').value = activeEditingToken.en || '';

      // Auto-select language in modal based on character script
      if (/[\uac00-\ud7af]/.test(activeEditingToken.text)) {
        document.getElementById('edit-modal-lang').value = 'ko';
      } else if (/[\u3040-\u30ff]/.test(activeEditingToken.text)) {
        document.getElementById('edit-modal-lang').value = 'ja';
      } else {
        document.getElementById('edit-modal-lang').value = 'zh';
      }

      document.getElementById('modal-edit-phonetics').classList.add('open');
      document.getElementById('edit-modal-pinyin').focus();
    }

    function closePhoneticsModal() {
      document.getElementById('modal-edit-phonetics').classList.remove('open');
      activeEditingToken = null;
    }

    function applyPhoneticsEdit() {
      if (!activeEditingToken) return;
      const txt = document.getElementById('edit-modal-text').value.trim();
      const py = document.getElementById('edit-modal-pinyin').value.trim();
      const en = document.getElementById('edit-modal-en').value.trim();
      const persist = document.getElementById('edit-modal-persist').checked;
      const lang = document.getElementById('edit-modal-lang').value;

      if (txt) activeEditingToken.text = txt;
      activeEditingToken.pinyin = py;
      activeEditingToken.en = en;

      if (persist && activeEditingToken.text) {
        if (!customDictPending[lang]) customDictPending[lang] = {};
        customDictPending[lang][activeEditingToken.text] = { pinyin: py, en: en };
      }

      closePhoneticsModal();
      renderActiveLine();
      renderLinesSidebar();
      updateWaveformRegions();
    }

    // Save Project & Persist Dict
    async function saveProject() {
      if (!projectData || !currentVid) return;
      const btn = document.getElementById('btn-save');
      const originalText = btn.textContent;
      btn.textContent = '💾 Saving...';
      btn.disabled = true;

      try {
        const payload = {
          lines: projectData.lines,
          custom_dict_updates: customDictPending
        };
        const res = await fetch(`/api/project/${currentVid}/save`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.ok) {
          projectData.lint = data.lint;
          customDictPending = {};
          updateQAHealth();
          renderLinesSidebar();
          btn.textContent = '✅ Saved!';
          setTimeout(() => { btn.textContent = originalText; btn.disabled = false; }, 1200);
        } else {
          alert("Save failed: " + data.error);
          btn.textContent = originalText;
          btn.disabled = false;
        }
      } catch (err) {
        alert("Save failed: " + err.message);
        btn.textContent = originalText;
        btn.disabled = false;
      }
    }

    // Video Render Modal & SSE Runner
    function openRenderModal() {
      document.getElementById('modal-render').classList.add('open');
    }
    function closeRenderModal() {
      document.getElementById('modal-render').classList.remove('open');
    }

    async function executeRender() {
      if (!currentVid) return;
      // Save any pending timing edits before rendering!
      await saveProject();

      const scope = document.getElementById('render-scope').value;
      const consoleEl = document.getElementById('render-console');
      consoleEl.textContent = `[*] Saving & starting render for ${currentVid}...\n`;

      const watchBtn = document.getElementById('render-watch-btn');
      watchBtn.style.display = 'none';

      let url = `/api/project/${currentVid}/render?`;
      if (scope === 'preview15') url += 'preview=15';
      else if (scope === 'ass_only') url += 'ass_only=1';

      const sse = new EventSource(url);
      sse.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          if (msg.type === 'log') {
            consoleEl.textContent += msg.text + '\n';
            consoleEl.scrollTop = consoleEl.scrollHeight;
          } else if (msg.type === 'complete') {
            sse.close();
            consoleEl.textContent += `\n[+] Finished with return code ${msg.returncode}.\n`;
            if (msg.video_url) {
              watchBtn.href = msg.video_url;
              watchBtn.style.display = 'inline-flex';
              document.getElementById('btn-toggle-video').style.display = 'inline-flex';
              document.getElementById('synced-video').src = msg.video_url;
            }
          } else if (msg.type === 'error') {
            sse.close();
            consoleEl.textContent += `\n[!] Error: ${msg.message}\n`;
          }
        } catch (err) {
          consoleEl.textContent += e.data + '\n';
        }
      };
      sse.onerror = () => {
        sse.close();
      };
    }

    // Playback & Keyboard Controls
    function togglePlay() {
      if (!wavesurfer || !isWaveReady) return;
      lineStopTimeSec = null;
      wavesurfer.playPause();
    }

    function seekRelative(sec) {
      if (!wavesurfer || !isWaveReady) return;
      lineStopTimeSec = null;
      wavesurfer.setTime(Math.max(0, wavesurfer.getCurrentTime() + sec));
    }

    function playActiveLine() {
      if (!wavesurfer || !isWaveReady || !projectData || !projectData.lines[activeLineIdx]) return;
      const ln = projectData.lines[activeLineIdx];
      const toks = (ln.tokens || []).filter(t => !t.is_deleted);
      if (!toks.length) return;
      const startSec = Math.max(0, (toks[0].start - 100) / 1000.0);
      lineStopTimeSec = (toks[toks.length - 1].end + 150) / 1000.0;
      wavesurfer.setTime(startSec);
      wavesurfer.play();
    }

    function setPlaybackRate(rate) {
      if (wavesurfer) wavesurfer.setPlaybackRate(parseFloat(rate));
    }

    function setWaveformZoom(val) {
      currentZoom = parseInt(val, 10);
      if (wavesurfer && isWaveReady) {
        try { wavesurfer.zoom(currentZoom); } catch (e) {}
      }
    }

    function toggleVideoViewport() {
      const box = document.getElementById('video-container');
      box.classList.toggle('visible');
    }

    function setupKeybindings() {
      window.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') {
          if (e.key === 'Escape') {
            closePhoneticsModal();
            closeRenderModal();
            closePipelineModal();
            e.target.blur();
          } else if (e.key === 'Enter' && document.getElementById('modal-edit-phonetics').classList.contains('open')) {
            e.preventDefault();
            applyPhoneticsEdit();
          }
          return;
        }

        if (isTapAlongActive) {
          if (e.code === 'Space') {
            e.preventDefault();
            handleTapSpace();
            return;
          } else if (e.key === 'Escape') {
            e.preventDefault();
            cancelTapAlong();
            return;
          }
        }

        if (e.code === 'Space') {
          e.preventDefault();
          togglePlay();
        } else if (e.key === 'r' || e.key === 'R') {
          e.preventDefault();
          playActiveLine();
        } else if (e.key === 'j' || e.key === 'J') {
          e.preventDefault();
          seekRelative(-3);
        } else if (e.key === 'l' || e.key === 'L') {
          e.preventDefault();
          seekRelative(3);
        } else if (e.key === 't' || e.key === 'T') {
          e.preventDefault();
          startTapAlong();
        } else if (e.key === 'b' || e.key === 'B') {
          e.preventDefault();
          toggleBackingActiveLine();
        } else if (e.key === 'v' || e.key === 'V') {
          e.preventDefault();
          toggleStem();
        } else if (e.key === 'n' || e.key === 'N' || e.key === ']') {
          e.preventDefault();
          jumpIssue(1);
        } else if (e.key === 'p' || e.key === 'P' || e.key === '[') {
          e.preventDefault();
          jumpIssue(-1);
        } else if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
          e.preventDefault();
          saveProject();
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          if (activeLineIdx > 0) selectLine(activeLineIdx - 1, true);
        } else if (e.key === 'ArrowDown') {
          e.preventDefault();
          if (projectData && activeLineIdx < projectData.lines.length - 1) selectLine(activeLineIdx + 1, true);
        } else if (e.key === 'Escape') {
          closePhoneticsModal();
          closeRenderModal();
          closePipelineModal();
        }
      });
    }
  </script>
</body>
</html>
"""


def find_free_port(start_port: int = 8000, max_tries: int = 50) -> int:
    for port in range(start_port, start_port + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start_port


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", nargs="?", default=None, help="Optional YouTube ID (e.g. XEGWxPmWTYs), URL, or path to <vid>.json")
    parser.add_argument("--workdir", type=Path, default=Path("work"), help="Directory containing projects and stems (default: work)")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument("--no-reexec", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    workdir = args.workdir.resolve()
    if not workdir.exists():
        workdir.mkdir(parents=True, exist_ok=True)

    port = find_free_port(args.port)
    server = ThreadingHTTPServer((args.host, port), EditorRequestHandler)
    server.workdir = workdir

    target_vid = ""
    if args.target:
        cand = Path(args.target)
        if cand.exists():
            target_vid = cand.stem
        else:
            m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/|^)([A-Za-z0-9_-]{11})(?:$|[^A-Za-z0-9_-])", args.target.strip())
            target_vid = m.group(1) if m else args.target.strip()

    query = f"?vid={target_vid}" if target_vid else ""
    url = f"http://{args.host}:{port}/{query}"

    print(f"\n=======================================================")
    print(f"🎤 Singalong Studio — Step 2 Web Editor & QA Workbench")
    print(f"=======================================================")
    print(f"Serving workspace at : {workdir}")
    print(f"Web Editor URL       : \033[1;32m{url}\033[0m")
    if target_vid:
        print(f"Target Video / ID    : {target_vid}")
    print(f"\nPress Ctrl+C to terminate the server.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Shutting down Singalong Studio.")
        server.server_close()


if __name__ == "__main__":
    main()
