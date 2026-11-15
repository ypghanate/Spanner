"""Deepgram Nova-3 speech-to-text for the phone microphone. No keyword lists.

    from .voice import stt_deepgram as stt
    tr = await stt.transcribe(audio_bytes, "clip.m4a")
    tr.text         the whole transcript
    tr.n_speakers   how many different voices Deepgram heard in the clip
    tr.labeled      "Voice 1: ... / Voice 2: ..." when there are several voices, else ""
    stt.noise_reason(tr)   None = real speech, else why it was dropped (empty / unreadable only)

Deciding which voice is talking to Spanner is left to the conversation model (coach._converse).
Needs: pip install deepgram-sdk   and   DEEPGRAM_API_KEY in the environment.
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache

# Starting guesses, not measured values: log the drops and tune on real recordings
MIN_CONF = float(os.getenv("STT_MIN_CONF", "0.6"))             # whole utterance
MIN_WORD_CONF = float(os.getenv("STT_MIN_WORD_CONF", "0.5"))   # average over words


@dataclass
class Transcript:
    text: str
    confidence: float | None = None
    word_confidences: list[float] = field(default_factory=list)
    n_speakers: int = 1
    labeled: str = ""


@lru_cache(maxsize=1)
def _client():
    from deepgram import DeepgramClient

    return DeepgramClient()          # reads DEEPGRAM_API_KEY


def _label_turns(words) -> tuple[int, str]:
    """Group consecutive words by speaker. Returns (number of voices, labeled transcript or "")."""
    ids = {getattr(w, "speaker", None) for w in words} - {None}
    if len(ids) < 2:
        return max(len(ids), 1), ""
    turns, cur, buf = [], None, []
    for w in words:
        s = getattr(w, "speaker", None)
        token = getattr(w, "punctuated_word", None) or getattr(w, "word", "")
        if buf and s != cur:
            turns.append(f"Voice {cur + 1}: {' '.join(buf)}")
            buf = []
        cur = s
        buf.append(token)
    if buf:
        turns.append(f"Voice {cur + 1}: {' '.join(buf)}")
    return len(ids), "\n".join(turns)


def _run(audio: bytes) -> Transcript:
    resp = _client().listen.v1.media.transcribe_file(
        request=audio, model="nova-3", language="en", smart_format=True, diarize=True)
    channels = resp.results.channels
    alt = channels[0].alternatives[0] if channels and channels[0].alternatives else None
    if alt is None:
        return Transcript("")
    words = getattr(alt, "words", None) or []
    confs = [w.confidence for w in words if getattr(w, "confidence", None) is not None]
    n, labeled = _label_turns(words)
    return Transcript((alt.transcript or "").strip(), getattr(alt, "confidence", None), confs, n, labeled)


async def transcribe(audio: bytes, filename: str = "clip.m4a") -> Transcript:
    """Same call shape as the old stt.transcribe. Deepgram detects the container itself, so filename is unused."""
    return await asyncio.to_thread(_run, audio)


def noise_reason(tr: Transcript) -> str | None:
    """Only drops clips with nothing usable in them. Background talk is handled by the conversation model."""
    if not tr.text.strip():
        return "empty (silence or noise)"
    if not re.findall(r"[a-z']+", tr.text.lower()):
        return "no words"
    if tr.confidence is not None and tr.confidence < MIN_CONF:
        return f"low confidence {tr.confidence:.2f}"
    if tr.word_confidences:
        mean = sum(tr.word_confidences) / len(tr.word_confidences)
        if mean < MIN_WORD_CONF:
            return f"low word confidence {mean:.2f}"
    return None