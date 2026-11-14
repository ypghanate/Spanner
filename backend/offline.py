"""Recorded-video helpers (ffmpeg + faster-whisper).

Turns a video file into what the rest of Spanner works with:
  - frames: 1 per second, max 768 px, named 00001.jpg, 00002.jpg, ... (frame n = second n-1)
  - audio:  16 kHz mono WAV
  - transcript: word-level timestamps

Used by the training pipeline (training/pipeline.py) and for replaying recorded
videos through the live pipeline during evaluation.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

FRAME_FPS = 1
FRAME_MAX_SIDE = 768


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} not found on PATH. Install ffmpeg first.")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def video_duration(video: Path) -> float:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", str(video)])
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {video}: {out.stderr.strip()}")
    return float(out.stdout.strip())


def has_audio(video: Path) -> bool:
    out = _run(["ffprobe", "-v", "error", "-select_streams", "a",
                "-show_entries", "stream=index", "-of", "csv=p=0", str(video)])
    return bool(out.stdout.strip())


def extract_frames(video: Path, out_dir: Path, fps: int = FRAME_FPS,
                   max_side: int = FRAME_MAX_SIDE, force: bool = False) -> list[Path]:
    """Extract `fps` frames per second, scaled to fit inside max_side x max_side."""
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(out_dir.glob("*.jpg"))
    if existing and not force:
        return existing
    for f in existing:
        f.unlink()
    scale = (f"scale={max_side}:{max_side}:force_original_aspect_ratio=decrease"
             ":force_divisible_by=2")
    out = _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
                "-vf", f"fps={fps},{scale}", "-q:v", "3", str(out_dir / "%05d.jpg")])
    if out.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extraction failed: {out.stderr.strip()}")
    return sorted(out_dir.glob("*.jpg"))


def frame_time(frame: Path, fps: int = FRAME_FPS) -> float:
    """Seconds into the video for a frame named 00001.jpg (= 0 s), 00002.jpg (= 1 s), ..."""
    return (int(frame.stem) - 1) / fps


def extract_audio(video: Path, wav: Path, force: bool = False) -> Path | None:
    """16 kHz mono WAV. Returns None if the video has no audio track."""
    if wav.exists() and not force:
        return wav
    if not has_audio(video):
        return None
    wav.parent.mkdir(parents=True, exist_ok=True)
    out = _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
                "-vn", "-ar", "16000", "-ac", "1", str(wav)])
    if out.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {out.stderr.strip()}")
    return wav


def transcribe(wav: Path | None, out_json: Path, model_size: str = "small",
               language: str | None = "en", vocabulary: list[str] | None = None,
               force: bool = False) -> dict:
    """Word-level transcript with faster-whisper (local, CPU). Cached to out_json.

    `vocabulary` (part and tool names) is given to Whisper as a hint so it spells
    domain words like "derailleur" correctly.

    Returns {"language": str | None, "words": [{"start": s, "end": s, "word": str}, ...]}.
    """
    if out_json.exists() and not force:
        return json.loads(out_json.read_text(encoding="utf-8"))
    result: dict = {"language": None, "words": []}
    if wav is not None:
        from faster_whisper import WhisperModel  # heavy import, only when needed

        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        # Pass samples, not a path: faster-whisper's own decoder breaks with newer PyAV.
        segments, info = model.transcribe(
            _read_wav(wav), language=language, word_timestamps=True, vad_filter=True,
            initial_prompt=("Repair vocabulary: " + ", ".join(vocabulary)) if vocabulary else None)
        result["language"] = info.language
        for seg in segments:
            for w in seg.words or []:
                result["words"].append({"start": round(w.start, 2), "end": round(w.end, 2),
                                        "word": w.word.strip()})
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def _read_wav(wav: Path):
    """16-bit mono WAV (as written by extract_audio) -> float32 samples in [-1, 1]."""
    import wave

    import numpy as np

    with wave.open(str(wav), "rb") as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def transcript_slice(transcript: dict, start: float, end: float) -> str:
    """The words spoken between start and end seconds."""
    return " ".join(w["word"] for w in transcript["words"]
                    if w["end"] > start and w["start"] < end).strip()


def sharpness(frame: Path) -> float:
    """Variance of the Laplacian for a frame on disk (low = blurry). Same metric as the live
    preprocess stage: it reuses backend/frames.py."""
    import cv2

    from backend.frames import sharpness as _sharpness

    img = cv2.imread(str(frame))
    return _sharpness(img) if img is not None else 0.0
