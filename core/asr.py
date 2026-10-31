"""
core.asr - Multi-Engine Acoustic Speech Recognition (ASR) dispatcher and token builders.
Supports:
  - 'paraformer': FunASR Paraformer-large (Mandarin character-level timestamps)
  - 'whisper': faster-whisper with word_timestamps=True (English, Japanese, Korean, Multilingual)
  - 'sensevoice': FunASR SenseVoiceSmall (Fast multilingual CTC + Language ID)
  - 'auto': Smart routing based on target language / song metadata
"""

import json
import re
import sys
from pathlib import Path
from core.phonetics import tokenize_text, has_kana, is_hangul


def build_tokens(text: str, stamps: list, lang: str | None = None) -> list[dict]:
    toks = tokenize_text(text, lang=lang)
    if not toks or not stamps:
        return []
    # If token count matches stamp count, zip 1-to-1
    if len(toks) == len(stamps):
        return [{"text": t, "start": int(s[0]), "end": int(s[1])} for t, s in zip(toks, stamps)]
    # Fallback when re-tokenizing (e.g. Japanese compound grouping on character stamps)
    out = []
    n_s = len(stamps)
    n_t = len(toks)
    for idx, t in enumerate(toks):
        s_idx = min(n_s - 1, int(round(idx * n_s / max(1, n_t))))
        e_idx = min(n_s - 1, max(s_idx, int(round((idx + 1) * n_s / max(1, n_t))) - 1))
        out.append({
            "text": t,
            "start": int(stamps[s_idx][0]),
            "end": int(stamps[e_idx][1]),
        })
    return out


def transcribe_paraformer(wav: Path, device: str) -> tuple[str, list[list[int]]]:
    from funasr import AutoModel

    ms_root = Path.home() / ".cache" / "modelscope" / "models"
    local_asr = (
        ms_root
        / "iic--speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
        / "snapshots"
        / "master"
    )
    local_vad = ms_root / "iic--speech_fsmn_vad_zh-cn-16k-common-pytorch" / "snapshots" / "master"

    asr_model_arg = str(local_asr) if local_asr.exists() else "paraformer-zh"
    vad_model_arg = str(local_vad) if local_vad.exists() else "fsmn-vad"

    model = AutoModel(
        model=asr_model_arg,
        vad_model=vad_model_arg,
        device=device,
        disable_update=True,
    )
    res = model.generate(input=str(wav), batch_size_s=300)
    text = res[0]["text"]
    stamps = res[0].get("timestamp")
    if not stamps:
        sys.exit("FunASR Paraformer returned no timestamps.")
    return text, stamps


def _preload_cuda12_libs():
    """
    Preload libcublas.so.12 and libcublasLt.so.12 with RTLD_GLOBAL when present in the
    virtual environment so ctranslate2 (compiled for CUDA 12) can run alongside PyTorch CUDA 13.
    """
    import ctypes

    try:
        import nvidia.cublas.lib

        lib_dir = Path(nvidia.cublas.lib.__file__).parent
        for lib_name in ("libcublas.so.12", "libcublasLt.so.12"):
            lib_path = lib_dir / lib_name
            if lib_path.exists():
                ctypes.CDLL(str(lib_path), mode=ctypes.RTLD_GLOBAL)
    except Exception:
        pass


def transcribe_whisper(
    wav: Path,
    device: str,
    lang: str | None = None,
    model_size: str = "large-v3-turbo",
) -> tuple[str, list[list[int]]]:
    """
    Run faster-whisper with word-level timestamps and normalize output to match build_tokens(text, stamps).
    Loads the 16kHz mono waveform via librosa to bypass PyAV compatibility issues, and uses local Hugging Face
    cache snapshots first to avoid unnecessary Hugging Face Hub API checks.
    """
    import librosa
    from faster_whisper import WhisperModel

    whisper_device = "cuda" if device.startswith("cuda") else "cpu"
    compute_type = "float16" if whisper_device == "cuda" else "int8"

    if whisper_device == "cuda":
        _preload_cuda12_libs()

    model = None
    for local_only in (True, False):
        try:
            model = WhisperModel(
                model_size,
                device=whisper_device,
                compute_type=compute_type,
                local_files_only=local_only,
            )
            break
        except Exception:
            continue

    if model is None:
        model = WhisperModel("base", device=whisper_device, compute_type="int8")

    # Decode directly to 16kHz float32 numpy array (bypasses av.open metadata_errors incompatibility)
    audio_16k, _ = librosa.load(str(wav), sr=16000, mono=True)

    whisper_lang = lang if lang in ("en", "zh", "ja", "ko") else None
    # Note: vad_filter=False is critical for singing vocals because Silero speech VAD clips out
    # sustained/high-register singing in choruses.
    segments, info = model.transcribe(
        audio_16k,
        language=whisper_lang,
        word_timestamps=True,
        vad_filter=False,
        condition_on_previous_text=False,
        repetition_penalty=1.15,
        no_repeat_ngram_size=3,
    )

    print(f"  [Whisper] Detected language: {info.language} (prob={info.language_probability:.2f})")

    tok_strings: list[str] = []
    stamps: list[list[int]] = []

    for seg in segments:
        words = getattr(seg, "words", None) or []
        for w in words:
            w_raw = (w.word or "").strip()
            if not w_raw:
                continue
            w_start = int(round(w.start * 1000))
            w_end = max(w_start + 60, int(round(w.end * 1000)))
            sub_toks = tokenize_text(w_raw, lang=whisper_lang or info.language)
            if not sub_toks:
                continue
            if len(sub_toks) == 1:
                tok_strings.append(sub_toks[0])
                stamps.append([w_start, w_end])
            else:
                dur = max(len(sub_toks) * 40, w_end - w_start)
                step = dur / len(sub_toks)
                for idx, st_tok in enumerate(sub_toks):
                    ts = int(round(w_start + idx * step))
                    te = int(round(w_start + (idx + 1) * step))
                    tok_strings.append(st_tok)
                    stamps.append([ts, max(ts + 40, te)])

    text = " ".join(tok_strings)
    return text, stamps


def transcribe_sensevoice(wav: Path, device: str, lang: str | None = None) -> tuple[str, list[list[int]]]:
    """
    Run FunASR SenseVoiceSmall with VAD segmentation and distribute timestamps across tokens.
    """
    from funasr import AutoModel

    ms_root = Path.home() / ".cache" / "modelscope" / "models"
    local_vad = ms_root / "iic--speech_fsmn_vad_zh-cn-16k-common-pytorch" / "snapshots" / "master"
    vad_model_arg = str(local_vad) if local_vad.exists() else "fsmn-vad"

    model = AutoModel(
        model="iic/SenseVoiceSmall",
        vad_model=vad_model_arg,
        vad_kwargs={"max_single_segment_time": 12000},
        device=device,
        disable_update=True,
    )
    sv_lang = lang if lang in ("en", "zh", "ja", "ko") else "auto"
    res = model.generate(
        input=str(wav),
        cache={},
        language=sv_lang,
        use_itn=False,
        batch_size_s=60,
        merge_vad=False,
    )

    tok_strings: list[str] = []
    stamps: list[list[int]] = []

    for item in res:
        raw_txt = re.sub(r"<\|[^>]+?\|>", " ", item.get("text", "")).strip()
        if not raw_txt:
            continue
        sub_toks = tokenize_text(raw_txt, lang=lang)
        if not sub_toks:
            continue
        item_stamps = item.get("timestamp")
        if item_stamps and len(item_stamps) == len(sub_toks):
            for st_tok, s in zip(sub_toks, item_stamps):
                tok_strings.append(st_tok)
                stamps.append([int(s[0]), int(s[1])])
        else:
            # Distribute segment bounds proportionally across tokens
            seg_s = int(item_stamps[0][0]) if item_stamps else (stamps[-1][1] + 200 if stamps else 0)
            seg_e = int(item_stamps[-1][1]) if item_stamps else seg_s + len(sub_toks) * 280
            dur = max(len(sub_toks) * 60, seg_e - seg_s)
            step = dur / len(sub_toks)
            for idx, st_tok in enumerate(sub_toks):
                ts = int(round(seg_s + idx * step))
                te = int(round(seg_s + (idx + 1) * step))
                tok_strings.append(st_tok)
                stamps.append([ts, max(ts + 50, te)])

    text = " ".join(tok_strings)
    return text, stamps


def resolve_asr_engine(engine: str | None, lang: str | None, workdir: Path, vid: str) -> str:
    eng = (engine or "auto").lower().strip()
    if eng in ("paraformer", "whisper", "sensevoice"):
        return eng

    # Auto routing based on target language or metadata hints
    norm_lang = (lang or "auto").lower().strip()
    if norm_lang == "zh":
        return "paraformer"
    if norm_lang in ("en", "ja", "ko"):
        return "whisper"

    # Check project metadata title for explicit language tags or Kana/Hangul
    meta_path = workdir / f"{vid}.meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            title = meta.get("title", "")
            if has_kana(title) or any(is_hangul(ch) for ch in title):
                return "whisper"
            if re.search(r"\b(en|eng|english|jp|jpn|japanese|kr|kor|korean)\s*(ver|version)?\b", title, re.I):
                return "whisper"
        except Exception:
            pass

    return "paraformer"


def transcribe(
    wav: Path,
    workdir: Path,
    vid: str,
    device: str,
    engine: str = "auto",
    lang: str | None = None,
    force_asr: bool = False,
) -> tuple[str, list[list[int]]]:
    resolved_engine = resolve_asr_engine(engine, lang, workdir, vid)

    engine_cache = workdir / f"{vid}.asr.{resolved_engine}.json"
    para_cache = workdir / f"{vid}.asr.paraformer.json"
    legacy_cache = workdir / f"{vid}.asr.json"  # legacy name: paraformer output only

    def _load(p: Path) -> dict | None:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return d if d.get("text") is not None and d.get("stamps") is not None else None
        except Exception:
            return None

    if not force_asr:
        cached = _load(engine_cache) if engine_cache.exists() else None
        if cached is not None and resolved_engine != "paraformer":
            # Older versions saved a *paraformer fallback* under the whisper/sensevoice name.
            # Detect that (declared engine mismatch, or text identical to the paraformer cache) and ignore it.
            poisoned = cached.get("engine") not in (None, resolved_engine)
            for other in (para_cache, legacy_cache):
                if not poisoned and other.exists():
                    o = _load(other)
                    if o is not None and o.get("text") == cached.get("text"):
                        poisoned = True
            if poisoned:
                print(f"  [!] Ignoring poisoned {resolved_engine} ASR cache (paraformer output saved under wrong engine): {engine_cache}")
                cached = None
        if cached is not None:
            print(f"[3/4] Reusing cached ASR timestamps ({resolved_engine}): {engine_cache}")
            return cached["text"], cached["stamps"]
        if resolved_engine == "paraformer" and legacy_cache.exists():
            cached = _load(legacy_cache)
            if cached is not None and cached.get("engine") in (None, "paraformer"):
                print(f"[3/4] Reusing cached ASR timestamps (paraformer): {legacy_cache}")
                return cached["text"], cached["stamps"]

    print(f"[3/4] Transcribing with ASR engine '{resolved_engine}' (lang={lang or 'auto'})...")
    used_engine = resolved_engine
    try:
        if resolved_engine == "whisper":
            text, stamps = transcribe_whisper(wav, device, lang=lang)
        elif resolved_engine == "sensevoice":
            text, stamps = transcribe_sensevoice(wav, device, lang=lang)
        else:
            text, stamps = transcribe_paraformer(wav, device)
    except Exception as e:
        if resolved_engine != "paraformer":
            print(f"  [!] ASR engine '{resolved_engine}' failed ({e}); falling back to 'paraformer' (NOT cached as {resolved_engine})...")
            used_engine = "paraformer"
            text, stamps = transcribe_paraformer(wav, device)
        else:
            raise

    payload = json.dumps(
        {"engine": used_engine, "lang": lang or "auto", "text": text, "stamps": stamps},
        ensure_ascii=False,
    )
    (workdir / f"{vid}.asr.{used_engine}.json").write_text(payload, encoding="utf-8")
    if used_engine == "paraformer":
        legacy_cache.write_text(payload, encoding="utf-8")
    return text, stamps

