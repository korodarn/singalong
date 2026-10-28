"""
core.asr - Acoustic Speech Recognition (ASR) engines and token builders.
"""

import json
import sys
from pathlib import Path
from core.phonetics import tokenize_text


def build_tokens(text: str, stamps):
    toks = tokenize_text(text)
    return [{"text": t, "start": s[0], "end": s[1]} for t, s in zip(toks, stamps)]


def transcribe_paraformer(wav: Path, device: str):
    from funasr import AutoModel

    ms_root = Path.home() / ".cache" / "modelscope" / "models"
    local_asr = ms_root / "iic--speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch" / "snapshots" / "master"
    local_vad = ms_root / "iic--speech_fsmn_vad_zh-cn-16k-common-pytorch" / "snapshots" / "master"

    asr_model_arg = str(local_asr) if local_asr.exists() else "paraformer-zh"
    vad_model_arg = str(local_vad) if local_vad.exists() else "fsmn-vad"

    model = AutoModel(model=asr_model_arg, vad_model=vad_model_arg,
                      device=device, disable_update=True)
    res = model.generate(input=str(wav), batch_size_s=300)
    text = res[0]["text"]
    stamps = res[0].get("timestamp")
    if not stamps:
        sys.exit("FunASR returned no timestamps.")
    return text, stamps


def transcribe(wav: Path, workdir: Path, vid: str, device: str, engine: str = "paraformer"):
    cache_path = workdir / f"{vid}.asr.json"
    if cache_path.exists():
        print(f"[3/4] Reusing cached ASR timestamps: {cache_path}")
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        return cached["text"], cached["stamps"]

    print(f"[3/4] Transcribing with engine '{engine}'...")
    text, stamps = transcribe_paraformer(wav, device)

    cache_path.write_text(json.dumps({"text": text, "stamps": stamps}, ensure_ascii=False), encoding="utf-8")
    return text, stamps
