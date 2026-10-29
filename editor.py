#!/usr/bin/env python3
"""
editor.py - Web Editor & QA Workbench Server for Singalong AI.

Serves the Bun + SolidJS + TypeScript frontend from web/dist and provides REST & SSE APIs.
"""

import argparse
import json
import mimetypes
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

from core.linter import lint_project
from core.phonetics import load_custom_dict, save_custom_dict
from core.lyrics import (
    search_all_lyrics,
    parse_lrc,
    detect_script_distribution,
    validate_lyrics_candidate,
)

CONFIG_PATH = Path("config.json")
DEFAULT_WORKDIR = Path("work")
WEB_DIR = Path(__file__).parent / "web"
WEB_DIST = WEB_DIR / "dist"


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
        if (
            p.name.startswith(".")
            or p.name.endswith(".asr.json")
            or p.name.endswith(".bak")
            or p.name.endswith(".tmp")
            or p.name.endswith(".meta.json")
        ):
            continue
        vid = p.stem
        wav_path = workdir / f"{vid}.wav"
        vocals_path = workdir / "htdemucs" / vid / "vocals.wav"
        webm_path = workdir / f"{vid}.webm"
        mp4_path = workdir / f"{vid}.mp4"
        meta_path = workdir / f"{vid}.meta.json"

        title = vid
        artist = ""
        duration = 0
        if meta_path.exists():
            try:
                m_data = json.loads(meta_path.read_text(encoding="utf-8"))
                title = m_data.get("title", vid)
                artist = m_data.get("artist", "")
                duration = m_data.get("duration", 0)
            except Exception:
                pass

        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            num_lines = len(data) if isinstance(data, list) else 0
        except Exception:
            num_lines = 0

        projects.append({
            "id": vid,
            "filename": p.name,
            "title": title,
            "artist": artist,
            "duration": duration,
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

        if path.startswith("/api/"):
            if path == "/api/projects":
                self.send_json(find_projects(self.server.workdir))
            elif path == "/api/pipeline/run":
                url_or_vid = query.get("url", [""])[0].strip()
                custom_query = query.get("query", [""])[0].strip()
                target_lang = query.get("target_lang", [""])[0].strip() or None
                lyrics_file = query.get("lyrics_file", [""])[0].strip() or None
                skip_demucs = query.get("skip_demucs", ["0"])[0] in ("1", "true")
                skip_netease = query.get("skip_netease", ["0"])[0] in ("1", "true")
                force_lrc = query.get("force_lrc", ["0"])[0] in ("1", "true")
                preview_only = query.get("preview_only", ["0"])[0] in ("1", "true")
                self.serve_pipeline_sse(
                    url_or_vid,
                    custom_query,
                    skip_demucs,
                    skip_netease,
                    force_lrc,
                    target_lang,
                    lyrics_file,
                    preview_only,
                )
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
        else:
            self.serve_static(path)

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
            elif len(parts) >= 4 and parts[3] == "meta":
                self.handle_meta_update(vid)
            else:
                self.send_error(404, "Endpoint not found")
        elif path == "/api/dict":
            self.handle_dict_update()
        elif path == "/api/lyrics/search":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                payload = json.loads(body.decode("utf-8"))
                query = payload.get("query", "")
                url = payload.get("url", "")
                target_lang = payload.get("target_lang")
                candidates = search_all_lyrics(
                    query=query,
                    url_or_vid=url,
                    target_lang=target_lang,
                    workdir=self.server.workdir,
                )
                self.send_json({"ok": True, "candidates": candidates})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, status=500)
        elif path == "/api/lyrics/validate":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                payload = json.loads(body.decode("utf-8"))
                raw_text = payload.get("raw_text", "")
                target_lang = payload.get("target_lang")
                script_info = detect_script_distribution(raw_text)
                parsed = parse_lrc(raw_text)
                is_valid, reason, score = validate_lyrics_candidate(raw_text, target_lang=target_lang)
                preview_lines = [p["text"] for p in parsed[:8]] if parsed else [l.strip() for l in raw_text.splitlines() if l.strip()][:8]
                self.send_json({
                    "ok": True,
                    "script": script_info,
                    "is_synced": bool(parsed),
                    "line_count": len(parsed) if parsed else len(raw_text.splitlines()),
                    "preview_lines": preview_lines,
                    "validation": {"is_valid": is_valid, "reason": reason, "score": score},
                })
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, status=500)
        elif path == "/api/pipeline/prepare_lyrics":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                payload = json.loads(body.decode("utf-8"))
                raw_text = payload.get("raw_text", "")
                vid = payload.get("video_id") or "custom"
                clean_vid = re.sub(r"[^A-Za-z0-9_-]", "", vid)[:24]
                tmp_path = self.server.workdir / f".tmp_{clean_vid}_lyrics.txt"
                tmp_path.write_text(raw_text, encoding="utf-8")
                self.send_json({"ok": True, "lyrics_file": str(tmp_path)})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, status=500)
        else:
            self.send_error(404, "Not Found")

    def serve_static(self, req_path: str):
        if not WEB_DIST.exists():
            self.send_error(503, "web/dist not found. Please run 'cd web && bun run build'")
            return

        clean_path = req_path.lstrip("/")
        if not clean_path or clean_path == "index.html":
            file_path = WEB_DIST / "index.html"
        else:
            file_path = WEB_DIST / clean_path
            if not file_path.exists() or file_path.is_dir():
                file_path = WEB_DIST / "index.html"

        if not file_path.exists():
            self.send_error(404, "Static file not found")
            return

        mime_type, _ = mimetypes.guess_type(str(file_path))
        if mime_type is None:
            mime_type = "application/octet-stream"
        if file_path.suffix == ".html":
            mime_type = "text/html; charset=utf-8"

        try:
            content = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

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
        try:
            vocals_cand = self.server.workdir / "htdemucs" / vid / "vocals.wav"
            if not vocals_cand.exists():
                vocals_cand = self.server.workdir / f"{vid}.wav"
            lint_report = lint_project(json_path, vocals_cand if vocals_cand.exists() else None)
        except Exception as e:
            lint_report = {"error": str(e), "mean_confidence": 100.0, "total_issues": 0, "lines": []}

        cfg = load_config()
        custom_dict = load_custom_dict()

        meta_path = self.server.workdir / f"{vid}.meta.json"
        metadata = {"video_id": vid, "title": vid, "artist": "", "duration": 0}
        if meta_path.exists():
            try:
                metadata.update(json.loads(meta_path.read_text(encoding="utf-8")))
            except Exception:
                pass

        response = {
            "exists": True,
            "video_id": vid,
            "metadata": metadata,
            "lines": lines,
            "config": cfg,
            "lint": lint_report,
            "custom_dict": custom_dict,
            "has_vocals": (self.server.workdir / "htdemucs" / vid / "vocals.wav").exists(),
            "has_mix": (self.server.workdir / f"{vid}.wav").exists(),
            "has_video": (self.server.workdir / f"{vid}.webm").exists() or (self.server.workdir / f"{vid}.mp4").exists(),
        }
        self.send_json(response)

    def handle_meta_update(self, vid: str):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            payload = json.loads(body.decode("utf-8"))
            meta_path = self.server.workdir / f"{vid}.meta.json"
            current = {"video_id": vid, "title": vid, "artist": "", "duration": 0}
            if meta_path.exists():
                try:
                    current.update(json.loads(meta_path.read_text(encoding="utf-8")))
                except Exception:
                    pass
            if "title" in payload:
                current["title"] = str(payload["title"]).strip()
            if "artist" in payload:
                current["artist"] = str(payload["artist"]).strip()
            meta_path.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
            self.send_json({"ok": True, "metadata": current})
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, status=400)

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

            for ln in lines:
                active_toks = [t for t in ln.get("tokens", []) if not t.get("is_deleted")]
                if active_toks:
                    ln["start"] = active_toks[0]["start"]
                    ln["end"] = active_toks[-1]["end"]

            bak_path = self.server.workdir / f"{vid}.json.bak"
            if json_path.exists():
                shutil.copyfile(json_path, bak_path)

            temp_path = self.server.workdir / f"{vid}.json.tmp"
            temp_path.write_text(json.dumps(lines, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(json_path)

            if dict_updates and isinstance(dict_updates, dict):
                current_dict = load_custom_dict()
                for lang, words in dict_updates.items():
                    if lang not in current_dict:
                        current_dict[lang] = {}
                    current_dict[lang].update(words)
                save_custom_dict(current_dict)

            lint_report = None
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

    def serve_pipeline_sse(
        self,
        url_or_vid: str,
        custom_query: str,
        skip_demucs: bool,
        skip_netease: bool,
        force_lrc: bool,
        target_lang: str | None = None,
        lyrics_file: str | None = None,
        preview_only: bool = False,
    ):
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
        if target_lang:
            cmd.extend(["--target-lang", target_lang])
        if lyrics_file:
            cmd.extend(["--lyrics-file", lyrics_file])
        if skip_demucs:
            cmd.append("--skip-demucs")
        if skip_netease:
            cmd.append("--skip-netease")
        if force_lrc:
            cmd.append("--force-lrc")
        if preview_only:
            cmd.append("--preview-only")

        send_event("start", {"command": " ".join(cmd)})

        resolved_vid = None
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
            preview_data = None
            if preview_only and resolved_vid:
                preview_path = self.server.workdir / f".preview_{resolved_vid}.json"
                if preview_path.exists():
                    try:
                        preview_data = json.loads(preview_path.read_text(encoding="utf-8"))
                    except Exception:
                        pass
            send_event("complete", {
                "returncode": ret,
                "video_id": resolved_vid,
                "preview_only": preview_only,
                "preview": preview_data,
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


def ensure_web_build():
    dist_index = WEB_DIST / "index.html"
    needs_build = not dist_index.exists()
    if not needs_build and WEB_DIR.exists():
        dist_mtime = dist_index.stat().st_mtime
        src_files = list((WEB_DIR / "src").rglob("*")) + [WEB_DIR / "index.html", WEB_DIR / "package.json"]
        if any(f.is_file() and f.stat().st_mtime > dist_mtime for f in src_files):
            needs_build = True

    if needs_build:
        print("[*] Building SolidJS TypeScript frontend (web/dist)...")
        subprocess.run(["bun", "run", "build"], cwd=WEB_DIR, check=True)


def find_free_port(start_port: int = 8000, max_tries: int = 50) -> int:
    for port in range(start_port, start_port + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start_port


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", nargs="?", default=None, help="Optional YouTube ID, URL, or path to <vid>.json")
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR, help="Directory containing projects (default: work)")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument("--no-reexec", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    ensure_web_build()

    workdir = args.workdir.resolve()
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
    print(f"🎤 Singalong Studio — SolidJS + TypeScript Workbench")
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
