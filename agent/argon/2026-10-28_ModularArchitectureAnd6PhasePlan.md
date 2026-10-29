# Singalong Architecture & Phased Implementation Blueprint

This blueprint is structured so **Gemini 3.8 Flash** (or any execution agent) can implement one phase at a time in small, isolated files with minimal token usage and immediate verification after each phase.

---

## 1. Root-Cause Diagnosis: Why Luuk's Song (`15ozx-3FuAs`) Pulled Chinese Lyrics

Inspecting [15ozx-3FuAs.asr.json](file:///home/korodarn/Projects/singalong/work/15ozx-3FuAs.asr.json) and [15ozx-3FuAs.netease.lrc](file:///home/korodarn/Projects/singalong/work/15ozx-3FuAs.netease.lrc) showed three compounding issues in [singalong.py](file:///home/korodarn/Projects/singalong/singalong.py):

1. **Mandarin-Only ASR Hallucinated Hanzi on English Vocals:**
   [`transcribe()`](file:///home/korodarn/Projects/singalong/singalong.py#L709-L735) hardcodes `paraformer-zh`. Fed an English vocal stem, it emitted broken English mixed with Mandarin phonetic hallucinations (`"i was school great ... 每一段那你的你走了真美丽的那..."`).
2. **Hallucinated Hanzi Poisoned the NetEase Search Queries:**
   [`fetch_netease_lyrics()`](file:///home/korodarn/Projects/singalong/singalong.py#L629-L639) saw $\ge 12$ Hanzi characters in `asr_text`, sliced 5-character chunks of hallucinated Chinese, and queried NetEase Cloud Music with them.
3. **Permissive Similarity Threshold (`0.015`) Accepted an Unrelated Track:**
   [`compute_text_similarity()`](file:///home/korodarn/Projects/singalong/singalong.py#L555-L570) matched common English stopwords (`"i"`, `"the"`, `"say"`, `"my"`) against a Taiwanese indie song (*Tizzy Bac – 昏沉沉大太阳 / California*), clearing the `best_sim > 0.015` bar at [singalong.py](file:///home/korodarn/Projects/singalong/singalong.py#L687).

---

## 2. Target Modular Directory Hierarchy

Splitting the current monoliths ([editor.py](file:///home/korodarn/Projects/singalong/editor.py) at 3,701 lines and [singalong.py](file:///home/korodarn/Projects/singalong/singalong.py) at 1,222 lines) into focused Python and TypeScript modules keeps every file under ~250 lines:

```text
singalong/                       # Root workspace
├── singalong.py                 # Thin CLI wrapper -> calls core.pipeline (~60 lines)
├── colorfill.py                 # Thin CLI wrapper -> calls core.render (~60 lines)
├── editor.py                    # HTTP/SSE API server + static web/dist host (~250 lines)
├── lint.py                      # Thin CLI wrapper -> calls core.linter (~50 lines)
├── config.json
├── custom_dict.json
├── core/                        # Modular Python backend package
│   ├── __init__.py
│   ├── models.py                # Dataclasses / TypedDicts for Token, Line, ProjectMeta, LyricsCandidate
│   ├── media.py                 # Audio-only yt-dlp download, separate bg-video download, ffmpeg, demucs
│   ├── asr.py                   # Multi-engine ASR dispatcher (paraformer-zh, sensevoice, faster-whisper)
│   ├── aligner.py               # VocalAnalyzer, LRC-to-ASR aligner + plain-text forced aligner
│   ├── linter.py                # Acoustic RMS & timing QA rules (moved from lint.py)
│   ├── render.py                # ASS Ruby generator + FFmpeg video/visualizer compositor (from colorfill.py)
│   ├── visualizer.py            # Phase 6: FFmpeg / Canvas waveform & spectrum overlay generator
│   ├── lyrics/                  # Multi-source lyrics discovery & upfront validation
│   │   ├── __init__.py          # Search orchestrator across providers
│   │   ├── parser.py            # LRC + plain-text parser, backing-vocal splitter
│   │   ├── netease.py           # NetEase search + title sanitizer + tlyric support
│   │   ├── lrclib.py            # LRCLIB API client (EN / JA / KO / global tracks)
│   │   ├── youtube_cc.py        # YouTube subtitles (yt-dlp --write-subs) & description lyrics extractor
│   │   └── validator.py         # Language/script ratio sense-checker & duration/title scorer
│   └── phonetics/               # Per-language tokenization, romanization & US-English respelling
│       ├── __init__.py          # Language dispatcher & custom_dict.json loader
│       ├── zh.py                # Mandarin pypinyin + US-English respelling
│       ├── ko.py                # Hangul phonological assimilation + RR + respelling
│       ├── ja.py                # Japanese Kanji/Kana (pykakasi) -> Hepburn Romaji + respelling
│       └── en.py                # English word tokenizer & single-tier collapse marker
└── web/                         # Editor frontend (Bun + TypeScript + SolidJS + Vite)
    ├── package.json
    ├── tsconfig.json
    ├── vite.config.ts
    ├── index.html
    └── src/
        ├── main.tsx
        ├── types.ts             # Shared TypeScript interfaces matching core/models.py
        ├── api.ts               # Typed REST & SSE helpers
        ├── state/
        │   └── store.ts         # SolidJS fine-grained reactive store
        └── components/
            ├── HeaderBar.tsx        # Project selector, stem toggle, QA score nav, Save/Render
            ├── IngestModal.tsx      # 2-Step Ingest: Step 1 (Fetch/Sense-Check/Preview/Paste) -> Step 2 (ASR/Align)
            ├── KaraokeStage.tsx     # 60fps CSS Ruby stage +獨立 <video> bg layer + <canvas> waveform visualizer
            ├── VideoBgModal.tsx     # Drop-in background video URL/file manager & offset/opacity controls
            ├── WaveformPane.tsx     # WaveSurfer v7 editor, regions, Tap-Along (T) mode
            ├── LyricTable.tsx       # Line & syllable inspector table
            ├── PhoneticModal.tsx    # Double-click token dictionary override modal
            └── RenderModal.tsx      # Render settings (bg video, waveform overlay, preview sec) & SSE logs
```

---

## 3. Multi-Engine ASR Strategy (`core/asr.py`)

Why having **multiple selectable ASR engines** is beneficial for singing across languages:

| Engine ID | Underlying Model | Strengths in Singing / Karaoke | Best For |
| :--- | :--- | :--- | :--- |
| **`paraformer`** | FunASR `seaco_paraformer_large` (already cached) | Acoustic character-by-character timestamps trained specifically on Mandarin syllables; fast and rock-solid on Chinese vocals. | **Mandarin (`zh`)** |
| **`whisper`** | `faster-whisper` (`large-v3-turbo` or `medium`, `word_timestamps=True`) | Best cross-attention word alignment on English singing, handles held vowels/melismas and rock/pop backing mixes without hallucinating CJK characters; strong on Japanese & Korean. | **English (`en`)**, **Japanese (`ja`)**, **Korean (`ko`)** |
| **`sensevoice`** | FunASR `iic/SenseVoiceSmall` | Ultra-fast (~1s per song) multilingual CTC + automatic Spoken/Sung Language Identification (`zh`, `en`, `ja`, `ko`, `yue`). | **Fast Language ID** + rapid CJK fallback |
| **`auto`** *(Default)* | Smart routing | Runs fast language check (or uses user's selected language): routes `zh` $\rightarrow$ `paraformer`, `en`/`ja`/`ko` $\rightarrow$ `whisper` (or `sensevoice` if `faster-whisper` is not installed). | General use |

- **Per-Engine Caching:** Cache files are stored at `work/<vid>.asr.<engine>.json` (with fallback to `work/<vid>.asr.json`), so switching engines in the editor to compare alignment quality never re-downloads audio or re-runs Demucs.

---

## 4. Phased Execution Instructions for Gemini 3.8 Flash

> [!TIP]
> **How to Run Each Phase with 3.8 Flash:**
> Prompt 3.8 Flash with: *"Implement Phase N from `implementation_plan.md`"* (or run one phase per session). Each phase below lists the exact files to create/modify and the exact verification commands to run.

---

### Phase 1: Backend Modularization (`core/` Package Split)

**Objective:** Extract logic from [singalong.py](file:///home/korodarn/Projects/singalong/singalong.py), [colorfill.py](file:///home/korodarn/Projects/singalong/colorfill.py), and [lint.py](file:///home/korodarn/Projects/singalong/lint.py) into `core/` without changing existing behavior or CLI flags.

#### Tasks:
1. Create `core/__init__.py` and `core/models.py` (helper types for `Token`, `Line`, `ProjectMeta`).
2. Create `core/phonetics/`:
   - `core/phonetics/zh.py`: Move `INITIALS`, `INITIAL_EN`, `Y_MAP`, `W_MAP`, `FINAL_EN`, `respell()`, `is_han()`, and Mandarin `pypinyin` logic from [singalong.py](file:///home/korodarn/Projects/singalong/singalong.py#L30-L95).
   - `core/phonetics/ko.py`: Move `is_hangul()`, `HANGUL_*`, `decompose_hangul()`, and `apply_korean_phonology()` from [singalong.py](file:///home/korodarn/Projects/singalong/singalong.py#L97-L295).
   - `core/phonetics/en.py`: Move Latin word tokenization & verbatim tier assignment.
   - `core/phonetics/__init__.py`: Expose `tokenize_text()`, `tokenize_tokens()`, `load_custom_dict()`, `save_custom_dict()`, and `add_phonetics()`.
3. Create `core/media.py`: Move `run()`, `extract_youtube_id()`, `download_audio()`, `isolate_vocals()`, and `get_audio_duration()`. Ensure `download_audio()` strictly downloads audio only.
4. Create `core/asr.py`: Move `transcribe()` and `build_tokens()`.
5. Create `core/lyrics/`:
   - `core/lyrics/parser.py`: Move `parse_lrc()` and `compute_text_similarity()`.
   - `core/lyrics/netease.py`: Move `query_netease_api()` and `fetch_netease_lyrics()`.
6. Create `core/aligner.py`: Move `VocalAnalyzer`, `allocate_chars_to_islands()`, `align_line_with_clauses()`, `enforce_non_overlapping_lines()`, `align_lrc_with_tokens()`, `group_lines()`, and `write_outputs()`.
7. Create `core/linter.py`: Move `load_audio_mono()`, `rms_dbfs()`, and `lint_project()` from [lint.py](file:///home/korodarn/Projects/singalong/lint.py).
8. Create `core/render.py`: Move `DEFAULT_CONFIG`, `hex_to_ass()`, `parse_alignment()`, `fmt_ass_time()`, `estimate_text_width()`, `load_config()`, `generate_ass()`, and `render_video()` from [colorfill.py](file:///home/korodarn/Projects/singalong/colorfill.py).
9. Slim down [singalong.py](file:///home/korodarn/Projects/singalong/singalong.py), [colorfill.py](file:///home/korodarn/Projects/singalong/colorfill.py), and [lint.py](file:///home/korodarn/Projects/singalong/lint.py) so they import from `core.*` and keep re-exports for any symbols imported elsewhere.

#### Verification Commands:
```bash
./.venv/bin/python lint.py work/jgWBRVy3uIg.json
./.venv/bin/python colorfill.py work/jgWBRVy3uIg.json --ass-only
./.venv/bin/python singalong.py --help
```

---

### Phase 2: Frontend Extraction to Bun + TypeScript + SolidJS (`web/`)

**Objective:** Replace the 3,100-line `EDITOR_HTML` string in [editor.py](file:///home/korodarn/Projects/singalong/editor.py) with a clean, modular Bun + Vite + SolidJS + TypeScript app in `web/`, served by `editor.py`.

#### Tasks:
1. Initialize `web/`:
   - `web/package.json` with `solid-js`, `wavesurfer.js` (`^7.8.0`), and devDependencies `typescript`, `vite`, `vite-plugin-solid`.
   - `web/tsconfig.json` and `web/vite.config.ts` (configuring `build.outDir = "dist"` and dev proxy `/api` $\rightarrow$ `http://127.0.0.1:8000`).
2. Create `web/src/types.ts`: Define `Token`, `Line`, `ProjectSummary`, `ProjectData`, `LintIssue`, `LintReport`, `ProjectConfig`.
3. Create `web/src/api.ts`: Typed wrappers for `/api/projects`, `/api/project/:vid`, `/api/project/:vid/save`, `/api/dict`, and SSE helpers for `/api/pipeline/run` and `/api/project/:vid/render`.
4. Create `web/src/state/store.ts`: SolidJS store managing project state, `activeLineIdx`, `currentTime`, `isPlaying`, `followPlayhead`, `stemMode` (`vocals` | `mix`), `tapMode`, `undoStack`, and keyboard shortcuts (`Space`, `V`, `R`, `J`, `L`, `B`, `T`, `N`, `P`, `Ctrl+S`, `Ctrl+Z`).
5. Create modular components in `web/src/components/`:
   - `HeaderBar.tsx`: Brand, YouTube ingest trigger, project dropdown, stem switcher, QA score chip & Prev/Next issue buttons, Save & Render actions.
   - `KaraokeStage.tsx`: 60fps `--wipe-pct` Ruby column preview stage with `Click` (seek), `Alt+Click` (delete token), `Double-Click` (phonetics modal), and `✂ Split` button.
   - `WaveformPane.tsx`: WaveSurfer v7 instance + Regions plugin with Lead (Gold) and Backing (Cyan) draggable syllable boxes + Tap-Along (`T`) handler.
   - `LyricTable.tsx`: Scrollable line & syllable editor table with inline backing toggle and line split/merge.
   - `PhoneticModal.tsx`, `IngestModal.tsx`, `RenderModal.tsx`.
6. Update [editor.py](file:///home/korodarn/Projects/singalong/editor.py) to serve static assets from `web/dist` (auto-building with `bun run build` on startup if `web/dist/index.html` is missing).

#### Verification Commands:
```bash
cd web && bun install && bunx tsc --noEmit && bun run build
./.venv/bin/python editor.py --help
```

---

### Phase 3: Multi-Source Lyrics, Upfront Sense-Checking & Pre-JSON Preview/Drop-In

**Status:** ✅ Completed

**Objective:** Fix the `15ozx-3FuAs` failure mode by adding multi-provider search, upfront language/script sense-checking, a pre-alignment **Lyrics Preview & Manual Paste/Edit step** in the editor, and plain-text forced alignment when timestamps aren't available.

#### Tasks:
1. **`core/lyrics/validator.py` (Upfront Sense-Checker):**
   - Implement `clean_search_title(raw_title: str) -> list[str]` to strip YouTube channel tags (`[Wuthering Waves]`, `Official`, `MV`, `EP`, `Resonator Showcase`, brackets) and extract candidate song titles.
   - Implement `analyze_script_profile(text: str) -> dict` returning character ratios: `{"en": float, "zh": float, "ko": float, "ja": float}` (distinguishing Japanese via Hiragana/Katakana `[\u3040-\u30ff]` presence alongside Kanji).
   - Implement `validate_candidate(candidate_text: str, expected_lang: str | None, audio_duration_s: float | None, asr_text: str | None) -> ValidationResult`:
     - Rejects candidates where script profile contradicts `expected_lang` (e.g., rejecting Chinese lyrics when `expected_lang == "en"` or when title says `EN Ver.`).
     - Rejects candidates whose last LRC timestamp exceeds `audio_duration_s + 25s`.
     - Raises minimum ASR similarity threshold from `0.015` to `0.12` (and ignores English stopword-only overlap on predominantly CJK lyric sheets).
2. **Additional Lyric Providers:**
   - `core/lyrics/lrclib.py`: Query `https://lrclib.net/api/search?q=...` returning both `syncedLyrics` (`.lrc`) and `plainLyrics`.
   - `core/lyrics/youtube_cc.py`: Use `yt-dlp --skip-download --write-subs --sub-langs "en.*,ja.*,ko.*,zh.*"` and video description extraction (`%(description)s`) to pull official lyrics embedded by Kuro Games / music channels.
   - Update `core/lyrics/netease.py`: Never build Chinese Hanzi search queries from ASR unless `expected_lang` is `zh` (or `auto` with $>40\%$ high-confidence CJK ratio). Also parse `tlyric` (translated lyrics) when present.
3. **Plain-Text (Unsynchronized) Lyrics Aligner (`core/aligner.py`):**
   - Implement `align_plain_lyrics_with_tokens(plain_lines: list[dict], asr_tokens: list[dict], analyzer: VocalAnalyzer) -> list[dict]`:
     - Uses global fuzzy sequence alignment (dynamic programming / anchor matching between tokenized lyrics and ASR tokens, falling back to proportional allocation across `analyzer.active_islands()` for unmatched vocal phrases) so dropped-in plain text lyrics get accurate initial line and syllable timings.
4. **Two-Step Editor Ingestion API & UI (`editor.py` + `IngestModal.tsx`):**
   - Add `POST /api/lyrics/preview`: Accepts `{ url, query, lang }` $\rightarrow$ fetches video metadata + searches YouTube CC, LRCLIB, and NetEase $\rightarrow$ returns ranked candidates with sense-check badges (`Verified`, `Script Mismatch`, `Plain Text`, `Synced LRC`).
   - Update `IngestModal.tsx`:
     - **Step 1 (Preview & Edit Lyrics):** Select a candidate from the list *or* paste/edit raw LRC or plain lyrics directly in the preview textarea. Choose Target Language (`Auto`, `EN`, `ZH`, `JA`, `KO`) and ASR Engine (`Auto`, `Whisper`, `SenseVoice`, `Paraformer`).
     - **Step 2 (Run Alignment & Create JSON):** Sends the confirmed/edited lyrics text to `POST /api/pipeline/align` (or SSE stream) which runs Demucs + ASR + Alignment using the exact approved lyrics and loads `<vid>.json`.

#### Verification Commands:
```bash
# Test lyric search & validator on Luuk's song (15ozx-3FuAs) - must reject Tizzy Bac Chinese LRC
./.venv/bin/python -c "from core.lyrics import search_all_lyrics; print(search_all_lyrics('15ozx-3FuAs', expected_lang='en'))"
cd web && bunx tsc --noEmit && bun run build
```

---

### Phase 4: Multi-Engine ASR (`whisper` / `sensevoice` / `paraformer`) & Full 4-Language Support (EN, ZH, KO, JA)

**Objective:** Add selectable ASR engines (`faster-whisper` and `SenseVoiceSmall` alongside `paraformer-zh`), implement Japanese (`ja`) Kanji/Kana romanization & English phonetic respelling, and add clean single-tier English karaoke rendering.

#### Tasks:
1. **Install Lightweight Dependencies in `.venv`:**
   - `pykakasi` (Japanese Kanji/Kana $\rightarrow$ Hepburn Romaji & Hiragana decomposition).
   - `faster-whisper` (CTranslate2-based Whisper with word-level timestamps).
2. **Japanese Phonetics Module (`core/phonetics/ja.py`):**
   - Detect Japanese text via Hiragana (`\u3040-\u309f`) and Katakana (`\u30a0-\u30ff`) or `lang == "ja"`.
   - Use `pykakasi` to segment Kanji+Okurigana words into phonetic units with Hepburn Romaji (`pinyin` tier) and US-English phonetic respelling (`en` tier: e.g., `ka` $\rightarrow$ `kah`, `ki` $\rightarrow$ `kee`, `ku` $\rightarrow$ `koo`, `ke` $\rightarrow$ `keh`, `ko` $\rightarrow$ `koh`, `shi` $\rightarrow$ `shee`, `chi` $\rightarrow$ `chee`, `tsu` $\rightarrow$ `tsoo`, `fu` $\rightarrow$ `foo`, long vowels).
   - Auto-select `Source Han Sans JP` / `Noto Sans JP` in `core/render.py` when Japanese script is present.
3. **Multi-Engine ASR Implementation (`core/asr.py`):**
   - Implement `transcribe_paraformer()`, `transcribe_sensevoice()`, and `transcribe_whisper()` with engine-specific cache files `work/<vid>.asr.<engine>.json`.
   - Implement `transcribe(wav, workdir, vid, device, engine="auto", lang="auto")`.
4. **Display Tier Mode (`single_tier` vs `multi_tier`):**
   - Allow toggling 1-tier (plain lyrics only—ideal for pure English songs or native-only karaoke) vs. 2-tier (Native + Romanization) vs. 3-tier (US-English Phonetics + Romanization + Native) in the editor header and render config.

#### Verification Commands:
```bash
./.venv/bin/python -c "from core.phonetics import add_phonetics; toks=[{'text':'世界'},{'text':'を'},{'text':'照らす'}]; add_phonetics(toks, lang='ja'); print(toks)"
cd web && bunx tsc --noEmit && bun run build
```

---

### Phase 5: Decoupled Background Video Link & Synchronized Stage Preview

**Objective:** Allow dropping any video link (or local video) into the editor as a visual background for the karaoke stage and final render, completely decoupled from the song's audio pipeline.

#### Tasks:
1. **Decoupled Video Downloader & Metadata (`core/media.py` & `editor.py`):**
   - Implement `download_bg_video(video_url_or_id: str, workdir: Path) -> Path`: Downloads video-only (`yt-dlp -f "bestvideo[height<=1080][ext=mp4]/best[height<=1080][ext=mp4]/best[height<=1080]"`) into `work/bg/<bg_vid>.mp4` so multiple projects can even reuse the same background video without re-downloading.
   - Store per-project visual settings in `work/<vid>.meta.json`:
     ```json
     {
       "bg_video_id": "dQw4w9WgXcQ",
       "bg_video_path": "work/bg/dQw4w9WgXcQ.mp4",
       "bg_offset_ms": 0,
       "bg_dim_pct": 35,
       "tier_mode": "multi_tier"
     }
     ```
   - Add endpoints:
     - `POST /api/project/<vid>/meta` (save project visual/tier metadata)
     - `GET /api/bg-video/fetch?url=...&project=<vid>` (SSE progress while `yt-dlp` downloads the background video)
     - `GET /api/bg-video/stream/<bg_id>` (HTTP 206 byte-range video streaming)
2. **Live Synchronized `<video>` Layer in Editor (`KaraokeStage.tsx` & `VideoBgModal.tsx`):**
   - Add a `🎬 Background Video` button in the stage/header bar where you can paste any video URL, adjust **Video Start Offset (ms)** (to line up a specific cutscene drop with the chorus), and adjust **Background Dimming (%)** (dark overlay between video and text for legibility).
   - Render a muted `<video>` element behind the karaoke text in `KaraokeStage.tsx`. Sync its play/pause and `currentTime = (audioTimeMs + bgOffsetMs) / 1000` with WaveSurfer.
3. **Final Render Integration (`core/render.py`):**
   - Update `render_video()` so when `bg_video_path` is set in `<vid>.meta.json` (or passed via CLI), FFmpeg scales/crops the background video to `1920x1080`, applies the configured dimming (`drawbox` / `colorlevels`) and start offset (`-ss`), overlays the `.ass` karaoke subtitles, and mixes the project's `.wav` audio.

#### Verification Commands:
```bash
cd web && bunx tsc --noEmit && bun run build
./.venv/bin/python colorfill.py work/jgWBRVy3uIg.json --preview 5
```

---

### Phase 6: Audio Waveform & Spectrum Visualizer Overlays (Preview + Render)

**Objective:** Allow previewing and rendering customizable audio waveform/spectrum representations directly onto the video (e.g., sleek bottom bar spectrum, center-line oscillating wave, or vocal-reactive pulse synced to either `vocals.wav` or the full mix).

#### Tasks:
1. **Visualizer Styles & Configuration (`<vid>.meta.json` & `config.json`):**
   - Support selectable visualizer presets:
     - `none`: Disabled
     - `bars_bottom`: Modern frequency spectrum bars along the bottom edge or behind/above the lyrics
     - `line_wave`: Smooth oscillating time-domain waveform (`showwaves` `cline` / `p2p`)
     - `mirror_leds`: Symmetric mirrored spectrum bars
   - Configurable parameters: `audio_source` (`vocals` vs `mix`), `color` (e.g. `#FFD700` or `#70D6FF`), `opacity`, `position` (`bottom`, `top`, `behind-text`), and `height`.
2. **Live 60fps Editor Preview (`KaraokeStage.tsx`):**
   - Add a `<canvas>` overlay inside `KaraokeStage.tsx` driven by Web Audio API `AnalyserNode` connected to the active WaveSurfer media element, rendering the chosen visualizer preset in real time over the background video and behind the karaoke lyrics.
3. **FFmpeg Filtergraph Render (`core/visualizer.py` & `core/render.py`):**
   - Build an FFmpeg `-filter_complex` pipeline combining the background video (or solid background), the audio visualizer filter (`showfreqs` / `showwaves` with custom hex color & alpha blending), and the final `ass` subtitle filter so the rendered `.webm`/`.mp4` matches the live stage preview.
