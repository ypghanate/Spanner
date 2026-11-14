"""Speech-to-text.

* transcribe(): one-shot ElevenLabs Scribe on a finished clip. Backs the /audio fallback.
* LiveTranscriber: ElevenLabs realtime Scribe over a websocket. Phone PCM16 goes in,
  partial and final (utterance-committed) transcripts come out. The server commits an
  utterance after ~0.7 s of silence (CommitStrategy.VAD).
* MockLiveTranscriber: deterministic stand-in for tests and USE_MOCKS=1.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
from dataclasses import dataclass
from typing import AsyncIterator, Literal

from ..config import get_settings
from ..contracts import Transcript

log = logging.getLogger(__name__)

_BYTES_PER_SECOND = 16000 * 2  # PCM16 mono at 16 kHz


class SttError(RuntimeError):
    pass


# --- shared client ----------------------------------------------------------

_client = None


def _get_client():
    global _client
    if _client is None:
        key = get_settings().elevenlabs_api_key
        if not key:
            raise SttError("ELEVENLABS_API_KEY is not set")      # check before the (heavy) import
        from elevenlabs.client import AsyncElevenLabs

        _client = AsyncElevenLabs(api_key=key)
    return _client


async def close_client() -> None:
    global _client
    _client = None


def stt_available() -> bool:
    return bool(get_settings().elevenlabs_api_key)


def _clean_keyterms(keyterms: list[str] | None) -> list[str]:
    """ElevenLabs accepts at most 50 keyterms of at most 20 characters each."""
    out: list[str] = []
    for k in keyterms or []:
        k = k.strip().replace("_", " ")
        if k and len(k) <= 20 and k not in out:
            out.append(k)
    return out[:50]


# --- one-shot (fallback /audio route) --------------------------------------

async def transcribe(audio: bytes, filename: str, keyterms: list[str] | None = None,
                     duration_hint_s: float | None = None) -> Transcript:
    """Transcribe a finished clip. Raises SttError on any failure (5 s timeout, no retries)."""
    settings = get_settings()
    client = _get_client()
    content_type = mimetypes.guess_type(filename)[0] or "audio/mp4"
    kwargs: dict = {}
    terms = _clean_keyterms(keyterms)
    if terms:
        kwargs["keyterms"] = terms
    try:
        resp = await asyncio.wait_for(
            client.speech_to_text.convert(
                model_id=settings.eleven_stt_model,
                file=(filename, audio, content_type),
                **kwargs,
            ),
            timeout=settings.stt_timeout_s,
        )
    except asyncio.TimeoutError as e:
        raise SttError("speech-to-text timed out") from e
    except Exception as e:  # SDK raises its own ApiError family; treat all as STT failure
        raise SttError(f"speech-to-text failed: {e}") from e

    words = getattr(resp, "words", None) or []
    ends = [w.end for w in words if getattr(w, "end", None) is not None]
    duration = float(max(ends)) if ends else float(duration_hint_s or 0.0)
    return Transcript(
        text=(getattr(resp, "text", "") or "").strip(),
        language=getattr(resp, "language_code", None) or "en",
        duration_s=duration,
    )


# --- live -------------------------------------------------------------------

@dataclass
class LiveTranscript:
    kind: Literal["partial", "final", "error"]
    text: str = ""
    duration_s: float = 0.0
    message: str = ""


class LiveTranscriber:
    """One transcriber per live connection."""

    async def start(self, keyterms: list[str] | None = None) -> None: ...
    async def send(self, pcm_chunk: bytes) -> None: ...
    def results(self) -> AsyncIterator[LiveTranscript]: ...
    async def close(self) -> None: ...


class ElevenLabsLiveTranscriber(LiveTranscriber):
    def __init__(self) -> None:
        self._conn = None
        self._queue: asyncio.Queue[LiveTranscript | None] = asyncio.Queue()
        self._closing = False
        self._reconnects = 0
        self._keyterms: list[str] = []
        self._bytes_since_final = 0

    async def start(self, keyterms: list[str] | None = None) -> None:
        self._keyterms = _clean_keyterms(keyterms)
        await self._connect()

    async def _connect(self) -> None:
        from elevenlabs.realtime import AudioFormat, CommitStrategy, RealtimeEvents, ScribeRealtime

        s = get_settings()
        if not s.elevenlabs_api_key:
            raise SttError("ELEVENLABS_API_KEY is not set")
        options: dict = {
            "model_id": s.eleven_stt_live_model,
            "audio_format": AudioFormat.PCM_16000,
            "sample_rate": s.live_sample_rate,
            "commit_strategy": CommitStrategy.VAD,
            "vad_silence_threshold_secs": s.live_vad_silence_s,
        }
        if self._keyterms:
            options["keyterms"] = self._keyterms
        try:
            conn = await ScribeRealtime(api_key=s.elevenlabs_api_key).connect(options)
        except Exception as e:
            raise SttError(f"could not open realtime speech-to-text: {e}") from e

        conn.on(RealtimeEvents.PARTIAL_TRANSCRIPT, self._on_partial)
        conn.on(RealtimeEvents.COMMITTED_TRANSCRIPT, self._on_final)
        conn.on(RealtimeEvents.ERROR, self._on_error)
        conn.on(RealtimeEvents.CLOSE, self._on_close)
        self._conn = conn

    # callbacks run on the connection's message task, in our event loop
    def _on_partial(self, data: dict) -> None:
        text = (data.get("text") or "").strip()
        if text:
            self._queue.put_nowait(LiveTranscript("partial", text))

    def _on_final(self, data: dict) -> None:
        text = (data.get("text") or "").strip()
        duration = self._bytes_since_final / _BYTES_PER_SECOND
        self._bytes_since_final = 0
        if text:
            self._queue.put_nowait(LiveTranscript("final", text, duration_s=duration))

    def _on_error(self, data: dict) -> None:
        msg = data.get("error") or data.get("message") or str(data)
        log.warning("realtime STT error: %s", msg)
        self._queue.put_nowait(LiveTranscript("error", message=str(msg)))

    def _on_close(self, *_: object) -> None:
        if self._closing:
            return
        asyncio.get_running_loop().create_task(self._reconnect())

    async def _reconnect(self) -> None:
        if self._closing:
            return
        if self._reconnects >= 1:
            self._queue.put_nowait(LiveTranscript("error", message="speech-to-text connection lost"))
            return
        self._reconnects += 1
        try:
            await self._connect()
            log.info("realtime STT reconnected")
        except SttError as e:
            self._queue.put_nowait(LiveTranscript("error", message=str(e)))

    async def send(self, pcm_chunk: bytes) -> None:
        if self._conn is None or self._closing or not pcm_chunk:
            return
        self._bytes_since_final += len(pcm_chunk)
        try:
            await self._conn.send({"audio_base_64": base64.b64encode(pcm_chunk).decode("ascii")})
        except Exception as e:
            log.warning("realtime STT send failed: %s", e)

    async def results(self) -> AsyncIterator[LiveTranscript]:  # type: ignore[override]
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        self._closing = True
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None
        self._queue.put_nowait(None)


class MockLiveTranscriber(LiveTranscriber):
    """Emits a partial after the first speech chunk and a final after `utterance_s` of speech.

    Chunks that are all zeros count as silence and are ignored, like a real voice-activity detector."""

    def __init__(self, text: str = "Which size hex key do I need for the pad bolt?",
                 utterance_s: float = 1.0) -> None:
        self.text = text
        self.utterance_bytes = int(utterance_s * _BYTES_PER_SECOND)
        self._queue: asyncio.Queue[LiveTranscript | None] = asyncio.Queue()
        self._bytes = 0
        self._partial_sent = False

    async def start(self, keyterms: list[str] | None = None) -> None:
        return None

    async def send(self, pcm_chunk: bytes) -> None:
        if not pcm_chunk or not any(pcm_chunk):
            return                        # empty or all-zero audio is silence: it doesn't count as speech
        self._bytes += len(pcm_chunk)
        if not self._partial_sent:
            self._partial_sent = True
            self._queue.put_nowait(LiveTranscript("partial", self.text.split(" ")[0]))
        if self._bytes >= self.utterance_bytes:
            self._queue.put_nowait(LiveTranscript("final", self.text, duration_s=self._bytes / _BYTES_PER_SECOND))
            self._bytes = 0
            self._partial_sent = False

    async def results(self) -> AsyncIterator[LiveTranscript]:  # type: ignore[override]
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        self._queue.put_nowait(None)


def make_live_transcriber() -> LiveTranscriber:
    """Real transcriber when mocks are off and a key is set, otherwise the mock."""
    s = get_settings()
    if s.use_mocks or not s.elevenlabs_api_key:
        return MockLiveTranscriber()
    return ElevenLabsLiveTranscriber()
