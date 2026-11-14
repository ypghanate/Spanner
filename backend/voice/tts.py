"""Text-to-speech (ElevenLabs), cached on disk by a hash of voice + model + text.

* start(text): returns the cache filename at once; generation runs in a background task.
* wait_for(filename): used by GET /tts/{file}.mp3 to wait for that generation.
* stream(text): audio chunks as they are produced, for the live voice socket. Cancellable
  (barge-in); a cancelled stream never leaves a half-written file in the cache.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import AsyncIterator

from ..config import get_settings

log = logging.getLogger(__name__)

_CHUNK = 8192
_client = None
_inflight: dict[str, asyncio.Task] = {}
_errors: dict[str, str] = {}


def _get_client():
    global _client
    if _client is None:
        from elevenlabs.client import AsyncElevenLabs

        _client = AsyncElevenLabs(api_key=get_settings().elevenlabs_api_key)
    return _client


async def close_client() -> None:
    global _client
    for t in list(_inflight.values()):
        t.cancel()
    _inflight.clear()
    _client = None


def available() -> bool:
    s = get_settings()
    return bool(s.elevenlabs_api_key and s.elevenlabs_voice_id)


def cache_dir() -> Path:
    d = get_settings().media_cache_dir / "tts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def tts_filename(text: str) -> str:
    s = get_settings()
    key = f"{s.elevenlabs_voice_id}|{s.eleven_tts_model}|{s.eleven_tts_format}|{text}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16] + ".mp3"


def _is_safe_name(filename: str) -> bool:
    return filename == Path(filename).name and filename.endswith(".mp3") and ".." not in filename


async def _open_stream(text: str) -> AsyncIterator[bytes]:
    """The ElevenLabs call. Tests replace this."""
    s = get_settings()
    return _get_client().text_to_speech.stream(
        s.elevenlabs_voice_id,
        text=text,
        model_id=s.eleven_tts_model,
        output_format=s.eleven_tts_format,
    )


async def _generate(text: str, path: Path) -> None:
    part = path.with_suffix(".part")
    try:
        with part.open("wb") as f:
            async for chunk in await _open_stream(text):
                if chunk:
                    f.write(chunk)
        part.replace(path)
    except asyncio.CancelledError:
        part.unlink(missing_ok=True)
        raise
    except Exception as e:
        part.unlink(missing_ok=True)
        _errors[path.name] = str(e)
        log.warning("TTS generation failed: %s", e)
        raise


def start(text: str) -> str | None:
    """Start generating speech for `text`. Returns the filename, or None if nothing to say
    or TTS is unavailable (the app then falls back to expo-speech). Needs a running event loop."""
    text = (text or "").strip()
    if not text or not available():
        return None
    name = tts_filename(text)
    path = cache_dir() / name
    if path.exists() or name in _inflight:
        return name
    _errors.pop(name, None)
    task = asyncio.get_running_loop().create_task(_generate(text, path), name=f"tts:{name}")
    _inflight[name] = task
    task.add_done_callback(lambda t, n=name: (_inflight.pop(n, None), t.exception() if not t.cancelled() else None))
    return name


async def wait_for(filename: str, timeout: float = 8.0) -> Path | None:
    if not _is_safe_name(filename):
        return None
    path = cache_dir() / filename
    if path.exists():
        return path
    task = _inflight.get(filename)
    if task is None:
        return None
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout)
    except (asyncio.TimeoutError, Exception):
        return None
    return path if path.exists() else None


async def stream(text: str) -> AsyncIterator[bytes]:
    """Yield audio for `text` as it is produced. Cancel the consuming task to stop (barge-in)."""
    text = (text or "").strip()
    if not text or not available():
        return
    path = cache_dir() / tts_filename(text)
    if path.exists():
        data = path.read_bytes()
        for i in range(0, len(data), _CHUNK):
            yield data[i:i + _CHUNK]
            await asyncio.sleep(0)
        return

    part = path.with_suffix(f".{id(object())}.part")
    complete = False
    try:
        with part.open("wb") as f:
            async for chunk in await _open_stream(text):
                if not chunk:
                    continue
                f.write(chunk)
                yield chunk
        complete = True
    finally:
        if complete:
            part.replace(path)
        else:
            part.unlink(missing_ok=True)
